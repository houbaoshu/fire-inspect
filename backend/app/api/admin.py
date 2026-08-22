"""管理：用户、审计日志、环境初始化/重置（PRD §16）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.models import User, AuditLog
from ..core.security import get_current_user, require_admin, hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "inspector"


@router.get("/users")
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return [{"id": u.id, "username": u.username, "role": u.role} for u in db.query(User).order_by(User.id)]


@router.post("/users")
def create_user(body: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if body.role not in ("inspector", "admin"):
        raise HTTPException(400, "角色须为 inspector / admin")
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(400, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    return {"id": u.id, "username": u.username, "role": u.role}


@router.delete("/users/{uid}")
def delete_user(uid: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    u = db.get(User, uid)
    if not u or u.id == admin.id:
        raise HTTPException(400, "不能删除自己或不存在的用户")
    db.delete(u)
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_logs(object_type: str = "", object_id: int = 0, limit: int = 100,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(AuditLog)
    if object_type:
        q = q.filter_by(object_type=object_type)
    if object_id:
        q = q.filter_by(object_id=object_id)
    rows = q.order_by(AuditLog.at.desc()).limit(min(limit, 500)).all()
    users = {u.id: u.username for u in db.query(User)}
    return [{"id": r.id, "at": r.at.isoformat(), "actor": users.get(r.actor_id, "?"),
             "action": r.action, "object_type": r.object_type, "object_id": r.object_id,
             "detail": r.detail} for r in rows]
