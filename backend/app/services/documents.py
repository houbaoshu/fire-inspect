import io
import re
import zipfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.models import (
    Citation,
    DocumentDraft,
    DocumentTemplate,
    Evidence,
    Fact,
    FactEvidence,
    Inspection,
    LegalAnalysis,
)
from app.services.ai import ai_provider

FIELD_CODE_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
JINJA_EXPRESSION_PATTERN = re.compile(r"{{(.*?)}}", re.DOTALL)


class InlineImageList:
    """Render several images into one regular docxtpl placeholder."""

    def __init__(self, images: list[InlineImage]):
        self.images = images

    def __str__(self) -> str:
        line_break = '</w:t></w:r><w:r><w:br/></w:r><w:r><w:t xml:space="preserve">'
        return line_break.join(str(image) for image in self.images)

    def __html__(self) -> str:
        return str(self)


def scan_template_fields(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )
    plain = re.sub(r"<[^>]+>", "", xml)
    if any(token in plain for token in ("{%", "%}", "{#", "#}")):
        raise ValueError("模板只允许 {{ organization }} 这类字段占位符，不允许控制语句或注释")
    expressions = JINJA_EXPRESSION_PATTERN.findall(plain)
    if any(not FIELD_CODE_PATTERN.fullmatch(expression.strip()) for expression in expressions):
        raise ValueError("模板占位符只能使用字母、数字和下划线")
    remaining = JINJA_EXPRESSION_PATTERN.sub("", plain)
    if "{{" in remaining or "}}" in remaining:
        raise ValueError("模板中存在不完整的占位符")
    return sorted({expression.strip() for expression in expressions})


def default_field_manifest(codes: list[str]) -> list[dict[str, Any]]:
    labels = {
        "inspection_title": "检查名称",
        "organization": "被检查单位",
        "address": "地址",
        "inspection_date": "检查时间",
        "inspectors": "检查人员",
        "facts": "检查事实",
        "legal_basis": "法规依据",
        "questions_answers": "询问与回答",
        "photo_caption": "照片说明",
        "photo": "证据照片",
    }
    return [
        {
            "code": code,
            "label": labels.get(code, code.replace("_", " ").title()),
            "type": "photo_list"
            if code == "photo"
            else "multiline"
            if code in {"facts", "legal_basis", "questions_answers"}
            else "text",
            "description": labels.get(code, "由当前检查事实填写"),
        }
        for code in codes
    ]


def _fact_value(fact: Fact) -> str:
    value = fact.content.get("value") if isinstance(fact.content, dict) else None
    return str(value or fact.summary)


def build_document_fields(
    db: Session, inspection: Inspection, template: DocumentTemplate
) -> dict[str, Any]:
    facts = db.scalars(
        select(Fact).where(Fact.inspection_id == inspection.id, Fact.status == "confirmed")
    ).all()
    fact_ids = [fact.id for fact in facts]
    evidence_rows = db.execute(
        select(FactEvidence.fact_id, FactEvidence.evidence_id).where(
            FactEvidence.fact_id.in_(fact_ids or [""])
        )
    ).all()
    evidence_by_fact: dict[str, list[str]] = {}
    for fact_id, evidence_id in evidence_rows:
        evidence_by_fact.setdefault(fact_id, []).append(evidence_id)

    analyses = db.scalars(
        select(LegalAnalysis)
        .where(LegalAnalysis.inspection_id == inspection.id)
        .order_by(LegalAnalysis.created_at.desc())
    ).all()
    citation_ids = db.scalars(
        select(Citation.id).where(Citation.analysis_id.in_([item.id for item in analyses] or [""]))
    ).all()

    by_kind: dict[str, list[Fact]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact)

    default_values: dict[str, Any] = {
        "inspection_title": inspection.title,
        "organization": inspection.extra_fields.get("organization", ""),
        "address": inspection.extra_fields.get("address", ""),
        "inspection_date": inspection.extra_fields.get("inspection_date", ""),
        "inspectors": inspection.extra_fields.get("inspectors", ""),
        "facts": "\n".join(f"{index}. {_fact_value(fact)}" for index, fact in enumerate(facts, 1)),
        "legal_basis": "\n".join(
            f"{index}. {item.characterization}：{item.reasoning}"
            for index, item in enumerate(analyses, 1)
        ),
        "questions_answers": "\n".join(
            _fact_value(fact) for fact in by_kind.get("qa", []) + by_kind.get("statement", [])
        ),
        "photo_caption": "\n".join(_fact_value(fact) for fact in by_kind.get("observation", [])),
        "photo": "",
    }

    unknown_fields = [
        item
        for item in template.fields_json
        if item["code"] not in default_values
        and item.get("type", "text") not in {"image", "photo_list"}
    ]
    generated = (
        ai_provider.chat_json(
            system=(
                "根据已确认检查事实填写模板字段。只能使用输入事实，不得猜测。"
                "返回 JSON 对象 fields，键必须来自字段清单，值为字符串。"
            ),
            user=(
                f"检查：{inspection.title}\n扩展信息：{inspection.extra_fields}\n"
                f"事实：{[{'id': fact.id, 'kind': fact.kind, 'summary': fact.summary} for fact in facts]}\n"
                f"字段：{unknown_fields}"
            ),
        )
        if unknown_fields
        else None
    )

    output: dict[str, Any] = {}
    all_evidence_ids = sorted({item for values in evidence_by_fact.values() for item in values})
    for field in template.fields_json:
        code = field["code"]
        field_type = field.get("type", "text")
        if field_type == "photo_list":
            value: Any = []
        elif field_type == "image":
            value = ""
        else:
            value = default_values.get(code, (generated or {}).get("fields", {}).get(code, ""))
        if code == "facts" or code not in default_values:
            relevant_fact_ids = fact_ids
        elif code == "photo_caption":
            relevant_fact_ids = [item.id for item in by_kind.get("observation", [])]
        elif code == "questions_answers":
            relevant_fact_ids = [
                item.id for item in by_kind.get("qa", []) + by_kind.get("statement", [])
            ]
        else:
            relevant_fact_ids = []
        output[code] = {
            "value": value,
            "edited": False,
            "stale": False,
            "depends_on_facts": bool(relevant_fact_ids)
            or code in {"facts", "photo_caption", "questions_answers"}
            or code not in default_values,
            "fact_ids": relevant_fact_ids,
            "evidence_ids": all_evidence_ids if relevant_fact_ids else [],
            "citation_ids": list(citation_ids) if code == "legal_basis" else [],
        }
    return output


