from __future__ import annotations

import hashlib

import pytest
from django.contrib.sessions.models import Session
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

import fire_inspect.operations.management.commands.post_restore_smoke as smoke_command
from fire_inspect.documents.models import (
    DocumentExport,
    DocumentRevision,
    InspectionDocument,
)
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.materials.models import Material
from fire_inspect.operations.database_privileges import RUNTIME_ROLE
from fire_inspect.operations.management.commands.post_restore_smoke import (
    PostRestoreSmokeConfiguration,
    run_post_restore_smoke,
)
from fire_inspect.templates.models import (
    TemplateMappingRevision,
    TemplateVersion,
    WordTemplate,
)


@pytest.mark.django_db
def test_post_restore_smoke_exercises_authenticated_read_export_and_writes(
    inspector_a,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    settings.ALLOWED_HOSTS = ["fire-inspect.test"]
    settings.CSRF_TRUSTED_ORIGINS = ["https://fire-inspect.test"]
    inspection = Inspection.objects.create(
        owner=inspector_a,
        unit_name="恢复冒烟单位",
        unit_address="测试地址",
        responsible_person="负责人",
        contact_name="联系人",
        contact_phone="13800000000",
        inspection_at=timezone.now(),
        inspection_type=Inspection.Type.OTHER,
        general_notes="",
    )
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="restore-smoke.txt",
        text="controlled restore smoke payload",
    )

    artifact = b"PK\x03\x04post-restore-document-artifact"
    artifact_sha256 = hashlib.sha256(artifact).hexdigest()
    template = WordTemplate.objects.create(
        document_type=WordTemplate.Type.RECORD,
        name="恢复冒烟模板",
        recognition_status=WordTemplate.RecognitionStatus.SUCCEEDED,
        created_by=inspector_a,
    )
    version = TemplateVersion(
        template=template,
        version_no=1,
        original_filename="restore-smoke.docx",
        sha256=artifact_sha256,
        validation_result={"passed": True},
        created_by=inspector_a,
    )
    version.original_file.save("original.docx", ContentFile(artifact), save=False)
    version.save(force_insert=True)
    mapping = TemplateMappingRevision.objects.create(
        template_version=version,
        revision_no=1,
        state=TemplateMappingRevision.State.CONFIRMED,
        recognizer_version="restore-smoke-v1",
        created_by=inspector_a,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
    )
    document = InspectionDocument.objects.create(
        inspection=inspection,
        document_type=InspectionDocument.Type.RECORD,
        name="恢复冒烟文书",
        template_version=version,
        current_mapping_revision=mapping,
        state=InspectionDocument.State.CONFIRMED,
        generator_status=InspectionDocument.GeneratorStatus.SUCCEEDED,
        generation_valid=True,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
        created_by=inspector_a,
    )
    storage_key = default_storage.save(
        f"documents/{document.id}/restore-smoke/document.docx", ContentFile(artifact)
    )
    revision = DocumentRevision.objects.create(
        document=document,
        revision_no=1,
        document_revision_no=document.revision,
        event_type=DocumentRevision.EventType.GENERATED,
        content_snapshot={},
        input_revision_vector={},
        mapping_revision=mapping,
        generator_version=document.generator_version,
        generated_storage_key=storage_key,
        generated_sha256=artifact_sha256,
        page_count=1,
        structure_check_result={"passed": True},
        render_check_result={"passed": True},
        created_by=inspector_a,
    )
    document.last_generated_revision = revision
    document.save(update_fields=("last_generated_revision", "updated_at"))

    monkeypatch.setenv("FIRE_INSPECT_DATABASE_ROLE", RUNTIME_ROLE)
    monkeypatch.setattr(smoke_command, "validate_database_role", lambda **kwargs: {})
    configuration = PostRestoreSmokeConfiguration(
        username=inspector_a.username,
        password="Strong-Test-Password-42!",
        inspection_id=inspection.id,
        material_id=material.id,
        material_sha256=material.sha256,
        document_id=document.id,
        document_sha256=artifact_sha256,
    )

    run_post_restore_smoke(configuration)

    assert Session.objects.count() == 1
    assert DocumentExport.objects.filter(document=document).count() == 1
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.FACT_EXTRACTION,
            scope_id=material.id,
        ).count()
        == 1
    )
    assert Material.objects.filter(inspection=inspection).count() == 2
