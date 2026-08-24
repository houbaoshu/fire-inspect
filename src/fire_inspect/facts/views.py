from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.common.api import ApiError, expected_revision
from fire_inspect.evidence.services import (
    EvidenceValidationError,
    evidence_candidates_for,
)
from fire_inspect.facts.models import Fact
from fire_inspect.facts.serializers import FactRevisionSerializer, FactSerializer
from fire_inspect.facts.services import (
    InvalidFactTransition,
    RevisionConflict,
    create_manual_fact,
    delete_fact,
    edit_fact,
    link_fact_evidence,
    transition_fact,
)
from fire_inspect.inspections.policies import InspectionAccessPolicy


def fact_queryset_for(user):
    return Fact.objects.filter(
        inspection__in=InspectionAccessPolicy.queryset_for(user)
    ).select_related("inspection", "created_by", "confirmed_by", "primary_source_material")


def _inspection(request, inspection_id):
    return get_object_or_404(InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id)


def _map_domain_error(exc):
    if isinstance(exc, RevisionConflict):
        return ApiError(
            code="revision_conflict",
            message=f"事实已被其他操作修改，当前修订号为 {exc.current_revision}。",
            status_code=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, EvidenceValidationError):
        return ApiError(code=exc.code, message=exc.message)
    if isinstance(exc, InvalidFactTransition):
        return ApiError(
            code="invalid_fact_transition",
            message=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    return ApiError(code="invalid_fact", message=str(exc))


class InspectionFactListCreateView(APIView):
    def get(self, request, inspection_id):
        inspection = _inspection(request, inspection_id)
        facts = fact_queryset_for(request.user).filter(inspection=inspection)
        return Response(FactSerializer(facts, many=True).data)

    def post(self, request, inspection_id):
        inspection = _inspection(request, inspection_id)
        content = request.data.get("content", "")
        needs_verification = request.data.get("needs_verification", False)
        evidence = request.data.get("evidence", [])
        if not isinstance(content, str):
            raise ApiError(code="invalid_fact", message="事实内容格式不正确。")
        if not isinstance(needs_verification, bool):
            raise ApiError(code="invalid_fact", message="待核实标记格式不正确。")
        if not isinstance(evidence, list):
            raise ApiError(code="invalid_fact", message="事实证据列表格式不正确。")
        try:
            fact = create_manual_fact(
                inspection=inspection,
                actor=request.user,
                content=content,
                needs_verification=needs_verification,
                verification_note=request.data.get("verification_note", ""),
                evidence=evidence,
            )
        except (ValueError, EvidenceValidationError) as exc:
            raise _map_domain_error(exc) from exc
        return Response(FactSerializer(fact).data, status=status.HTTP_201_CREATED)


class InspectionFactDetailView(APIView):
    def _fact(self, request, inspection_id, fact_id):
        _inspection(request, inspection_id)
        return get_object_or_404(
            fact_queryset_for(request.user), pk=fact_id, inspection_id=inspection_id
        )

    def get(self, request, inspection_id, fact_id):
        return Response(FactSerializer(self._fact(request, inspection_id, fact_id)).data)

    def patch(self, request, inspection_id, fact_id):
        fact = self._fact(request, inspection_id, fact_id)
        if "content" in request.data and not isinstance(request.data["content"], str):
            raise ApiError(code="invalid_fact", message="事实内容格式不正确。")
        if "needs_verification" in request.data and not isinstance(
            request.data["needs_verification"], bool
        ):
            raise ApiError(code="invalid_fact", message="待核实标记格式不正确。")
        if "evidence" in request.data and not isinstance(request.data["evidence"], list):
            raise ApiError(code="invalid_fact", message="事实证据列表格式不正确。")
        values = {
            key: request.data[key]
            for key in ("content", "needs_verification", "verification_note")
            if key in request.data
        }
        evidence_marker = request.data.get("evidence") if "evidence" in request.data else None
        try:
            fact = edit_fact(
                fact_id=fact.id,
                actor=request.user,
                expected_revision=expected_revision(request, "revision", "expected_revision"),
                values=values,
                evidence_marker=evidence_marker,
            )
        except (RevisionConflict, EvidenceValidationError, ValueError) as exc:
            raise _map_domain_error(exc) from exc
        return Response(FactSerializer(fact).data)

    def delete(self, request, inspection_id, fact_id):
        fact = self._fact(request, inspection_id, fact_id)
        if request.data.get("confirm") is not True:
            raise ApiError(code="confirmation_required", message="删除前必须二次确认。")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            raise ApiError(code="deletion_reason_required", message="请填写删除原因。")
        delete_fact(fact=fact, actor=request.user, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FactTransitionView(APIView):
    ACTIONS = {"confirm": "confirmed", "withdraw": "withdrawn", "restore": "restored"}

    def post(self, request, inspection_id, fact_id, command):
        _inspection(request, inspection_id)
        fact = get_object_or_404(
            fact_queryset_for(request.user), pk=fact_id, inspection_id=inspection_id
        )
        try:
            fact = transition_fact(
                fact_id=fact.id,
                actor=request.user,
                expected_revision=expected_revision(request, "revision", "expected_revision"),
                action=self.ACTIONS[command],
                reason=str(request.data.get("reason", "")).strip(),
            )
        except (RevisionConflict, InvalidFactTransition) as exc:
            raise _map_domain_error(exc) from exc
        return Response(FactSerializer(fact).data)


class FactHistoryView(APIView):
    def get(self, request, inspection_id, fact_id):
        _inspection(request, inspection_id)
        fact = get_object_or_404(
            fact_queryset_for(request.user), pk=fact_id, inspection_id=inspection_id
        )
        return Response(FactRevisionSerializer(fact.revisions.all(), many=True).data)


class InspectionEvidenceCandidateListView(APIView):
    def get(self, request, inspection_id):
        inspection = _inspection(request, inspection_id)
        return Response(evidence_candidates_for(inspection=inspection))


class FactEvidenceLinkView(APIView):
    def post(self, request, inspection_id, fact_id):
        _inspection(request, inspection_id)
        fact = get_object_or_404(
            fact_queryset_for(request.user), pk=fact_id, inspection_id=inspection_id
        )
        if not request.headers.get("Idempotency-Key", "").strip():
            raise ApiError(code="idempotency_key_required", message="请提交幂等键。")
        try:
            fact = link_fact_evidence(
                fact_id=fact.id,
                actor=request.user,
                expected_revision=expected_revision(request, "revision", "expected_revision"),
                candidate_id=request.data.get("id", ""),
                applicability_reason=request.data.get("applicability_reason", ""),
            )
        except (RevisionConflict, EvidenceValidationError, ValueError) as exc:
            raise _map_domain_error(exc) from exc
        return Response(FactSerializer(fact).data)
