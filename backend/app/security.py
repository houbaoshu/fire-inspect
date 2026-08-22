import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import AuthSession, Inspection, InspectionMember, User, UserRole

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db: Session, user: User) -> tuple[str, str, AuthSession]:
    settings = get_settings()
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_hours),
    )
    db.add(auth_session)
    db.commit()
    return token, csrf, auth_session


def current_user(
    session_token: str | None = Cookie(default=None, alias=get_settings().session_cookie),
    db: Session = Depends(get_db),
) -> User:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_digest(session_token))
    )
    expires_at = auth_session.expires_at if auth_session else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not auth_session or expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话已过期")
    user = db.get(User, auth_session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不可用")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def accessible_inspections(user: User):
    if user.role == UserRole.ADMIN:
        return select(Inspection)
    return (
        select(Inspection)
        .outerjoin(InspectionMember, InspectionMember.inspection_id == Inspection.id)
        .where(or_(Inspection.owner_id == user.id, InspectionMember.user_id == user.id))
        .distinct()
    )


def require_inspection(db: Session, user: User, inspection_id: uuid.UUID) -> Inspection:
    statement = accessible_inspections(user).where(Inspection.id == inspection_id)
    inspection = db.scalar(statement)
    if not inspection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "检查不存在或无权访问")
    return inspection


def verify_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path.endswith("/auth/login"):
        return
    settings = get_settings()
    if not request.cookies.get(settings.session_cookie):
        return
    cookie = request.cookies.get(settings.csrf_cookie)
    header = request.headers.get("x-csrf-token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF 校验失败")
