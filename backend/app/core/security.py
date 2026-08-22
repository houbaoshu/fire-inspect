"""认证与会话：会话 Cookie（HttpOnly）+ PBKDF2 密码哈希（stdlib，零额外依赖）。"""
import hashlib, hmac, os, secrets
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as OrmSession
from .db import get_db
from .models import User, Session
from .config import settings

COOKIE = "fi_session"
_PBKDF2_ROUNDS = 120_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_session(db: OrmSession, user: User, resp: Response):
    token = secrets.token_urlsafe(32)
    s = Session(token=token, user_id=user.id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours))
    db.add(s)
    db.commit()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=settings.session_ttl_hours * 3600)
    return token


def destroy_session(db: OrmSession, token: str, resp: Response):
    db.query(Session).filter(Session.token == token).delete()
    db.commit()
    resp.delete_cookie(COOKIE)


def get_current_user(request: Request, db: OrmSession = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "未登录")
    s = db.get(Session, token)
    if not s or s.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "会话已过期")
    user = db.get(User, s.user_id)
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
