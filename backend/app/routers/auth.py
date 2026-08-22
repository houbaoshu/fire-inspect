from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AuthSession, User
from ..schemas import LoginIn, UserCreate, UserOut
from ..security import (
    current_user,
    hash_password,
    new_session,
    require_admin,
    token_digest,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    token, csrf, _ = new_session(db, user)
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie,
        token,
        httponly=True,
        secure=settings.session_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    response.set_cookie(
        settings.csrf_cookie,
        csrf,
        httponly=False,
        secure=settings.session_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> None:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie)
    if token:
        session = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_digest(token))
        )
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(settings.session_cookie)
    response.delete_cookie(settings.csrf_cookie)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@users_router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@users_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    return user
