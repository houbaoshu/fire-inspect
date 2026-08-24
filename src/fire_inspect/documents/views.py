from __future__ import annotations

import re

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.facts.models import Fact
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.materials.models import Material, MaterialDerivative

from .handlers import enqueue_document_generation
from .models import InspectionDocument, InterviewQaItem, PhotoDocumentItem
from .serializers import (
    DocumentFieldValueSerializer,
    InspectionDocumentSerializer,
)
from .services import (
    DocumentServiceError,
    add_photo_sources,
    confirm_document,
    create_document,
    create_qa_item,
    delete_photo_item,
    delete_qa_item,
    export_document,
    rename_document,
    reorder_qa_items,
    replace_fact_dependencies,
    sync_qa_candidates,
    update_document_field,
    update_photo_item,
    update_qa_item,
)


def _document_queryset(user):
    allowed = InspectionAccessPolicy.queryset_for(user)
    return (
        InspectionDocument.objects.filter(inspection__in=allowed)
        .select_related(
            "inspection",
            "template_version",
            "current_mapping_revision",
            "last_generated_revision",
        )
        .prefetch_related("field_values", "photo_items", "qa_items", "fact_dependencies")
    )


def _document(user, document_id):
    return get_object_or_404(_document_queryset(user), pk=document_id)


def _expected_revision(request) -> int:
    raw = request.data.get("expected_revision") if hasattr(request.data, "get") else None
    if raw is None:
        raw = request.headers.get("If-Match")
        if raw:
            match = re.fullmatch(r'(?:W/)?"?(\d+)"?', raw.strip())
            raw = match.group(1) if match else None
    if raw is None:
        raise DocumentServiceError(
            "revision_required",
            "更新时必须提交当前文书修订号。",
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        )
    if isinstance(raw, bool):
        raise DocumentServiceError("invalid_revision", "文书修订号格式不正确。")
    if not isinstance(raw, (int, str)) or (
        isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()) is None
    ):
        raise DocumentServiceError("invalid_revision", "文书修订号格式不正确。")
    try:
        revision = int(raw.strip()) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise DocumentServiceError("invalid_revision", "文书修订号格式不正确。") from exc
    if revision < 1:
        raise DocumentServiceError("invalid_revision", "文书修订号格式不正确。")
    return revision


def _error(exc: DocumentServiceError) -> Response:
    # This module's public command contract is intentionally flat so the same
    # stable code/message shape is consumed by the in-app client and tests.
    return Response({"code": exc.code, "message": exc.message}, status=exc.status_code)


class InspectionDocumentListCreateView(APIView):
    def get(self, request, inspection_id):
        inspection = get_object_or_404(
            InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id
        )
        documents = _document_queryset(request.user).filter(inspection=inspection)
        document_type = str(request.query_params.get("document_type", "")).strip()
        if document_type:
            documents = documents.filter(document_type=document_type)
        return Response(InspectionDocumentSerializer(documents, many=True).data)

    def post(self, request, inspection_id):
        inspection = get_object_or_404(
            InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id
        )
        try:
            document, created = create_document(
                inspection=inspection,
                document_type=str(request.data.get("document_type", "")),
                actor=request.user,
            )
        except DocumentServiceError as exc:
            return _error(exc)
        document = _document(request.user, document.id)
        return Response(
            InspectionDocumentSerializer(document).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DocumentDetailView(APIView):
    def get(self, request, document_id):
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)

    def patch(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            renamed = rename_document(
                document=document,
                name=str(request.data.get("name", "")),
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, renamed.id)).data)


class DocumentFactDependencyView(APIView):
    def get(self, request, document_id):
        document = _document(request.user, document_id)
        dependency_ids = list(document.fact_dependencies.values_list("fact_id", flat=True))
        facts = Fact.objects.filter(
            id__in=dependency_ids,
            inspection=document.inspection,
        ).order_by("created_at", "id")
        return Response(
            {
                "document_id": str(document.id),
                "revision": document.revision,
                "fact_ids": [str(value) for value in dependency_ids],
                "facts": [
                    {
                        "id": str(fact.id),
                        "content": fact.content,
                        "revision": fact.revision,
                        "needs_verification": fact.needs_verification,
                    }
                    for fact in facts
                ],
            }
        )

    def put(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            updated = replace_fact_dependencies(
                document=document,
                fact_ids=request.data.get("fact_ids", ()),
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, updated.id)).data)


