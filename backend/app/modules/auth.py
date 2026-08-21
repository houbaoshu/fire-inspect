from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, model_dict, require_admin
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import hash_password, hash_session_token, new_session_token, verify_password
from app.models import User, UserSession

router = APIRouter(prefix="/auth", tags=["身份"])
users_router = APIRouter(prefix="/users", tags=["用户"])


class LoginInput(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: str = "inspector"


def serialize_user(user: User) -> dict:
    return model_dict(user, "id", "username", "role", "active", "created_at")


@router.post("/login")
def login(payload: LoginInput, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.active or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    token, token_hash, expires_at = new_session_token()
    db.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    db.commit()
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_lifetime_hours * 3600,
    )
    return serialize_user(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == hash_session_token(token)))
        db.commit()
    response.delete_cookie(get_settings().session_cookie_name)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return serialize_user(user)


@users_router.get("")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [serialize_user(user) for user in db.scalars(select(User).order_by(User.username)).all()]


@users_router.post("", status_code=201)
def create_user(
    payload: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)
):
    if payload.role not in {"admin", "inspector"}:
        raise HTTPException(422, "角色只能是 admin 或 inspector")
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "用户名已存在")
    user = User(
        username=payload.username, password_hash=hash_password(payload.password), role=payload.role
    )
    db.add(user)
    db.commit()
    return serialize_user(user)
