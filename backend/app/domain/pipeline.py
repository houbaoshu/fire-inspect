"""媒体处理任务实现：转写双跑、关键帧、VL 描述、事实提取（PLAN §1.5-2/3/4）。"""
from pathlib import Path
from sqlalchemy.orm import Session
from ..core.config import MEDIA_DIR, settings
from ..core.models import Material, Transcript, TranscriptSegment, Keyframe, Fact, FactEvidence
from ..core import ai
from . import media


def _work_dir(material: Material) -> Path:
    d = MEDIA_DIR / f"m{material.id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audio_path(db: Session, material: Material) -> Path:
    """抽取（或复用）16kHz 单声道音频。录音材料直接转码。"""
    d = _work_dir(material)
    audio = d / "audio.mp3"
    if not audio.exists():
        media.extract_audio(str(MEDIA_DIR.parent / material.file_path), str(audio))
    return audio


def run_transcribe_full(db: Session, material: Material):
    audio = _audio_path(db, material)
    text = ai.asr_transcribe(str(audio))
    total = material.duration_ms or media.duration_ms(str(audio))
    db.query(Transcript).filter(Transcript.material_id == material.id, Transcript.kind == "full").delete()
    t = Transcript(material_id=material.id, kind="full")
    t.segments.append(TranscriptSegment(material_id=material.id, seq=0, start_ms=0, end_ms=total, text=text))
    db.add(t)
    db.flush()


def run_transcribe_windows(db: Session, material: Material):
    audio = _audio_path(db, material)
    win_dir = _work_dir(material) / "windows"
    segments = media.split_audio_windows(str(audio), win_dir)
    db.query(Transcript).filter(Transcript.material_id == material.id, Transcript.kind == "window").delete()
    t = Transcript(material_id=material.id, kind="window")
    db.add(t)
    db.flush()
    for seq, (path, start_ms, end_ms) in enumerate(segments):
        text = ai.asr_transcribe(str(path))
        db.add(TranscriptSegment(transcript_id=t.id, material_id=material.id, seq=seq,
                                 start_ms=start_ms, end_ms=end_ms, text=text))
    db.flush()


def run_keyframes(db: Session, material: Material):
    src = str(MEDIA_DIR.parent / material.file_path)
    frames = media.extract_keyframes(src, _work_dir(material) / "frames")
    db.query(Keyframe).filter(Keyframe.material_id == material.id, Keyframe.source == "extracted").delete()
    db.flush()
    rows = []
    for f in frames:
        rel = str(Path(f["path"]).relative_to(MEDIA_DIR.parent))
        kf = Keyframe(inspection_id=material.inspection_id, material_id=material.id, ts_ms=f["ts_ms"],
                      image_path=rel, quality_score=f["quality_score"], phash=f["phash"], source="extracted")
        db.add(kf)
        rows.append(kf)
    db.flush()
    # VL 画面描述：失败不影响抽帧结果（PLAN §1.5：单步失败独立）
    if settings.ai_available("vl"):
        for kf in rows:
            try:
                kf.caption = ai.vl_describe(
                    str(MEDIA_DIR.parent / kf.image_path),
                    "这是消防监督检查视频中抽取的一帧画面。请用一句简短客观的话描述画面内容（不超过50字），"
                    "重点关注：场所类型、消防设施、人员、可见的隐患或问题。")
            except ai.AIError:
                pass  # 描述失败不阻塞；用户仍可见帧图
    db.flush()


FACT_SYSTEM = """你是消防监督检查辅助系统的信息抽取模块。根据检查视频转写文本，抽取候选检查事实。
要求：
1. 只抽取文本中明确提到的内容，不确定的信息不要猜测，宁可少抽。
2. 每条事实是简短客观的陈述句（如"消防控制室值班人员今日休息，无人值班"）。
3. 基础信息（单位名称、地址、人员姓名等）若文本中提及也作为事实抽取，前缀注明类别，如"【基础信息】单位名称：XX"。
4. 为每条事实给出支撑它的转写时间段编号（segment_ids）。
输出 JSON：{"facts": [{"content": "...", "segment_ids": [0, 1]}]}"""


def run_fact_extract(db: Session, material: Material):
    """从整段转写 + 窗口分段提取候选事实，并自动关联时间锚点证据。"""
    full = (db.query(Transcript).filter_by(material_id=material.id, kind="full").first())
    if not full or not full.segments or not full.segments[0].text.strip():
        raise RuntimeError("尚无整段转写结果，无法提取事实")
    win = db.query(Transcript).filter_by(material_id=material.id, kind="window").first()
    segs = sorted(win.segments, key=lambda s: s.seq) if win else []
    seg_lines = "\n".join(f"[{s.seq}] ({s.start_ms//1000}s-{s.end_ms//1000}s) {s.text}" for s in segs)
    captions = [k.caption for k in db.query(Keyframe).filter_by(material_id=material.id, deleted=False)
                if k.caption]
    user_msg = f"整段转写：\n{full.segments[0].text}\n\n分段转写（带编号）：\n{seg_lines or '（无）'}"
    if captions:
        user_msg += "\n\n关键画面描述：\n" + "\n".join(f"- {c}" for c in captions)
    result = ai.llm_json(FACT_SYSTEM, user_msg)
    for item in result.get("facts", []):
        content = (item.get("content") or "").strip()
        if not content:
            continue
        fact = Fact(inspection_id=material.inspection_id, content=content, status="candidate", source="ai")
        db.add(fact)
        db.flush()
        for sid in item.get("segment_ids", []):
            seg = next((s for s in segs if s.seq == sid), None)
            if seg:
                db.add(FactEvidence(fact_id=fact.id, evidence_type="video_segment",
                                    material_id=material.id,
                                    ref={"start_ms": seg.start_ms, "end_ms": seg.end_ms,
                                         "segment_id": seg.id}))
    db.flush()
