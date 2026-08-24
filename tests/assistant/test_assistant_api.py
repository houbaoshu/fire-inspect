import pytest

from fire_inspect.ai.schemas import AssistantAnswer, NavigationAction, NavigationKind
from fire_inspect.assistant.views import _context_sources, _navigation_payload
from fire_inspect.documents.models import InspectionDocument
from fire_inspect.evidence.models import FactClauseLink
from fire_inspect.facts.models import Fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.knowledge.django_models import (
    KnowledgeClauseRecord,
    KnowledgeDocumentRecord,
)
from fire_inspect.materials.models import Material
from fire_inspect.templates.services import initialize_authoritative_templates
from fire_inspect.transcripts.models import Transcript


def create_inspection(client):
    response = client.post(
        "/api/v1/inspections",
        {
            "unit_name": "助手权限测试单位",
            "inspection_at": "2026-08-24T10:00:00+08:00",
            "inspection_type": "other",
        },
        format="json",
    )
    assert response.status_code == 201
    return response.data


@pytest.mark.django_db
def test_assistant_is_read_only_and_owner_scoped(
    authenticated_client, inspector_a, inspector_b, monkeypatch
) -> None:
    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "fixed")
    monkeypatch.setenv("FIRE_INSPECT_ALLOW_FIXED_AI", "1")
    inspection = create_inspection(authenticated_client(inspector_a))
    owner_response = authenticated_client(inspector_a).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": inspection["id"],
            "question": "本次检查有哪些待核实内容？",
            "route": f"/inspections/{inspection['id']}/facts",
        },
        format="json",
    )
    assert owner_response.status_code == 200
    assert owner_response.data["read_only"] is True
    assert "只读建议" in owner_response.data["text"]
    foreign_response = authenticated_client(inspector_b).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": inspection["id"],
            "question": "读取别人的检查",
            "route": "/",
        },
        format="json",
    )
    assert foreign_response.status_code == 404


@pytest.mark.django_db
def test_unconfigured_assistant_reports_unavailable_without_fake_answer(
    authenticated_client, inspector_a, monkeypatch
) -> None:
    for suffix in ("PROVIDER", "ENDPOINT", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"FIRE_INSPECT_LLM_{suffix}", raising=False)
    inspection = create_inspection(authenticated_client(inspector_a))
    response = authenticated_client(inspector_a).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": inspection["id"],
            "question": "总结",
            "route": "/",
        },
        format="json",
    )
    assert response.status_code == 503
    assert "未配置" in response.data["error"]["message"]


