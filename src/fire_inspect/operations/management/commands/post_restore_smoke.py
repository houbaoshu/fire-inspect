from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from fire_inspect.operations.database_privileges import RUNTIME_ROLE
from fire_inspect.operations.preflight import validate_database_role

_HOST = "fire-inspect.test"
_ORIGIN = f"https://{_HOST}"
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


class PostRestoreSmokeError(RuntimeError):
    def __init__(self, stage: str, *, status_code: int | None = None):
        self.stage = stage
        self.status_code = status_code
        super().__init__(stage)


@dataclass(frozen=True, slots=True, repr=False)
class PostRestoreSmokeConfiguration:
    username: str
    password: str
    inspection_id: UUID
    material_id: UUID
    material_sha256: str
    document_id: UUID
    document_sha256: str

    def __repr__(self) -> str:
        return (
            "PostRestoreSmokeConfiguration(username=<redacted>, password=<redacted>, "
            "inspection_id=<redacted>, material_id=<redacted>, document_id=<redacted>)"
        )

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> PostRestoreSmokeConfiguration:
        source = os.environ if environment is None else environment
        required = {
            "username": source.get("FIRE_INSPECT_SMOKE_USERNAME", "").strip(),
            "password": source.get("FIRE_INSPECT_SMOKE_PASSWORD", ""),
            "inspection_id": source.get("FIRE_INSPECT_SMOKE_INSPECTION_ID", "").strip(),
            "material_id": source.get("FIRE_INSPECT_SMOKE_MATERIAL_ID", "").strip(),
            "material_sha256": source.get("FIRE_INSPECT_SMOKE_MATERIAL_SHA256", "").strip(),
            "document_id": source.get("FIRE_INSPECT_SMOKE_DOCUMENT_ID", "").strip(),
            "document_sha256": source.get("FIRE_INSPECT_SMOKE_DOCUMENT_SHA256", "").strip(),
        }
        if not all(required.values()):
            raise PostRestoreSmokeError("configuration")
        try:
            inspection_id = UUID(required["inspection_id"])
            material_id = UUID(required["material_id"])
            document_id = UUID(required["document_id"])
        except (TypeError, ValueError, AttributeError) as exc:
            raise PostRestoreSmokeError("configuration") from exc
        if not _SHA256_RE.fullmatch(required["material_sha256"]):
            raise PostRestoreSmokeError("configuration")
        if not _SHA256_RE.fullmatch(required["document_sha256"]):
            raise PostRestoreSmokeError("configuration")
        return cls(
            username=required["username"],
            password=required["password"],
            inspection_id=inspection_id,
            material_id=material_id,
            material_sha256=required["material_sha256"].lower(),
            document_id=document_id,
            document_sha256=required["document_sha256"].lower(),
        )


def _request_options() -> dict[str, object]:
    return {"HTTP_HOST": _HOST, "secure": True}


def _unsafe_request_options(csrf_token: str) -> dict[str, object]:
    return {
        **_request_options(),
        "HTTP_ORIGIN": _ORIGIN,
        "HTTP_X_CSRFTOKEN": csrf_token,
    }


def _expect_status(response, expected: Iterable[int], stage: str) -> None:
    expected_codes = frozenset(expected)
    if response.status_code not in expected_codes:
        response.close()
        raise PostRestoreSmokeError(stage, status_code=response.status_code)


def _json(response, stage: str):
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise PostRestoreSmokeError(stage, status_code=response.status_code) from exc


def _response_sha256(response, stage: str) -> str:
    digest = hashlib.sha256()
    try:
        if getattr(response, "streaming", False):
            for chunk in response.streaming_content:
                digest.update(chunk)
        else:
            digest.update(response.content)
    except Exception as exc:
        raise PostRestoreSmokeError(stage, status_code=response.status_code) from exc
    finally:
        response.close()
    return digest.hexdigest()


