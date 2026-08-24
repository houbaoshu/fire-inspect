from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job_by_id
from fire_inspect.templates.models import TemplateVersion, WordTemplate

pytestmark = pytest.mark.django_db

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "docs" / "templates"


def _upload(
    client,
    *,
    filename: str,
    document_type: str,
    name: str,
    idempotency_key: str | None = None,
):
    payload = (TEMPLATE_ROOT / filename).read_bytes()
    upload = SimpleUploadedFile(
        filename,
        payload,
        content_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )
    return client.post(
        "/api/v1/templates/",
        {"name": name, "document_type": document_type, "file": upload},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY=idempotency_key or f"upload-{document_type}",
    ), payload


def _run_recognition(template_id) -> ProcessingJob:
    job = ProcessingJob.objects.get(
        task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
        scope_type="word_template",
        scope_id=template_id,
    )
    claimed = acquire_job_by_id(job_id=job.id, worker_id="template-test")
    assert claimed is not None
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    job.refresh_from_db()
    return job


def test_template_management_is_admin_only(authenticated_client, inspector_a, admin_user):
    inspector_client = authenticated_client(inspector_a)
    assert inspector_client.get("/api/v1/templates/").status_code == 403
    denied, _ = _upload(
        inspector_client,
        filename="照片.docx",
        document_type="photo",
        name="照片模板",
    )
    assert denied.status_code == 403

    admin_client = authenticated_client(admin_user)
    assert admin_client.get("/api/v1/templates/").status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("filename", "document_type", "region_names"),
    [
        (
            "照片.docx",
            "photo",
            {"照片", "反映内容", "拍摄地点", "摄制人", "拍摄时间", "说明", "确认人"},
        ),
        (
            "询问笔录.docx",
            "interview",
            {
                "询问次数",
                "起止时间",
                "地点",
                "询问人及工作单位",
                "记录人及工作单位",
                "被询问人基础信息",
                "证件",
                "地址",
                "联系方式",
                "问答正文",
            },
        ),
        (
            "监督检查记录表.docx",
            "record",
            {
                "编号",
                "日期",
                "检查类型",
                "单位名称",
                "负责人",
                "地址",
                "联系人及电话",
                "检查内容和情况",
                "备注及签名区域",
            },
        ),
    ],
)
def test_upload_preserves_original_then_worker_exposes_stable_chinese_mapping(
    authenticated_client,
    admin_user,
    filename,
    document_type,
    region_names,
):
    client = authenticated_client(admin_user)
    response, payload = _upload(
        client,
        filename=filename,
        document_type=document_type,
        name=f"{document_type}-template",
    )

    assert response.status_code == 202, response.data
    assert response.data["recognition_status"] == "queued"
    assert response.data["recognition_job"]["status"] == "queued"
    assert response.data["recognition_job"]["task_type"] == "template_recognition"
    assert response.data["mapping"] is None
    assert "sha256" not in response.data

    template = WordTemplate.objects.get(pk=response.data["id"])
    version = template.versions.get()
    with version.original_file.open("rb") as stream:
        stored = stream.read()
    assert stored == payload
    assert version.sha256 == sha256(payload).hexdigest()
    assert sha256((TEMPLATE_ROOT / filename).read_bytes()).digest() == sha256(payload).digest()

    job = _run_recognition(template.id)
    assert job.status == ProcessingJob.Status.SUCCEEDED
    recognized = client.get(f"/api/v1/templates/{template.id}/")
    assert recognized.status_code == 200
    assert recognized.data["recognition_status"] == "succeeded"
    assert recognized.data["recognition_job"]["status"] == "succeeded"
    assert recognized.data["mapping"]["state"] == "draft"
    assert {item["display_name"] for item in recognized.data["mapping"]["regions"]} == region_names
    assert all("locator" not in item for item in recognized.data["mapping"]["regions"])


