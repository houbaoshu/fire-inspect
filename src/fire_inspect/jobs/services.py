from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from fire_inspect.audit.models import AuditEvent
from fire_inspect.jobs.models import JobAttempt, ProcessingJob
from fire_inspect.operations.redaction import redact_text


class IdempotencyConflict(Exception):
    pass


class LostLease(Exception):
    pass


class InvalidJobTransition(Exception):
    pass


class StaleJobInput(RuntimeError):
    """The scoped input changed while a worker was processing its snapshot."""


class RecoverableJobFailure(RuntimeError):
    """A domain-safe failure that the worker may expose without leaking internals."""

    def __init__(self, *, code: str, message: str):
        self.code = str(code or "processing_failed")[:100]
        super().__init__(message)


@transaction.atomic
def enqueue_job(
    *,
    task_type: str,
    scope_type: str,
    scope_id,
    inspection,
    input_fingerprint: str,
    idempotency_key: str,
    created_by,
    expected_scope_revision: int | None = None,
    max_attempts: int = 3,
    available_at=None,
) -> tuple[ProcessingJob, bool]:
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key:
        raise ValueError("idempotency key is required")
    inspection_id = getattr(inspection, "id", None)
    existing = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        same_command = (
            existing.task_type == task_type
            and existing.scope_type == scope_type
            and existing.scope_id == scope_id
            and existing.inspection_id == inspection_id
            and existing.input_fingerprint == input_fingerprint
        )
        if not same_command:
            raise IdempotencyConflict("idempotency key was already used for another command")
        return existing, False
    same_input = ProcessingJob.objects.filter(
        task_type=task_type,
        scope_type=scope_type,
        scope_id=scope_id,
        inspection_id=inspection_id,
        input_fingerprint=input_fingerprint,
    ).first()
    if same_input:
        return same_input, False
    try:
        with transaction.atomic():
            job = ProcessingJob.objects.create(
                task_type=task_type,
                scope_type=scope_type,
                scope_id=scope_id,
                inspection=inspection,
                input_fingerprint=input_fingerprint,
                idempotency_key=idempotency_key,
                expected_scope_revision=expected_scope_revision,
                max_attempts=max_attempts,
                available_at=available_at or timezone.now(),
                created_by=created_by,
            )
    except IntegrityError as exc:
        raced_by_key = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
        if raced_by_key is not None:
            same_command = (
                raced_by_key.task_type == task_type
                and raced_by_key.scope_type == scope_type
                and raced_by_key.scope_id == scope_id
                and raced_by_key.inspection_id == inspection_id
                and raced_by_key.input_fingerprint == input_fingerprint
            )
            if not same_command:
                raise IdempotencyConflict(
                    "idempotency key was already used for another command"
                ) from exc
            return raced_by_key, False
        job = ProcessingJob.objects.filter(
            task_type=task_type,
            scope_type=scope_type,
            scope_id=scope_id,
            inspection_id=inspection_id,
            input_fingerprint=input_fingerprint,
        ).first()
        if job is None:
            raise
        return job, False
    AuditEvent.append(
        actor=created_by,
        object_type="processing_job",
        object_id=job.id,
        inspection_id_snapshot=inspection_id,
        action="queued",
        masked_change_summary=f"job queued: task_type={task_type}",
    )
    return job, True


def _lockable(queryset):
    if connection.features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True)
    return queryset.select_for_update()


@transaction.atomic
def acquire_job(
    *, worker_id: str, lease_seconds: int | None = None, task_types: list[str] | None = None
) -> ProcessingJob | None:
    # Backups create this marker before waiting for active jobs.  Existing leases
    # may finish, but no worker may claim another write while the snapshot is taken.
    if settings.FIRE_INSPECT_MAINTENANCE_FILE.exists():
        return None
    now = timezone.now()
    _fail_exhausted_expired_jobs(now)
    queryset = ProcessingJob.objects.filter(attempt_count__lt=F("max_attempts")).filter(
        Q(status=ProcessingJob.Status.QUEUED, available_at__lte=now)
        | Q(status=ProcessingJob.Status.RUNNING, lease_expires_at__lt=now)
    )
    if task_types:
        queryset = queryset.filter(task_type__in=task_types)
    job = _lockable(queryset.order_by("available_at", "created_at", "id")).first()
    if job is None:
        return None
    return _claim_locked_job(job, worker_id=worker_id, lease_seconds=lease_seconds, now=now)


