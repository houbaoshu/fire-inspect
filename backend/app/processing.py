from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import get_ai
from .models import (
    Evidence,
    EvidenceType,
    Fact,
    FactEvidenceLink,
    FactStatus,
    Material,
    MaterialKind,
    ProcessingStatus,
    ProcessingStep,
    StepKind,
    TranscriptSegment,
    ValueSource,
)
from .storage import get_storage

ALGORITHM_VERSION = "v1"


def expected_steps(kind: MaterialKind) -> list[StepKind]:
    mapping = {
        MaterialKind.VIDEO: [
            StepKind.METADATA,
            StepKind.TRANSCRIPTION,
            StepKind.KEYFRAMES,
            StepKind.OCR_VISION,
            StepKind.FACT_EXTRACTION,
        ],
        MaterialKind.AUDIO: [
            StepKind.METADATA,
            StepKind.TRANSCRIPTION,
            StepKind.FACT_EXTRACTION,
        ],
        MaterialKind.IMAGE: [StepKind.METADATA, StepKind.OCR_VISION, StepKind.FACT_EXTRACTION],
        MaterialKind.TEXT: [StepKind.METADATA, StepKind.TRANSCRIPTION, StepKind.FACT_EXTRACTION],
        MaterialKind.OTHER: [StepKind.METADATA],
    }
    return mapping[kind]


def ensure_material_steps(db: Session, material: Material) -> list[ProcessingStep]:
    existing = {
        step.kind: step
        for step in db.scalars(
            select(ProcessingStep).where(
                ProcessingStep.subject_type == "material",
                ProcessingStep.subject_id == material.id,
                ProcessingStep.algorithm_version == ALGORITHM_VERSION,
            )
        )
    }
    for kind in expected_steps(material.kind):
        if kind not in existing:
            step = ProcessingStep(
                subject_type="material",
                subject_id=material.id,
                kind=kind,
                algorithm_version=ALGORITHM_VERSION,
            )
            db.add(step)
            existing[kind] = step
    db.commit()
    return [existing[kind] for kind in expected_steps(material.kind)]


def _run_step(db: Session, step: ProcessingStep, operation: Callable[[], dict[str, Any]]) -> None:
    if step.status == ProcessingStatus.SUCCEEDED:
        return
    step.status = ProcessingStatus.RUNNING
    step.attempt_count += 1
    step.error_code = None
    step.error_message = None
    db.commit()
    try:
        step.artifact = operation()
        step.status = ProcessingStatus.SUCCEEDED
    except Exception as exc:
        step.status = ProcessingStatus.FAILED
        step.error_code = type(exc).__name__
        step.error_message = str(exc)[:4000]
    db.commit()


def _metadata(data: bytes, material: Material) -> dict[str, Any]:
    result: dict[str, Any] = {
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": material.content_type,
    }
    if material.kind not in {MaterialKind.VIDEO, MaterialKind.AUDIO}:
        return result
    suffix = Path(material.filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix) as source:
        source.write(data)
        source.flush()
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
                source.name,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        result["media"] = json.loads(completed.stdout)
    return result


def _transcribe(db: Session, material: Material, data: bytes) -> dict[str, Any]:
    transcription_data = data
    transcription_content_type = material.content_type
    if material.kind == MaterialKind.VIDEO:
        suffix = Path(material.filename).suffix or ".mp4"
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / f"source{suffix}"
            audio = Path(temp_dir) / "audio.wav"
            source.write_bytes(data)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
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
                    str(audio),
                ],
                check=True,
            )
            transcription_data = audio.read_bytes()
        transcription_content_type = "audio/wav"
    results = get_ai().transcribe(transcription_data, transcription_content_type)
    existing = {
        (segment.start_ms, segment.end_ms): segment
        for segment in db.scalars(
            select(TranscriptSegment).where(TranscriptSegment.material_id == material.id)
        )
    }
    for result in results:
        segment = existing.get((result.start_ms, result.end_ms))
        if not segment:
            db.add(
                TranscriptSegment(
                    material_id=material.id,
                    start_ms=result.start_ms,
                    end_ms=result.end_ms,
                    speaker_label=result.speaker_label,
                    text=result.text,
                    confidence=result.confidence,
                )
            )
        elif segment.row_version == 1:
            segment.speaker_label = result.speaker_label
            segment.text = result.text
            segment.confidence = result.confidence
    db.flush()
    return {"segment_count": len(results), "requires_speaker_confirmation": bool(results)}


