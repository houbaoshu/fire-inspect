from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from PIL import Image, ImageFilter, ImageStat


class MediaPipelineError(RuntimeError):
    """A user-safe media processing failure."""


@dataclass(frozen=True, slots=True)
class StreamProbe:
    index: int
    kind: str
    codec: str
    width: int | None = None
    height: int | None = None
    frame_rate: str = ""
    channels: int | None = None
    rotation: int = 0


@dataclass(frozen=True, slots=True)
class MediaProbe:
    format_name: str
    duration_ms: int
    byte_size: int
    streams: tuple[StreamProbe, ...]

    @property
    def video(self) -> StreamProbe | None:
        return next((stream for stream in self.streams if stream.kind == "video"), None)

    @property
    def audio(self) -> StreamProbe | None:
        return next((stream for stream in self.streams if stream.kind == "audio"), None)


@dataclass(frozen=True, slots=True)
class KeyFrame:
    timestamp_ms: int
    path: Path
    sha256: str
    width: int
    height: int
    brightness: float
    sharpness: float
    duplicate_group: str


@dataclass(slots=True)
class _FrameCandidate:
    timestamp_ms: int
    image: Image.Image
    brightness: float
    sharpness: float
    perceptual_hash: int


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaPipelineError(f"缺少媒体处理组件: {name}")
    return path


def _safe_subprocess(command: list[str], *, timeout: float, capture_stdout: bool = False) -> bytes:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaPipelineError("媒体处理超时，可稍后重试") from exc
    except OSError as exc:
        raise MediaPipelineError("无法启动媒体处理组件") from exc
    if completed.returncode != 0:
        # ffmpeg stderr can contain absolute paths and media metadata.  Keep it
        # out of the public exception and ordinary logs.
        raise MediaPipelineError("媒体损坏、格式不受支持或处理失败")
    return completed.stdout if capture_stdout else b""


def _rotation(stream: dict[str, Any]) -> int:
    tag_value = stream.get("tags", {}).get("rotate")
    if tag_value is not None:
        try:
            return int(float(tag_value)) % 360
        except (TypeError, ValueError):
            pass
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            try:
                return int(round(float(side_data["rotation"]))) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe_media(path: Path, *, timeout: float = 30) -> MediaProbe:
    if not path.is_file():
        raise MediaPipelineError("材料不存在或已删除")
    command = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    payload_bytes = _safe_subprocess(command, timeout=timeout, capture_stdout=True)
    try:
        payload = json.loads(payload_bytes)
        duration_ms = round(float(payload["format"]["duration"]) * 1000)
        byte_size = int(payload["format"].get("size", path.stat().st_size))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaPipelineError("材料缺少可用的媒体元数据") from exc
    streams = tuple(
        StreamProbe(
            index=int(stream["index"]),
            kind=str(stream.get("codec_type", "unknown")),
            codec=str(stream.get("codec_name", "unknown")),
            width=int(stream["width"]) if stream.get("width") else None,
            height=int(stream["height"]) if stream.get("height") else None,
            frame_rate=str(stream.get("avg_frame_rate", "")),
            channels=int(stream["channels"]) if stream.get("channels") else None,
            rotation=_rotation(stream),
        )
        for stream in payload.get("streams", [])
    )
    if duration_ms <= 0 or not streams:
        raise MediaPipelineError("材料没有可处理的媒体流")
    return MediaProbe(
        format_name=str(payload["format"].get("format_name", "")),
        duration_ms=duration_ms,
        byte_size=byte_size,
        streams=streams,
    )


def _atomic_target(output_path: Path) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f".{output_path.stem}-",
        suffix=output_path.suffix,
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    return temporary, output_path


