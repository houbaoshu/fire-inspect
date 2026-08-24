from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.common.permissions import IsAdministrator
from fire_inspect.jobs.services import IdempotencyConflict, InvalidJobTransition

from .models import TemplateRegion, WordTemplate
from .serializers import TemplateRegionSerializer, WordTemplateSerializer
from .services import (
    TemplateServiceError,
    confirm_template_mapping,
    create_template_region,
    create_template_version,
    create_word_template,
    retry_template_recognition,
    revise_template_mapping,
    update_template_region,
)


def _service_error(exc: TemplateServiceError) -> Response:
    conflict_codes = {"confirmed_mapping_immutable", "mapping_not_draft"}
    return Response(
        {"code": exc.code, "message": exc.message},
        status=status.HTTP_409_CONFLICT if exc.code in conflict_codes else 400,
    )


def _template(pk):
    return get_object_or_404(
        WordTemplate.objects.prefetch_related(
            "versions",
            "versions__mapping_revisions",
            "versions__mapping_revisions__regions",
        ).select_related("active_mapping_revision"),
        pk=pk,
    )


class TemplateListCreateView(APIView):
    permission_classes = [IsAdministrator]

    def get(self, request):
        templates = WordTemplate.objects.prefetch_related(
            "versions",
            "versions__mapping_revisions",
            "versions__mapping_revisions__regions",
        ).select_related("active_mapping_revision")
        return Response(WordTemplateSerializer(templates, many=True).data)

    def post(self, request):
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"code": "idempotency_key_required", "message": "请提交幂等键。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"code": "file_required", "message": "请选择 DOCX 模板文件。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            template = create_word_template(
                name=str(request.data.get("name", "")),
                document_type=str(request.data.get("document_type", "")),
                upload=upload,
                actor=request.user,
                idempotency_key=idempotency_key,
            )
        except TemplateServiceError as exc:
            return _service_error(exc)
        except IdempotencyConflict:
            return Response(
                {"code": "idempotency_conflict", "message": "此幂等键已用于其他操作。"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            WordTemplateSerializer(_template(template.pk)).data,
            status=(
                status.HTTP_202_ACCEPTED
                if getattr(template, "_recognition_job_created", False)
                else status.HTTP_200_OK
            ),
        )


class TemplateDetailView(APIView):
    permission_classes = [IsAdministrator]

    def get(self, request, template_id):
        return Response(WordTemplateSerializer(_template(template_id)).data)


class TemplateVersionCreateView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, template_id):
        template = _template(template_id)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"code": "idempotency_key_required", "message": "请提交幂等键。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"code": "file_required", "message": "请选择 DOCX 模板文件。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            version = create_template_version(
                template=template,
                upload=upload,
                actor=request.user,
                idempotency_key=idempotency_key,
            )
        except TemplateServiceError as exc:
            return _service_error(exc)
        except IdempotencyConflict:
            return Response(
                {"code": "idempotency_conflict", "message": "此幂等键已用于其他操作。"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            WordTemplateSerializer(_template(template_id)).data,
            status=(
                status.HTTP_202_ACCEPTED
                if getattr(version, "_recognition_job_created", False)
                else status.HTTP_200_OK
            ),
        )


class TemplateRecognizeView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, template_id):
        template = _template(template_id)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"code": "idempotency_key_required", "message": "请提交幂等键。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            template = retry_template_recognition(
                template=template,
                actor=request.user,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflict:
            return Response(
                {"code": "idempotency_conflict", "message": "此幂等键已用于其他操作。"},
                status=status.HTTP_409_CONFLICT,
            )
        except InvalidJobTransition:
            return Response(
                {
                    "code": "invalid_job_transition",
                    "message": "只有失败的模板识别任务可以重试。",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            WordTemplateSerializer(_template(template_id)).data,
            status=(
                status.HTTP_200_OK
                if getattr(template, "_recognition_retry_repeated", False)
                else status.HTTP_202_ACCEPTED
            ),
        )


class TemplateMappingConfirmView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, template_id):
        template = _template(template_id)
        try:
            confirm_template_mapping(template=template, actor=request.user)
        except TemplateServiceError as exc:
            return _service_error(exc)
        return Response(WordTemplateSerializer(_template(template_id)).data)


class TemplateMappingReviseView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, template_id):
        template = _template(template_id)
        try:
            revise_template_mapping(template=template, actor=request.user)
        except TemplateServiceError as exc:
            return _service_error(exc)
        return Response(
            WordTemplateSerializer(_template(template_id)).data,
            status=status.HTTP_201_CREATED,
        )


class TemplateRegionDetailView(APIView):
    permission_classes = [IsAdministrator]

    def patch(self, request, template_id, region_id):
        template = _template(template_id)
        mapping = template.current_mapping_revision
        region = get_object_or_404(
            TemplateRegion.objects.select_related("mapping_revision"),
            pk=region_id,
            mapping_revision=mapping,
        )
        values = {
            key: request.data[key]
            for key in ("display_name", "fill_strategy", "repeat_scope", "enabled", "required")
            if key in request.data
        }
        try:
            region = update_template_region(region=region, values=values)
        except TemplateServiceError as exc:
            return _service_error(exc)
        return Response(TemplateRegionSerializer(region).data)


class TemplateRegionCreateView(APIView):
    permission_classes = [IsAdministrator]

    def post(self, request, template_id):
        template = _template(template_id)
        mapping = template.current_mapping_revision
        if mapping is None:
            return _service_error(TemplateServiceError("mapping_missing", "模板尚无映射。"))
        values = {
            key: request.data[key]
            for key in (
                "display_name",
                "business_field_key",
                "repeat_scope",
                "fill_strategy",
                "enabled",
                "required",
                "anchor",
                "location_preview",
            )
            if key in request.data
        }
        try:
            region = create_template_region(mapping=mapping, values=values)
        except TemplateServiceError as exc:
            return _service_error(exc)
        return Response(TemplateRegionSerializer(region).data, status=status.HTTP_201_CREATED)