class DocumentFieldView(APIView):
    def patch(self, request, document_id, field_key):
        document = _document(request.user, document_id)
        try:
            expected_revision = _expected_revision(request)
            field = update_document_field(
                document=document,
                business_field_key=field_key,
                value=request.data.get("value"),
                actor=request.user,
                expected_revision=expected_revision,
                origin=str(request.data.get("origin", "manual")),
                manual_locked=bool(request.data.get("manual_locked", True)),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        payload = dict(DocumentFieldValueSerializer(field).data)
        payload["document_revision"] = (
            InspectionDocument.objects.only("revision").get(pk=document.id).revision
        )
        return Response(payload)


class DocumentPhotoCandidateView(APIView):
    def get(self, request, document_id):
        document = _document(request.user, document_id)
        if document.document_type != InspectionDocument.Type.PHOTO:
            return _error(DocumentServiceError("wrong_document_type", "当前文书不是照片文书。"))
        materials = Material.objects.filter(
            inspection=document.inspection, kind=Material.Kind.IMAGE
        ).order_by("created_at", "id")
        derivatives = (
            MaterialDerivative.objects.select_related("material")
            .filter(
                material__inspection=document.inspection,
                kind=MaterialDerivative.Kind.KEYFRAME,
            )
            .order_by("source_timestamp_ms", "id")
        )
        payload = [
            {
                "id": str(item.id),
                "label": item.display_name,
                "kind": "material_photo",
                "thumbnail_url": f"/api/v1/materials/{item.id}/preview/",
            }
            for item in materials
        ]
        payload.extend(
            {
                "id": str(item.id),
                "label": f"{item.material.display_name} · {int(item.source_timestamp_ms or 0) / 1000:.1f}s",
                "kind": "keyframe",
                "thumbnail_url": f"/api/v1/material-derivatives/{item.id}/preview/",
            }
            for item in derivatives
        )
        return Response(payload)


class DocumentPhotoItemListView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            expected_revision = _expected_revision(request)
            add_photo_sources(
                document=document,
                source_ids=request.data.get("source_ids", ()),
                actor=request.user,
                expected_revision=expected_revision,
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)


class DocumentPhotoItemDetailView(APIView):
    def patch(self, request, document_id, item_id):
        document = _document(request.user, document_id)
        item = get_object_or_404(PhotoDocumentItem, pk=item_id, document=document)
        try:
            update_photo_item(
                document=document,
                item=item,
                values={
                    key: request.data[key]
                    for key in ("reflected_content", "description")
                    if key in request.data
                },
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)

    def delete(self, request, document_id, item_id):
        document = _document(request.user, document_id)
        item = get_object_or_404(PhotoDocumentItem, pk=item_id, document=document)
        try:
            delete_photo_item(
                document=document,
                item=item,
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)


class DocumentQaItemDetailView(APIView):
    def patch(self, request, document_id, item_id):
        document = _document(request.user, document_id)
        item = get_object_or_404(InterviewQaItem, pk=item_id, document=document)
        try:
            update_qa_item(
                document=document,
                item=item,
                values={
                    key: request.data[key]
                    for key in ("text", "confirmed_role")
                    if key in request.data
                },
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)

    def delete(self, request, document_id, item_id):
        document = _document(request.user, document_id)
        item = get_object_or_404(InterviewQaItem, pk=item_id, document=document)
        try:
            delete_qa_item(
                document=document,
                item=item,
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, document_id)).data)


class DocumentQaItemListView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            updated = create_qa_item(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
                text=request.data.get("text", ""),
                confirmed_role=request.data.get("confirmed_role"),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(
            InspectionDocumentSerializer(_document(request.user, updated.id)).data,
            status=status.HTTP_201_CREATED,
        )

    def put(self, request, document_id):
        document = _document(request.user, document_id)
        item_ids = request.data.get("item_ids")
        if not isinstance(item_ids, list):
            return _error(DocumentServiceError("invalid_qa_order", "问答顺序格式不正确。"))
        try:
            updated = reorder_qa_items(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
                item_ids=item_ids,
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, updated.id)).data)


class DocumentQaSyncView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            updated = sync_qa_candidates(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, updated.id)).data)


class DocumentGenerateView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            enqueue_document_generation(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        document = _document(request.user, document_id)
        return Response(
            InspectionDocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED,
        )


class DocumentConfirmView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            confirmed = confirm_document(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        return Response(InspectionDocumentSerializer(_document(request.user, confirmed.id)).data)


class DocumentExportView(APIView):
    def post(self, request, document_id):
        document = _document(request.user, document_id)
        try:
            _, payload = export_document(
                document=document,
                actor=request.user,
                expected_revision=_expected_revision(request),
            )
        except DocumentServiceError as exc:
            return _error(exc)
        filename = re.sub(r"[\\/\r\n\x00]+", "_", document.name).strip() or "document"
        response = HttpResponse(
            payload,
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.docx"'
        return response
