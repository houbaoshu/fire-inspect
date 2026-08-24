from __future__ import annotations

from datetime import date, datetime

import pytest

from fire_inspect.documents import (
    ChangeKind,
    DocumentSnapshot,
    DocumentState,
    DocumentType,
    FieldOrigin,
    FieldValue,
    InputChange,
    InvalidationReason,
    format_chinese_date,
    format_chinese_datetime,
    invalidate_documents,
    merge_regenerated_fields,
    new_document_name,
)


def test_regeneration_preserves_manual_fields_and_replaces_ai_fields() -> None:
    existing = {
        "document_number": FieldValue("人工编号", FieldOrigin.MANUAL, manual_locked=True),
        "inspection_details": FieldValue("旧 AI 内容", FieldOrigin.AI),
        "optional_note": FieldValue("人工备注", FieldOrigin.MANUAL, manual_locked=True),
    }

    result = merge_regenerated_fields(
        existing,
        {
            "document_number": "AI 编号",
            "inspection_details": "新 AI 内容",
            "new_suggestion": "新增建议",
        },
    )

    assert result.fields["document_number"].value == "人工编号"
    assert result.fields["document_number"].origin is FieldOrigin.MANUAL
    assert result.fields["optional_note"].value == "人工备注"
    assert result.fields["inspection_details"] == FieldValue("新 AI 内容", FieldOrigin.AI)
    assert result.fields["new_suggestion"] == FieldValue("新增建议", FieldOrigin.AI)
    assert result.preserved_manual_keys == frozenset({"document_number", "optional_note"})


def test_manual_field_requires_explicit_key_to_overwrite() -> None:
    existing = {"field": FieldValue("人工值", FieldOrigin.MANUAL, manual_locked=True)}
    result = merge_regenerated_fields(
        existing,
        {"field": "明确覆盖值"},
        explicit_manual_overrides={"field"},
    )
    assert result.fields["field"] == FieldValue("明确覆盖值", FieldOrigin.AI)
    assert result.overwritten_manual_keys == frozenset({"field"})


def test_empty_generation_is_not_allowed_to_masquerade_as_success() -> None:
    with pytest.raises(ValueError, match="空结果"):
        merge_regenerated_fields({}, {})


def _confirmed_documents() -> tuple[DocumentSnapshot, ...]:
    return (
        DocumentSnapshot(
            id="record",
            document_type=DocumentType.RECORD,
            state=DocumentState.CONFIRMED,
            fact_dependencies=frozenset({"fact-record"}),
            template_mapping_id="map-record",
        ),
        DocumentSnapshot(
            id="photo",
            document_type=DocumentType.PHOTO,
            state=DocumentState.CONFIRMED,
            fact_dependencies=frozenset({"fact-shared"}),
            template_mapping_id="map-photo",
        ),
        DocumentSnapshot(
            id="interview",
            document_type=DocumentType.INTERVIEW,
            state=DocumentState.CONFIRMED,
            fact_dependencies=frozenset({"fact-shared"}),
            template_mapping_id="map-interview",
        ),
    )


@pytest.mark.parametrize(
    ("change", "expected_ids", "reason"),
    (
        (
            InputChange(ChangeKind.SHARED_FIELDS, object_id="inspection"),
            {"record", "photo", "interview"},
            InvalidationReason.SHARED_FIELDS_CHANGED,
        ),
        (
            InputChange(ChangeKind.FACT, object_id="fact-shared"),
            {"photo", "interview"},
            InvalidationReason.REFERENCED_FACT_CHANGED,
        ),
        (
            InputChange(ChangeKind.PHOTO_CONTENT, object_id="item"),
            {"photo"},
            InvalidationReason.PHOTO_CONTENT_CHANGED,
        ),
        (
            InputChange(ChangeKind.QA_BODY, object_id="qa"),
            {"interview"},
            InvalidationReason.QA_BODY_CHANGED,
        ),
        (
            InputChange(ChangeKind.RECORD_FIELD, object_id="number"),
            {"record"},
            InvalidationReason.DOCUMENT_FIELD_EDITED,
        ),
        (
            InputChange(ChangeKind.TEMPLATE_MAPPING, object_id="map-photo"),
            {"photo"},
            InvalidationReason.TEMPLATE_MAPPING_CHANGED,
        ),
    ),
)
def test_confirmed_documents_are_invalidated_by_the_precise_dependency_matrix(
    change: InputChange, expected_ids: set[str], reason: InvalidationReason
) -> None:
    result = invalidate_documents(_confirmed_documents(), change)
    assert {event.document_id for event in result.events} == expected_ids
    assert {event.reason for event in result.events} == {reason}
    assert {
        document.id for document in result.documents if document.state is DocumentState.DRAFT
    } == expected_ids


def test_unrelated_or_noop_changes_do_not_modify_confirmed_documents() -> None:
    original = _confirmed_documents()
    unrelated = invalidate_documents(
        original,
        InputChange(ChangeKind.FACT, object_id="unused-fact"),
    )
    noop = invalidate_documents(
        original,
        InputChange(ChangeKind.SHARED_FIELDS, object_id="inspection", effective=False),
    )
    background_upgrade = invalidate_documents(
        original,
        InputChange(ChangeKind.GENERATOR_UPGRADE, object_id="v2"),
    )

    assert unrelated.documents == original and unrelated.events == ()
    assert noop.documents == original and noop.events == ()
    assert background_upgrade.documents == original and background_upgrade.events == ()


def test_date_time_and_default_name_formatting() -> None:
    assert format_chinese_date(date(2026, 8, 4)) == "2026年8月4日"
    assert format_chinese_datetime(datetime(2026, 8, 4, 9, 5)) == "2026年8月4日 09:05"
    assert new_document_name(" 某某单位 ", DocumentType.INTERVIEW) == "某某单位询问笔录"
    with pytest.raises(ValueError, match="单位名称"):
        new_document_name("  ", DocumentType.PHOTO)
