"""
阿里云 OSS 管理器：签发前端直传 Post Policy、校验公开 URL、服务端上传 PLY，
以及 3090 worker 使用的私有任务队列（pending / claimed / status）。

公开访问 URL 格式：
  https://<bucket>.<endpoint>/<object_name>
  示例：https://suying-3dgs.oss-cn-shenzhen.aliyuncs.com/images/{user}/{recon}/001.jpg
  示例：https://suying-3dgs.oss-cn-shenzhen.aliyuncs.com/output/recon_x/recon_x.ply

前提：Bucket 读写权限已设为「公共读」；写权限仅通过后端签发的短期 Post Policy。
队列对象强制 private ACL，不走公共读。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .config import settings

logger = logging.getLogger("oss_manager")

# 仅在配置了 OSS 凭据时才导入 oss2，避免无 OSS 环境装依赖
try:
    import oss2  # type: ignore
    _OSS_AVAILABLE = True
except ImportError:
    _OSS_AVAILABLE = False


class OSSManager:
    """封装阿里云 OSS 的上传操作。"""

    def __init__(self) -> None:
        self._bucket: Optional["oss2.Bucket"] = None

    def _get_bucket(self) -> "oss2.Bucket":
        if self._bucket is None:
            auth = oss2.Auth(
                settings.OSS_ACCESS_KEY_ID,
                settings.OSS_ACCESS_KEY_SECRET,
            )
            self._bucket = oss2.Bucket(
                auth,
                settings.OSS_ENDPOINT,
                settings.OSS_BUCKET,
            )
        return self._bucket

    def upload_file(self, local_path: Path, object_name: str) -> str:
        """
        上传本地文件到 OSS，返回公开访问 URL。

        :param local_path: 本地文件路径
        :param object_name: OSS 对象名（不含 bucket），如 "images/recon_x/001.jpg"
        :return: 公开访问 URL
        """
        bucket = self._get_bucket()
        logger.info(
            "[OSS] 上传 %s -> %s/%s",
            local_path.name,
            settings.OSS_BUCKET,
            object_name,
        )
        result = bucket.put_object_from_file(object_name, str(local_path))
        if result.status != 200:
            raise RuntimeError(
                f"OSS 上传失败（HTTP {result.status}）: {result}"
            )
        url = self.build_url(object_name)
        logger.info("[OSS] 上传成功: %s", url)
        return url

    def upload_image(self, local_path: Path, object_name: str) -> str:
        """上传图片到 OSS，返回公开访问 URL（语义别名，内部同 upload_file）。"""
        return self.upload_file(local_path, object_name)

    def upload_ply(self, local_path: Path, object_name: str) -> str:
        """
        上传本地 PLY 文件到 OSS，返回公开访问 URL。

        :param local_path: 本地 PLY 文件路径
        :param object_name: OSS 对象名（不含 bucket），如 "abc123.ply"
        :return: 公开访问 URL
        """
        return self.upload_file(local_path, object_name)

    @staticmethod
    def build_url(object_name: str) -> str:
        """根据对象名生成公开访问 URL。

        endpoint 可能带协议头（如 https://oss-cn-shenzhen.aliyuncs.com），
        拼接前先剥掉协议头，避免生成 bucket.https://host 的坏 URL。
        """
        endpoint = settings.OSS_ENDPOINT
        if "://" in endpoint:
            endpoint = endpoint.split("://", 1)[1]
        return (
            f"https://{settings.OSS_BUCKET}"
            f".{endpoint}"
            f"/{object_name}"
        )

    def is_enabled(self) -> bool:
        """是否已配置可用的 OSS 凭据且 oss2 库已安装。"""
        return _OSS_AVAILABLE and settings.oss_enabled

    @staticmethod
    def endpoint_host() -> str:
        endpoint = settings.OSS_ENDPOINT
        if "://" in endpoint:
            endpoint = endpoint.split("://", 1)[1]
        return endpoint.rstrip("/")

    def public_host(self) -> str:
        return f"https://{settings.OSS_BUCKET}.{self.endpoint_host()}"

    def image_key_prefix(self, user_id: str, reconstruction_id: str) -> str:
        return (
            f"{settings.OSS_IMAGE_PREFIX.rstrip('/')}"
            f"/{user_id}/{reconstruction_id}/"
        )

    def generate_post_policy(self, user_id: str, reconstruction_id: str) -> dict:
        """
        签发阿里云 OSS PostObject 凭证（短期、前缀锁定、大小限制）。
        前端用返回的 policy/signature 直传，AK/SK 不离开服务器。
        """
        if not settings.oss_enabled:
            raise RuntimeError("OSS 未配置")
        expire_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.OSS_POLICY_EXPIRE_SECONDS
        )
        key_prefix = self.image_key_prefix(user_id, reconstruction_id)
        max_bytes = settings.max_file_size_bytes
        policy_doc = {
            "expiration": expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "conditions": [
                {"bucket": settings.OSS_BUCKET},
                ["starts-with", "$key", key_prefix],
                ["content-length-range", 1, max_bytes],
                [
                    "in",
                    "$content-type",
                    [
                        "image/jpeg",
                        "image/png",
                        "image/bmp",
                        "image/tiff",
                        "image/x-tiff",
                    ],
                ],
            ],
        }
        policy_b64 = base64.b64encode(
            json.dumps(policy_doc).encode("utf-8")
        ).decode("utf-8")
        signature = base64.b64encode(
            hmac.new(
                settings.OSS_ACCESS_KEY_SECRET.encode("utf-8"),
                policy_b64.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        return {
            "host": self.public_host(),
            "bucket": settings.OSS_BUCKET,
            "dir": key_prefix,
            "expire": int(expire_at.timestamp()),
            "access_key_id": settings.OSS_ACCESS_KEY_ID,
            "policy": policy_b64,
            "signature": signature,
            "max_file_size": max_bytes,
        }

    def parse_object_name(self, url: str) -> Optional[str]:
        """从本 Bucket 公开 URL 解析 object_name；外站或不匹配则返回 None。"""
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        expected_host = f"{settings.OSS_BUCKET}.{self.endpoint_host()}".lower()
        if parsed.scheme != "https":
            return None
        if parsed.netloc.lower() != expected_host:
            return None
        object_name = parsed.path.lstrip("/")
        if not object_name or ".." in object_name:
            return None
        return object_name

    def validate_image_url(
        self, url: str, user_id: str, reconstruction_id: str
    ) -> str:
        """
        校验 URL 属于本 Bucket 且前缀属于当前用户/重建编号。
        成功返回 object_name，失败抛 ValueError。
        """
        object_name = self.parse_object_name(url)
        if not object_name:
            raise ValueError("不是本 Bucket 的合法 OSS 地址")
        prefix = self.image_key_prefix(user_id, reconstruction_id)
        if not object_name.startswith(prefix):
            raise ValueError("OSS 对象前缀不属于当前用户或任务")
        filename = object_name[len(prefix) :]
        if "/" in filename or not filename:
            raise ValueError("非法对象名")
        lower = filename.lower()
        if not lower.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")):
            raise ValueError("仅支持图片文件")
        return object_name

    # ----- 私有任务队列（3090 从 OSS 领取，不必连本机）-----

    def queue_root(self) -> str:
        return settings.OSS_QUEUE_PREFIX.strip().strip("/") or "queue"

    def pending_key(self, task_id: str) -> str:
        return f"{self.queue_root()}/pending/{task_id}.json"

    def claimed_key(self, task_id: str) -> str:
        return f"{self.queue_root()}/claimed/{task_id}.json"

    def status_key(self, task_id: str) -> str:
        return f"{self.queue_root()}/status/{task_id}.json"

    def put_queue_json(self, key: str, payload: dict[str, Any], *,
                       overwrite: bool = True) -> bool:
        """写入 JSON 队列对象（private ACL）。overwrite=False 时已存在返回 False。"""
        bucket = self._get_bucket()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "x-oss-object-acl": "private",
            "Content-Type": "application/json",
        }
        if not overwrite:
            headers["x-oss-forbid-overwrite"] = "true"
        try:
            result = bucket.put_object(key, body, headers=headers)
            if result.status not in (200, 201):
                raise RuntimeError(f"OSS 写入失败 HTTP {result.status}: {key}")
            return True
        except Exception as exc:
            status = getattr(exc, "status", None)
            if not overwrite and status in (409, 412):
                return False
            raise

    def get_queue_json(self, key: str) -> Optional[dict[str, Any]]:
        bucket = self._get_bucket()
        try:
            result = bucket.get_object(key)
            raw = result.read()
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status in (404, 204):
                return None
            code = str(getattr(exc, "code", "") or "")
            if "NoSuchKey" in code or "NotFound" in type(exc).__name__:
                return None
            raise
        if not raw:
            return None
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None

    def delete_queue_key(self, key: str) -> None:
        try:
            self._get_bucket().delete_object(key)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status in (404, 204):
                return
            code = str(getattr(exc, "code", "") or "")
            if "NoSuchKey" in code:
                return
            raise

    def queue_object_exists(self, key: str) -> bool:
        try:
            return bool(self._get_bucket().object_exists(key))
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status in (404, 204):
                return False
            raise

    def list_queue_keys(self, subdir: str) -> list[str]:
        """列出 queue/<subdir> 下的对象 key，按 Last-Modified 升序（FIFO）。"""
        if not _OSS_AVAILABLE:
            return []
        import oss2  # type: ignore

        prefix = f"{self.queue_root()}/{subdir.strip('/')}/"
        bucket = self._get_bucket()
        items: list[tuple[float, str]] = []
        for obj in oss2.ObjectIterator(bucket, prefix=prefix):
            key = getattr(obj, "key", "") or ""
            if not key.endswith(".json") or key.endswith("/"):
                continue
            lm = getattr(obj, "last_modified", None)
            if hasattr(lm, "timestamp"):
                ts = float(lm.timestamp())
            else:
                try:
                    ts = float(lm)
                except (TypeError, ValueError):
                    ts = time.time()
            items.append((ts, key))
        items.sort(key=lambda x: (x[0], x[1]))
        return [k for _, k in items]

    def task_id_from_queue_key(self, key: str) -> str:
        name = key.rsplit("/", 1)[-1]
        return name[:-5] if name.endswith(".json") else name

    def claim_next(self, worker_id: str) -> Optional[dict[str, Any]]:
        """
        从 pending/ 原子领取最早的任务：PUT claimed（禁止覆盖）成功则删除 pending。
        """
        now = time.time()
        for key in self.list_queue_keys("pending"):
            task_id = self.task_id_from_queue_key(key)
            if not task_id:
                continue
            data = self.get_queue_json(key)
            if not data:
                continue
            data["claimed_by"] = worker_id
            data["claimed_at"] = now
            data["started_at"] = data.get("started_at") or now
            data["heartbeat_at"] = now
            data["attempts"] = int(data.get("attempts") or 0) + 1
            data["status"] = "uploading"
            data["progress"] = 0
            data["current_step"] = f"i18n:step.workerClaimed|{worker_id}"
            logs = list(data.get("log_lines") or [])
            logs.append(f"[worker:{worker_id}] 已领取任务，开始拉取图片")
            data["log_lines"] = logs[-200:]
            claimed_key = self.claimed_key(task_id)
            if not self.put_queue_json(claimed_key, data, overwrite=False):
                continue
            try:
                self.delete_queue_key(key)
            except Exception:
                logger.warning("[OSS queue] 删除 pending 失败: %s", key)
            try:
                self.put_queue_json(self.status_key(task_id), data, overwrite=True)
            except Exception:
                logger.warning("[OSS queue] 写入 status 失败: %s", task_id)
            logger.info("[OSS queue] worker=%s 领取任务 %s", worker_id, task_id)
            return data
        return None


# 全局单例
_oss_manager = OSSManager()


def get_oss_manager() -> OSSManager:
    return _oss_manager