def _keyframes(db: Session, material: Material, data: bytes) -> dict[str, Any]:
    if material.kind != MaterialKind.VIDEO:
        return {"frame_count": 0}
    storage = get_storage()
    suffix = Path(material.filename).suffix or ".mp4"
    keys: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / f"source{suffix}"
        source.write_bytes(data)
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(json.loads(probe.stdout).get("format", {}).get("duration") or 0)
        timestamps = {float(second) for second in range(0, max(1, int(duration) + 1), 30)}
        scenes = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(source),
                "-vf",
                "select=gt(scene\\,0.35),showinfo",
                "-vsync",
                "vfr",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        timestamps.update(
            float(value)
            for value in re.findall(r"pts_time:([0-9.]+)", scenes.stderr)
            if float(value) <= duration
        )
        hashes: list[int] = []
        existing = {
            evidence.object_key: evidence
            for evidence in db.scalars(
                select(Evidence).where(
                    Evidence.material_id == material.id, Evidence.type == EvidenceType.IMAGE
                )
            )
        }
        for index, timestamp in enumerate(sorted(timestamps)[:120]):
            frame = Path(temp_dir) / f"frame-{index:04d}.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(frame),
                ],
                check=True,
            )
            if not frame.exists():
                continue
            frame_data = frame.read_bytes()
            image = Image.open(io.BytesIO(frame_data)).convert("L").resize((9, 8))
            pixels = list(image.getdata())
            perceptual_hash = sum(
                1 << bit
                for bit, (left, right) in enumerate(
                    (pixels[row * 9 + column], pixels[row * 9 + column + 1])
                    for row in range(8)
                    for column in range(8)
                )
                if left > right
            )
            if any((perceptual_hash ^ previous).bit_count() <= 6 for previous in hashes):
                continue
            hashes.append(perceptual_hash)
            timestamp_ms = round(timestamp * 1000)
            key = (
                f"inspections/{material.inspection_id}/derived/{material.id}/frames/"
                f"{timestamp_ms:010d}.jpg"
            )
            storage.put_bytes(key, frame_data, "image/jpeg")
            keys.append(key)
            evidence = existing.get(key)
            if not evidence:
                evidence = Evidence(
                    inspection_id=material.inspection_id,
                    type=EvidenceType.IMAGE,
                    material_id=material.id,
                    object_key=key,
                )
                db.add(evidence)
            evidence.start_ms = timestamp_ms
            evidence.end_ms = timestamp_ms
            evidence.label = f"{material.filename} {timestamp:.1f}s 关键画面"
            evidence.locator = {
                "frame_index": index,
                "timestamp_ms": timestamp_ms,
                "perceptual_hash": f"{perceptual_hash:016x}",
            }
    db.flush()
    return {"frame_count": len(keys), "object_keys": keys, "sampling": "scene+30s+dhash"}


def _vision(db: Session, material: Material, data: bytes) -> dict[str, Any]:
    analyzer = get_ai()
    if material.kind == MaterialKind.IMAGE:
        description = analyzer.analyze([data])[0]
        ocr_text = analyzer.extract(data).strip()
        evidence = db.scalar(
            select(Evidence).where(
                Evidence.material_id == material.id,
                Evidence.type == EvidenceType.IMAGE,
                Evidence.object_key == material.object_key,
            )
        )
        if not evidence:
            evidence = Evidence(
                inspection_id=material.inspection_id,
                type=EvidenceType.IMAGE,
                material_id=material.id,
                object_key=material.object_key,
                label=material.filename,
            )
            db.add(evidence)
        evidence.locator = {
            **(evidence.locator or {}),
            "description": str(description.get("description") or "").strip(),
            "vision_confidence": float(description.get("confidence") or 0.0),
            "ocr_text": ocr_text,
        }
        db.flush()
        return {
            "evidence_id": str(evidence.id),
            "description": description,
            "ocr_text": ocr_text,
            "requires_human_review": True,
        }
    frames = list(
        db.scalars(
            select(Evidence)
            .where(Evidence.material_id == material.id, Evidence.type == EvidenceType.IMAGE)
            .order_by(Evidence.start_ms, Evidence.created_at)
        )
    )
    frame_images = [
        (frame, get_storage().read_bytes(frame.object_key)) for frame in frames if frame.object_key
    ]
    results = analyzer.analyze([image for _, image in frame_images]) if frame_images else []
    described_count = 0
    ocr_count = 0
    for (frame, image), result in zip(frame_images, results, strict=False):
        description = str(result.get("description") or "").strip()
        ocr_text = analyzer.extract(image).strip()
        confidence = float(result.get("confidence") or 0.0)
        frame.locator = {
            **(frame.locator or {}),
            "description": description,
            "vision_confidence": confidence,
            "ocr_text": ocr_text,
        }
        described_count += bool(description)
        ocr_count += bool(ocr_text)
    db.flush()
    return {
        "image_count": len(frames),
        "analyzed_count": len(results),
        "described_count": described_count,
        "ocr_count": ocr_count,
        "requires_human_review": bool(frames),
    }


