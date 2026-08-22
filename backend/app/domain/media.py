"""ffmpeg 媒体流水线封装（ASR-VERIFICATION §1/§4 已验证形态）。

- 抽音频：16kHz 单声道 MP3（转写用）
- 窗口切分：默认 10s 窗 + 1s 重叠（硬切边界失真缓解）
- 关键帧：等间隔抽帧 → 模糊/过暗过滤（拉普拉斯方差、平均亮度）→ phash 去重 → 限量
"""
import json, subprocess, shutil
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter
import imagehash
from ..core.config import settings


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"{cmd[0]} 未安装（app 镜像内需包含 ffmpeg）")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}... 失败：{p.stderr[-300:]}")
    return p


def duration_ms(path: str) -> int:
    p = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path])
    return int(float(json.loads(p.stdout)["format"]["duration"]) * 1000)


def extract_audio(video_path: str, out_path: str):
    _run(["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", out_path])


def split_audio_windows(audio_path: str, out_dir: Path) -> list[tuple[Path, int, int]]:
    """按窗长+重叠切分，返回 [(文件, start_ms, end_ms)]。静音切分不可靠（ASR-VERIFICATION §4），用硬窗。"""
    total_ms = duration_ms(audio_path)
    win = int(settings.asr_window_sec * 1000)
    step = int((settings.asr_window_sec - settings.asr_overlap_sec) * 1000)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    start = 0
    idx = 0
    while start < total_ms:
        end = min(start + win, total_ms)
        out = out_dir / f"win_{idx:03d}.mp3"
        _run(["ffmpeg", "-y", "-ss", f"{start/1000:.3f}", "-to", f"{end/1000:.3f}",
              "-i", audio_path, "-ac", "1", "-ar", "16000", "-b:a", "32k", out])
        segments.append((out, start, end))
        if end >= total_ms:
            break
        start += step
        idx += 1
    return segments


def _laplacian_variance(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    # 简单卷积（帧图不大，无需 scipy）
    padded = np.pad(g, 1, mode="edge")
    lap = sum(kernel[1 + dy, 1 + dx] * padded[1 + dy:1 + dy + g.shape[0], 1 + dx:1 + dx + g.shape[1]]
              for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    return float(lap.var())


def _brightness(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean())


def extract_keyframes(video_path: str, out_dir: Path) -> list[dict]:
    """抽帧 → 质量过滤 → phash 去重 → 限量。返回 [{path, ts_ms, quality_score, phash}]。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "raw_%04d.jpg")
    _run(["ffmpeg", "-y", "-i", video_path, "-vf", f"fps=1/{settings.keyframe_interval_sec}",
          "-q:v", "3", pattern])
    frames = sorted(out_dir.glob("raw_*.jpg"))
    total = duration_ms(video_path)
    scored: list[dict] = []
    for i, fp in enumerate(frames):
        try:
            img = Image.open(fp)
        except Exception:
            continue
        bright = _brightness(img)
        sharp = _laplacian_variance(img)
        ts = min(int(i * settings.keyframe_interval_sec * 1000), total)
        scored.append({"path": str(fp), "ts_ms": ts, "quality_score": round(sharp, 1), "bright": bright})
    # 质量过滤：模糊/过暗剔除（澄清 §3）
    passed = [f for f in scored
              if f["bright"] >= settings.keyframe_dark_threshold and f["quality_score"] >= settings.keyframe_blur_threshold]
    if len(passed) < settings.keyframe_min_keep and scored:
        # 兜底：过滤后过少时按清晰度保留头部，绝不让选图为空
        passed = sorted(scored, key=lambda f: -f["quality_score"])[:settings.keyframe_min_keep]
        passed.sort(key=lambda f: f["ts_ms"])
    dropped = {f["path"] for f in scored} - {f["path"] for f in passed}
    for p in dropped:
        Path(p).unlink(missing_ok=True)
    kept: list[dict] = []
    hashes: list[imagehash.ImageHash] = []
    for f in passed:
        img = Image.open(f["path"])
        h = imagehash.phash(img)
        if any(abs(h - prev) <= settings.keyframe_phash_hamming for prev in hashes):
            Path(f["path"]).unlink(missing_ok=True)
            continue  # 相似帧去重
        hashes.append(h)
        kept.append({"path": f["path"], "ts_ms": f["ts_ms"], "quality_score": f["quality_score"], "phash": str(h)})
    # 限量：均匀取样到 keyframe_max
    if len(kept) > settings.keyframe_max:
        idxs = np.linspace(0, len(kept) - 1, settings.keyframe_max).round().astype(int)
        removed = {i for i in range(len(kept))} - set(idxs.tolist())
        for i in removed:
            Path(kept[i]["path"]).unlink(missing_ok=True)
        kept = [kept[i] for i in sorted(set(idxs.tolist()))]
    return kept


def extract_frame_at(video_path: str, ts_ms: int, out_path: str):
    _run(["ffmpeg", "-y", "-ss", f"{ts_ms/1000:.3f}", "-i", video_path, "-frames:v", "1", "-q:v", "3", out_path])