def _claim_locked_job(
    job: ProcessingJob,
    *,
    worker_id: str,
    lease_seconds: int | None,
    now,
) -> ProcessingJob:
    if job.status == ProcessingJob.Status.RUNNING:
        JobAttempt.objects.filter(
            job=job, attempt_no=job.attempt_count, lease_token=job.lease_token
        ).update(finished_at=now, outcome="lease_expired", safe_error_code="lease_expired")
    job.attempt_count += 1
    job.status = ProcessingJob.Status.RUNNING
    job.lease_owner = worker_id[:200]
    job.lease_token = secrets.token_urlsafe(32)
    job.lease_expires_at = now + timedelta(
        seconds=lease_seconds or settings.FIRE_INSPECT_JOB_LEASE_SECONDS
    )
    job.started_at = job.started_at or now
    job.finished_at = None
    job.safe_error_code = ""
    job.safe_error_message = ""
    job.save(
        update_fields=(
            "attempt_count",
            "status",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "started_at",
            "finished_at",
            "safe_error_code",
            "safe_error_message",
        )
    )
    JobAttempt.objects.create(
        job=job,
        attempt_no=job.attempt_count,
        lease_token=job.lease_token,
        capability=job.task_type,
        started_at=now,
    )
    return job


@transaction.atomic
def acquire_job_by_id(
    *, job_id, worker_id: str, lease_seconds: int | None = None
) -> ProcessingJob | None:
    """Atomically claim one specific eligible job without consuming another queue item."""

    if settings.FIRE_INSPECT_MAINTENANCE_FILE.exists():
        return None
    now = timezone.now()
    _fail_exhausted_expired_jobs(now)
    queryset = (
        ProcessingJob.objects.filter(pk=job_id, attempt_count__lt=F("max_attempts"))
        .filter(
            Q(status=ProcessingJob.Status.QUEUED, available_at__lte=now)
            | Q(status=ProcessingJob.Status.RUNNING, lease_expires_at__lt=now)
        )
        .order_by("available_at", "created_at", "id")
    )
    job = _lockable(queryset).first()
    if job is None:
        return None
    return _claim_locked_job(job, worker_id=worker_id, lease_seconds=lease_seconds, now=now)


def _fail_exhausted_expired_jobs(now) -> None:
    queryset = ProcessingJob.objects.filter(
        status=ProcessingJob.Status.RUNNING,
        lease_expires_at__lt=now,
        attempt_count__gte=F("max_attempts"),
    )
    for job in _lockable(queryset.order_by("created_at", "id")):
        JobAttempt.objects.filter(
            job=job, attempt_no=job.attempt_count, lease_token=job.lease_token
        ).update(
            finished_at=now,
            outcome="failed",
            safe_error_code="lease_expired",
        )
        job.status = ProcessingJob.Status.FAILED
        job.finished_at = now
        job.safe_error_code = "lease_expired"
        job.safe_error_message = "任务租约已过期，且已达到自动重试上限。"
        job.lease_owner = ""
        job.lease_token = ""
        job.lease_expires_at = None
        job.save(
            update_fields=(
                "status",
                "finished_at",
                "safe_error_code",
                "safe_error_message",
                "lease_owner",
                "lease_token",
                "lease_expires_at",
            )
        )


def _locked_active_job(job_id, lease_token: str) -> ProcessingJob:
    try:
        job = ProcessingJob.objects.select_for_update().get(pk=job_id)
    except ProcessingJob.DoesNotExist as exc:
        raise LostLease("job no longer exists") from exc
    if (
        job.status != ProcessingJob.Status.RUNNING
        or not lease_token
        or not secrets.compare_digest(job.lease_token, lease_token)
        or job.lease_expires_at is None
        or job.lease_expires_at <= timezone.now()
    ):
        raise LostLease("job lease is no longer valid")
    return job


@transaction.atomic
def assert_active_lease(*, job_id, lease_token: str) -> ProcessingJob:
    """Fence a publication transaction with the worker's current lease token."""

    return _locked_active_job(job_id, lease_token)


@transaction.atomic
def heartbeat_job(*, job_id, lease_token: str, lease_seconds: int | None = None) -> ProcessingJob:
    job = _locked_active_job(job_id, lease_token)
    job.lease_expires_at = timezone.now() + timedelta(
        seconds=lease_seconds or settings.FIRE_INSPECT_JOB_LEASE_SECONDS
    )
    job.save(update_fields=("lease_expires_at",))
    return job


