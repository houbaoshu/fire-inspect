import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import enqueue_job
from fire_inspect.materials.models import Material, MaterialDerivative


@pytest.fixture
def synthetic_upload(tmp_path: Path) -> SimpleUploadedFile:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    path = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        check=True,
    )
    return SimpleUploadedFile("fixture.mp4", path.read_bytes(), content_type="video/mp4")


@pytest.fixture
def video_material(authenticated_client, inspector_a, synthetic_upload):
    client = authenticated_client(inspector_a)
    inspection_response = client.post(
        "/api/v1/inspections",
        {
            "unit_name": "媒体任务测试单位",
            "inspection_at": "2026-08-24T10:00:00+08:00",
            "inspection_type": "other",
        },
        format="json",
    )
    assert inspection_response.status_code == 201
    upload_response = client.post(
        f"/api/v1/inspections/{inspection_response.data['id']}/materials",
        {"file": synthetic_upload},
        format="multipart",
    )
    assert upload_response.status_code == 201, upload_response.data
    return Material.objects.get(pk=upload_response.data["id"])


@pytest.mark.django_db(transaction=True)
def test_upload_records_duration_before_any_processing(video_material) -> None:
    assert video_material.duration_ms is not None
    assert 1_500 <= video_material.duration_ms <= 2_500
    assert not ProcessingJob.objects.filter(scope_id=video_material.id).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("task_type", "expected_kind"),
    [
        ("transcode", MaterialDerivative.Kind.PLAYBACK),
        ("audio_extract", MaterialDerivative.Kind.AUDIO_EXTRACT),
        ("keyframes", MaterialDerivative.Kind.KEYFRAME),
    ],
)
def test_independent_media_handlers_publish_verified_derivatives(
    task_type, expected_kind, video_material, inspector_a
) -> None:
    job, _ = enqueue_job(
        task_type=task_type,
        scope_type="material",
        scope_id=video_material.id,
        inspection=video_material.inspection,
        input_fingerprint=f"{task_type}-fingerprint",
        idempotency_key=str(uuid4()),
        created_by=inspector_a,
        expected_scope_revision=video_material.revision,
    )
    call_command("runworker", once=True, concurrency=1)
    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED
    derivatives = MaterialDerivative.objects.filter(
        material=video_material, kind=expected_kind, source_job_id=job.id
    )
    assert derivatives.exists()
    assert all(
        (Path(__import__("django.conf").conf.settings.MEDIA_ROOT) / item.storage_key).is_file()
        for item in derivatives
    )
    video_material.refresh_from_db()
    assert video_material.duration_ms
