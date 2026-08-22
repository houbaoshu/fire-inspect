from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.models import User
from ..core.security import verify_password, create_session, destroy_session, get_current_user, COOKIE

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    create_session(db, user, response)
    return {"id": user.id, "username": user.username, "role": user.role}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        destroy_session(db, token, response)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}