@pytest.mark.django_db
def test_assistant_context_uses_installed_models_and_remains_inspection_scoped(
    authenticated_client, inspector_a, inspector_b, admin_user
) -> None:
    owner_payload = create_inspection(authenticated_client(inspector_a))
    foreign_payload = create_inspection(authenticated_client(inspector_b))
    inspection = Inspection.objects.get(pk=owner_payload["id"])
    foreign_inspection = Inspection.objects.get(pk=foreign_payload["id"])

    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="现场说明.txt",
        text="问：现场情况？答：需要核实。",
    )
    transcript = Transcript.objects.create(
        material=material,
        full_text="问：现场情况？答：需要核实。",
        updated_by=inspector_a,
    )
    fact = Fact.objects.create(
        inspection=inspection,
        content="疏散通道需要核实",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    foreign_fact = Fact.objects.create(
        inspection=foreign_inspection,
        content="另一检查的事实",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_b,
    )
    photo = Material.objects.create(
        inspection=inspection,
        kind=Material.Kind.IMAGE,
        display_name="本次检查照片.jpg",
        original_file=f"originals/inspections/{inspection.id}/photo/original",
        mime_type="image/jpeg",
        extension=".jpg",
        sha256="a" * 64,
        byte_size=10,
        created_by=inspector_a,
    )
    foreign_photo = Material.objects.create(
        inspection=foreign_inspection,
        kind=Material.Kind.IMAGE,
        display_name="另一检查照片.jpg",
        original_file=f"originals/inspections/{foreign_inspection.id}/photo/original",
        mime_type="image/jpeg",
        extension=".jpg",
        sha256="b" * 64,
        byte_size=10,
        created_by=inspector_b,
    )
    law = KnowledgeDocumentRecord.objects.create(
        title="测试法规",
        category=KnowledgeDocumentRecord.Category.LAW,
        original_file="knowledge/law/original.pdf",
        mime_type="application/pdf",
        sha256="c" * 64,
        byte_size=10,
    )
    clause = KnowledgeClauseRecord.objects.create(
        document=law,
        sequence=1,
        clause_identifier="第一条",
        content="消防设施应当保持完好有效。",
        normalized_text="消防设施应当保持完好有效",
        page_start=1,
        page_end=1,
    )
    foreign_clause = KnowledgeClauseRecord.objects.create(
        document=law,
        sequence=2,
        clause_identifier="第二条",
        content="另一检查关联的条款原文。",
        normalized_text="另一检查关联的条款原文",
        page_start=1,
        page_end=1,
    )
    FactClauseLink.objects.create(
        inspection=inspection,
        fact=fact,
        clause=clause,
        applicability_reason="与已确认事实相关",
        confirmed_by=inspector_a,
    )
    FactClauseLink.objects.create(
        inspection=foreign_inspection,
        fact=foreign_fact,
        clause=foreign_clause,
        applicability_reason="另一检查的关联",
        confirmed_by=inspector_b,
    )
    templates = initialize_authoritative_templates(actor=admin_user)
    mapping = templates["record"].active_mapping_revision
    document = InspectionDocument.objects.create(
        inspection=inspection,
        document_type=InspectionDocument.Type.RECORD,
        name="监督检查记录",
        template_version=mapping.template_version,
        current_mapping_revision=mapping,
        created_by=inspector_a,
    )

    sources, navigation, materials, material_navigation = _context_sources(inspection)
    source_ids = {(source.source_type, source.source_id) for source in sources}

    assert ("fact", fact.id) in source_ids
    assert ("transcript", transcript.id) in source_ids
    assert ("document", document.id) in source_ids
    assert ("photo", photo.id) in source_ids
    assert ("clause", clause.id) in source_ids
    assert ("fact", foreign_fact.id) not in source_ids
    assert ("photo", foreign_photo.id) not in source_ids
    assert ("clause", foreign_clause.id) not in source_ids
    assert fact.id in navigation[NavigationKind.FACT]
    assert document.id in navigation[NavigationKind.DOCUMENT]
    assert photo.id in navigation[NavigationKind.PHOTO]
    assert foreign_photo.id not in navigation[NavigationKind.PHOTO]
    assert clause.id in navigation[NavigationKind.CLAUSE]
    assert foreign_clause.id not in navigation[NavigationKind.CLAUSE]
    assert (
        next(source for source in sources if source.source_id == clause.id).text == clause.content
    )
    assert material.id in materials
    assert material_navigation == {}

    photo_payload = _navigation_payload(
        NavigationAction(NavigationKind.PHOTO, photo.id), inspection
    )
    clause_payload = _navigation_payload(
        NavigationAction(NavigationKind.CLAUSE, clause.id), inspection
    )
    document_payload = _navigation_payload(
        NavigationAction(NavigationKind.DOCUMENT, document.id), inspection
    )
    assert photo_payload["photo_id"] == str(photo.id)
    assert clause_payload["document_id"] == str(law.id)
    assert document_payload["document_type"] == InspectionDocument.Type.RECORD


@pytest.mark.django_db
def test_assistant_accepts_json_uuid_for_selected_authorized_source(
    authenticated_client, inspector_a, monkeypatch
) -> None:
    class Provider:
        def answer(self, question, context):
            assert question == "解释所选事实"
            assert context["selected_text"] == "疏散通道需要核实"
            return AssistantAnswer("只读解释")

    payload = create_inspection(authenticated_client(inspector_a))
    inspection = Inspection.objects.get(pk=payload["id"])
    fact = Fact.objects.create(
        inspection=inspection,
        content="疏散通道需要核实",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    monkeypatch.setattr("fire_inspect.assistant.views.provider_for", lambda capability: Provider())

    response = authenticated_client(inspector_a).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": str(inspection.id),
            "question": "解释所选事实",
            "route": f"/inspections/{inspection.id}/facts",
            "selected_source_id": str(fact.id),
            "selected_revision": 1,
            "selected_text": "疏散通道需要核实",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["text"] == "只读解释"


@pytest.mark.django_db
def test_assistant_retrieves_traceable_unlinked_clause_without_widening_inspection_scope(
    authenticated_client, inspector_a, inspector_b, monkeypatch
) -> None:
    class Provider:
        def answer(self, question, context):
            assert question == "消防设施保持完好有效涉及什么法规条款？"
            clause_sources = [
                item for item in context["untrusted_data"] if item["source_type"] == "clause"
            ]
            assert len(clause_sources) == 1
            assert "测试消防法规" in clause_sources[0]["text"]
            assert "第一条" in clause_sources[0]["text"]
            assert "消防设施应当保持完好有效" in clause_sources[0]["text"]
            assert "待核验" in clause_sources[0]["text"]
            return AssistantAnswer("请结合本次检查事实人工判断条款适用性。")

    owner_inspection = create_inspection(authenticated_client(inspector_a))
    foreign_inspection = create_inspection(authenticated_client(inspector_b))
    law = KnowledgeDocumentRecord.objects.create(
        title="测试消防法规",
        category=KnowledgeDocumentRecord.Category.LAW,
        original_file="knowledge/unlinked/original.pdf",
        mime_type="application/pdf",
        sha256="d" * 64,
        byte_size=10,
        processing_status=KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED,
    )
    clause = KnowledgeClauseRecord.objects.create(
        document=law,
        sequence=1,
        clause_identifier="第一条",
        content="消防设施应当保持完好有效。",
        normalized_text="消防设施应当保持完好有效",
        page_start=3,
        page_end=3,
    )
    assert not FactClauseLink.objects.filter(clause=clause).exists()
    monkeypatch.setattr("fire_inspect.assistant.views.provider_for", lambda capability: Provider())

    owner_response = authenticated_client(inspector_a).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": owner_inspection["id"],
            "question": "消防设施保持完好有效涉及什么法规条款？",
            "route": f"/inspections/{owner_inspection['id']}/facts",
        },
        format="json",
    )

    assert owner_response.status_code == 200
    payload = owner_response.json()
    assert payload["read_only"] is True
    assert "《测试消防法规》第一条" in payload["text"]
    assert "待核验" in payload["text"]
    assert payload["citations"] == [
        {
            "source_type": "clause",
            "source_id": str(clause.id),
            "excerpt": "《测试消防法规》第一条（第3页，待核验）：消防设施应当保持完好有效。",
        }
    ]
    assert payload["navigation"] == [
        {
            "kind": "clause",
            "target_id": str(clause.id),
            "inspection_id": owner_inspection["id"],
            "material_id": None,
            "start_ms": None,
            "clause_id": str(clause.id),
            "document_id": str(law.id),
        }
    ]
    assert not FactClauseLink.objects.filter(clause=clause).exists()

    foreign_response = authenticated_client(inspector_b).post(
        "/api/v1/assistant/query",
        {
            "inspection_id": owner_inspection["id"],
            "question": "消防设施保持完好有效涉及什么法规条款？",
            "route": f"/inspections/{foreign_inspection['id']}/facts",
        },
        format="json",
    )
    assert foreign_response.status_code == 404
