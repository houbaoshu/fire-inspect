from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job


def create_job(db: Session, *, kind: str, target_type: str, target_id: str) -> Job:
    job = Job(kind=kind, target_type=target_type, target_id=target_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    if get_settings().task_mode == "rq":
        from redis import Redis
        from rq import Queue

        Queue(connection=Redis.from_url(get_settings().redis_url)).enqueue(
            "app.workers.tasks.run_job", job.id, job_timeout="30m"
        )
    else:
        from app.workers.tasks import run_job

        run_job(job.id)
        db.refresh(job)
    return job
