from __future__ import annotations

from pathlib import Path

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from fire_inspect.common.api import ApiError
from fire_inspect.jobs.serializers import ProcessingJobSerializer
from fire_inspect.jobs.services import IdempotencyConflict, InvalidJobTransition
from fire_inspect.knowledge.django_models import KnowledgeClauseRecord, KnowledgeDocumentRecord
from fire_inspect.knowledge.serializers import (
    KnowledgeDocumentSerializer,
    KnowledgeMetadataUpdateSerializer,
    KnowledgeUploadSerializer,
)
from fire_inspect.knowledge.services import (
    delete_knowledge_document,
    enqueue_knowledge_embedding,
    retry_knowledge_parse,
    search_knowledge_with_status,
)
from fire_inspect.materials.validation import UploadValidationError, sanitize_display_name


class IsAdminRole(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(request.user.is_authenticated and request.user.role == "admin")


class KnowledgeDocumentViewSet(viewsets.ModelViewSet):
    queryset = KnowledgeDocumentRecord.objects.prefetch_related("clauses")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in {
            "list",
            "create",
            "partial_update",
            "destroy",
            "retry",
            "embedding",
        }:
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "create":
            return KnowledgeUploadSerializer
        if self.action == "partial_update":
            return KnowledgeMetadataUpdateSerializer
        return KnowledgeDocumentSerializer

    def create(self, request, *args, **kwargs):
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = serializer.save()
        except UploadValidationError as exc:
            raise ApiError(code=exc.code, message=exc.message) from exc
        except ValueError as exc:
            raise ApiError(code="invalid_metadata", message=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise ApiError(
                code="idempotency_conflict",
                message="此幂等键已用于其他操作。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        return Response(
            KnowledgeDocumentSerializer(record).data,
            status=(
                status.HTTP_202_ACCEPTED
                if getattr(record, "_parse_job_created", False)
                else status.HTTP_200_OK
            ),
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        record = serializer.save()
        if record.processing_status == KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED:
            enqueue_knowledge_embedding(record=record, actor=request.user)
        return Response(KnowledgeDocumentSerializer(record).data)

    def destroy(self, request, *args, **kwargs):
        record = self.get_object()
        if request.data.get("confirm") is not True:
            raise ApiError(code="confirmation_required", message="删除前必须二次确认。")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            raise ApiError(code="deletion_reason_required", message="请填写删除原因。")
        delete_knowledge_document(record=record, actor=request.user, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        record = self.get_object()
        extension = Path(record.original_file.name).suffix
        filename = sanitize_display_name(f"{record.title}{extension}")
        return FileResponse(
            record.original_file.open("rb"),
            as_attachment=True,
            filename=filename,
            content_type=record.mime_type,
        )

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        try:
            record = retry_knowledge_parse(
                record=self.get_object(),
                actor=request.user,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                code="idempotency_conflict",
                message="此幂等键已用于其他操作。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        except InvalidJobTransition as exc:
            raise ApiError(
                code="invalid_job_transition",
                message="只有失败的知识解析任务可以重试。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        return Response(
            KnowledgeDocumentSerializer(record).data,
            status=(
                status.HTTP_200_OK
                if getattr(record, "_parse_retry_repeated", False)
                else status.HTTP_202_ACCEPTED
            ),
        )

    @action(detail=True, methods=["post"])
    def embedding(self, request, pk=None):
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        try:
            job, created = enqueue_knowledge_embedding(
                record=self.get_object(),
                actor=request.user,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                code="idempotency_conflict",
                message="此幂等键已用于其他操作。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        except ValueError as exc:
            raise ApiError(code="embedding_input_invalid", message=str(exc)) from exc
        return Response(
            ProcessingJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def knowledge_search(request):
    query = str(
        request.query_params.get("q", "")
        if request.method == "GET"
        else request.data.get("q", request.data.get("query", ""))
    ).strip()
    if not query:
        raise ApiError(code="query_required", message="请输入法规查询内容。")
    outcome = search_knowledge_with_status(query)
    results = outcome.results
    if not results:
        return Response(
            {
                "found": False,
                "query": query,
                "message": "未找到可靠的相关条款，请调整查询或人工核验。",
                "results": [],
                "retrieval_mode": outcome.retrieval_mode,
                "embedding_status": outcome.embedding_status,
                "embedding_fallback": outcome.embedding_fallback,
            }
        )
    return Response(
        {
            "found": True,
            "query": query,
            "message": "已找到可能相关的原文，适用性需人工确认。",
            "retrieval_mode": outcome.retrieval_mode,
            "embedding_status": outcome.embedding_status,
            "embedding_fallback": outcome.embedding_fallback,
            "results": [
                {
                    "issue": item.issue,
                    "clause_id": item.clause_id,
                    "clause_identifier": item.clause_identifier,
                    "original_text": item.original_text,
                    "applicability_reason": item.applicability_reason,
                    "document_title": item.document_title,
                    "document_id": item.document_id,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "source_status": item.source_status,
                    "score": item.score,
                    "retrieval_sources": list(item.retrieval_sources),
                    "document_url": (f"/api/v1/knowledge/documents/{item.document_id}/download/"),
                }
                for item in results
            ],
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def knowledge_clause_detail(request, document_id, clause_id):
    clause = get_object_or_404(
        KnowledgeClauseRecord.objects.select_related("document"),
        pk=clause_id,
        document_id=document_id,
    )
    document = clause.document
    return Response(
        {
            "clause_id": str(clause.id),
            "clause_identifier": clause.clause_identifier,
            "heading": clause.heading,
            "chapter": clause.chapter,
            "original_text": clause.content,
            "page_start": clause.page_start,
            "page_end": clause.page_end,
            "source_locator": clause.source_locator,
            "document_id": str(document.id),
            "document_title": document.title,
            "document_url": f"/api/v1/knowledge/documents/{document.id}/download/",
            "source_verification": document.source_verification,
            "source_label": (
                "已核验"
                if document.source_verification
                == KnowledgeDocumentRecord.SourceVerification.VERIFIED
                else "待核验"
            ),
            "validity": document.validity,
        }
    )
