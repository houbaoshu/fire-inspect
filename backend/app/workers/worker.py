"""进程内任务执行器（PLAN §1.1.5）：DB 任务表 + asyncio worker。

- 轮询 pending 任务，信号量限并发；
- 崩溃恢复：启动时把超时 running 任务重置为 pending；
- 失败记录 error，retry 由 API 触发（重置为 pending）。
"""
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from ..core.db import SessionLocal
from ..core.models import Job, Material
from ..core import ai
from ..domain import pipeline, knowledge

JOB_HANDLERS = {}


def handler(kind: str):
    def deco(fn):
        JOB_HANDLERS[kind] = fn
        return fn
    return deco


@handler("transcribe_full")
def _tf(db: Session, job: Job):
    pipeline.run_transcribe_full(db, db.get(Material, job.material_id))


@handler("transcribe_windows")
def _tw(db: Session, job: Job):
    pipeline.run_transcribe_windows(db, db.get(Material, job.material_id))


@handler("keyframes")
def _kf(db: Session, job: Job):
    pipeline.run_keyframes(db, db.get(Material, job.material_id))


@handler("fact_extract")
def _fe(db: Session, job: Job):
    pipeline.run_fact_extract(db, db.get(Material, job.material_id))


@handler("knowledge_ingest")
def _ki(db: Session, job: Job):
    knowledge.ingest_doc(db, job.payload["doc_id"])


RUNNING_TIMEOUT = timedelta(minutes=30)


def recover_stale_jobs():
    """崩溃恢复：running 超时 → pending。"""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - RUNNING_TIMEOUT
        stale = db.query(Job).filter(Job.status == "running",
                                     Job.started_at < cutoff).all()
        for j in stale:
            j.status = "pending"
            j.error = (j.error or "") + "（执行中断，自动重置待重试）"
        db.commit()
    finally:
        db.close()


def enqueue(db: Session, inspection_id: int, kind: str, material_id: int | None = None,
            payload: dict | None = None) -> Job:
    j = Job(inspection_id=inspection_id, material_id=material_id, kind=kind, payload=payload or {})
    db.add(j)
    db.flush()
    return j


def enqueue_media_pipeline(db: Session, material: Material):
    """材料上传后的标准任务链。视频/音频：双跑转写；视频：关键帧。"""
    if material.type in ("video", "audio"):
        enqueue(db, material.inspection_id, "transcribe_full", material.id)
        enqueue(db, material.inspection_id, "transcribe_windows", material.id)
    if material.type == "video":
        enqueue(db, material.inspection_id, "keyframes", material.id)


def maybe_enqueue_fact_extract(db: Session, material_id: int):
    """整段转写成功后触发事实提取（避免重复：已有成功/排队任务则跳过）。"""
    material = db.get(Material, material_id)
    if not material:
        return
    exists = db.query(Job).filter(Job.material_id == material_id, Job.kind == "fact_extract",
                                  Job.status.in_(["pending", "running", "succeeded"])).first()
    if not exists:
        enqueue(db, material.inspection_id, "fact_extract", material_id)


def _run_job(job_id: int):
    db = SessionLocal()
    job = db.get(Job, job_id)
    try:
        JOB_HANDLERS[job.kind](db, job)
        db.commit()
        db.query(Job).filter(Job.id == job_id).update(
            {"status": "succeeded", "finished_at": datetime.now(timezone.utc), "error": ""})
        db.commit()
        if job.kind == "transcribe_full" and job.material_id:
            maybe_enqueue_fact_extract(db, job.material_id)
            db.commit()
    except (ai.AIError, RuntimeError, Exception) as e:  # noqa: BLE001 — 失败原因须展示（PRD §4）
        db.rollback()
        db.query(Job).filter(Job.id == job_id).update(
            {"status": "failed", "error": str(e)[:1000], "finished_at": datetime.now(timezone.utc)})
        db.commit()
    finally:
        db.close()


class Worker:
    def __init__(self, concurrency: int = 2, poll_sec: float = 1.0):
        self.sem = asyncio.Semaphore(concurrency)
        self.poll_sec = poll_sec
        self._stop = asyncio.Event()

    async def loop(self):
        recover_stale_jobs()
        while not self._stop.is_set():
            db = SessionLocal()
            try:
                job = (db.query(Job).filter(Job.status == "pending")
                       .order_by(Job.id).with_for_update(skip_locked=True).first())
                if job:
                    db.query(Job).filter(Job.id == job.id).update(
                        {"status": "running", "started_at": datetime.now(timezone.utc)})
                    db.commit()
                    asyncio.create_task(self._guarded(job.id))
                else:
                    db.commit()
            finally:
                db.close()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_sec)
            except asyncio.TimeoutError:
                pass

    async def _guarded(self, job_id: int):
        async with self.sem:
            await asyncio.to_thread(_run_job, job_id)

    def stop(self):
        self._stop.set()