def _facts(db: Session, material: Material) -> dict[str, Any]:
    segments = list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.material_id == material.id)
            .order_by(TranscriptSegment.start_ms)
        )
    )
    sources: list[tuple[str, TranscriptSegment | Evidence]] = [
        (segment.text, segment) for segment in segments if segment.text.strip()
    ]
    image_evidence = db.scalars(
        select(Evidence)
        .where(Evidence.material_id == material.id, Evidence.type == EvidenceType.IMAGE)
        .order_by(Evidence.start_ms, Evidence.created_at)
    )
    for evidence in image_evidence:
        locator = evidence.locator or {}
        description = str(locator.get("description") or "").strip()
        ocr_text = str(locator.get("ocr_text") or "").strip()
        confidence = float(locator.get("vision_confidence") or 0.0)
        visual_parts = []
        if description and confidence > 0:
            visual_parts.append(f"画面客观描述：{description}")
        if ocr_text:
            visual_parts.append(f"画面可辨认文字：{ocr_text}")
        if visual_parts:
            sources.append(("\n".join(visual_parts), evidence))
    candidates = get_ai().extract_facts([text for text, _ in sources])
    created = 0
    for candidate in candidates:
        evidence_indexes = sorted(
            {index for index in candidate.evidence_indexes if 0 <= index < len(sources)}
        )
        if not evidence_indexes:
            continue
        duplicate = db.scalar(
            select(Fact).where(
                Fact.inspection_id == material.inspection_id,
                Fact.title == candidate.title,
                Fact.status != FactStatus.DELETED,
            )
        )
        if duplicate:
            continue
        fact = Fact(
            inspection_id=material.inspection_id,
            title=candidate.title,
            detail=candidate.detail,
            status=FactStatus.CANDIDATE,
            confidence=candidate.confidence,
            source=ValueSource.GENERATED,
            needs_confirmation=True,
        )
        db.add(fact)
        db.flush()
        for index in evidence_indexes:
            source_text, source = sources[index]
            if isinstance(source, TranscriptSegment):
                evidence = db.scalar(
                    select(Evidence).where(
                        Evidence.type == EvidenceType.TRANSCRIPT_RANGE,
                        Evidence.transcript_segment_id == source.id,
                    )
                )
                if not evidence:
                    evidence = Evidence(
                        inspection_id=material.inspection_id,
                        type=EvidenceType.TRANSCRIPT_RANGE,
                        material_id=material.id,
                        transcript_segment_id=source.id,
                        start_ms=source.start_ms,
                        end_ms=source.end_ms,
                        label=(
                            f"{material.filename} "
                            f"{source.start_ms // 1000}s–{source.end_ms // 1000}s"
                        ),
                        quote=source_text,
                        locator={},
                    )
                    db.add(evidence)
                    db.flush()
            else:
                evidence = source
            db.add(FactEvidenceLink(fact_id=fact.id, evidence_id=evidence.id))
        created += 1
    db.flush()
    return {"candidate_count": created}


def process_material(db: Session, material_id: uuid.UUID, only: StepKind | None = None) -> None:
    material = db.get(Material, material_id)
    if not material or material.deleted_at:
        raise ValueError("材料不存在")
    data = get_storage().read_bytes(material.object_key)
    steps = ensure_material_steps(db, material)
    operations: dict[StepKind, Callable[[], dict[str, Any]]] = {
        StepKind.METADATA: lambda: _metadata(data, material),
        StepKind.TRANSCRIPTION: lambda: _transcribe(db, material, data),
        StepKind.KEYFRAMES: lambda: _keyframes(db, material, data),
        StepKind.OCR_VISION: lambda: _vision(db, material, data),
        StepKind.FACT_EXTRACTION: lambda: _facts(db, material),
    }
    for step in steps:
        if only and step.kind != only:
            continue
        _run_step(db, step, operations[step.kind])
