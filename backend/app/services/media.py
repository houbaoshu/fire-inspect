import base64
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import get_storage
from app.models import Asset, Evidence, Fact, FactEvidence
from app.services.ai import ai_provider


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _probe(path: Path) -> dict[str, Any]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def _extract_audio(source: Path, target: Path) -> None:
    _run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(target)])


def _extract_frames(source: Path, directory: Path) -> list[tuple[Path, int]]:
    interval_dir = directory / "interval"
    scene_dir = directory / "scene"
    interval_dir.mkdir()
    scene_dir.mkdir()
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vf",
            ("select='eq(n,0)+gte(t-prev_selected_t,10)',scale='min(1280,iw)':-2,format=yuvj420p"),
            "-fps_mode",
            "vfr",
            "-frames:v",
            "18",
            str(interval_dir / "%03d.jpg"),
        ]
    )
    scene = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vf",
            ("select='gt(scene,0.35)',showinfo,scale='min(1280,iw)':-2,format=yuvj420p"),
            "-fps_mode",
            "vfr",
            "-frames:v",
            "12",
            str(scene_dir / "%03d.jpg"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    times = [int(float(value) * 1000) for value in re.findall(r"pts_time:([0-9.]+)", scene.stderr)]
    frames = [
        (path, (index - 1) * 10_000)
        for index, path in enumerate(sorted(interval_dir.glob("*.jpg")), 1)
    ]
    frames += [
        (path, times[index] if index < len(times) else 0)
        for index, path in enumerate(sorted(scene_dir.glob("*.jpg")))
    ]
    unique: dict[int, Path] = {}
    for path, timestamp in frames:
        unique.setdefault(timestamp, path)
    return [(path, timestamp) for timestamp, path in sorted(unique.items())]


def _persist_fact_candidates(
    db: Session,
    asset: Asset,
    evidence: list[Evidence],
    extracted: dict[str, Any] | list[Any] | None,
) -> int:
    if not extracted:
        return 0
    candidates = extracted if isinstance(extracted, list) else extracted.get("facts", [])
    if not isinstance(candidates, list):
        return 0
    evidence_ids = {item.id for item in evidence}
    count = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source_ids = [item for item in candidate.get("evidence_ids", []) if item in evidence_ids]
        summary = str(candidate.get("summary", "")).strip()
        if not source_ids or not summary:
            continue
        fact = Fact(
            inspection_id=asset.inspection_id,
            kind=str(candidate.get("kind") or "observation"),
            summary=summary,
            content=candidate.get("content") if isinstance(candidate.get("content"), dict) else {},
            status="candidate",
            source="ai",
            confidence=float(candidate["confidence"])
            if candidate.get("confidence") is not None
            else None,
        )
        db.add(fact)
        db.flush()
        for evidence_id in source_ids:
            db.add(FactEvidence(fact_id=fact.id, evidence_id=evidence_id))
        count += 1
    return count


def process_asset(db: Session, asset_id: str) -> str:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("材料不存在")
    asset.processing_status = "processing"
    asset.processing_error = None
    db.commit()
    storage = get_storage()
    evidence: list[Evidence] = []
    try:
        suffix = Path(asset.filename).suffix.lower()
        with (
            storage.materialize(asset.storage_key, suffix=suffix) as source,
            TemporaryDirectory() as temp,
        ):
            directory = Path(temp)
            if asset.kind in {"video", "audio"}:
                metadata = _probe(source)
                asset.metadata_json = metadata
                audio = directory / "audio.wav"
                _extract_audio(source, audio)
                for segment in ai_provider.transcribe(audio):
                    item = Evidence(
                        inspection_id=asset.inspection_id,
                        asset_id=asset.id,
                        kind="transcript",
                        start_ms=segment["start_ms"],
                        end_ms=segment["end_ms"],
                        excerpt=segment["text"],
                    )
                    db.add(item)
                    db.flush()
                    evidence.append(item)
                if asset.kind == "video":
                    for frame, timestamp in _extract_frames(source, directory):
                        key = f"derived/{asset.id}/frame-{timestamp}.jpg"
                        with frame.open("rb") as fileobj:
                            storage.put_fileobj(key, fileobj, "image/jpeg")
                        item = Evidence(
                            inspection_id=asset.inspection_id,
                            asset_id=asset.id,
                            kind="keyframe",
                            start_ms=timestamp,
                            end_ms=timestamp,
                            storage_key=key,
                        )
                        db.add(item)
                        db.flush()
                        evidence.append(item)
            elif asset.kind == "image":
                item = Evidence(
                    inspection_id=asset.inspection_id,
                    asset_id=asset.id,
                    kind="photo",
                    storage_key=asset.storage_key,
                )
                db.add(item)
                db.flush()
                evidence.append(item)
            elif asset.kind == "text":
                text = source.read_text(encoding="utf-8-sig")
                item = Evidence(
                    inspection_id=asset.inspection_id,
                    asset_id=asset.id,
                    kind="user_material",
                    excerpt=text,
                )
                db.add(item)
                db.flush()
                evidence.append(item)

            evidence_payload = [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "excerpt": item.excerpt,
                }
                for item in evidence
            ]
            analysis_input: str | list[dict[str, Any]] = (
                f"证据：{json.dumps(evidence_payload, ensure_ascii=False)}"
            )
            if get_settings().ai_enabled:
                analysis_input = [
                    {
                        "type": "text",
                        "text": (
                            "以下证据清单中的 ID 是返回 evidence_ids 的唯一允许值：\n"
                            + json.dumps(evidence_payload, ensure_ascii=False)
                        ),
                    }
                ]
                for item in [row for row in evidence if row.kind in {"keyframe", "photo"}][:12]:
                    analysis_input.append(
                        {
                            "type": "text",
                            "text": f"关键画面证据 ID={item.id}，时间={item.start_ms or 0}ms",
                        }
                    )
                    encoded = base64.b64encode(storage.read_bytes(item.storage_key)).decode()
                    image_type = "image/jpeg" if item.kind == "keyframe" else asset.content_type
                    analysis_input.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_type};base64,{encoded}",
                                "detail": "low",
                            },
                        }
                    )
            extracted = ai_provider.chat_json(
                system=(
                    "你是消防检查材料整理助手。只整理输入证据中明确存在的事实，不作最终执法判断。"
                    "必须返回顶层 JSON 对象，不得直接返回数组。严格格式为："
                    '{"facts":[{"kind":"...","summary":"...","content":{},'
                    '"confidence":0.0,"evidence_ids":["..."]}]}。'
                    "evidence_ids 必须至少包含一个输入 ID。"
                ),
                user=analysis_input,
            )
            count = _persist_fact_candidates(db, asset, evidence, extracted)
            metadata = dict(asset.metadata_json)
            metadata["candidate_fact_count"] = count
            if not get_settings().ai_enabled:
                metadata["warning"] = "未配置 AI 服务，已提取基础证据，未生成候选事实"
            asset.metadata_json = metadata
            asset.processing_status = "completed"
            db.commit()
            return f"已生成 {len(evidence)} 条证据、{count} 条候选事实"
    except Exception as exc:
        db.rollback()
        asset = db.get(Asset, asset_id)
        asset.processing_status = "failed"
        asset.processing_error = str(exc)
        db.commit()
        raise
