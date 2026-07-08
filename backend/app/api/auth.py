"""认证 API：登录发令牌 / 当前身份 / 登出。

air-gap 仅用标准库自签令牌（app/auth.py），口令经 PBKDF2 校验。/login 开放（供未认证
用户登录）；/me、/logout 需有效令牌。令牌无状态——登出仅由前端清除本地令牌。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import issue_token, verify_password
from app.db import get_db
from app.dependencies import VALID_ROLES, Identity, get_current_user
from app.models.ontology_meta import AppUser

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    display_name: str | None = None


class MeResponse(BaseModel):
    username: str
    role: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """校验口令并签发 12h 令牌。失败统一 401（不区分用户名/口令，防账号枚举）。"""
    user = db.query(AppUser).filter_by(username=body.username).first()
    role_name = user.role.name if user and user.role else None
    if (
        user is None
        or not user.is_active
        or not verify_password(body.password, user.password_hash)
        or role_name not in VALID_ROLES
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_token(user.username, role_name)
    return LoginResponse(
        token=token,
        username=user.username,
        role=role_name,
        display_name=user.display_name,
    )


@router.get("/me", response_model=MeResponse)
def me(identity: Identity = Depends(get_current_user)):
    return MeResponse(username=identity.username, role=identity.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(_: Identity = Depends(get_current_user)) -> Response:
    # 无状态令牌：服务端无需注销，前端清除本地令牌即可。
    return Response(status_code=status.HTTP_204_NO_CONTENT)
