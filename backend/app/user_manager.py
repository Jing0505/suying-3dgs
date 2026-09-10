"""
用户存储：JSON 文件，不引入数据库。

账号只能由管理员开号（无注册接口）。启动时可用环境变量播种管理员，
已存在同名用户则不覆盖密码。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from passlib.context import CryptContext

from .config import settings

logger = logging.getLogger("user_manager")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    role: str = "user"  # admin | user
    created_at: float = field(default_factory=time.time)
    disabled: bool = False

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at,
            "disabled": self.disabled,
        }


class UserManager:
    """线程安全的 JSON 用户库。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._path = settings.LOCAL_STORAGE_DIR / "users.json"
        self._load()
        self.seed_admin()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw.get("users", []):
                user = User(
                    id=item["id"],
                    username=item["username"],
                    password_hash=item["password_hash"],
                    role=item.get("role", "user"),
                    created_at=item.get("created_at", time.time()),
                    disabled=bool(item.get("disabled", False)),
                )
                self._users[user.id] = user
        except Exception as exc:
            logger.warning("加载 users.json 失败: %s", exc)

    def _persist(self) -> None:
        payload = {
            "users": [asdict(u) for u in self._users.values()],
            "updated_at": time.time(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def seed_admin(self) -> None:
        """用 ADMIN_USERNAME / ADMIN_PASSWORD 播种管理员；已存在则跳过。"""
        username = (settings.ADMIN_USERNAME or "").strip()
        password = settings.ADMIN_PASSWORD or ""
        if not username or not password:
            if not self._users:
                logger.warning(
                    "未配置 ADMIN_PASSWORD，且尚无用户。请设置环境变量后重启以播种管理员。"
                )
            return
        existing = self.get_by_username(username)
        if existing:
            return
        with self._lock:
            if self.get_by_username(username):
                return
            user = User(
                id=uuid.uuid4().hex[:12],
                username=username,
                password_hash=pwd_context.hash(password),
                role="admin",
            )
            self._users[user.id] = user
            self._persist()
            logger.info("已播种管理员账号: %s", username)

    def get(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def get_by_username(self, username: str) -> Optional[User]:
        name = username.strip().lower()
        for user in self._users.values():
            if user.username.lower() == name:
                return user
        return None

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get_by_username(username)
        if user is None or user.disabled:
            return None
        try:
            if not pwd_context.verify(password, user.password_hash):
                return None
        except Exception:
            return None
        return user

    def create_user(self, username: str, password: str, role: str = "user") -> User:
        username = username.strip()
        if not username or len(username) < 2:
            raise ValueError("用户名至少 2 个字符")
        if len(username) > 32:
            raise ValueError("用户名最多 32 个字符")
        if not password or len(password) < 6:
            raise ValueError("密码至少 6 个字符")
        if role not in ("admin", "user"):
            raise ValueError("角色只能是 admin 或 user")
        password_hash = pwd_context.hash(password)
        with self._lock:
            if self.get_by_username(username):
                raise ValueError("用户名已存在")
            user = User(
                id=uuid.uuid4().hex[:12],
                username=username,
                password_hash=password_hash,
                role=role,
            )
            self._users[user.id] = user
            self._persist()
            return user

    def set_disabled(self, user_id: str, disabled: bool) -> Optional[User]:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            user.disabled = disabled
            self._persist()
            return user

    def list_users(self) -> list[User]:
        return sorted(self._users.values(), key=lambda u: u.created_at)


_manager: Optional[UserManager] = None


def get_user_manager() -> UserManager:
    global _manager
    if _manager is None:
        _manager = UserManager()
    return _manager
