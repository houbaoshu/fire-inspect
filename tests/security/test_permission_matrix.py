from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import enqueue_job
from fire_inspect.transcripts.services import publish_transcript_output
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection, inspection_payload

pytestmark = pytest.mark.django_db


def _owned_graph(owner):
    inspection = create_inspection(owner)
    material = create_media_material(inspection=inspection, created_by=owner)
    transcript = publish_transcript_output(
        TranscriptOutput(
            full_text="问：测试？\n答：测试。",
            segments=(TranscriptSegment(material.id, 0, 10_000, "测试？", SemanticRole.QUESTION),),
        ),
        updated_by=owner,
    )[0]
    job = enqueue_job(
        task_type=ProcessingJob.TaskType.ASR,
        scope_type="material",
        scope_id=material.id,
        inspection=inspection,
        input_fingerprint="permission-matrix",
        idempotency_key=str(uuid4()),
        created_by=owner,
        expected_scope_revision=material.revision,
    )[0]
    return inspection, material, transcript, transcript.segments.get(), job


def test_anonymous_business_and_file_endpoints_consistently_return_401(
    api_client, inspector_a
) -> None:
    inspection, material, transcript, segment, job = _owned_graph(inspector_a)
    paths = (
        ("get", "/api/v1/inspections"),
        ("get", f"/api/v1/inspections/{inspection.id}"),
        ("get", f"/api/v1/inspections/{inspection.id}/history"),
        ("get", f"/api/v1/inspections/{inspection.id}/materials"),
        ("get", f"/api/v1/materials/{material.id}/download"),
        ("get", f"/api/v1/materials/{material.id}/play"),
        ("get", f"/api/v1/materials/{material.id}/transcript"),
        ("get", f"/api/v1/transcripts/{transcript.id}"),
        ("get", f"/api/v1/transcripts/{transcript.id}/history"),
        ("get", f"/api/v1/jobs/{job.id}"),
        ("get", f"/api/v1/inspections/{inspection.id}/facts"),
        ("post", "/api/v1/assistant/query"),
        ("get", "/api/v1/knowledge/search/?q=test"),
        ("get", "/api/v1/admin/audit"),
    )
    for method, path in paths:
        response = getattr(api_client, method)(path, {}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (method, path, response.data)
        assert response.json()["error"]["code"] == "not_authenticated"


def test_other_inspector_cannot_guess_read_write_delete_or_submit_jobs(
    authenticated_client, inspector_a, inspector_b
) -> None:
    inspection, material, transcript, segment, job = _owned_graph(inspector_a)
    owner = authenticated_client(inspector_a)
    created_fact = owner.post(
        f"/api/v1/inspections/{inspection.id}/facts",
        {"content": "仅检查员 A 可见"},
        format="json",
    )
    assert created_fact.status_code == status.HTTP_201_CREATED
    fact_id = created_fact.json()["id"]

    other = authenticated_client(inspector_b)
    calls = (
        ("get", f"/api/v1/inspections/{inspection.id}", None),
        (
            "patch",
            f"/api/v1/inspections/{inspection.id}",
            {"unit_name": "越权", "shared_revision": 1},
        ),
        ("delete", f"/api/v1/inspections/{inspection.id}", {"confirm": True, "reason": "越权"}),
        ("get", f"/api/v1/inspections/{inspection.id}/history", None),
        ("get", f"/api/v1/inspections/{inspection.id}/deletion-impact", None),
        ("get", f"/api/v1/inspections/{inspection.id}/materials", None),
        (
            "post",
            f"/api/v1/inspections/{inspection.id}/materials",
            {"kind": "text", "text": "越权"},
        ),
        ("get", f"/api/v1/materials/{material.id}/download", None),
        ("get", f"/api/v1/materials/{material.id}/play", None),
        ("delete", f"/api/v1/materials/{material.id}", {"confirm": True, "reason": "越权"}),
        ("post", f"/api/v1/materials/{material.id}/jobs/asr", None),
        ("get", f"/api/v1/jobs/{job.id}", None),
        ("post", f"/api/v1/jobs/{job.id}/retry", None),
        ("get", f"/api/v1/materials/{material.id}/transcript", None),
        ("get", f"/api/v1/transcripts/{transcript.id}", None),
        ("patch", f"/api/v1/transcripts/{transcript.id}", {"full_text": "越权", "revision": 1}),
        ("get", f"/api/v1/transcripts/{transcript.id}/history", None),
        (
            "patch",
            f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}",
            {"text": "越权", "transcript_revision": 1},
        ),
        (
            "post",
            f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}/confirm-role",
            {"role": "answer", "transcript_revision": 1},
        ),
        ("get", f"/api/v1/inspections/{inspection.id}/facts", None),
        ("post", f"/api/v1/inspections/{inspection.id}/facts", {"content": "越权"}),
        ("get", f"/api/v1/inspections/{inspection.id}/facts/{fact_id}", None),
        (
            "patch",
            f"/api/v1/inspections/{inspection.id}/facts/{fact_id}",
            {"content": "越权", "revision": 1},
        ),
        (
            "delete",
            f"/api/v1/inspections/{inspection.id}/facts/{fact_id}",
            {"confirm": True, "reason": "越权"},
        ),
        ("post", f"/api/v1/inspections/{inspection.id}/facts/{fact_id}/confirm", {"revision": 1}),
        ("get", f"/api/v1/inspections/{inspection.id}/facts/{fact_id}/history", None),
        (
            "post",
            "/api/v1/assistant/query",
            {"inspection_id": str(inspection.id), "question": "越权读取"},
        ),
    )
    for method, path, payload in calls:
        extra = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())} if "/jobs/" in path else {}
        response = getattr(other, method)(path, payload or {}, format="json", **extra)
        assert response.status_code == status.HTTP_404_NOT_FOUND, (method, path, response.data)

    inspection.refresh_from_db()
    transcript.refresh_from_db()
    assert inspection.unit_name == "某商业综合体"
    assert transcript.full_text == "问：测试？\n答：测试。"


