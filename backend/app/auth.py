"""
JWT 鉴权：登录签发 Bearer Token，依赖注入当前用户 / 管理员。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .user_manager import User, get_user_manager

logger = logging.getLogger("auth")

_bearer = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


def _jwt_secret() -> str:
    if settings.JWT_SECRET:
        return settings.JWT_SECRET
    # 未配置时用随机密钥（重启后已签发 token 失效），仅建议本地调试
    secret = getattr(_jwt_secret, "_fallback", None)
    if not secret:
        secret = secrets.token_hex(32)
        _jwt_secret._fallback = secret  # type: ignore[attr-defined]
        logger.warning("JWT_SECRET 未配置，已使用临时密钥（重启后登录失效）")
    return secret


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def user_from_token(token: str) -> Optional[User]:
    payload = decode_token(token)
    if not payload:
        return None
    user = get_user_manager().get(str(payload.get("sub", "")))
    if user is None or user.disabled:
        return None
    return user


async def get_optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[User]:
    if creds is None or creds.scheme.lower() != "bearer":
        return None
    return user_from_token(creds.credentials)


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="请先登录")
    user = user_from_token(creds.credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def ws_current_user(websocket: WebSocket) -> Optional[User]:
    """WebSocket 从 query `token` 或 Authorization 头读取 JWT。"""
    token = websocket.query_params.get("token")
    if not token:
        auth = websocket.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    return user_from_token(token)
