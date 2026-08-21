from app.core.database import SessionLocal
from app.models import Job
from app.services.knowledge import index_document
from app.services.media import process_asset


def run_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = "running"
        job.progress = 10
        db.commit()
        try:
            if job.kind == "process_asset":
                message = process_asset(db, job.target_id)
            elif job.kind == "index_knowledge":
                index_document(db, job.target_id)
                message = "知识文档索引完成"
            else:
                raise ValueError(f"不支持的任务类型：{job.kind}")
            job = db.get(Job, job_id)
            job.status = "completed"
            job.progress = 100
            job.message = message
            job.error = None
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            job.status = "failed"
            job.error = str(exc)
            db.commit()
