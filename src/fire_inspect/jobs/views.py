from __future__ import annotations

import hashlib

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.common.api import ApiError
from fire_inspect.common.permissions import IsAdministrator
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.jobs.material_steps import MATERIAL_STEP_KINDS, supports_material_step
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.serializers import ProcessingJobSerializer
from fire_inspect.jobs.services import (
    IdempotencyConflict,
    InvalidJobTransition,
    enqueue_job,
    retry_job,
)
from fire_inspect.materials.views import material_queryset_for

ALLOWED_MATERIAL_STEPS = frozenset(MATERIAL_STEP_KINDS)


def job_queryset_for(user):
    if getattr(user, "role", "") == "admin":
        return ProcessingJob.objects.select_related("inspection")
    return ProcessingJob.objects.filter(
        inspection__isnull=False, inspection__in=InspectionAccessPolicy.queryset_for(user)
    ).select_related("inspection")


class MaterialJobSubmitView(APIView):
    def post(self, request, material_id, step):
        if step not in ALLOWED_MATERIAL_STEPS:
            raise ApiError(code="unsupported_job_step", message="不支持此处理步骤。")
        material = get_object_or_404(material_queryset_for(request.user), pk=material_id)
        if not supports_material_step(kind=material.kind, step=step):
            raise ApiError(
                code="unsupported_material_job_step",
                message="此材料类型不支持该处理步骤。",
            )
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        fingerprint = hashlib.sha256(
            f"{step}:{material.id}:{material.sha256}:{material.revision}".encode()
        ).hexdigest()
        try:
            job, created = enqueue_job(
                task_type=step,
                scope_type="material",
                scope_id=material.id,
                inspection=material.inspection,
                input_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                created_by=request.user,
                expected_scope_revision=material.revision,
            )
        except IdempotencyConflict as exc:
            raise ApiError(
                code="idempotency_conflict",
                message="此幂等键已用于其他操作。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        return Response(
            ProcessingJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class JobDetailView(APIView):
    def get(self, request, job_id):
        job = get_object_or_404(job_queryset_for(request.user), pk=job_id)
        return Response(ProcessingJobSerializer(job).data)


class AdminJobListView(APIView):
    permission_classes = [IsAdministrator]

    def get(self, request):
        queryset = ProcessingJob.objects.select_related("inspection").order_by("-created_at", "-id")
        status_filter = request.query_params.get("status", "").strip()
        task_filter = request.query_params.get("task_type", "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if task_filter:
            queryset = queryset.filter(task_type=task_filter)
        return Response(ProcessingJobSerializer(queryset, many=True).data)


class JobRetryView(APIView):
    def post(self, request, job_id):
        job = get_object_or_404(job_queryset_for(request.user), pk=job_id)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        repeated = job.last_retry_idempotency_key == key
        try:
            job = retry_job(job_id=job.id, actor=request.user, idempotency_key=key)
        except IdempotencyConflict as exc:
            raise ApiError(
                code="idempotency_conflict",
                message="此幂等键已用于其他操作。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        except InvalidJobTransition as exc:
            raise ApiError(
                code="invalid_job_transition",
                message="只有失败的任务可以重试。",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        return Response(
            ProcessingJobSerializer(job).data,
            status=status.HTTP_200_OK if repeated else status.HTTP_202_ACCEPTED,
        )