def test_inspector_is_forbidden_from_admin_management_endpoints(
    authenticated_client, inspector_a
) -> None:
    client = authenticated_client(inspector_a)
    assert client.get("/api/v1/users").status_code == status.HTTP_403_FORBIDDEN
    assert client.get("/api/v1/knowledge/documents/").status_code == status.HTTP_403_FORBIDDEN
    response = client.post(
        "/api/v1/knowledge/documents/",
        {"title": "越权", "category": "other"},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert client.get("/api/v1/admin/audit").status_code == status.HTTP_403_FORBIDDEN


def test_session_login_and_all_mutations_require_csrf(inspector_a) -> None:
    client = APIClient(enforce_csrf_checks=True)
    csrf_response = client.get("/api/v1/auth/csrf")
    token = csrf_response.cookies["csrftoken"].value
    credentials = {"username": inspector_a.username, "password": "Strong-Test-Password-42!"}

    missing_login_csrf = client.post("/api/v1/auth/login", credentials, format="json")
    assert missing_login_csrf.status_code == status.HTTP_403_FORBIDDEN
    logged_in = client.post(
        "/api/v1/auth/login", credentials, format="json", HTTP_X_CSRFTOKEN=token
    )
    assert logged_in.status_code == status.HTTP_200_OK

    without_csrf = client.post("/api/v1/inspections", inspection_payload(), format="json")
    assert without_csrf.status_code == status.HTTP_403_FORBIDDEN
    rotated_token = client.cookies["csrftoken"].value
    with_csrf = client.post(
        "/api/v1/inspections",
        inspection_payload(),
        format="json",
        HTTP_X_CSRFTOKEN=rotated_token,
    )
    assert with_csrf.status_code == status.HTTP_201_CREATED
