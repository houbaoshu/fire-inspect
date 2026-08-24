from pathlib import Path

import pytest

from fire_inspect.media_pipeline import (
    extract_keyframes,
    probe_media,
    transcode_compatible_video,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.local_acceptance
@pytest.mark.parametrize(
    ("name", "expected_rotation"),
    [("1.mp4", 90), ("4.mp4", 0)],
)
def test_real_video_probe_and_keyframe_fallback(
    name: str, expected_rotation: int, tmp_path: Path
) -> None:
    source = ROOT / "testdata/videos" / name
    if not source.is_file():
        pytest.skip("authorized local video is unavailable")
    probe = probe_media(source)
    assert probe.video is not None
    assert (probe.video.width, probe.video.height) == (1920, 1080)
    assert probe.video.rotation == expected_rotation
    assert probe.audio is not None
    playback = tmp_path / f"{name}.playback.mp4"
    playback_probe = transcode_compatible_video(source, playback)
    assert playback.is_file() and playback.stat().st_size > 0
    assert playback_probe.video is not None
    assert playback_probe.video.codec == "h264"
    assert playback_probe.video.rotation == 0
    assert abs(playback_probe.duration_ms - probe.duration_ms) <= 750
    frames = extract_keyframes(
        source,
        tmp_path / name,
        sample_count=5,
        maximum_frames=3,
    )
    assert frames
    assert all(frame.path.is_file() for frame in frames)
