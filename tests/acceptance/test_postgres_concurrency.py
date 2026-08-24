from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.conf import settings
from django.db import close_old_connections, connections
from tests.inspections.test_inspection_api import create_inspection

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job, enqueue_job


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_postgres_workers_claim_distinct_jobs_with_skip_locked(inspector_a) -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    inspection = create_inspection(inspector_a)
    expected_ids = {
        enqueue_job(
            task_type="keyframes",
            scope_type="material",
            scope_id=None,
            inspection=inspection,
            input_fingerprint=f"postgres-concurrency-{index}",
            idempotency_key=f"postgres-concurrency-{index}",
            created_by=inspector_a,
        )[0].id
        for index in (1, 2)
    }
    barrier = Barrier(2)

    def claim(worker_id: str):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            claimed = acquire_job(worker_id=worker_id, lease_seconds=60)
            assert claimed is not None
            return claimed.id, claimed.lease_token
        finally:
            # CONN_MAX_AGE deliberately keeps production connections alive, but
            # thread-owned test connections must not survive into database teardown.
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("postgres-worker-a", "postgres-worker-b")))

    claimed_ids = {job_id for job_id, _ in results}
    tokens = {token for _, token in results}
    assert claimed_ids == expected_ids
    assert len(tokens) == 2
    assert ProcessingJob.objects.filter(status=ProcessingJob.Status.RUNNING).count() == 2


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_postgres_unique_constraints_keep_repeated_submission_single(inspector_a) -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    inspection = create_inspection(inspector_a)
    first, first_created = enqueue_job(
        task_type="asr",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="same-revision",
        idempotency_key="postgres-repeat-first",
        created_by=inspector_a,
    )
    second, second_created = enqueue_job(
        task_type="asr",
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="same-revision",
        idempotency_key="postgres-repeat-second",
        created_by=inspector_a,
    )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert ProcessingJob.objects.count() == 1
