from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .knowledge_service import search_knowledge
from .models import (
    DocumentDraft,
    DocumentField,
    Evidence,
    EvidenceType,
    Fact,
    FactEvidenceLink,
    FactStatus,
    Inspection,
)


def _matches(question: str, text: str) -> bool:
    compact = question.replace("这条", "").replace("这个", "").strip()
    return not compact or any(token in text for token in compact if "\u4e00" <= token <= "\u9fff")


def answer_assistant(
    db: Session, question: str, inspection: Inspection | None, context: dict[str, Any]
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    citations: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    findings: list[str] = []
    if inspection:
        facts = list(
            db.scalars(
                select(Fact).where(
                    Fact.inspection_id == inspection.id, Fact.status != FactStatus.DELETED
                )
            )
        )
        preferred_fact_id = context.get("fact_id")
        for fact in facts:
            if preferred_fact_id and str(fact.id) != str(preferred_fact_id):
                continue
            if preferred_fact_id or _matches(question, f"{fact.title} {fact.detail or ''}"):
                findings.append(f"检查事实：{fact.title}")
                actions.append(
                    {"type": "open_fact", "params": {"fact_id": str(fact.id)}, "label": fact.title}
                )
                evidence_rows = db.execute(
                    select(Evidence)
                    .join(FactEvidenceLink, FactEvidenceLink.evidence_id == Evidence.id)
                    .where(FactEvidenceLink.fact_id == fact.id)
                ).scalars()
                for evidence in evidence_rows:
                    citations.append(
                        {
                            "type": evidence.type.value,
                            "evidence_id": str(evidence.id),
                            "label": evidence.label,
                            "quote": evidence.quote,
                        }
                    )
                    if (
                        evidence.type
                        in {
                            EvidenceType.MEDIA_RANGE,
                            EvidenceType.TRANSCRIPT_RANGE,
                        }
                        and evidence.material_id
                    ):
                        actions.append(
                            {
                                "type": "seek_media",
                                "params": {
                                    "material_id": str(evidence.material_id),
                                    "start_ms": evidence.start_ms,
                                    "end_ms": evidence.end_ms,
                                },
                                "label": evidence.label,
                            }
                        )
                    elif evidence.type == EvidenceType.IMAGE:
                        actions.append(
                            {
                                "type": "open_image",
                                "params": {"evidence_id": str(evidence.id)},
                                "label": evidence.label,
                            }
                        )
        if context.get("document_id"):
            document = db.scalar(
                select(DocumentDraft).where(
                    DocumentDraft.id == uuid.UUID(str(context["document_id"])),
                    DocumentDraft.inspection_id == inspection.id,
                )
            )
            if document:
                fields = list(
                    db.scalars(
                        select(DocumentField).where(DocumentField.document_id == document.id)
                    )
                )
                findings.append(f"当前文书“{document.title}”包含 {len(fields)} 个可编辑字段。")
                actions.append(
                    {
                        "type": "open_document",
                        "params": {"document_id": str(document.id)},
                        "label": document.title,
                    }
                )
    if any(word in question for word in ("法规", "依据", "条款", "规定", "规范")):
        knowledge = search_knowledge(db, question, 5)
        if knowledge["citations"]:
            findings.append(knowledge["answer"])
            for citation in knowledge["citations"]:
                citation_data = {
                    **citation,
                    "document_id": str(citation["document_id"]),
                    "version_id": str(citation["version_id"]),
                    "category": citation["category"].value,
                }
                citations.append({"type": "knowledge_excerpt", **citation_data})
                actions.append(
                    {
                        "type": "open_knowledge",
                        "params": {
                            "document_id": citation_data["document_id"],
                            "version_id": citation_data["version_id"],
                            "anchor": citation_data["anchor"],
                        },
                        "label": citation_data["title"],
                    }
                )
    if not findings:
        return "未在当前用户可访问的检查和知识材料中找到可靠依据。", [], []
    return "\n\n".join(findings), citations, actions
