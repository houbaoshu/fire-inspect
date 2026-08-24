from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.audit.serializers import DomainChangeHistorySerializer
from fire_inspect.common.api import ApiError, expected_revision
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.materials.models import Material
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord
from fire_inspect.transcripts.serializers import (
    TranscriptSegmentSerializer,
    TranscriptSerializer,
)
from fire_inspect.transcripts.services import (
    RevisionConflict,
    TranscriptAlreadyExists,
    confirm_segment_role,
    create_manual_transcript,
    edit_full_text,
    edit_segment,
    edit_segment_text,
)


def transcript_queryset_for(user):
    return Transcript.objects.filter(
        material__inspection__in=InspectionAccessPolicy.queryset_for(user)
    ).select_related("material__inspection")


def _revision_error(exc: RevisionConflict) -> ApiError:
    return ApiError(
        code="revision_conflict",
        message=f"转写已被其他操作修改，当前修订号为 {exc.current_revision}。",
        status_code=status.HTTP_409_CONFLICT,
    )


class MaterialTranscriptView(APIView):
    def get(self, request, material_id):
        transcript = get_object_or_404(
            transcript_queryset_for(request.user), material_id=material_id
        )
        return Response(TranscriptSerializer(transcript).data)

    def put(self, request, material_id):
        material = get_object_or_404(
            Material.objects.filter(
                inspection__in=InspectionAccessPolicy.queryset_for(request.user)
            ),
            pk=material_id,
        )
        segments = request.data.get("segments")
        if segments is not None and not isinstance(segments, list):
            raise ApiError(code="invalid_transcript_segments", message="转写片段格式无效。")
        try:
            transcript = create_manual_transcript(
                material_id=material.id,
                actor=request.user,
                full_text=str(request.data.get("full_text", "")),
                segments=segments,
                language=str(request.data.get("language", "zh-CN")),
            )
        except TranscriptAlreadyExists as exc:
            raise ApiError(
                code="transcript_already_exists",
                message=str(exc),
                status_code=status.HTTP_409_CONFLICT,
            ) from exc
        except ValueError as exc:
            raise ApiError(code="invalid_transcript", message=str(exc)) from exc
        return Response(TranscriptSerializer(transcript).data, status=status.HTTP_201_CREATED)


class InspectionTranscriptListView(APIView):
    def get(self, request, inspection_id):
        inspection = get_object_or_404(
            InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id
        )
        transcripts = (
            transcript_queryset_for(request.user)
            .filter(material__inspection=inspection)
            .select_related("material")
            .prefetch_related("segments__role_confirmed_by")
            .order_by("material__created_at", "created_at", "id")
        )
        return Response(TranscriptSerializer(transcripts, many=True).data)


class TranscriptDetailView(APIView):
    def get(self, request, transcript_id):
        transcript = get_object_or_404(transcript_queryset_for(request.user), pk=transcript_id)
        return Response(TranscriptSerializer(transcript).data)

    def patch(self, request, transcript_id):
        transcript = get_object_or_404(transcript_queryset_for(request.user), pk=transcript_id)
        try:
            transcript = edit_full_text(
                transcript_id=transcript.id,
                actor=request.user,
                expected_revision=expected_revision(request, "revision", "expected_revision"),
                full_text=str(request.data.get("full_text", "")),
            )
        except RevisionConflict as exc:
            raise _revision_error(exc) from exc
        except ValueError as exc:
            raise ApiError(code="invalid_transcript_text", message=str(exc)) from exc
        return Response(TranscriptSerializer(transcript).data)


class TranscriptSegmentDetailView(APIView):
    def patch(self, request, transcript_id, segment_id):
        transcript = get_object_or_404(transcript_queryset_for(request.user), pk=transcript_id)
        try:
            revision = expected_revision(
                request, "transcript_revision", "revision", "expected_revision"
            )
            if "confirmed_role" in request.data:
                changes = {"confirmed_role": request.data.get("confirmed_role")}
                if "text" in request.data:
                    changes["text"] = request.data.get("text")
                transcript, segment = edit_segment(
                    transcript_id=transcript.id,
                    segment_id=segment_id,
                    actor=request.user,
                    expected_revision=revision,
                    **changes,
                )
            else:
                transcript, segment = edit_segment_text(
                    transcript_id=transcript.id,
                    segment_id=segment_id,
                    actor=request.user,
                    expected_revision=revision,
                    text=str(request.data.get("text", "")),
                )
        except TranscriptSegmentRecord.DoesNotExist as exc:
            raise ApiError(
                code="not_found", message="转写片段不存在。", status_code=status.HTTP_404_NOT_FOUND
            ) from exc
        except RevisionConflict as exc:
            raise _revision_error(exc) from exc
        except ValueError as exc:
            raise ApiError(code="invalid_segment_text", message=str(exc)) from exc
        payload = dict(TranscriptSegmentSerializer(segment).data)
        payload["transcript_revision"] = transcript.revision
        return Response(payload)


class TranscriptRoleConfirmView(APIView):
    def post(self, request, transcript_id, segment_id):
        transcript = get_object_or_404(transcript_queryset_for(request.user), pk=transcript_id)
        try:
            transcript, segment = confirm_segment_role(
                transcript_id=transcript.id,
                segment_id=segment_id,
                actor=request.user,
                expected_revision=expected_revision(
                    request, "transcript_revision", "revision", "expected_revision"
                ),
                role=str(request.data.get("role", "")),
            )
        except TranscriptSegmentRecord.DoesNotExist as exc:
            raise ApiError(
                code="not_found", message="转写片段不存在。", status_code=status.HTTP_404_NOT_FOUND
            ) from exc
        except RevisionConflict as exc:
            raise _revision_error(exc) from exc
        except ValueError as exc:
            raise ApiError(code="invalid_semantic_role", message=str(exc)) from exc
        payload = dict(TranscriptSegmentSerializer(segment).data)
        payload["transcript_revision"] = transcript.revision
        return Response(payload)


class TranscriptHistoryView(APIView):
    def get(self, request, transcript_id):
        transcript = get_object_or_404(transcript_queryset_for(request.user), pk=transcript_id)
        history = transcript.material.inspection.domain_history.filter(
            object_type="transcript", object_id=transcript.id
        )
        return Response(DomainChangeHistorySerializer(history, many=True).data)
