from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, model_dict
from app.core.database import get_db
from app.models import Asset, Job, User

router = APIRouter(prefix="/jobs", tags=["后台任务"])


@router.get("/{job_id}")
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.target_type == "asset":
        asset = db.get(Asset, job.target_id)
        if not asset or (user.role != "admin" and asset.uploaded_by != user.id):
            raise HTTPException(404, "任务不存在")
    elif job.target_type == "knowledge_document" and user.role != "admin":
        raise HTTPException(404, "任务不存在")
    return model_dict(
        job,
        "id",
        "kind",
        "target_type",
        "target_id",
        "status",
        "progress",
        "message",
        "error",
        "created_at",
        "updated_at",
    )
