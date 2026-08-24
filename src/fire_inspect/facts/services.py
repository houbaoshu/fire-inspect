from __future__ import annotations

import re
import unicodedata
from hashlib import sha256

from django.db import transaction
from django.utils import timezone

from fire_inspect.ai.schemas import FactExtractionOutput
from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.evidence.serializers import serialize_fact_evidence
from fire_inspect.evidence.services import replace_fact_media_ranges
from fire_inspect.facts.models import Fact, FactRevision
from fire_inspect.facts.signals import fact_changed
from fire_inspect.materials.models import Material


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"current revision is {current_revision}")


class InvalidFactTransition(Exception):
    pass


def normalized_fact_hash(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return sha256(normalized.encode("utf-8")).hexdigest()


def fact_snapshot(fact: Fact) -> dict:
    return {
        "content": fact.content,
        "review_state": fact.review_state,
        "needs_verification": fact.needs_verification,
        "verification_note": fact.verification_note,
        "origin": fact.origin,
        "evidence": serialize_fact_evidence(fact),
        "revision": fact.revision,
    }


def _record_fact_change(
    *,
    fact: Fact,
    actor,
    action: str,
    old_snapshot: dict,
    reason_code: str,
    reason: str = "",
) -> None:
    new_snapshot = fact_snapshot(fact)
    FactRevision.objects.create(
        fact=fact,
        revision=fact.revision,
        action=action,
        snapshot=new_snapshot,
        actor=actor,
    )
    DomainChangeHistory.objects.create(
        inspection=fact.inspection,
        actor=actor,
        object_type="fact",
        object_id=fact.id,
        action=action,
        old_values=old_snapshot,
        new_values=new_snapshot,
        reason=str(reason).strip()[:500],
    )
    AuditEvent.append(
        actor=actor,
        object_type="fact",
        object_id=fact.id,
        inspection_id_snapshot=fact.inspection_id,
        action=action,
        reason=reason,
        masked_change_summary=f"fact changed: action={action}, revision={fact.revision}",
    )
    fact_changed.send(
        sender=Fact,
        fact=fact,
        actor=actor,
        action=action,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
        reason_code=reason_code,
    )


@transaction.atomic
def create_manual_fact(
    *, inspection, actor, content: str, needs_verification=False, verification_note="", evidence=()
) -> Fact:
    content = content.strip()
    if not content:
        raise ValueError("事实内容不能为空")
    fact = Fact.objects.create(
        inspection=inspection,
        content=content,
        origin=Fact.Origin.MANUAL,
        needs_verification=bool(needs_verification),
        verification_note=str(verification_note).strip()[:1000],
        created_by=actor,
    )
    replace_fact_media_ranges(fact=fact, payloads=list(evidence))
    _record_fact_change(
        fact=fact,
        actor=actor,
        action="created",
        old_snapshot={},
        reason_code="FACT_CREATED",
    )
    return fact


@transaction.atomic
def publish_ai_fact_candidates(
    *, inspection, primary_material, input_revision: int, output: FactExtractionOutput, created_by
) -> list[Fact]:
    if primary_material.inspection_id != inspection.id:
        raise ValueError("事实来源材料不属于当前检查")
    allowed_materials = {
        material.id: material.duration_ms
        for material in Material.objects.filter(inspection=inspection, duration_ms__isnull=False)
    }
    output.validate_references(allowed_materials)
    published: list[Fact] = []
    for candidate in output.facts:
        content_hash = normalized_fact_hash(candidate.content)
        fact = Fact.objects.filter(
            primary_source_material=primary_material,
            source_input_revision=input_revision,
            normalized_content_hash=content_hash,
        ).first()
        if fact:
            published.append(fact)
            continue
        fact = Fact.objects.create(
            inspection=inspection,
            content=candidate.content.strip(),
            origin=Fact.Origin.AI,
            needs_verification=candidate.needs_verification,
            verification_note=candidate.verification_note[:1000],
            primary_source_material=primary_material,
            source_input_revision=input_revision,
            normalized_content_hash=content_hash,
            created_by=created_by,
        )
        replace_fact_media_ranges(
            fact=fact,
            payloads=[
                {
                    "material_id": reference.material_id,
                    "start_ms": reference.start_ms,
                    "end_ms": reference.end_ms,
                }
                for reference in candidate.evidence
            ],
        )
        _record_fact_change(
            fact=fact,
            actor=created_by,
            action="created",
            old_snapshot={},
            reason_code="FACT_CREATED",
        )
        published.append(fact)
    return published


def _locked_fact(fact_id, expected_revision: int) -> Fact:
    fact = Fact.objects.select_for_update().select_related("inspection").get(pk=fact_id)
    if fact.revision != expected_revision:
        raise RevisionConflict(fact.revision)
    return fact


@transaction.atomic
def transition_fact(
    *, fact_id, actor, expected_revision: int, action: str, reason: str = ""
) -> Fact:
    fact = _locked_fact(fact_id, expected_revision)
    old = fact_snapshot(fact)
    if action == "confirmed":
        if fact.review_state == Fact.ReviewState.WITHDRAWN:
            raise InvalidFactTransition("已撤回事实需先恢复为候选")
        if fact.review_state == Fact.ReviewState.CONFIRMED:
            return fact
        fact.review_state = Fact.ReviewState.CONFIRMED
        fact.confirmed_by = actor
        fact.confirmed_at = timezone.now()
    elif action == "withdrawn":
        if fact.review_state == Fact.ReviewState.WITHDRAWN:
            return fact
        fact.review_state = Fact.ReviewState.WITHDRAWN
    elif action == "restored":
        if fact.review_state != Fact.ReviewState.WITHDRAWN:
            raise InvalidFactTransition("只有已撤回事实可以恢复")
        fact.review_state = Fact.ReviewState.CANDIDATE
        fact.confirmed_by = None
        fact.confirmed_at = None
    else:
        raise InvalidFactTransition("未知事实状态操作")
    fact.revision += 1
    fact.save(
        update_fields=(
            "review_state",
            "confirmed_by",
            "confirmed_at",
            "revision",
            "updated_at",
        )
    )
    _record_fact_change(
        fact=fact,
        actor=actor,
        action=action,
        old_snapshot=old,
        reason_code="REFERENCED_FACT_CHANGED",
        reason=reason,
    )
    return fact


@transaction.atomic
def edit_fact(
    *, fact_id, actor, expected_revision: int, values: dict, evidence_marker=None
) -> Fact:
    fact = _locked_fact(fact_id, expected_revision)
    old = fact_snapshot(fact)
    changed = False
    if "content" in values:
        content = str(values["content"]).strip()
        if not content:
            raise ValueError("事实内容不能为空")
        if content != fact.content:
            fact.content = content
            changed = True
    for field in ("needs_verification", "verification_note"):
        if field in values:
            value = values[field]
            if field == "verification_note":
                value = str(value).strip()[:1000]
            if getattr(fact, field) != value:
                setattr(fact, field, value)
                changed = True
    if evidence_marker is not None:
        replace_fact_media_ranges(fact=fact, payloads=list(evidence_marker))
        changed = old["evidence"] != fact_snapshot(fact)["evidence"] or changed
        if not evidence_marker and not fact.needs_verification:
            fact.needs_verification = True
            changed = True
    if not changed:
        return fact
    fact.revision += 1
    fact.save(
        update_fields=(
            "content",
            "needs_verification",
            "verification_note",
            "revision",
            "updated_at",
        )
    )
    _record_fact_change(
        fact=fact,
        actor=actor,
        action="edited",
        old_snapshot=old,
        reason_code="REFERENCED_FACT_CHANGED",
    )
    return fact


@transaction.atomic
def remove_deleted_material_evidence(*, material, actor) -> None:
    from django.db.models import Q

    fact_ids = list(
        Fact.objects.filter(
            Q(media_ranges__material=material)
            | Q(primary_source_material=material)
            | Q(keyframe_links__derivative__material=material)
            | Q(photo_links__photo_material=material)
            | Q(transcript_links__segment__transcript__material=material)
        )
        .values_list("id", flat=True)
        .distinct()
    )
    for fact_id in fact_ids:
        fact = Fact.objects.select_for_update().select_related("inspection").get(pk=fact_id)
        old = fact_snapshot(fact)
        fact.media_ranges.filter(material=material).delete()
        fact.keyframe_links.filter(derivative__material=material).delete()
        fact.photo_links.filter(photo_material=material).delete()
        fact.transcript_links.filter(segment__transcript__material=material).delete()
        if fact.primary_source_material_id == material.id:
            fact.primary_source_material = None
        fact.needs_verification = True
        if not fact.verification_note:
            fact.verification_note = "原证据材料已删除，需要重新核实。"
        fact.revision += 1
        fact.save(
            update_fields=(
                "primary_source_material",
                "needs_verification",
                "verification_note",
                "revision",
                "updated_at",
            )
        )
        _record_fact_change(
            fact=fact,
            actor=actor,
            action="source_deleted",
            old_snapshot=old,
            reason_code="SOURCE_DELETED",
        )


@transaction.atomic
def link_fact_evidence(
    *,
    fact_id,
    actor,
    expected_revision: int,
    candidate_id: str,
    applicability_reason: str = "",
) -> Fact:
    from uuid import UUID

    from django.core.exceptions import ValidationError

    from fire_inspect.evidence.models import (
        FactClauseLink,
        FactKeyframeLink,
        FactMediaRange,
        FactPhotoLink,
        FactTranscriptLink,
    )
    from fire_inspect.evidence.services import EvidenceValidationError
    from fire_inspect.knowledge.django_models import KnowledgeClauseRecord
    from fire_inspect.materials.models import MaterialDerivative
    from fire_inspect.transcripts.models import TranscriptSegmentRecord

    try:
        candidate_type, raw_id = str(candidate_id).split(":", 1)
        source_id = UUID(raw_id)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(
            "invalid_evidence_candidate", "证据候选标识格式不正确。"
        ) from exc

    fact = Fact.objects.select_for_update().select_related("inspection").get(pk=fact_id)
    relation = None
    relation_values: dict = {"fact": fact, "inspection": fact.inspection}
    lookup: dict = {}
    try:
        if candidate_type == "media_range":
            source = Material.objects.get(
                pk=source_id,
                inspection_id=fact.inspection_id,
                kind__in=(Material.Kind.VIDEO, Material.Kind.AUDIO),
                duration_ms__isnull=False,
            )
            relation = FactMediaRange
            relation_values.update(
                {
                    "material": source,
                    "start_ms": 0,
                    "end_ms": source.duration_ms,
                    "note": "用户关联的材料时间范围。",
                }
            )
            lookup = {
                "fact": fact,
                "material": source,
                "start_ms": 0,
                "end_ms": source.duration_ms,
            }
        elif candidate_type == "transcript":
            source = TranscriptSegmentRecord.objects.select_related("transcript__material").get(
                pk=source_id,
                transcript__material__inspection_id=fact.inspection_id,
            )
            relation = FactTranscriptLink
            relation_values["segment"] = source
            lookup = {"fact": fact, "segment": source}
        elif candidate_type == "keyframe":
            source = MaterialDerivative.objects.select_related("material").get(
                pk=source_id,
                kind=MaterialDerivative.Kind.KEYFRAME,
                material__inspection_id=fact.inspection_id,
            )
            relation = FactKeyframeLink
            relation_values["derivative"] = source
            lookup = {"fact": fact, "derivative": source}
        elif candidate_type == "photo":
            source = Material.objects.get(
                pk=source_id,
                kind=Material.Kind.IMAGE,
                inspection_id=fact.inspection_id,
            )
            relation = FactPhotoLink
            relation_values["photo_material"] = source
            lookup = {"fact": fact, "photo_material": source}
        elif candidate_type == "clause":
            source = KnowledgeClauseRecord.objects.select_related("document").get(pk=source_id)
            relation = FactClauseLink
            relation_values.update(
                {
                    "clause": source,
                    "applicability_reason": (
                        str(applicability_reason).strip() or "用户从法规候选中显式关联。"
                    )[:1000],
                    "confirmed_by": actor,
                }
            )
            lookup = {"fact": fact, "clause": source}
        else:
            raise EvidenceValidationError("invalid_evidence_candidate", "不支持此证据候选类型。")
    except (
        KnowledgeClauseRecord.DoesNotExist,
        Material.DoesNotExist,
        MaterialDerivative.DoesNotExist,
        TranscriptSegmentRecord.DoesNotExist,
    ) as exc:
        raise EvidenceValidationError(
            "evidence_candidate_unavailable", "证据候选不存在或不属于当前检查。"
        ) from exc

    existing = relation.objects.filter(**lookup).first()
    if existing is not None:
        return fact
    if fact.revision != expected_revision:
        raise RevisionConflict(fact.revision)

    old = fact_snapshot(fact)
    try:
        relation.objects.create(**relation_values)
    except ValidationError as exc:
        raise EvidenceValidationError(
            "invalid_evidence_candidate", "证据候选无法关联到当前事实。"
        ) from exc
    fact.revision += 1
    fact.save(update_fields=("revision", "updated_at"))
    _record_fact_change(
        fact=fact,
        actor=actor,
        action="evidence_linked",
        old_snapshot=old,
        reason_code="REFERENCED_FACT_CHANGED",
    )
    return fact


def delete_fact(*, fact: Fact, actor, reason: str) -> None:
    with transaction.atomic():
        fact = Fact.objects.select_for_update().get(pk=fact.pk)
        old_snapshot = fact_snapshot(fact)
        DomainChangeHistory.objects.create(
            inspection=fact.inspection,
            actor=actor,
            object_type="fact",
            object_id=fact.id,
            action="deleted",
            old_values=old_snapshot,
            new_values={},
        )
        AuditEvent.append(
            actor=actor,
            object_type="fact",
            object_id=fact.id,
            inspection_id_snapshot=fact.inspection_id,
            action="deleted",
            reason=reason,
            masked_change_summary="fact deleted",
        )
        fact_changed.send(
            sender=Fact,
            fact=fact,
            actor=actor,
            action="deleted",
            old_snapshot=old_snapshot,
            new_snapshot={},
            reason_code="SOURCE_DELETED",
        )
        fact.delete()
