from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.apps import apps
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.ai.config import CapabilityConfigurationError
from fire_inspect.ai.factory import provider_for
from fire_inspect.ai.providers import CapabilityUnavailable, ProviderFailure
from fire_inspect.ai.schemas import (
    AssistantAnswer,
    AssistantCitation,
    NavigationAction,
    NavigationKind,
)
from fire_inspect.assistant.service import (
    AssistantQuery,
    AssistantSecurityError,
    AssistantSource,
    AuthorizedAssistant,
)
from fire_inspect.common.api import ApiError
from fire_inspect.inspections.models import Inspection
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.knowledge.django_models import KnowledgeClauseRecord, KnowledgeDocumentRecord
from fire_inspect.knowledge.services import search_knowledge_with_status

_KNOWLEDGE_QUERY_MARKERS = (
    "法规",
    "法律",
    "法条",
    "条款",
    "规定",
    "规范",
    "标准",
    "依据",
    "消防法",
)


@dataclass(frozen=True, slots=True)
class _RetrievedClause:
    source: AssistantSource
    document_id: UUID
    excerpt: str
    applicability_reason: str


def _optional_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _optional_uuid(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("对象标识格式不正确")
    return UUID(str(value))


def _optional_positive_revision(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("所选内容修订号格式不正确")
    revision = int(value)
    if revision < 1:
        raise ValueError("所选内容修订号格式不正确")
    return revision


def _navigation_payload(
    action,
    inspection: Inspection,
    *,
    authorized_clause_documents: dict[UUID, UUID] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": action.kind.value,
        "target_id": str(action.target_id),
        "inspection_id": str(inspection.id),
        "material_id": str(action.material_id) if action.material_id else None,
        "start_ms": action.start_ms,
    }
    if action.kind is NavigationKind.PHOTO:
        payload["photo_id"] = str(action.target_id)
    elif action.kind is NavigationKind.FACT:
        payload["fact_id"] = str(action.target_id)
    elif action.kind is NavigationKind.CLAUSE:
        payload["clause_id"] = str(action.target_id)
        document_id = (authorized_clause_documents or {}).get(action.target_id)
        link_model = _optional_model("evidence", "FactClauseLink")
        if document_id is None and link_model:
            document_id = (
                link_model.objects.filter(
                    inspection=inspection,
                    fact__inspection=inspection,
                    clause_id=action.target_id,
                )
                .values_list("clause__document_id", flat=True)
                .first()
            )
        if document_id:
            payload["document_id"] = str(document_id)
    elif action.kind is NavigationKind.DOCUMENT:
        payload["document_id"] = str(action.target_id)
        document_model = _optional_model("documents", "InspectionDocument")
        if document_model:
            document_type = (
                document_model.objects.filter(
                    inspection=inspection,
                    pk=action.target_id,
                )
                .values_list("document_type", flat=True)
                .first()
            )
            if document_type:
                payload["document_type"] = document_type
    return payload


def _is_knowledge_query(question: str, route: str) -> bool:
    normalized_route = route.casefold()
    return "/regulations" in normalized_route or any(
        marker in question for marker in _KNOWLEDGE_QUERY_MARKERS
    )


def _page_label(page_start: int, page_end: int) -> str:
    if page_start == page_end:
        return f"第{page_start}页"
    return f"第{page_start}—{page_end}页"


def _retrieved_clauses(question: str, route: str) -> tuple[_RetrievedClause, ...] | None:
    if not _is_knowledge_query(question, route):
        return None
    results = search_knowledge_with_status(question, limit=5).results
    if not results:
        return ()
    result_by_clause_id = {UUID(item.clause_id): item for item in results}
    records = {
        item.id: item
        for item in KnowledgeClauseRecord.objects.filter(
            id__in=result_by_clause_id,
            document__processing_status=KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED,
        ).select_related("document")
    }
    retrieved: list[_RetrievedClause] = []
    for result in results:
        clause_id = UUID(result.clause_id)
        record = records.get(clause_id)
        if record is None or str(record.document_id) != result.document_id:
            continue
        excerpt = (
            f"《{result.document_title}》{result.clause_identifier}"
            f"（{_page_label(result.page_start, result.page_end)}，{result.source_status}）："
            f"{result.original_text}"
        )
        retrieved.append(
            _RetrievedClause(
                source=AssistantSource(
                    "clause",
                    clause_id,
                    record.document.revision,
                    f"{excerpt}\n适用理由：{result.applicability_reason}",
                ),
                document_id=record.document_id,
                excerpt=excerpt,
                applicability_reason=result.applicability_reason,
            )
        )
    return tuple(retrieved)


def _authoritative_knowledge_answer(
    retrieved: tuple[_RetrievedClause, ...],
) -> AssistantAnswer:
    if not retrieved:
        return AssistantAnswer("知识库未找到可靠的相关条款，请调整查询或人工核验。")
    details = "；".join(
        f"{item.excerpt} 适用理由：{item.applicability_reason}" for item in retrieved
    )
    return AssistantAnswer(
        f"知识库检索到以下可能相关原文，适用性需人工确认：{details}",
        citations=tuple(
            AssistantCitation("clause", item.source.source_id, item.excerpt) for item in retrieved
        ),
        navigation=tuple(
            NavigationAction(NavigationKind.CLAUSE, item.source.source_id) for item in retrieved
        ),
    )


def _context_sources(inspection: Inspection):
    sources = [
        AssistantSource(
            "inspection",
            inspection.id,
            inspection.shared_revision,
            "；".join(
                filter(
                    None,
                    [
                        f"单位：{inspection.unit_name}",
                        f"地址：{inspection.unit_address}" if inspection.unit_address else "",
                        (
                            f"负责人：{inspection.responsible_person}"
                            if inspection.responsible_person
                            else ""
                        ),
                        f"备注：{inspection.general_notes}" if inspection.general_notes else "",
                    ],
                )
            ),
        )
    ]
    authorized_navigation = {
        NavigationKind.FACT: set(),
        NavigationKind.DOCUMENT: set(),
        NavigationKind.MATERIAL_TIME: set(),
        NavigationKind.PHOTO: set(),
        NavigationKind.CLAUSE: set(),
    }
    authorized_materials = set(inspection.materials.values_list("id", flat=True))
    authorized_material_navigation = {}

    fact_model = _optional_model("facts", "Fact")
    if fact_model:
        for fact in fact_model.objects.filter(inspection=inspection):
            sources.append(AssistantSource("fact", fact.id, fact.revision, fact.content))
            authorized_navigation[NavigationKind.FACT].add(fact.id)

    transcript_model = _optional_model("transcripts", "Transcript")
    if transcript_model:
        for transcript in transcript_model.objects.filter(material__inspection=inspection):
            sources.append(
                AssistantSource(
                    "transcript", transcript.id, transcript.revision, transcript.full_text
                )
            )

    document_model = _optional_model("documents", "InspectionDocument")
    if document_model:
        for document in document_model.objects.filter(inspection=inspection):
            sources.append(
                AssistantSource(
                    "document",
                    document.id,
                    document.revision,
                    f"{document.name}；状态：{document.state}",
                )
            )
            authorized_navigation[NavigationKind.DOCUMENT].add(document.id)

    for photo in inspection.materials.filter(kind="image"):
        sources.append(AssistantSource("photo", photo.id, photo.revision, photo.display_name))
        authorized_navigation[NavigationKind.PHOTO].add(photo.id)

    evidence_model = _optional_model("evidence", "FactMediaRange")
    if evidence_model:
        evidence_items = evidence_model.objects.filter(
            inspection=inspection,
            fact__inspection=inspection,
            material__inspection=inspection,
        ).select_related("fact", "material")
        for evidence in evidence_items:
            sources.append(
                AssistantSource(
                    "media_range",
                    evidence.id,
                    evidence.fact.revision,
                    evidence.note or evidence.fact.content,
                    material_id=evidence.material_id,
                    start_ms=evidence.start_ms,
                    end_ms=evidence.end_ms,
                )
            )
            authorized_navigation[NavigationKind.MATERIAL_TIME].add(evidence.id)
            authorized_material_navigation[evidence.id] = (
                evidence.material_id,
                evidence.start_ms,
                evidence.end_ms,
            )

    clause_link_model = _optional_model("evidence", "FactClauseLink")
    if clause_link_model:
        clause_ids = set()
        links = clause_link_model.objects.filter(
            inspection=inspection,
            fact__inspection=inspection,
        ).select_related("clause__document")
        for link in links:
            clause = link.clause
            if clause.id in clause_ids:
                continue
            clause_ids.add(clause.id)
            sources.append(
                AssistantSource(
                    "clause",
                    clause.id,
                    clause.document.revision,
                    clause.content,
                )
            )
            authorized_navigation[NavigationKind.CLAUSE].add(clause.id)

    return (
        sources,
        {key: frozenset(value) for key, value in authorized_navigation.items()},
        frozenset(authorized_materials),
        authorized_material_navigation,
    )


class AssistantQueryView(APIView):
    def post(self, request):
        try:
            inspection_id = _optional_uuid(request.data.get("inspection_id"))
        except (TypeError, ValueError) as exc:
            raise ApiError(code="invalid_assistant_query", message=str(exc)) from exc
        if inspection_id is None:
            raise ApiError(code="invalid_assistant_query", message="必须指定检查。")
        inspection = get_object_or_404(
            InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id
        )
        try:
            query = AssistantQuery(
                inspection_id=inspection.id,
                question=str(request.data.get("question", "")),
                route=str(request.data.get("route", "")),
                selected_source_id=_optional_uuid(request.data.get("selected_source_id")),
                selected_revision=_optional_positive_revision(
                    request.data.get("selected_revision")
                ),
                selected_text=str(request.data.get("selected_text", "")),
            )
            sources, navigation, materials, material_navigation = _context_sources(inspection)
            retrieved = _retrieved_clauses(query.question, query.route)
            clause_documents: dict[UUID, UUID] = {}
            if retrieved is not None:
                retrieved_ids = {item.source.source_id for item in retrieved}
                sources = [source for source in sources if source.source_id not in retrieved_ids]
                sources.extend(item.source for item in retrieved)
                navigation = {kind: set(ids) for kind, ids in navigation.items()}
                navigation[NavigationKind.CLAUSE].update(retrieved_ids)
                navigation = {kind: frozenset(ids) for kind, ids in navigation.items()}
                clause_documents = {item.source.source_id: item.document_id for item in retrieved}
            answer = AuthorizedAssistant(provider_for("llm")).answer(
                query,
                authorized_inspection_id=inspection.id,
                sources=sources,
                authorized_navigation=navigation,
                authorized_materials=materials,
                authorized_material_navigation=material_navigation,
            )
            if retrieved is not None:
                answer = _authoritative_knowledge_answer(retrieved)
        except (CapabilityUnavailable, CapabilityConfigurationError) as exc:
            raise ApiError(
                code="assistant_unavailable",
                message=str(exc),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        except ProviderFailure as exc:
            raise ApiError(
                code="assistant_failed",
                message=str(exc),
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        except AssistantSecurityError as exc:
            raise ApiError(
                code="assistant_context_invalid",
                message=str(exc),
                status_code=status.HTTP_403_FORBIDDEN,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ApiError(code="invalid_assistant_query", message=str(exc)) from exc
        return Response(
            {
                "text": answer.text,
                "citations": [
                    {
                        "source_type": item.source_type,
                        "source_id": str(item.source_id),
                        "excerpt": item.excerpt,
                    }
                    for item in answer.citations
                ],
                "navigation": [
                    _navigation_payload(
                        item,
                        inspection,
                        authorized_clause_documents=clause_documents,
                    )
                    for item in answer.navigation
                ],
                "read_only": True,
            }
        )
