from __future__ import annotations

from types import SimpleNamespace

import pytest

from fire_inspect.ai.handlers import embedding_handler
from fire_inspect.ai.providers import DisabledProvider, FixedResponseProvider, ProviderFailure
from fire_inspect.ai.schemas import EmbeddingOutput
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import (
    LostLease,
    acquire_job,
    fail_job,
    retry_job,
)
from fire_inspect.knowledge.django_models import (
    KnowledgeClauseRecord,
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from fire_inspect.knowledge.services import (
    enqueue_knowledge_embedding,
    search_knowledge_with_status,
)

pytestmark = pytest.mark.django_db(transaction=True)


class StaticEmbeddingProvider:
    def __init__(self, outputs):
        self.outputs = outputs

    def embed(self, texts):
        del texts
        return self.outputs


class FailingEmbeddingProvider:
    def embed(self, texts):
        del texts
        raise ProviderFailure("Embedding 服务超时")


def knowledge_document(admin_user, *, clauses: int = 2):
    record = KnowledgeDocumentRecord.objects.create(
        title="消防知识测试文档",
        category=KnowledgeDocumentRecord.Category.LAW,
        original_file="knowledge/test.pdf",
        mime_type="application/pdf",
        sha256=f"{KnowledgeDocumentRecord.objects.count() + 1:064x}",
        byte_size=100,
        processing_status=KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED,
        uploaded_by=admin_user,
    )
    KnowledgeClauseRecord.objects.bulk_create(
        [
            KnowledgeClauseRecord(
                document=record,
                sequence=sequence,
                clause_identifier=f"第{sequence}条",
                content=(
                    "自动消防系统操作人员必须持证上岗"
                    if sequence == 1
                    else f"测试条款正文 {sequence}"
                ),
                page_start=sequence,
                page_end=sequence,
                normalized_text=f"测试条款正文{sequence}",
            )
            for sequence in range(1, clauses + 1)
        ]
    )
    return record


def run_embedding_job(record, admin_user, monkeypatch, provider):
    job, _ = enqueue_knowledge_embedding(record=record, actor=admin_user)
    claimed = acquire_job(worker_id="embedding-test", task_types=["embedding"])
    assert claimed is not None and claimed.id == job.id
    monkeypatch.setattr("fire_inspect.ai.handlers.provider_for", lambda capability: provider)
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    job.refresh_from_db()
    return job


def test_embedding_handler_publishes_complete_idempotent_batch(admin_user, monkeypatch) -> None:
    record = knowledge_document(admin_user)
    job = run_embedding_job(record, admin_user, monkeypatch, FixedResponseProvider())

    assert job.status == ProcessingJob.Status.SUCCEEDED
    rows = list(KnowledgeEmbeddingRecord.objects.order_by("clause__sequence"))
    assert len(rows) == 2
    assert {row.input_revision for row in rows} == {record.revision}
    assert {row.dimensions for row in rows} == {16}
    assert len({row.model_fingerprint for row in rows}) == 1
    repeated, created = enqueue_knowledge_embedding(
        record=record,
        actor=admin_user,
        idempotency_key="same-embedding-input-again",
    )
    assert created is False
    assert repeated.id == job.id
    assert KnowledgeEmbeddingRecord.objects.count() == 2


@pytest.mark.parametrize(
    "outputs",
    [
        (EmbeddingOutput((1.0, 0.0), "model-a"),),
        (
            EmbeddingOutput((1.0, 0.0), "model-a"),
            EmbeddingOutput((1.0,), "model-a"),
        ),
        (
            EmbeddingOutput((1.0,), "model-a"),
            EmbeddingOutput((1.0,), "model-b"),
        ),
        (
            SimpleNamespace(values=(), model_fingerprint="model-a"),
            SimpleNamespace(values=(1.0,), model_fingerprint="model-a"),
        ),
        (
            SimpleNamespace(values=(float("nan"),), model_fingerprint="model-a"),
            SimpleNamespace(values=(1.0,), model_fingerprint="model-a"),
        ),
        (
            SimpleNamespace(values=(float("inf"),), model_fingerprint="model-a"),
            SimpleNamespace(values=(1.0,), model_fingerprint="model-a"),
        ),
        (
            SimpleNamespace(values=(1.0,), model_fingerprint=123),
            SimpleNamespace(values=(1.0,), model_fingerprint=123),
        ),
        (
            SimpleNamespace(values=(0.0, 0.0), model_fingerprint="model-a"),
            SimpleNamespace(values=(1.0, 0.0), model_fingerprint="model-a"),
        ),
    ],
    ids=(
        "count",
        "dimensions",
        "fingerprint",
        "empty",
        "nan",
        "inf",
        "invalid-fingerprint",
        "zero-vector",
    ),
)
def test_malformed_embedding_batches_fail_without_partial_publication(
    admin_user, monkeypatch, outputs
) -> None:
    record = knowledge_document(admin_user)
    job = run_embedding_job(record, admin_user, monkeypatch, StaticEmbeddingProvider(outputs))

    assert job.status == ProcessingJob.Status.FAILED
    assert KnowledgeEmbeddingRecord.objects.count() == 0


def test_failed_embedding_can_be_retried_alone_without_duplicates(admin_user, monkeypatch) -> None:
    record = knowledge_document(admin_user)
    job = run_embedding_job(record, admin_user, monkeypatch, FailingEmbeddingProvider())
    assert job.status == ProcessingJob.Status.FAILED
    assert record.clauses.count() == 2

    retry_job(job_id=job.id, actor=admin_user, idempotency_key="retry-embedding-only")
    claimed = acquire_job(worker_id="embedding-retry", task_types=["embedding"])
    assert claimed is not None and claimed.id == job.id
    monkeypatch.setattr(
        "fire_inspect.ai.handlers.provider_for",
        lambda capability: FixedResponseProvider(),
    )
    WorkerCommand._execute(claimed.id, claimed.lease_token)

    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED
    assert KnowledgeEmbeddingRecord.objects.count() == 2
    assert ProcessingJob.objects.count() == 1


def test_stale_revision_is_superseded_and_lost_lease_cannot_publish(
    admin_user, monkeypatch
) -> None:
    record = knowledge_document(admin_user)
    stale_job, _ = enqueue_knowledge_embedding(record=record, actor=admin_user)
    stale_claim = acquire_job(worker_id="stale", task_types=["embedding"])

    class RevisionMutatingProvider:
        def embed(self, texts):
            outputs = FixedResponseProvider().embed(texts)
            KnowledgeDocumentRecord.objects.filter(pk=record.pk).update(
                revision=record.revision + 1
            )
            return outputs

    monkeypatch.setattr(
        "fire_inspect.ai.handlers.provider_for",
        lambda capability: RevisionMutatingProvider(),
    )
    WorkerCommand._execute(stale_claim.id, stale_claim.lease_token)
    stale_job.refresh_from_db()
    assert stale_job.status == ProcessingJob.Status.SUPERSEDED
    assert KnowledgeEmbeddingRecord.objects.count() == 0

    record.refresh_from_db()
    fresh_job, _ = enqueue_knowledge_embedding(record=record, actor=admin_user)
    fresh_claim = acquire_job(worker_id="old-owner", task_types=["embedding"])

    class LeaseStealingProvider:
        def embed(self, texts):
            outputs = FixedResponseProvider().embed(texts)
            ProcessingJob.objects.filter(pk=fresh_job.pk).update(lease_token="replacement-token")
            return outputs

    monkeypatch.setattr(
        "fire_inspect.ai.handlers.provider_for",
        lambda capability: LeaseStealingProvider(),
    )
    with pytest.raises(LostLease):
        embedding_handler(fresh_claim)
    assert KnowledgeEmbeddingRecord.objects.count() == 0


def test_failed_new_revision_preserves_old_success_but_search_does_not_use_it(
    admin_user, monkeypatch
) -> None:
    record = knowledge_document(admin_user)
    succeeded = run_embedding_job(record, admin_user, monkeypatch, FixedResponseProvider())
    assert succeeded.status == ProcessingJob.Status.SUCCEEDED
    old_ids = set(KnowledgeEmbeddingRecord.objects.values_list("id", flat=True))

    record.revision += 1
    record.save(update_fields=("revision", "updated_at"))
    failed = run_embedding_job(record, admin_user, monkeypatch, FailingEmbeddingProvider())
    assert failed.status == ProcessingJob.Status.FAILED
    assert set(KnowledgeEmbeddingRecord.objects.values_list("id", flat=True)) == old_ids
    assert set(KnowledgeEmbeddingRecord.objects.values_list("input_revision", flat=True)) == {1}

    monkeypatch.setattr(
        "fire_inspect.knowledge.services.provider_for",
        lambda capability: FixedResponseProvider(),
    )
    outcome = search_knowledge_with_status("自动消防系统操作人员必须持证上岗")
    assert outcome.results
    assert outcome.retrieval_mode == "lexical_fallback"
    assert outcome.embedding_status == "not_indexed"
    assert outcome.embedding_fallback is True


@pytest.mark.parametrize(
    ("provider", "expected_status"),
    [
        (DisabledProvider(), "unavailable"),
        (FailingEmbeddingProvider(), "failed"),
    ],
)
def test_search_keeps_lexical_results_when_embedding_is_unavailable(
    admin_user, monkeypatch, provider, expected_status
) -> None:
    knowledge_document(admin_user)
    monkeypatch.setattr("fire_inspect.knowledge.services.provider_for", lambda capability: provider)
    outcome = search_knowledge_with_status("自动消防系统操作人员必须持证上岗")

    assert outcome.results[0].clause_identifier == "第1条"
    assert outcome.retrieval_mode == "lexical_fallback"
    assert outcome.embedding_status == expected_status
    assert outcome.embedding_fallback is True


def test_vector_recall_uses_only_matching_model_fingerprint(admin_user, monkeypatch) -> None:
    record = knowledge_document(admin_user)
    first, second = list(record.clauses.order_by("sequence"))
    KnowledgeEmbeddingRecord.objects.create(
        clause=first,
        model_fingerprint="query-model",
        input_revision=record.revision,
        dimensions=2,
        values=[0.9, 0.1],
        status=KnowledgeEmbeddingRecord.Status.SUCCEEDED,
    )
    KnowledgeEmbeddingRecord.objects.create(
        clause=second,
        model_fingerprint="old-model",
        input_revision=record.revision,
        dimensions=2,
        values=[1.0, 0.0],
        status=KnowledgeEmbeddingRecord.Status.SUCCEEDED,
    )
    provider = StaticEmbeddingProvider((EmbeddingOutput((1.0, 0.0), "query-model"),))
    monkeypatch.setattr("fire_inspect.knowledge.services.provider_for", lambda capability: provider)

    outcome = search_knowledge_with_status("完全不同的向量查询")
    assert outcome.retrieval_mode == "hybrid"
    assert [item.clause_id for item in outcome.results] == [str(first.id)]
    assert outcome.results[0].retrieval_sources == ("vector",)


def test_embedding_submit_is_admin_only_and_global_job_is_admin_retryable(
    authenticated_client, admin_user, inspector_a
) -> None:
    record = knowledge_document(admin_user)
    admin = authenticated_client(admin_user)
    submitted = admin.post(
        f"/api/v1/knowledge/documents/{record.id}/embedding/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="admin-embedding-submit",
    )
    assert submitted.status_code == 202, submitted.data
    job = ProcessingJob.objects.get(pk=submitted.data["id"])
    assert job.inspection_id is None
    repeated = admin.post(
        f"/api/v1/knowledge/documents/{record.id}/embedding/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="admin-embedding-submit",
    )
    assert repeated.status_code == 200
    assert repeated.data["id"] == submitted.data["id"]

    other_record = knowledge_document(admin_user)
    conflict = admin.post(
        f"/api/v1/knowledge/documents/{other_record.id}/embedding/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="admin-embedding-submit",
    )
    assert conflict.status_code == 409

    inspector = authenticated_client(inspector_a)
    forbidden = inspector.post(
        f"/api/v1/knowledge/documents/{record.id}/embedding/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="inspector-embedding-submit",
    )
    assert forbidden.status_code == 403

    claimed = acquire_job(worker_id="failed-global", task_types=["embedding"])
    fail_job(
        job_id=job.id,
        lease_token=claimed.lease_token,
        safe_error_code="provider_unavailable",
        safe_error_message="服务暂不可用。",
    )
    admin = authenticated_client(admin_user)
    retried = admin.post(
        f"/api/v1/jobs/{job.id}/retry/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="retry-global-embedding",
    )
    assert retried.status_code == 202, retried.data
    assert retried.data["inspection_id"] is None