def _mark_document_fields_stale(document: DocumentDraft, predicate) -> None:
    changed = False
    fields = dict(document.fields_json)
    for code, state in fields.items():
        if predicate(code, state):
            state = dict(state)
            state["stale"] = True
            fields[code] = state
            changed = True
    if changed:
        document.fields_json = fields
        document.status = "draft"
        document.confirmed_by = None
        document.confirmed_at = None


def mark_documents_stale(db: Session, fact_id: str) -> None:
    fact = db.get(Fact, fact_id)
    if not fact:
        return
    documents = db.scalars(
        select(DocumentDraft).where(DocumentDraft.inspection_id == fact.inspection_id)
    ).all()
    for document in documents:
        _mark_document_fields_stale(
            document, lambda _code, state: fact_id in state.get("fact_ids", [])
        )


def mark_fact_dependent_documents_stale(db: Session, inspection_id: str) -> None:
    documents = db.scalars(
        select(DocumentDraft).where(DocumentDraft.inspection_id == inspection_id)
    ).all()
    for document in documents:
        _mark_document_fields_stale(
            document,
            lambda code, state: bool(
                state.get("depends_on_facts")
                or state.get("fact_ids")
                or code in {"facts", "photo_caption", "questions_answers"}
            ),
        )


def refresh_unedited_questions_answers(db: Session, inspection_id: str) -> None:
    db.flush()
    facts = db.scalars(
        select(Fact)
        .where(
            Fact.inspection_id == inspection_id,
            Fact.status == "confirmed",
            Fact.kind.in_(["qa", "statement"]),
        )
        .order_by(Fact.created_at)
    ).all()
    fact_ids = [item.id for item in facts]
    evidence_ids = list(
        dict.fromkeys(
            db.scalars(
                select(FactEvidence.evidence_id).where(
                    FactEvidence.fact_id.in_(fact_ids or [""])
                )
            ).all()
        )
    )
    value = "\n\n".join(_fact_value(fact) for fact in facts)
    documents = db.scalars(
        select(DocumentDraft).where(
            DocumentDraft.inspection_id == inspection_id,
            DocumentDraft.document_type == "inquiry_record",
        )
    ).all()
    for document in documents:
        fields = dict(document.fields_json)
        current = fields.get("questions_answers")
        if not isinstance(current, dict) or current.get("edited"):
            continue
        state = dict(current)
        state.update(
            value=value,
            stale=False,
            depends_on_facts=True,
            fact_ids=fact_ids,
            evidence_ids=evidence_ids,
        )
        fields["questions_answers"] = state
        document.fields_json = fields
        document.status = "draft"
        document.confirmed_by = None
        document.confirmed_at = None


def render_document(db: Session, draft: DocumentDraft) -> bytes:
    template = db.get(DocumentTemplate, draft.template_id)
    if not template:
        raise ValueError("模板不存在")
    storage = get_storage()
    with storage.materialize(template.storage_key, suffix=".docx") as path:
        document = DocxTemplate(str(path))
        manifest = {item["code"]: item for item in template.fields_json}
        context: dict[str, Any] = {}
        with ExitStack() as image_files:
            for code, state in draft.fields_json.items():
                value = state.get("value", "")
                field_type = manifest.get(code, {}).get("type", "text")
                evidence_ids = value if field_type == "photo_list" else [value]
                if field_type in {"image", "photo_list"}:
                    images: list[InlineImage] = []
                    for evidence_id in evidence_ids or []:
                        evidence = db.get(Evidence, evidence_id)
                        if not evidence or not evidence.storage_key:
                            continue
                        image_path = image_files.enter_context(
                            storage.materialize(
                                evidence.storage_key,
                                suffix=Path(evidence.storage_key).suffix,
                            )
                        )
                        images.append(InlineImage(document, str(image_path), width=Mm(120)))
                    context[code] = (
                        InlineImageList(images)
                        if field_type == "photo_list" and images
                        else images[0]
                        if images
                        else ""
                    )
                else:
                    context[code] = value
            document.render(context)
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()