def _csrf_token(client: Client, stage: str) -> str:
    response = client.get("/api/v1/auth/csrf", **_request_options())
    _expect_status(response, (200,), stage)
    payload = _json(response, stage)
    token = payload.get("csrf_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise PostRestoreSmokeError(stage, status_code=response.status_code)
    return token


def run_post_restore_smoke(configuration: PostRestoreSmokeConfiguration) -> None:
    stage = "database-role"
    try:
        if os.environ.get("FIRE_INSPECT_DATABASE_ROLE", "").strip() != RUNTIME_ROLE:
            raise PostRestoreSmokeError(stage)
        validate_database_role(expected_role=RUNTIME_ROLE)

        stage = "session-isolation"
        if Session.objects.exists():
            raise PostRestoreSmokeError(stage)

        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        token = _csrf_token(client, "csrf-before-login")

        stage = "login"
        response = client.post(
            "/api/v1/auth/login",
            {"username": configuration.username, "password": configuration.password},
            content_type="application/json",
            **_unsafe_request_options(token),
        )
        _expect_status(response, (200,), stage)
        if not Session.objects.exists():
            raise PostRestoreSmokeError(stage)

        token = _csrf_token(client, "csrf-after-login")

        stage = "inspection-detail"
        response = client.get(
            f"/api/v1/inspections/{configuration.inspection_id}", **_request_options()
        )
        _expect_status(response, (200,), stage)
        payload = _json(response, stage)
        if not isinstance(payload, dict) or payload.get("id") != str(configuration.inspection_id):
            raise PostRestoreSmokeError(stage, status_code=response.status_code)

        stage = "material-download"
        response = client.get(
            f"/api/v1/materials/{configuration.material_id}/download", **_request_options()
        )
        _expect_status(response, (200,), stage)
        if _response_sha256(response, stage) != configuration.material_sha256:
            raise PostRestoreSmokeError(stage, status_code=200)

        stage = "document-list"
        response = client.get(
            f"/api/v1/inspections/{configuration.inspection_id}/documents",
            **_request_options(),
        )
        _expect_status(response, (200,), stage)
        documents = _json(response, stage)
        if not isinstance(documents, list) or str(configuration.document_id) not in {
            str(item.get("id")) for item in documents if isinstance(item, dict)
        }:
            raise PostRestoreSmokeError(stage, status_code=response.status_code)

        stage = "document-detail"
        response = client.get(
            f"/api/v1/documents/{configuration.document_id}", **_request_options()
        )
        _expect_status(response, (200,), stage)
        document = _json(response, stage)
        if (
            not isinstance(document, dict)
            or document.get("id") != str(configuration.document_id)
            or document.get("generator_status") != "succeeded"
            or not document.get("generated_page_count")
        ):
            raise PostRestoreSmokeError(stage, status_code=response.status_code)

        stage = "document-export"
        response = client.post(
            f"/api/v1/documents/{configuration.document_id}/export",
            {"expected_revision": document.get("revision")},
            content_type="application/json",
            **_unsafe_request_options(token),
        )
        _expect_status(response, (200,), stage)
        if _response_sha256(response, stage) != configuration.document_sha256:
            raise PostRestoreSmokeError(stage, status_code=200)

        stage = "persistent-job"
        response = client.post(
            f"/api/v1/materials/{configuration.material_id}/jobs/fact_extraction",
            {},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=(
                f"post-restore-smoke:fact-extraction:{configuration.material_id}"
            ),
            **_unsafe_request_options(token),
        )
        _expect_status(response, (200, 202), stage)

        stage = "business-write"
        response = client.post(
            f"/api/v1/inspections/{configuration.inspection_id}/materials",
            {
                "kind": "text",
                "display_name": "post-restore-runtime-smoke.txt",
                "text": f"post-restore-runtime-smoke:{uuid4().hex}",
            },
            content_type="application/json",
            **_unsafe_request_options(token),
        )
        _expect_status(response, (201,), stage)
    except PostRestoreSmokeError:
        raise
    except Exception as exc:
        raise PostRestoreSmokeError(stage) from exc


class Command(BaseCommand):
    help = "Exercise restored data through the authenticated runtime request stack."

    def handle(self, *args, **options):
        try:
            configuration = PostRestoreSmokeConfiguration.from_environment()
            run_post_restore_smoke(configuration)
        except PostRestoreSmokeError as exc:
            status = f" status={exc.status_code}" if exc.status_code is not None else ""
            raise CommandError(f"post-restore runtime smoke failed: {exc.stage}{status}") from None
        self.stdout.write("post-restore runtime smoke passed")
