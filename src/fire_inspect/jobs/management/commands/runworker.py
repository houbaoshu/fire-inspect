from __future__ import annotations

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections

from fire_inspect.ai.providers import CapabilityUnavailable, ProviderFailure
from fire_inspect.common.observability import correlation_context
from fire_inspect.jobs.registry import get_handler, registered_task_types
from fire_inspect.jobs.services import (
    LostLease,
    RecoverableJobFailure,
    StaleJobInput,
    acquire_job,
    complete_job,
    fail_job,
    heartbeat_job,
    supersede_job,
)
from fire_inspect.media_pipeline.ffmpeg import MediaPipelineError
from fire_inspect.transcripts.services import ManualTranscriptProtected

logger = logging.getLogger(__name__)


@contextmanager
def _lease_heartbeat(job_id, lease_token):
    stop = threading.Event()
    lost = threading.Event()
    interval = max(0.25, min(settings.FIRE_INSPECT_JOB_LEASE_SECONDS / 3, 30.0))

    def renew() -> None:
        while not stop.wait(interval):
            close_old_connections()
            try:
                heartbeat_job(job_id=job_id, lease_token=lease_token)
            except LostLease:
                lost.set()
                return
            except DatabaseError:
                lost.set()
                logger.warning("job heartbeat failed; treating lease as lost")
                return
            finally:
                close_old_connections()

    thread = threading.Thread(target=renew, name=f"job-heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=max(1.0, interval + 0.5))


class Command(BaseCommand):
    help = "Run the PostgreSQL-backed persistent job worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--concurrency", type=int, default=settings.FIRE_INSPECT_WORKER_CONCURRENCY
        )
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        concurrency = max(1, options["concurrency"])
        worker_prefix = f"{socket.gethostname()}:{id(self)}"
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            while True:
                futures = []
                for slot in range(concurrency):
                    job = acquire_job(
                        worker_id=f"{worker_prefix}:{slot}",
                        task_types=list(registered_task_types()) or None,
                    )
                    if job is not None:
                        futures.append(executor.submit(self._execute, job.id, job.lease_token))
                for future in futures:
                    future.result()
                if options["once"]:
                    return
                if not futures:
                    time.sleep(min(max(options["poll_seconds"], 0.1), 30.0))

    @staticmethod
    def _execute(job_id, lease_token):
        close_old_connections()
        from fire_inspect.jobs.models import ProcessingJob

        try:
            job = ProcessingJob.objects.get(pk=job_id)
        except ProcessingJob.DoesNotExist:
            logger.info("job was deleted before worker execution started")
            close_old_connections()
            return
        with correlation_context(job_id=str(job.id)):
            try:
                with _lease_heartbeat(job.id, lease_token) as lease_lost:
                    get_handler(job.task_type)(job)
                if lease_lost.is_set():
                    raise LostLease("job lease expired during processing")
            except LostLease:
                logger.info("job lease lost; stale worker result was not finalized")
            except StaleJobInput as exc:
                try:
                    supersede_job(
                        job_id=job.id,
                        lease_token=lease_token,
                        message=str(exc) or "任务输入已变化，本次结果未发布。",
                    )
                except LostLease:
                    logger.info("job lease lost before superseded status could be recorded")
            except (
                CapabilityUnavailable,
                ProviderFailure,
                MediaPipelineError,
                ManualTranscriptProtected,
                RecoverableJobFailure,
            ) as exc:
                logger.info("job processing reported a safe recoverable failure")
                try:
                    fail_job(
                        job_id=job.id,
                        lease_token=lease_token,
                        safe_error_code=(
                            exc.code
                            if isinstance(exc, RecoverableJobFailure)
                            else (
                                "capability_unavailable"
                                if isinstance(exc, CapabilityUnavailable)
                                else "provider_or_processing_failed"
                            )
                        ),
                        safe_error_message=str(exc) or "处理失败，可稍后单独重试。",
                    )
                except LostLease:
                    logger.info("job lease lost before failure status could be recorded")
            except Exception:
                logger.exception("job processing failed")
                try:
                    fail_job(
                        job_id=job.id,
                        lease_token=lease_token,
                        safe_error_code="processing_failed",
                        safe_error_message="处理失败，可稍后重试。",
                    )
                except LostLease:
                    logger.info("job lease lost before failure status could be recorded")
            else:
                try:
                    complete_job(job_id=job.id, lease_token=lease_token)
                except LostLease:
                    logger.info("job lease lost before success could be finalized")
            finally:
                close_old_connections()
