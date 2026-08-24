import shutil
import subprocess
from pathlib import Path

import pytest

from fire_inspect.media_pipeline import (
    MediaPipelineError,
    extract_audio,
    extract_keyframes,
    probe_media,
    transcode_compatible_video,
)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    path = tmp_path_factory.mktemp("media") / "safe-fixture.mp4"
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
    return path


def test_probe_reports_video_audio_and_duration(synthetic_video: Path) -> None:
    probe = probe_media(synthetic_video)
    assert probe.video and probe.video.codec == "h264"
    assert probe.video.width == 320
    assert probe.audio and probe.audio.codec == "aac"
    assert 1_800 <= probe.duration_ms <= 2_200


def test_compatibility_transcode_preserves_timeline(synthetic_video: Path, tmp_path: Path) -> None:
    output = tmp_path / "proxy.mp4"
    probe = transcode_compatible_video(synthetic_video, output)
    assert output.is_file()
    assert probe.video and probe.video.codec == "h264"
    assert probe.video.rotation == 0


def test_audio_and_keyframes_are_independent_derivatives(
    synthetic_video: Path, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.wav"
    extract_audio(synthetic_video, audio)
    frames = extract_keyframes(
        synthetic_video,
        tmp_path / "frames",
        sample_count=4,
        maximum_frames=3,
        minimum_sharpness=10_000_000,  # forces the documented fallback
    )
    assert audio.is_file()
    assert frames
    assert all(frame.path.is_file() and frame.width == 320 for frame in frames)


def test_public_error_does_not_leak_absolute_path(tmp_path: Path) -> None:
    secret_path = tmp_path / "SENSITIVE-directory" / "missing.mp4"
    with pytest.raises(MediaPipelineError) as error:
        probe_media(secret_path)
    assert str(secret_path) not in str(error.value)
