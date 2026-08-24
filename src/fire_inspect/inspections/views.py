from __future__ import annotations

import re

from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from fire_inspect.audit.serializers import DomainChangeHistorySerializer
from fire_inspect.common.api import ApiError
from fire_inspect.common.routing import STRICT_UUID_PATTERN
from fire_inspect.inspections.models import Inspection
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.inspections.serializers import InspectionSerializer
from fire_inspect.inspections.services import (
    RevisionConflict,
    delete_inspection,
    update_inspection,
)


class InspectionViewSet(viewsets.ModelViewSet):
    serializer_class = InspectionSerializer
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]
    lookup_value_regex = STRICT_UUID_PATTERN

    def get_queryset(self):
        queryset = Inspection.objects.select_related("owner").prefetch_related("personnel")
        return InspectionAccessPolicy.queryset_for(self.request.user, queryset)

    @staticmethod
    def _expected_revision(request) -> int:
        raw = request.data.get("shared_revision")
        if raw is None:
            raw = request.headers.get("If-Match")
            if raw:
                match = re.fullmatch(r'(?:W/)?"?(\d+)"?', raw.strip())
                raw = match.group(1) if match else None
        if raw is None:
            raise ApiError(
                code="revision_required",
                message="更新时必须提交当前共享修订号。",
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            )
        if isinstance(raw, bool):
            raise ApiError(code="invalid_revision", message="共享修订号格式不正确。")
        if not isinstance(raw, (int, str)) or (
            isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()) is None
        ):
            raise ApiError(code="invalid_revision", message="共享修订号格式不正确。")
        try:
            revision = int(raw.strip()) if isinstance(raw, str) else raw
        except (TypeError, ValueError) as exc:
            raise ApiError(code="invalid_revision", message="共享修订号格式不正确。") from exc
        if revision < 1:
            raise ApiError(code="invalid_revision", message="共享修订号格式不正确。")
        return revision

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        expected_revision = self._expected_revision(request)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        try:
            updated = update_inspection(
                inspection_id=instance.id,
                actor=request.user,
                expected_revision=expected_revision,
                values=values,
            )
        except RevisionConflict as exc:
            raise ApiError(
                code="revision_conflict",
                message=f"检查已被其他操作更新，当前修订号为 {exc.current_revision}。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        updated = self.get_queryset().get(pk=updated.pk)
        return Response(self.get_serializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if request.data.get("confirm") is not True:
            raise ApiError(code="confirmation_required", message="删除前必须二次确认。")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            raise ApiError(code="deletion_reason_required", message="请填写删除原因。")
        delete_inspection(inspection=instance, actor=request.user, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _deletion_impact(self, pk):
        inspection = get_object_or_404(
            self.get_queryset().annotate(
                material_count=Count("materials", distinct=True),
                job_count=Count("processing_jobs", distinct=True),
                fact_count=Count("facts", distinct=True),
                document_count=Count("documents", distinct=True),
            ),
            pk=pk,
        )
        return Response(
            {
                "inspection_id": str(inspection.id),
                "requires_confirmation": True,
                "materials": inspection.material_count,
                "jobs": inspection.job_count,
                "facts": inspection.fact_count,
                "documents": inspection.document_count,
            }
        )

    @action(detail=True, methods=["get"], url_path="deletion-impact")
    def deletion_impact(self, request, pk=None):
        return self._deletion_impact(pk)

    @action(detail=True, methods=["get"], url_path="delete-impact")
    def delete_impact(self, request, pk=None):
        return self._deletion_impact(pk)

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        inspection = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(
            DomainChangeHistorySerializer(inspection.domain_history.all(), many=True).data
        )