def transcode_compatible_video(
    source: Path,
    output_path: Path,
    *,
    timeout: float = 600,
    expected_duration_tolerance_ms: int = 750,
) -> MediaProbe:
    source_probe = probe_media(source)
    if source_probe.video is None:
        raise MediaPipelineError("材料不包含视频流")
    temporary, target = _atomic_target(output_path)
    command = [
        _binary("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-noautorotate",
        "-display_rotation:v:0",
        "0",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-metadata:s:v:0",
        "rotate=0",
        "-y",
        str(temporary),
    ]
    try:
        _safe_subprocess(command, timeout=timeout)
        output_probe = probe_media(temporary)
        if output_probe.video is None or output_probe.video.codec != "h264":
            raise MediaPipelineError("兼容播放副本编码校验失败")
        if output_probe.video.rotation != 0:
            raise MediaPipelineError("兼容播放副本方向校验失败")
        if (
            abs(output_probe.duration_ms - source_probe.duration_ms)
            > expected_duration_tolerance_ms
        ):
            raise MediaPipelineError("兼容播放副本时间轴校验失败")
        os.replace(temporary, target)
        return output_probe
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def extract_audio(
    source: Path,
    output_path: Path,
    *,
    timeout: float = 300,
) -> MediaProbe:
    source_probe = probe_media(source)
    if source_probe.audio is None:
        raise MediaPipelineError("材料不包含音频流")
    temporary, target = _atomic_target(output_path)
    command = [
        _binary("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(temporary),
    ]
    try:
        _safe_subprocess(command, timeout=timeout)
        output_probe = probe_media(temporary)
        if output_probe.audio is None or output_probe.audio.codec != "pcm_s16le":
            raise MediaPipelineError("音频派生物校验失败")
        os.replace(temporary, target)
        return output_probe
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _decode_frame(source: Path, timestamp_ms: int, *, timeout: float) -> Image.Image:
    command = [
        _binary("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-noautorotate",
        "-ss",
        f"{timestamp_ms / 1000:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    raw = _safe_subprocess(command, timeout=timeout, capture_stdout=True)
    try:
        image = Image.open(BytesIO(raw))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        raise MediaPipelineError("关键画面解码失败") from exc


def _average_hash(image: Image.Image) -> int:
    resized = image.convert("L").resize((8, 8))
    values = list(resized.get_flattened_data())
    mean = sum(values) / len(values)
    result = 0
    for index, value in enumerate(values):
        if value >= mean:
            result |= 1 << index
    return result


def _analyse_frame(timestamp_ms: int, image: Image.Image) -> _FrameCandidate:
    grayscale = image.convert("L")
    brightness = float(ImageStat.Stat(grayscale).mean[0])
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    sharpness = float(ImageStat.Stat(edges).var[0])
    return _FrameCandidate(
        timestamp_ms=timestamp_ms,
        image=image,
        brightness=brightness,
        sharpness=sharpness,
        perceptual_hash=_average_hash(image),
    )


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def extract_keyframes(
    source: Path,
    output_dir: Path,
    *,
    sample_count: int = 10,
    maximum_frames: int = 6,
    minimum_brightness: float = 18,
    maximum_brightness: float = 245,
    minimum_sharpness: float = 4,
    duplicate_distance: int = 5,
    timeout_per_frame: float = 30,
) -> tuple[KeyFrame, ...]:
    probe = probe_media(source)
    if probe.video is None:
        raise MediaPipelineError("材料不包含视频流")
    sample_count = max(1, sample_count)
    maximum_frames = max(1, maximum_frames)
    # Avoid exact first/last frames, which are commonly black or undecodable.
    timestamps = sorted(
        {
            round(probe.duration_ms * (index + 1) / (sample_count + 1))
            for index in range(sample_count)
        }
    )
    candidates = [
        _analyse_frame(timestamp, _decode_frame(source, timestamp, timeout=timeout_per_frame))
        for timestamp in timestamps
    ]
    eligible = [
        candidate
        for candidate in candidates
        if minimum_brightness <= candidate.brightness <= maximum_brightness
        and candidate.sharpness >= minimum_sharpness
    ]
    if not eligible:
        # Required degradation path: never turn a decodable video into zero
        # candidates merely because quality thresholds do not fit the scene.
        eligible = [max(candidates, key=lambda item: item.sharpness)]
    selected: list[_FrameCandidate] = []
    for candidate in sorted(eligible, key=lambda item: (-item.sharpness, item.timestamp_ms)):
        if all(
            _hamming(candidate.perceptual_hash, existing.perceptual_hash) >= duplicate_distance
            for existing in selected
        ):
            selected.append(candidate)
        if len(selected) >= maximum_frames:
            break
    if not selected:
        selected = [max(candidates, key=lambda item: item.sharpness)]
    selected.sort(key=lambda item: item.timestamp_ms)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[KeyFrame] = []
    for index, candidate in enumerate(selected, start=1):
        target = output_dir / f"frame-{index:02d}-{candidate.timestamp_ms:08d}.jpg"
        temporary, _ = _atomic_target(target)
        try:
            candidate.image.save(temporary, format="JPEG", quality=88, optimize=True)
            payload = temporary.read_bytes()
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        results.append(
            KeyFrame(
                timestamp_ms=candidate.timestamp_ms,
                path=target,
                sha256=sha256(payload).hexdigest(),
                width=candidate.image.width,
                height=candidate.image.height,
                brightness=round(candidate.brightness, 3),
                sharpness=round(candidate.sharpness, 3),
                duplicate_group=f"ahash:{candidate.perceptual_hash:016x}",
            )
        )
    return tuple(results)