def _finish_attempt(job: ProcessingJob, *, outcome: str, safe_error_code: str = "") -> None:
    JobAttempt.objects.filter(
        job=job, attempt_no=job.attempt_count, lease_token=job.lease_token
    ).update(finished_at=timezone.now(), outcome=outcome, safe_error_code=safe_error_code[:100])


@transaction.atomic
def complete_job(*, job_id, lease_token: str) -> ProcessingJob:
    job = _locked_active_job(job_id, lease_token)
    _finish_attempt(job, outcome="succeeded")
    job.status = ProcessingJob.Status.SUCCEEDED
    job.finished_at = timezone.now()
    job.lease_owner = ""
    job.lease_token = ""
    job.lease_expires_at = None
    job.save(
        update_fields=("status", "finished_at", "lease_owner", "lease_token", "lease_expires_at")
    )
    return job


@transaction.atomic
def fail_job(
    *, job_id, lease_token: str, safe_error_code: str, safe_error_message: str
) -> ProcessingJob:
    job = _locked_active_job(job_id, lease_token)
    _finish_attempt(job, outcome="failed", safe_error_code=safe_error_code)
    job.status = ProcessingJob.Status.FAILED
    job.finished_at = timezone.now()
    job.safe_error_code = safe_error_code[:100]
    job.safe_error_message = redact_text(
        safe_error_message,
        sensitive_roots=(
            str(settings.MEDIA_ROOT),
            str(settings.FIRE_INSPECT_STAGING_ROOT),
        ),
    )[:500]
    job.lease_owner = ""
    job.lease_token = ""
    job.lease_expires_at = None
    job.save(
        update_fields=(
            "status",
            "finished_at",
            "safe_error_code",
            "safe_error_message",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
        )
    )
    return job


@transaction.atomic
def supersede_job(*, job_id, lease_token: str, message: str) -> ProcessingJob:
    job = _locked_active_job(job_id, lease_token)
    _finish_attempt(job, outcome="superseded", safe_error_code="input_changed")
    job.status = ProcessingJob.Status.SUPERSEDED
    job.finished_at = timezone.now()
    job.safe_error_code = "input_changed"
    job.safe_error_message = redact_text(
        message,
        sensitive_roots=(
            str(settings.MEDIA_ROOT),
            str(settings.FIRE_INSPECT_STAGING_ROOT),
        ),
    )[:500]
    job.lease_owner = ""
    job.lease_token = ""
    job.lease_expires_at = None
    job.save(
        update_fields=(
            "status",
            "finished_at",
            "safe_error_code",
            "safe_error_message",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
        )
    )
    return job


@transaction.atomic
def retry_job(*, job_id, actor, idempotency_key: str | None = None) -> ProcessingJob:
    job = ProcessingJob.objects.select_for_update().get(pk=job_id)
    idempotency_key = str(idempotency_key or "").strip() or None
    if idempotency_key:
        conflicting = ProcessingJob.objects.filter(
            last_retry_idempotency_key=idempotency_key
        ).exclude(pk=job.pk)
        if conflicting.exists():
            raise IdempotencyConflict("retry idempotency key belongs to another job")
        if job.last_retry_idempotency_key == idempotency_key:
            return job
    if job.status != ProcessingJob.Status.FAILED:
        raise InvalidJobTransition("only failed jobs can be retried")
    if job.attempt_count >= job.max_attempts:
        job.max_attempts = job.attempt_count + 1
    job.status = ProcessingJob.Status.QUEUED
    job.available_at = timezone.now()
    job.finished_at = None
    job.safe_error_code = ""
    job.safe_error_message = ""
    job.lease_owner = ""
    job.lease_token = ""
    job.lease_expires_at = None
    job.last_retry_idempotency_key = idempotency_key
    job.save(
        update_fields=(
            "max_attempts",
            "status",
            "available_at",
            "finished_at",
            "safe_error_code",
            "safe_error_message",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "last_retry_idempotency_key",
        )
    )
    AuditEvent.append(
        actor=actor,
        object_type="processing_job",
        object_id=job.id,
        inspection_id_snapshot=job.inspection_id,
        action="retried",
        masked_change_summary=f"job retried: task_type={job.task_type}",
    )
    return job
