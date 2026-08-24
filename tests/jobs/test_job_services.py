from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.utils import timezone
from rest_framework import status

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import (
    IdempotencyConflict,
    LostLease,
    acquire_job,
    acquire_job_by_id,
    complete_job,
    enqueue_job,
    fail_job,
    heartbeat_job,
    retry_job,
)
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db(transaction=True)


def test_enqueue_is_idempotent_and_conflicting_reuse_is_rejected(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    first, created = enqueue_job(
        task_type="asr",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="input-v1",
        idempotency_key="same-click",
        created_by=inspector_a,
    )
    assert created is True
    second, created = enqueue_job(
        task_type="asr",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="input-v1",
        idempotency_key="same-click",
        created_by=inspector_a,
    )
    assert created is False
    assert second.id == first.id
    assert ProcessingJob.objects.count() == 1

    with pytest.raises(IdempotencyConflict):
        enqueue_job(
            task_type="asr",
            scope_type="inspection",
            scope_id=inspection.id,
            inspection=inspection,
            input_fingerprint="changed-input",
            idempotency_key="same-click",
            created_by=inspector_a,
        )


def test_two_claims_receive_distinct_jobs_and_attempts(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    jobs = [
        enqueue_job(
            task_type="keyframes",
            scope_type="material",
            scope_id=None,
            inspection=inspection,
            input_fingerprint=f"material-{number}",
            idempotency_key=f"key-{number}",
            created_by=inspector_a,
        )[0]
        for number in (1, 2)
    ]
    first = acquire_job(worker_id="worker-a", lease_seconds=30)
    second = acquire_job(worker_id="worker-b", lease_seconds=30)
    assert {first.id, second.id} == {job.id for job in jobs}
    assert first.lease_token != second.lease_token
    assert first.attempts.count() == 1
    assert second.attempts.count() == 1


def test_claim_by_id_never_consumes_another_eligible_job(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    first, _ = enqueue_job(
        task_type="keyframes",
        scope_type="material",
        scope_id=uuid4(),
        inspection=inspection,
        input_fingerprint="claim-specific-first",
        idempotency_key="claim-specific-first",
        created_by=inspector_a,
    )
    second, _ = enqueue_job(
        task_type="keyframes",
        scope_type="material",
        scope_id=uuid4(),
        inspection=inspection,
        input_fingerprint="claim-specific-second",
        idempotency_key="claim-specific-second",
        created_by=inspector_a,
    )

    claimed = acquire_job_by_id(job_id=second.id, worker_id="specific-worker")
    assert claimed is not None and claimed.id == second.id
    first.refresh_from_db()
    assert first.status == ProcessingJob.Status.QUEUED
    assert first.attempts.count() == 0
    assert acquire_job_by_id(job_id=second.id, worker_id="duplicate-worker") is None


def test_same_input_without_scope_id_is_not_reused_across_inspections(
    inspector_a, inspector_b
) -> None:
    first_inspection = create_inspection(inspector_a)
    second_inspection = create_inspection(inspector_b)
    first, first_created = enqueue_job(
        task_type="keyframes",
        scope_type="inspection",
        scope_id=None,
        inspection=first_inspection,
        input_fingerprint="same-input",
        idempotency_key="first-inspection",
        created_by=inspector_a,
    )
    second, second_created = enqueue_job(
        task_type="keyframes",
        scope_type="inspection",
        scope_id=None,
        inspection=second_inspection,
        input_fingerprint="same-input",
        idempotency_key="second-inspection",
        created_by=inspector_b,
    )

    assert first_created is True
    assert second_created is True
    assert first.id != second.id


def test_deleted_inspection_job_exits_worker_without_crashing(inspector_a) -> None:
    from fire_inspect.jobs.management.commands.runworker import Command

    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type="vision",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="deleted-before-execute",
        idempotency_key="deleted-before-execute",
        created_by=inspector_a,
    )[0]
    claimed = acquire_job(worker_id="worker", lease_seconds=30)
    inspection.delete()

    assert Command._execute(job.id, claimed.lease_token) is None


def test_heartbeat_database_error_marks_lease_lost(monkeypatch, settings) -> None:
    from fire_inspect.jobs.management.commands import runworker

    settings.FIRE_INSPECT_JOB_LEASE_SECONDS = 1

    def unavailable_database(**kwargs):
        del kwargs
        raise OperationalError("database unavailable")

    monkeypatch.setattr(runworker, "heartbeat_job", unavailable_database)
    with runworker._lease_heartbeat(uuid4(), "lease-token") as lease_lost:
        assert lease_lost.wait(timeout=1)
    assert lease_lost.is_set()


def test_fencing_token_prevents_expired_worker_from_publishing(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type="vision",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="v1",
        idempotency_key="vision-v1",
        created_by=inspector_a,
    )[0]
    claimed = acquire_job(worker_id="old-worker", lease_seconds=30)
    old_token = claimed.lease_token
    ProcessingJob.objects.filter(id=job.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    reclaimed = acquire_job(worker_id="new-worker", lease_seconds=30)
    assert reclaimed.id == job.id
    assert reclaimed.lease_token != old_token
    with pytest.raises(LostLease):
        complete_job(job_id=job.id, lease_token=old_token)
    complete_job(job_id=job.id, lease_token=reclaimed.lease_token)
    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED


def test_heartbeat_failure_and_retry_preserve_logical_job(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type="embedding",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="v1",
        idempotency_key="embedding-v1",
        created_by=inspector_a,
    )[0]
    claimed = acquire_job(worker_id="worker", lease_seconds=30)
    renewed = heartbeat_job(job_id=job.id, lease_token=claimed.lease_token, lease_seconds=60)
    assert renewed.lease_expires_at > claimed.lease_expires_at
    failed = fail_job(
        job_id=job.id,
        lease_token=claimed.lease_token,
        safe_error_code="provider_unavailable",
        safe_error_message="服务暂不可用，可稍后重试。",
    )
    assert failed.status == ProcessingJob.Status.FAILED
    retried = retry_job(job_id=job.id, actor=inspector_a)
    assert retried.id == job.id
    assert retried.status == ProcessingJob.Status.QUEUED
    assert ProcessingJob.objects.count() == 1


def test_retry_api_is_idempotent(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type="embedding",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="retry-api-v1",
        idempotency_key="initial-job-key",
        created_by=inspector_a,
    )[0]
    claimed = acquire_job(worker_id="worker", lease_seconds=30)
    fail_job(
        job_id=job.id,
        lease_token=claimed.lease_token,
        safe_error_code="provider_unavailable",
        safe_error_message="服务暂不可用。",
    )
    client = authenticated_client(inspector_a)
    url = f"/api/v1/jobs/{job.id}/retry"
    first = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="retry-command-key")
    second = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="retry-command-key")
    assert first.status_code == status.HTTP_202_ACCEPTED
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["id"] == second.json()["id"]
    assert ProcessingJob.objects.count() == 1


def test_expired_final_attempt_becomes_visible_failure(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type="vision",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="expires-v1",
        idempotency_key="expires-key",
        created_by=inspector_a,
        max_attempts=1,
    )[0]
    acquire_job(worker_id="crashed", lease_seconds=30)
    ProcessingJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert acquire_job(worker_id="next") is None
    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.FAILED
    assert job.safe_error_code == "lease_expired"
    assert job.attempts.get().outcome == "failed"


def test_material_job_api_is_scoped_and_idempotent(
    authenticated_client, inspector_a, inspector_b
) -> None:
    from fire_inspect.materials.models import Material

    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="content",
    )
    url = f"/api/v1/materials/{material.id}/jobs/fact_extraction"
    owner = authenticated_client(inspector_a)
    first = owner.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="job-key")
    assert first.status_code == status.HTTP_202_ACCEPTED
    second = owner.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="job-key")
    assert second.status_code == status.HTTP_200_OK
    assert second.json()["id"] == first.json()["id"]
    assert (
        authenticated_client(inspector_b)
        .post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="other-key")
        .status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_material_job_api_rejects_steps_that_cannot_run_for_the_material_kind(
    authenticated_client, inspector_a
) -> None:
    from fire_inspect.materials.models import Material

    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="大厅存在堆放物品。",
    )

    response = authenticated_client(inspector_a).post(
        f"/api/v1/materials/{material.id}/jobs/asr",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="invalid-text-asr",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "unsupported_material_job_step"
    assert ProcessingJob.objects.filter(scope_id=material.id).count() == 0
