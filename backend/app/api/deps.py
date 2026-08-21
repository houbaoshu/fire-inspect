from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import hash_session_token
from app.models import Inspection, User, UserSession


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    user = db.get(User, session.user_id) if session else None
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def accessible_inspection(db: Session, user: User, inspection_id: str) -> Inspection:
    inspection = db.get(Inspection, inspection_id)
    if not inspection or (user.role != "admin" and inspection.owner_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "检查不存在")
    return inspection


def model_dict(model, *fields: str) -> dict:
    return {field: getattr(model, field) for field in fields}