def test_template_upload_idempotency_does_not_duplicate_resource_or_job(
    authenticated_client, admin_user
):
    client = authenticated_client(admin_user)
    first, _ = _upload(
        client,
        filename="照片.docx",
        document_type="photo",
        name="幂等照片模板",
        idempotency_key="same-template-upload",
    )
    repeated, _ = _upload(
        client,
        filename="照片.docx",
        document_type="photo",
        name="幂等照片模板",
        idempotency_key="same-template-upload",
    )
    assert first.status_code == 202, first.data
    assert repeated.status_code == 200, repeated.data
    assert repeated.data["id"] == first.data["id"]
    assert repeated.data["recognition_job"]["id"] == first.data["recognition_job"]["id"]
    assert WordTemplate.objects.count() == 1
    assert TemplateVersion.objects.count() == 1
    assert (
        ProcessingJob.objects.filter(task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_mapping_edit_confirm_and_revise_are_versioned(authenticated_client, admin_user):
    client = authenticated_client(admin_user)
    uploaded, _ = _upload(
        client,
        filename="照片.docx",
        document_type="photo",
        name="照片模板",
    )
    template_id = uploaded.data["id"]
    _run_recognition(template_id)
    recognized = client.get(f"/api/v1/templates/{template_id}/")
    region = recognized.data["mapping"]["regions"][1]

    edited = client.patch(
        f"/api/v1/templates/{template_id}/mapping/regions/{region['id']}/",
        {"display_name": "现场反映内容", "fill_strategy": "label_suffix", "enabled": True},
        format="json",
    )
    assert edited.status_code == 200, edited.data
    assert edited.data["display_name"] == "现场反映内容"

    confirmed = client.post(
        f"/api/v1/templates/{template_id}/mapping/confirm/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="confirm-photo-mapping",
    )
    assert confirmed.status_code == 200, confirmed.data
    assert confirmed.data["mapping"]["state"] == "confirmed"

    immutable = client.patch(
        f"/api/v1/templates/{template_id}/mapping/regions/{region['id']}/",
        {"display_name": "不应写入"},
        format="json",
    )
    assert immutable.status_code == 409
    assert immutable.data["code"] == "confirmed_mapping_immutable"

    revised = client.post(
        f"/api/v1/templates/{template_id}/mapping/revise/",
        {},
        format="json",
    )
    assert revised.status_code == 201, revised.data
    assert revised.data["mapping"]["state"] == "draft"
    assert revised.data["mapping"]["revision_no"] == 2
    assert revised.data["mapping"]["regions"][1]["display_name"] == "现场反映内容"


def test_template_version_rejects_mutation_after_insert(authenticated_client, admin_user):
    client = authenticated_client(admin_user)
    uploaded, _ = _upload(
        client,
        filename="监督检查记录表.docx",
        document_type="record",
        name="记录模板",
    )
    version = TemplateVersion.objects.get(template_id=uploaded.data["id"])
    version.sha256 = "0" * 64
    with pytest.raises(ValidationError, match="不可修改"):
        version.save()


@pytest.mark.django_db(transaction=True)
def test_recognition_failure_is_visible_and_retry_reuses_only_failed_job(
    authenticated_client, admin_user
):
    client = authenticated_client(admin_user)
    queued, payload = _upload(
        client,
        filename="照片.docx",
        document_type="record",
        name="类型不匹配模板",
        idempotency_key="upload-mismatched-template",
    )
    assert queued.status_code == 202, queued.data
    template = WordTemplate.objects.get(pk=queued.data["id"])
    version = template.versions.get()
    with version.original_file.open("rb") as stream:
        assert stream.read() == payload

    job = _run_recognition(template.id)
    assert job.status == ProcessingJob.Status.FAILED
    failed = client.get(f"/api/v1/templates/{template.id}/")
    assert failed.data["recognition_status"] == "failed"
    assert failed.data["safe_error_message"]
    assert failed.data["recognition_job"]["safe_error_message"]
    assert failed.data["mapping"] is None

    retried = client.post(
        f"/api/v1/templates/{template.id}/recognize/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="retry-mismatched-template",
    )
    assert retried.status_code == 202, retried.data
    assert retried.data["recognition_status"] == "queued"
    assert retried.data["recognition_job"]["id"] == str(job.id)
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
            scope_id=template.id,
        ).count()
        == 1
    )

    rerun = _run_recognition(template.id)
    assert rerun.id == job.id
    assert rerun.status == ProcessingJob.Status.FAILED
    replayed_retry = client.post(
        f"/api/v1/templates/{template.id}/recognize/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="retry-mismatched-template",
    )
    assert replayed_retry.status_code == 200
    assert replayed_retry.data["recognition_status"] == "failed"
    assert replayed_retry.data["recognition_job"]["status"] == "failed"
