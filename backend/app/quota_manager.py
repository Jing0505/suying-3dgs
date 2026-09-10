"""
每用户每日上传配额 + 上传凭证接口限流。
配额落盘 storage/quotas/{user_id}_{date}.json。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Deque

from fastapi import HTTPException

from .config import settings


@dataclass
class DailyQuota:
    user_id: str
    date: str
    task_count: int = 0
    upload_bytes: int = 0


class QuotaManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._policy_hits: dict[str, Deque[float]] = defaultdict(deque)
        self._dir = settings.LOCAL_STORAGE_DIR / "quotas"
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _path(self, user_id: str, day: str) -> "os.PathLike[str]":
        return self._dir / f"{user_id}_{day}.json"

    def _load(self, user_id: str) -> DailyQuota:
        day = self._today()
        path = self._path(user_id, day)
        if not path.exists():  # type: ignore[union-attr]
            return DailyQuota(user_id=user_id, date=day)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            return DailyQuota(
                user_id=user_id,
                date=day,
                task_count=int(data.get("task_count", 0)),
                upload_bytes=int(data.get("upload_bytes", 0)),
            )
        except Exception:
            return DailyQuota(user_id=user_id, date=day)

    def _save(self, quota: DailyQuota) -> None:
        path = self._path(quota.user_id, quota.date)
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(quota), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def get_quota(self, user_id: str) -> DailyQuota:
        with self._lock:
            return self._load(user_id)

    def check_policy_rate(self, user_id: str) -> None:
        """上传凭证接口：每用户每分钟次数上限。"""
        now = time.time()
        window = 60.0
        limit = settings.POLICY_RATE_LIMIT_PER_MIN
        with self._lock:
            hits = self._policy_hits[user_id]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"获取上传凭证过于频繁，每分钟最多 {limit} 次",
                )
            hits.append(now)

    def assert_can_upload(self, user_id: str, file_count: int, total_bytes: int) -> None:
        """签发凭证 / 创建任务前校验配额。"""
        if file_count <= 0:
            raise HTTPException(status_code=400, detail="未检测到有效图片")
        if file_count > settings.MAX_IMAGES_PER_TASK:
            raise HTTPException(
                status_code=400,
                detail=f"单任务最多 {settings.MAX_IMAGES_PER_TASK} 张图片",
            )
        if total_bytes > settings.max_file_size_bytes * file_count:
            raise HTTPException(status_code=400, detail="文件总大小超出上限")
        with self._lock:
            quota = self._load(user_id)
            if quota.task_count >= settings.DAILY_TASK_LIMIT:
                raise HTTPException(
                    status_code=429,
                    detail=f"今日任务数已达上限（{settings.DAILY_TASK_LIMIT}）",
                )
            if quota.upload_bytes + total_bytes > settings.DAILY_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=429,
                    detail="今日上传流量已达上限",
                )

    def record_task(self, user_id: str, upload_bytes: int) -> DailyQuota:
        with self._lock:
            quota = self._load(user_id)
            quota.task_count += 1
            quota.upload_bytes += max(0, upload_bytes)
            self._save(quota)
            return quota


_quota: QuotaManager | None = None


def get_quota_manager() -> QuotaManager:
    global _quota
    if _quota is None:
        _quota = QuotaManager()
    return _quota
