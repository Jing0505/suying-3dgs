#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
溯影 3DGS 重建 Worker —— 部署在 3090 GPU 服务器上的独立脚本
============================================================

职责（拉模式任务队列）：
  1. 每隔 POLL_INTERVAL 秒从阿里云 OSS 私有队列领取待重建任务
     （queue/pending → queue/claimed，FIFO；不必连接本机 HTTP）
  2. 领取到任务后，把图片拉到本地：
       - 优先从 OSS 公开直链下载（oss_images）
       - OSS 未配置时从服务器下载（/api/worker/tasks/{id}/images/...）
  3. 在本地执行 3DGS 重建流水线：convert.py -> train.py -> render.py -> metrics.py
  4. 重建完成后：
       - 上传结果（point_cloud.ply）到 OSS
       - 把完成/失败写回 queue/status（本机后端同步到前端）
  5. 释放算力：默认自动删除本地图片与模型文件（keep_files=false 时）
  6. 全程维护本地过程记录文件：
       - worker_status.json  （哪些任务 未开始/在跑/跑完/失败、进度、起止时间、报错）
       - worker.log          （运行日志）

OSS 队列模式需 pip install oss2，并在 worker_config.json 填写 oss 四字段。
未配置 OSS 时回退 POST /api/worker/claim（需能访问本机 server_url）。

    python3 worker.py                 # 常驻轮询（默认 30s 一次）
    python3 worker.py --once          # 只处理一个任务就退出
    python3 worker.py --interval 60   # 自定义轮询间隔
    python3 worker.py --config /path/to/config.json

配置文件：worker_config.json（与本脚本同目录），所有字段可用环境变量覆盖
（前缀 WORKER_，如 WORKER_SERVER_URL、WORKER_POLL_INTERVAL）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ==================== 默认配置 ====================

DEFAULT_CONFIG = {
    # 服务器地址（FastAPI 后端，worker 从这里领取任务）
    "server_url": "http://localhost:8000",
    # 与服务器 .env 中 WORKER_API_KEY 一致；留空则服务器端不做鉴权
    "api_key": "",
    # 本 worker 的标识（多个 3090 时可区分）
    "worker_id": "3090-1",
    # 轮询间隔（秒）
    "poll_interval": 30,
    # ---- 3090 本地 3DGS 项目路径 ----
    "project_root": "/home/omnispace/Projects/gaussian-splatting",
    "data_root": "/home/omnispace/Projects/gaussian-splatting/data",
    "output_root": "/home/omnispace/Projects/gaussian-splatting/output",
    "conda_env": "gaussian_splatting",
    # conda 环境内 python 的绝对路径；留空则自动探测（anaconda3/miniconda3 等）
    "python_bin": "",
    # convert.py 是否用 GPU（--no_gpu 的取反）
    "use_gpu": False,
    # 完成后是否保留本地图片与模型（false = 自动删除，释放磁盘）
    "keep_files": False,
    # 是否同时解析并上报训练迭代进度
    "report_iterations": True,
    # ---- 可选：worker 直传 OSS（需 pip install oss2；否则结果回传服务器）----
    "oss": {
        "access_key_id": "",
        "access_key_secret": "",
        "bucket": "",
        "endpoint": "",
        "queue_prefix": "queue",
    },
}

# 训练阶段 GPU 显存兜底采样间隔（秒）：除匹配 iter 行外，每隔该间隔采样一次，
# 避免 train.py 日志格式不匹配 iter 正则时完全采不到显存（峰值恒 0、前端不显示）。
GPU_SAMPLE_INTERVAL = 3.0
# 长时间 COLMAP 期间：心跳保活 + 日志节流，避免逐行 HTTP 把任务误杀成 timed out
HEARTBEAT_INTERVAL = 20.0
LOG_THROTTLE_SECONDS = 2.0
PROGRESS_RETRIES = 2
_URGENT_LOG_RE = re.compile(
    r"(?i)(%|error|exception|failed|registering|initializing|"
    r"finding good|elapsed time|bundle adjustment|iter\w*\s*\d)"
)

# ==================== 工具函数 ====================

_log_file = None


def log(msg: str, level: str = "INFO") -> None:
    """打印并写入 worker.log。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    if _log_file:
        try:
            _log_file.write(line + "\n")
            _log_file.flush()
        except Exception:
            pass


def load_config() -> dict:
    """加载 worker_config.json（若存在），再用环境变量覆盖。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg["oss"] = dict(DEFAULT_CONFIG["oss"])

    parser = argparse.ArgumentParser(description="溯影 3DGS 重建 Worker")
    parser.add_argument("--config", default="worker_config.json",
                        help="配置文件路径（默认同目录 worker_config.json）")
    parser.add_argument("--once", action="store_true",
                        help="只处理一个任务后退出")
    parser.add_argument("--interval", type=int, default=None,
                        help="轮询间隔秒数，覆盖配置")
    parser.add_argument("--max-idle", type=int, default=None,
                        help="连续多少次空轮询后仍继续（默认无限）")
    args, _ = parser.parse_known_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = Path(__file__).resolve().parent / cfg_path
    if cfg_path.exists():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            log(
                f"配置文件解析失败，已退出（请运行 python3 -m json.tool {cfg_path}）: {e}",
                "ERROR",
            )
            sys.exit(1)
        if not isinstance(user_cfg, dict):
            log(f"配置文件必须是 JSON 对象: {cfg_path}", "ERROR")
            sys.exit(1)
        for k, v in user_cfg.items():
            if k == "oss" and isinstance(v, dict):
                cfg["oss"].update(v)
            else:
                cfg[k] = v
        log(f"已加载配置: {cfg_path}")
    else:
        log(f"未找到配置文件 {cfg_path}，使用默认配置", "WARN")

    # 环境变量覆盖（WORKER_ 前缀，OSS 子项用 WORKER_OSS_ 前缀）
    env_map = {
        "server_url": "WORKER_SERVER_URL",
        "api_key": "WORKER_API_KEY",
        "worker_id": "WORKER_ID",
        "poll_interval": "WORKER_POLL_INTERVAL",
        "project_root": "WORKER_PROJECT_ROOT",
        "data_root": "WORKER_DATA_ROOT",
        "output_root": "WORKER_OUTPUT_ROOT",
        "conda_env": "WORKER_CONDA_ENV",
    }
    for key, env in env_map.items():
        val = os.getenv(env)
        if val is not None:
            cfg[key] = val
    if os.getenv("WORKER_USE_GPU") is not None:
        cfg["use_gpu"] = os.getenv("WORKER_USE_GPU", "").lower() == "true"
    if os.getenv("WORKER_KEEP_FILES") is not None:
        cfg["keep_files"] = os.getenv("WORKER_KEEP_FILES", "").lower() == "true"
    for k in ("access_key_id", "access_key_secret", "bucket", "endpoint",
              "queue_prefix"):
        env = f"WORKER_OSS_{k.upper()}"
        val = os.getenv(env)
        if val is not None:
            cfg["oss"][k] = val

    try:
        cfg["poll_interval"] = int(args.interval or cfg["poll_interval"])
    except (TypeError, ValueError):
        pass
    return cfg, args


# ==================== HTTP 封装（纯标准库） ====================


class ServerAPI:
    """封装对服务器的 worker 接口调用。"""

    def __init__(self, cfg: dict):
        self.base = str(cfg["server_url"]).rstrip("/")
        self.api_key = str(cfg.get("api_key", ""))
        self.worker_id = str(cfg.get("worker_id", "3090-1"))
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPHandler()
        )

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"User-Agent": f"suying-worker/{self.worker_id}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str,
                 data: bytes | None = None,
                 headers: dict | None = None,
                 timeout: int = 300,
                 retries: int = 0) -> dict:
        url = self.base + path
        attempts = 1 + max(0, retries)
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(
                url, data=data, method=method, headers=self._headers(headers)
            )
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    if not body.strip():
                        return {}
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                err = RuntimeError(
                    f"服务器请求失败 {method} {path} -> HTTP {e.code}: {detail}"
                )
                if e.code >= 500 and attempt < attempts:
                    log(f"{err}，第 {attempt}/{attempts} 次，即将重试", "WARN")
                    time.sleep(min(8.0, 2.0 * attempt))
                    last_err = err
                    continue
                raise err from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                last_err = e
                if attempt < attempts:
                    log(
                        f"请求 {method} {path} 第 {attempt}/{attempts} 次失败"
                        f"（{reason}），即将重试",
                        "WARN",
                    )
                    time.sleep(min(8.0, 2.0 * attempt))
                    continue
                raise RuntimeError(
                    f"无法连接服务器 {self.base} {method} {path}: {reason}"
                ) from e
        raise RuntimeError(
            f"无法连接服务器 {self.base} {method} {path}: {last_err}"
        )

    # ----- 业务接口 -----

    def claim(self) -> dict | None:
        body = urllib.parse.urlencode({"worker_id": self.worker_id}).encode()
        resp = self._request(
            "POST", "/api/worker/claim", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            retries=PROGRESS_RETRIES,
        )
        task = resp.get("task")
        return task if task else None

    @staticmethod
    def _form_body(data: dict) -> bytes:
        encoded: dict[str, str] = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            encoded[k] = str(v)
        return urllib.parse.urlencode(encoded).encode()

    def report_progress(self, task_id: str, **fields) -> None:
        """上报进度。网络超时只记日志，绝不把重建任务打成 failed。"""
        data = {"task_id": task_id, **fields}
        body = self._form_body(data)
        try:
            self._request(
                "POST", "/api/worker/progress", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
                retries=PROGRESS_RETRIES,
            )
        except Exception as exc:
            log(f"进度上报失败（不影响任务）: {exc}", "WARN")

    def report_complete(self, task_id: str, **fields) -> None:
        data = {"task_id": task_id, **fields}
        body = self._form_body(data)
        self._request(
            "POST", "/api/worker/complete", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            retries=PROGRESS_RETRIES,
        )

    def report_fail(self, task_id: str, error: str) -> None:
        body = self._form_body({"task_id": task_id, "error": error[:4000]})
        self._request(
            "POST", "/api/worker/fail", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            retries=PROGRESS_RETRIES,
        )

    def list_images(self, task_id: str) -> list[str]:
        resp = self._request("GET", f"/api/worker/tasks/{task_id}/images")
        return resp.get("images", [])

    def get_task(self, task_id: str) -> dict:
        """获取任务最新详情（认领后刷新 OSS 直链用）。"""
        return self._request("GET", f"/api/tasks/{task_id}")

    def download_image(self, task_id: str, filename: str,
                       dest: Path, timeout: int = 300) -> None:
        url = (f"{self.base}/api/worker/tasks/{task_id}/images/"
               f"{urllib.parse.quote(filename)}")
        req = urllib.request.Request(url, headers=self._headers())
        with self._opener.open(req, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)

    def upload_model(self, task_id: str, ply_path: Path,
                     timeout: int = 1800) -> dict:
        """multipart 回传 ply 模型到服务器（OSS 未配置时）。"""
        boundary = "----suying-worker-" + uuid_hex()
        chunks = []
        def add_field(name: str, value: str) -> None:
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )
        add_field("task_id", task_id)
        fname = ply_path.name
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
        )
        chunks.append(ply_path.read_bytes())
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        return self._request(
            "POST", "/api/worker/upload-model", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=timeout,
        )


def oss_configured(cfg: dict) -> bool:
    oss = cfg.get("oss") or {}
    return bool(
        oss.get("access_key_id")
        and oss.get("access_key_secret")
        and oss.get("bucket")
        and oss.get("endpoint")
    )


class OssQueueAPI:
    """通过 OSS 私有队列领取/上报，3090 不必访问本机 HTTP。"""

    STATUS_THROTTLE = 5.0

    def __init__(self, cfg: dict):
        try:
            import oss2  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "OSS 队列模式需要 oss2，请在 3090 上执行: pip install oss2"
            ) from exc
        oss_cfg = cfg.get("oss") or {}
        self.worker_id = str(cfg.get("worker_id", "3090-1"))
        self.queue_prefix = (
            str(oss_cfg.get("queue_prefix") or "queue").strip().strip("/")
            or "queue"
        )
        self._bucket = oss2.Bucket(
            oss2.Auth(oss_cfg["access_key_id"], oss_cfg["access_key_secret"]),
            str(oss_cfg["endpoint"]),
            oss_cfg["bucket"],
        )
        self._oss2 = oss2
        self._state: dict[str, dict] = {}
        self._last_write: dict[str, tuple[float, str]] = {}

    def _pending_key(self, task_id: str) -> str:
        return f"{self.queue_prefix}/pending/{task_id}.json"

    def _claimed_key(self, task_id: str) -> str:
        return f"{self.queue_prefix}/claimed/{task_id}.json"

    def _status_key(self, task_id: str) -> str:
        return f"{self.queue_prefix}/status/{task_id}.json"

    def _put_json(self, key: str, payload: dict, overwrite: bool = True) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "x-oss-object-acl": "private",
            "Content-Type": "application/json",
        }
        if not overwrite:
            headers["x-oss-forbid-overwrite"] = "true"
        try:
            self._bucket.put_object(key, body, headers=headers)
            return True
        except Exception as exc:
            status = getattr(exc, "status", None)
            if not overwrite and status in (409, 412):
                return False
            raise

    def _get_json(self, key: str) -> dict | None:
        try:
            raw = self._bucket.get_object(key).read()
        except Exception as exc:
            if getattr(exc, "status", None) in (404, 204):
                return None
            code = str(getattr(exc, "code", "") or "")
            if "NoSuchKey" in code or "NotFound" in type(exc).__name__:
                return None
            raise
        if not raw:
            return None
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None

    def _delete(self, key: str) -> None:
        try:
            self._bucket.delete_object(key)
        except Exception as exc:
            if getattr(exc, "status", None) in (404, 204):
                return
            code = str(getattr(exc, "code", "") or "")
            if "NoSuchKey" in code:
                return
            raise

    def _list_pending_keys(self) -> list[str]:
        prefix = f"{self.queue_prefix}/pending/"
        items: list[tuple[float, str]] = []
        for obj in self._oss2.ObjectIterator(self._bucket, prefix=prefix):
            key = getattr(obj, "key", "") or ""
            if not key.endswith(".json"):
                continue
            lm = getattr(obj, "last_modified", None)
            if hasattr(lm, "timestamp"):
                ts = float(lm.timestamp())
            else:
                try:
                    ts = float(lm)
                except (TypeError, ValueError):
                    ts = 0.0
            items.append((ts, key))
        items.sort(key=lambda x: (x[0], x[1]))
        return [k for _, k in items]

    def claim(self) -> dict | None:
        keys = self._list_pending_keys()
        n = len(keys)
        if n == 0:
            log("OSS pending 对象数=0", "WARN")
            return None
        log(f"OSS pending 对象数={n}")
        now = time.time()
        for key in keys:
            task_id = key.rsplit("/", 1)[-1]
            if task_id.endswith(".json"):
                task_id = task_id[:-5]
            data = self._get_json(key)
            if not data:
                continue
            data["claimed_by"] = self.worker_id
            data["claimed_at"] = now
            data["started_at"] = data.get("started_at") or now
            data["heartbeat_at"] = now
            data["attempts"] = int(data.get("attempts") or 0) + 1
            data["status"] = "uploading"
            data["progress"] = 0
            data["current_step"] = f"i18n:step.workerClaimed|{self.worker_id}"
            logs = list(data.get("log_lines") or [])
            logs.append(f"[worker:{self.worker_id}] 已领取任务，开始拉取图片")
            data["log_lines"] = logs[-200:]
            if not self._put_json(self._claimed_key(task_id), data, overwrite=False):
                continue
            try:
                self._delete(key)
            except Exception as exc:
                log(f"删除 pending 失败 {key}: {exc}", "WARN")
            self._state[task_id] = data
            try:
                self._put_json(self._status_key(task_id), data, overwrite=True)
            except Exception as exc:
                log(f"写入 status 失败 {task_id}: {exc}", "WARN")
            log(f"OSS 队列领取任务 {task_id}")
            return data
        return None

    def _merge_fields(self, task_id: str, fields: dict) -> dict:
        state = self._state.setdefault(task_id, {"task_id": task_id})
        for key, value in fields.items():
            if value is None:
                continue
            if key == "log_line":
                logs = list(state.get("log_lines") or [])
                logs.append(str(value)[:4000])
                state["log_lines"] = logs[-200:]
            elif key == "stage_timings" and isinstance(value, dict):
                merged = dict(state.get("stage_timings") or {})
                for stage, timing in value.items():
                    if isinstance(timing, dict):
                        merged.setdefault(stage, {}).update(timing)
                state["stage_timings"] = merged
            else:
                state[key] = value
        state["heartbeat_at"] = time.time()
        state["updated_at"] = time.time()
        return state

    def _write_status(self, task_id: str, force: bool = False) -> None:
        state = self._state.get(task_id)
        if not state:
            return
        now = time.time()
        status = str(state.get("status") or "")
        last = self._last_write.get(task_id)
        if not force and last:
            last_ts, last_status = last
            if status == last_status and (now - last_ts) < self.STATUS_THROTTLE:
                return
        try:
            self._put_json(self._status_key(task_id), state, overwrite=True)
            self._last_write[task_id] = (now, status)
        except Exception as exc:
            log(f"OSS 进度写入失败（不影响任务）: {exc}", "WARN")

    def report_progress(self, task_id: str, **fields) -> None:
        self._merge_fields(task_id, fields)
        try:
            self._write_status(task_id, force="status" in fields)
        except Exception as exc:
            log(f"进度上报失败（不影响任务）: {exc}", "WARN")

    def report_complete(self, task_id: str, **fields) -> None:
        self._merge_fields(task_id, {
            **fields,
            "status": "completed",
            "progress": 100,
            "current_step": "i18n:step.completed",
            "finished_at": time.time(),
            "log_line": "[worker] 重建完成，结果已就绪",
        })
        self._write_status(task_id, force=True)
        try:
            self._delete(self._claimed_key(task_id))
        except Exception as exc:
            log(f"删除 claimed 失败: {exc}", "WARN")

    def report_fail(self, task_id: str, error: str) -> None:
        self._merge_fields(task_id, {
            "status": "failed",
            "error": error[:4000],
            "finished_at": time.time(),
            "current_step": f"i18n:step.failed|{error[:200]}",
            "log_line": f"[ERROR] [worker] {error[:4000]}",
        })
        self._write_status(task_id, force=True)
        try:
            self._delete(self._claimed_key(task_id))
        except Exception as exc:
            log(f"删除 claimed 失败: {exc}", "WARN")

    def list_images(self, task_id: str) -> list[str]:
        task = self._state.get(task_id) or {}
        names: list[str] = []
        for url in task.get("oss_images") or []:
            names.append(str(url).rsplit("/", 1)[-1])
        return names

    def get_task(self, task_id: str) -> dict:
        data = self._get_json(self._status_key(task_id))
        if data:
            self._state[task_id] = data
            return data
        return self._state.get(task_id) or {}

    def download_image(self, task_id: str, filename: str,
                       dest: Path, timeout: int = 300) -> None:
        raise RuntimeError("OSS 队列模式请使用任务中的 oss_images 直链下载图片")

    def upload_model(self, task_id: str, ply_path: Path,
                     timeout: int = 1800) -> dict:
        raise RuntimeError(
            "OSS 队列模式必须用 oss 直传 ply，无法回传到本机 HTTP"
        )


def make_api(cfg: dict):
    if oss_configured(cfg):
        return OssQueueAPI(cfg)
    return ServerAPI(cfg)


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ==================== 本地过程记录 ====================

class LocalStatus:
    """本地过程记录文件 worker_status.json（与服务器三份记录文件呼应）。"""

    def __init__(self, path: Path, worker_id: str):
        self.path = path
        self.worker_id = worker_id
        self.data: dict = {"worker_id": worker_id, "updated_at": None, "tasks": []}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
                self.data.setdefault("tasks", [])
            except Exception:
                self.data = {"worker_id": worker_id, "updated_at": None, "tasks": []}
        # 清理不属于本 worker 的记录
        if self.data.get("worker_id") != worker_id:
            self.data = {"worker_id": worker_id, "updated_at": None, "tasks": []}

    def _find(self, task_id: str) -> dict | None:
        for t in self.data["tasks"]:
            if t.get("task_id") == task_id:
                return t
        return None

    def update(self, task_id: str, **fields) -> None:
        t = self._find(task_id)
        if t is None:
            t = {"task_id": task_id}
            self.data["tasks"].append(t)
        for k, v in fields.items():
            if v is not None:
                t[k] = v
        self._save()

    def _save(self) -> None:
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass


# ==================== 3DGS 流水线 ====================

STAGE_LABELS = {
    "upload": "阶段1 拉取图片",
    "convert": "阶段2 COLMAP 位姿估计",
    "train": "阶段3 3DGS 训练",
    "render": "阶段4 数据集渲染",
    "metrics": "阶段4.5 质量指标",
    "download": "阶段5 定位 PLY 模型",
    "oss_upload": "阶段6 上传结果",
}


def build_train_params_args(params: dict) -> str:
    """把训练参数字典转成 train.py 命令行参数（白名单过滤）。"""
    if not params:
        return ""
    allowed = {
        "iterations", "resolution", "sh_degree", "white_background", "eval",
        "densify_grad_threshold", "densification_interval",
        "opacity_reset_interval", "densify_from_iter", "densify_until_iter",
        "lambda_dssim", "percent_dense", "random_background", "antialiasing",
    }
    parts = []
    for key, value in params.items():
        if key not in allowed:
            continue
        if isinstance(value, bool):
            if value:
                parts.append(f"--{key}")
        else:
            parts.append(f"--{key} {value}")
    return (" " + " ".join(parts)) if parts else ""


def resolve_python(cfg: dict) -> str:
    """
    返回执行 convert/train/render/metrics 所用的 python 命令。
    优先级：
      1. 配置项 python_bin（绝对路径，最可靠）
      2. 自动探测 conda 环境（conda_env）中的 python 解释器
      3. 回退 conda run（需 conda 在 PATH 中）
      4. 最后回退系统 python
    背景：torch/COLMAP 相关依赖装在 conda 环境 gaussian_splatting 里，
    直接用系统 python 会 ModuleNotFoundError。
    """
    python_bin = str(cfg.get("python_bin") or "").strip()
    if python_bin:
        log(f"使用配置的 python: {python_bin}")
        return python_bin

    env_name = str(cfg.get("conda_env") or "").strip()
    if env_name:
        for base in ("~/anaconda3", "~/miniconda3", "~/miniforge3",
                     "~/mambaforge", "/opt/conda"):
            cand = Path(os.path.expanduser(base)) / "envs" / env_name / "bin" / "python"
            if cand.exists():
                log(f"探测到 conda 环境 python: {cand}")
                return str(cand)
        log(f"未探测到 {env_name} 环境，回退 conda run", "WARN")
        return f"conda run --no-capture-output -n {env_name} python"
    log("未配置 conda_env，使用系统 python", "WARN")
    return "python"


# 当前正在执行的子进程（用于收到终止信号时连带清理，避免训练进程变孤儿占端口）
_CURRENT_PROC = None


def _cleanup_children() -> None:
    """杀掉当前正在跑的命令进程组（含 sh -c 拉起的 train.py 等后代进程）。"""
    global _CURRENT_PROC
    proc = _CURRENT_PROC
    if proc is None or proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            # Linux：start_new_session 让子进程自成会话/进程组，pgid==pid，
            # killpg 可连带杀掉其全部后代（如 sh -c 拉起的 train.py），
            # 避免 kill worker 后 train.py 变孤儿继续占端口
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            # Windows 无 killpg，退而求其次只杀直接子进程
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError) as e:
        log(f"清理子进程失败（可忽略）: {e}", "WARN")


def _handle_terminate(signum, _frame) -> None:
    log(f"收到终止信号 {signum}，清理子进程后退出...", "WARN")
    _cleanup_children()
    sys.exit(0)


def find_free_port() -> int:
    """让 OS 分配一个当前空闲的 TCP 端口（bind 到 0 后由内核分配）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def train_supports_port(project_root: Path) -> bool:
    """判断 3090 上的 train.py 是否接受 --port 参数，避免盲目追加导致
    'unrecognized arguments' 报错。"""
    tp = Path(project_root) / "train.py"
    if not tp.exists():
        return False
    try:
        return "--port" in tp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def query_gpu_memory() -> dict | None:
    """查询 NVIDIA GPU 显存占用（单位 GB）。

    通过 nvidia-smi 读取 memory.used / memory.total（MiB）换算成 GB，
    不依赖第三方 Python 库（如 pynvml），3090 默认自带 nvidia-smi。
    无 GPU / nvidia-smi 缺失 / 解析失败时返回 None。
    """
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode("utf-8", "ignore").strip().splitlines()
        if not out:
            return None
        used, total = [float(x) for x in out[0].split(",")]
        used_gb = round(used / 1024.0, 2)
        total_gb = round(total / 1024.0, 2)
        return {
            "used_gb": used_gb,
            "total_gb": total_gb,
            "percent": round(used / total * 100, 1) if total else 0.0,
        }
    except Exception:
        return None


def _sample_gpu(gpu_state: dict | None) -> dict | None:
    """采样一次 GPU 显存：更新 gpu_state 峰值并返回本次快照（含峰值），失败返回 None。"""
    if gpu_state is None:
        return None
    gm = query_gpu_memory()
    if not gm:
        return None
    gpu_state["peak_gb"] = max(gpu_state.get("peak_gb", 0.0), gm["used_gb"])
    gpu_state["current"] = gm
    return {**gm, "peak_gb": round(gpu_state["peak_gb"], 2)}


def run_command(cmd: str, api: ServerAPI, task_id: str,
                status: str, step_label: str,
                stage: dict, report_iterations: bool = False,
                total_iters: int | None = None,
                gpu_state: dict | None = None) -> None:
    """
    执行一条远程/本地命令，逐行输出日志并上报服务器。
    stage: 本阶段耗时记录 {"start": ts}（执行完写 end/duration）

    headless 服务器修复：COLMAP 是 Qt 图形应用，在无显示器环境启动会因
    连不上 X display 直接段错误（SIGSEGV）。这里统一注入
    QT_QPA_PLATFORM=offscreen，让 Qt 走离屏渲染，convert/render 均受益。
    """
    log(f">>> {cmd}")
    # Windows 子进程（cmd/conda）默认输出 GBK，Linux 为 UTF-8；按平台显式解码避免乱码
    enc = "gbk" if sys.platform == "win32" else "utf-8"
    env = os.environ.copy()
    if sys.platform != "win32":
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, errors="replace", encoding=enc, env=env,
        start_new_session=True,
    )
    global _CURRENT_PROC
    _CURRENT_PROC = proc
    iter_re = re.compile(r"iter\w*[\s:]*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
    pct_re = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    # 训练阶段 GPU 显存采样：启动后台线程定时采样（不依赖 stdout 行读取）。
    # 关键：train.py 用 tqdm 进度条、以 \r 回车刷新同一行（不输出 \n），逐行读取
    # proc.stdout 在训练期间几乎读不到任何行，故必须在独立线程采样才能采到峰值。
    gpu_sampler_stop = None
    if status == "training" and gpu_state is not None:
        gpu_sampler_stop = threading.Event()

        def _gpu_sampler_loop() -> None:
            while not gpu_sampler_stop.wait(GPU_SAMPLE_INTERVAL):
                _sample_gpu(gpu_state)

        threading.Thread(target=_gpu_sampler_loop, daemon=True).start()
    # COLMAP/训练可能数小时无换行：独立心跳，避免 HTTP 超时被误报成任务失败
    hb_stop = threading.Event()

    def _heartbeat_loop() -> None:
        while not hb_stop.wait(HEARTBEAT_INTERVAL):
            api.report_progress(task_id, status=status)

    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    last_log_report = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line.strip():
                continue
            log(f"    {line}")
            # 训练迭代进度上报（实时进度；tqdm \r 行读不到时退化为阶段进度）
            if report_iterations and status == "training":
                m = iter_re.search(line)
                if m:
                    cur, total = int(m.group(1)), int(m.group(2))
                    if total > 0:
                        pct = min(100, int(cur / total * 100))
                        last_log_report = time.time()
                        api.report_progress(
                            task_id,
                            progress=pct,
                            log_line=line[:400],
                            gpu_memory=_sample_gpu(gpu_state),
                        )
                        continue
            # 其它阶段百分比（convert 等）
            elif status in ("converting", "rendering") and "%" in line:
                m = pct_re.search(line)
                if m:
                    last_log_report = time.time()
                    api.report_progress(
                        task_id,
                        progress=min(100, int(float(m.group(1)))),
                        log_line=line[:400],
                    )
                    continue
            now = time.time()
            urgent = bool(_URGENT_LOG_RE.search(line))
            if not urgent and (now - last_log_report) < LOG_THROTTLE_SECONDS:
                continue
            last_log_report = now
            api.report_progress(task_id, log_line=line[:400])
        proc.wait()
    finally:
        hb_stop.set()
        if gpu_sampler_stop is not None:
            gpu_sampler_stop.set()
        _CURRENT_PROC = None
    if proc.returncode != 0:
        raise RuntimeError(
            f"「{step_label}」命令执行失败（退出码 {proc.returncode}）: {cmd}\n"
            f"请查看上方日志定位具体错误"
        )


def find_latest_ply(output_dir: Path) -> Path | None:
    """在 output/point_cloud/iteration_*/ 下找迭代次数最大的 point_cloud.ply。"""
    pc_root = output_dir / "point_cloud"
    if not pc_root.exists():
        return None
    best: tuple[int, Path] | None = None
    for d in pc_root.iterdir():
        if not d.is_dir():
            continue
        m = re.search(r"(\d+)", d.name)
        if not m:
            continue
        iters = int(m.group(1))
        ply = d / "point_cloud.ply"
        if ply.exists() and (best is None or iters > best[0]):
            best = (iters, ply)
    return best[1] if best else None


def read_results_json(output_dir: Path) -> dict:
    """读取 render 输出目录下的 results.json 质量指标。

    兼容 key 大小写（PSNR/psnr/SSIM/ssim/...）与 ours_30000 嵌套，
    统一转小写 key 返回；3DGS 原版 metrics 把每张测试图结果存成列表，
    这里取均值，便于前端按小写 psnr/ssim/lpips 取用。
    """
    def _scalar(v):
        if isinstance(v, (list, tuple)):
            try:
                return sum(float(x) for x in v) / len(v)
            except (TypeError, ValueError):
                return v[-1] if v else None
        return v

    def _flatten(d: dict) -> dict:
        out: dict = {}
        for k, v in d.items():
            nk = str(k).lower()
            if isinstance(v, dict):
                out.update(_flatten(v))   # 递归展开嵌套（如 ours_30000）
            else:
                out[nk] = _scalar(v)
        return out

    for root, _, files in os.walk(str(output_dir)):
        if "results.json" in files:
            try:
                data = json.loads(
                    Path(root, "results.json").read_text(encoding="utf-8")
                )
                if isinstance(data, dict):
                    if "ours_30000" in data and isinstance(data["ours_30000"], dict):
                        return _flatten(data["ours_30000"])
                    return _flatten(data)
                return {}
            except Exception:
                return {}
    return {}


def _download_with_retry(fn, label: str, retries: int = 2,
                         delay: float = 5.0) -> None:
    """带重试的下载封装：单张图失败重试 retries 次（共 retries+1 次尝试），
    每次失败间隔 delay 秒。全部失败才抛异常，避免一张图网络抖动拖垮整个任务。"""
    last_err: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            fn()
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt <= retries:
                log(f"{label} 第 {attempt} 次失败（{e}），{int(delay)}s 后重试", "WARN")
                time.sleep(delay)
    raise RuntimeError(f"{label} 重试 {retries} 次仍失败: {last_err}")


def download_images(task: dict, dest_dir: Path, api: ServerAPI) -> None:
    """拉取图片：优先 OSS 直链，否则从服务器下载。逐批上报进度。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    task_id = task["task_id"]
    # 认领快照里没有直链时，刷新一次任务详情（OSS 后台上传可能刚完成，
    # 避免大任务直链未生成就被认领，从而误走服务器下载慢路径）
    if not (task.get("oss_images") or []):
        try:
            fresh = api.get_task(task_id)
            if fresh and fresh.get("oss_images"):
                task["oss_images"] = fresh["oss_images"]
        except Exception:
            pass  # 刷新失败则继续走原逻辑（服务器下载兜底）
    oss_images = task.get("oss_images") or []
    total: int
    done = 0

    def _report(done_count: int, total_count: int) -> None:
        pct = int(done_count / total_count * 100) if total_count else 0
        try:
            api.report_progress(
                task_id, status="uploading", progress=min(100, pct),
                current_step="i18n:step.uploading",
                log_line=f"[worker] 图片下载进度 {done_count}/{total_count}",
            )
        except Exception:
            pass  # 进度上报失败不影响下载

    def _oss_fetch(url: str, dest: Path) -> None:
        # 带超时的 OSS 下载：urlretrieve 无超时会无限挂起
        with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)

    if oss_images:
        total = len(oss_images)
        log(f"从 OSS 拉取 {total} 张图片（失败自动重试）...")
        for url in oss_images:
            fname = Path(urllib.parse.urlparse(url).path).name
            dest = dest_dir / fname
            _download_with_retry(
                lambda url=url, dest=dest: _oss_fetch(url, dest),
                f"OSS 图片 {fname}",
            )
            done += 1
            if done % 5 == 0 or done == total:
                _report(done, total)
    else:
        names = api.list_images(task_id)
        if not names:
            raise RuntimeError("服务器未返回图片清单，且无 OSS 直链")
        total = len(names)
        log(f"从服务器下载 {total} 张图片（单张超时 600s，失败自动重试）...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(
                    _download_with_retry,
                    lambda name=name: api.download_image(
                        task_id, name, dest_dir / name, timeout=600
                    ),
                    f"图片 {name}",
                ): name
                for name in names
            }
            for fut in concurrent.futures.as_completed(futures):
                name = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    raise RuntimeError(f"下载图片 {name} 失败: {e}")
                done += 1
                if done % 5 == 0 or done == total:
                    _report(done, total)
    count = len(list(dest_dir.glob("*")))
    log(f"图片就绪，共 {count} 张")
    if count == 0:
        raise RuntimeError("本地图片目录为空，无法重建")


def upload_result_oss(ply_path: Path, cfg: dict, recon_id: str) -> str | None:
    """
    尝试用 oss2 直传 OSS（可选）。返回公开 URL；未配置/未安装返回 None。
    对象名按 output/<重建编号>/<重建编号>.ply 归档，与服务器回传模式一致。
    """
    oss_cfg = cfg.get("oss", {})
    if not (oss_cfg.get("access_key_id") and oss_cfg.get("access_key_secret")
            and oss_cfg.get("bucket") and oss_cfg.get("endpoint")):
        return None
    try:
        import oss2  # type: ignore
    except ImportError:
        log("worker 未安装 oss2，结果将回传服务器处理", "WARN")
        return None
    auth = oss2.Auth(oss_cfg["access_key_id"], oss_cfg["access_key_secret"])
    bucket = oss2.Bucket(auth, oss_cfg["endpoint"], oss_cfg["bucket"])
    object_name = f"output/{recon_id}/{recon_id}.ply"
    log(f"worker 直传 OSS: {object_name}")
    bucket.put_object_from_file(object_name, str(ply_path))
    # 兼容 endpoint 带/不带协议头：剥掉后再拼，避免拼出 bucket.https://xxx 垃圾 URL
    endpoint_host = (
        oss_cfg["endpoint"].replace("https://", "").replace("http://", "")
    )
    url = f"https://{oss_cfg['bucket']}.{endpoint_host}/{object_name}"
    log(f"OSS 直传成功: {url}")
    return url


def cleanup_local(task: dict, local_data: Path, local_output: Path | None,
                  cfg: dict) -> None:
    """完成后自动删除本地图片与模型，释放磁盘。"""
    if cfg.get("keep_files"):
        log("keep_files=true，保留本地文件")
        return
    for p in (local_data, local_output):
        if p and p.exists():
            shutil.rmtree(p, ignore_errors=True)
            log(f"已清理本地文件: {p}")


# ==================== 单个任务处理 ====================

def process_task(task: dict, api, cfg: dict,
                 status: LocalStatus) -> None:
    task_id = task["task_id"]
    recon_id = task["reconstruction_id"]
    project_root = Path(cfg["project_root"])
    data_dir = Path(cfg["data_root"]) / recon_id
    input_dir = data_dir / "input"
    train_params = task.get("train_params") or {}
    stage_timings: dict = {}
    output_folder: str | None = None
    started = time.time()

    status.update(task_id, status="running", progress=0,
                  current_step="拉取图片", started_at=started,
                  reconstruction_id=recon_id)
    log(f"===== 开始处理任务 {task_id}（{recon_id}，{task.get('image_count', 0)} 张图片）=====")

    try:
        # ---------- 阶段1：拉取图片 ----------
        stage_timings["upload"] = {"start": time.time()}
        api.report_progress(
            task_id, status="uploading", progress=0,
            current_step="i18n:step.uploading",
            log_line=f"[worker:{cfg['worker_id']}] 开始拉取图片",
        )
        download_images(task, input_dir, api)
        stage_timings["upload"]["end"] = time.time()
        stage_timings["upload"]["duration"] = (
            stage_timings["upload"]["end"] - stage_timings["upload"]["start"]
        )
        api.report_progress(task_id, status="uploading", progress=100,
                            current_step="i18n:step.uploadDone",
                            stage_timings=stage_timings)

        # ---------- 阶段2：convert.py（COLMAP） ----------
        stage_timings["convert"] = {"start": time.time()}
        api.report_progress(task_id, status="converting", progress=0,
                            current_step="i18n:step.converting",
                            stage_timings=stage_timings)
        PY = resolve_python(cfg)
        gpu_flag = "" if cfg["use_gpu"] else " --no_gpu"
        convert_cmd = (
            f"cd {project_root} && {PY} convert.py -s {data_dir}{gpu_flag}"
        )
        run_command(convert_cmd, api, task_id, "converting",
                    STAGE_LABELS["convert"], stage_timings["convert"])
        stage_timings["convert"]["end"] = time.time()
        stage_timings["convert"]["duration"] = (
            stage_timings["convert"]["end"] - stage_timings["convert"]["start"]
        )
        api.report_progress(task_id, status="converting", progress=100,
                            current_step="i18n:step.convertDone",
                            stage_timings=stage_timings)

        # ---------- 阶段3：train.py ----------
        stage_timings["train"] = {"start": time.time()}
        api.report_progress(task_id, status="training", progress=0,
                            current_step="i18n:step.training",
                            stage_timings=stage_timings)
        params_args = build_train_params_args(train_params)
        # 端口隔离：3DGS train.py 会无条件绑定 GUI 端口（默认 6009），若上一个训练
        # 进程残留 / 端口被占，新训练会直接崩溃（Address already in use）。
        # 改为运行时挑一个空闲端口，规避冲突；仅当 train.py 支持 --port 时才追加。
        train_port_args = ""
        if "--port" not in params_args and train_supports_port(project_root):
            train_port_args = f" --port {find_free_port()}"
        train_cmd = (
            f"cd {project_root} && {PY} train.py -s {data_dir}"
            f"{params_args}{train_port_args}"
        )
        total_iters = int(train_params.get("iterations") or 30000)
        # GPU 显存采样：训练阶段累计峰值，由 run_command 迭代上报回传
        gpu_state: dict = {"peak_gb": 0.0, "current": None}
        api.report_progress(
            task_id,
            log_line=f"[train] 执行命令: {PY} train.py -s {data_dir}"
                     f"{params_args}{train_port_args}",
        )
        run_command(train_cmd, api, task_id, "training",
                    STAGE_LABELS["train"], stage_timings["train"],
                    report_iterations=cfg.get("report_iterations", True),
                    total_iters=total_iters, gpu_state=gpu_state)
        stage_timings["train"]["end"] = time.time()
        stage_timings["train"]["duration"] = (
            stage_timings["train"]["end"] - stage_timings["train"]["start"]
        )
        api.report_progress(task_id, status="training", progress=100,
                            current_step="i18n:step.trainDone",
                            stage_timings=stage_timings,
                            gpu_memory=_sample_gpu(gpu_state))

        # 确定输出目录：output 下最新的子目录
        output_root = Path(cfg["output_root"])
        if output_root.exists():
            folders = sorted(
                (d for d in output_root.iterdir() if d.is_dir()),
                key=lambda d: d.stat().st_mtime,
            )
            if folders:
                output_folder = folders[-1].name
        if not output_folder:
            output_folder = recon_id
        output_dir = output_root / output_folder
        log(f"输出目录: {output_dir}")

        # ---------- 阶段4：render.py ----------
        stage_timings["render"] = {"start": time.time()}
        api.report_progress(task_id, status="rendering", progress=0,
                            current_step="i18n:step.rendering",
                            stage_timings=stage_timings)
        render_cmd = (
            f"cd {project_root} && {PY} render.py -m output/{output_folder} "
            f"-s {data_dir}"
        )
        api.report_progress(
            task_id, log_line=f"[render] 执行命令: {render_cmd}"
        )
        run_command(render_cmd, api, task_id, "rendering",
                    STAGE_LABELS["render"], stage_timings["render"])
        stage_timings["render"]["end"] = time.time()
        stage_timings["render"]["duration"] = (
            stage_timings["render"]["end"] - stage_timings["render"]["start"]
        )
        api.report_progress(task_id, status="rendering", progress=100,
                            current_step="i18n:step.renderDone",
                            stage_timings=stage_timings)

        # ---------- 阶段4.5：metrics.py（可选，失败不阻断） ----------
        try:
            metrics_cmd = (
                f"cd {project_root} && {PY} metrics.py -m output/{output_folder}"
            )
            run_command(metrics_cmd, api, task_id, "rendering",
                        STAGE_LABELS["metrics"], stage_timings["render"])
        except Exception as e:
            log(f"metrics.py 执行失败（不阻断）: {e}", "WARN")
            api.report_progress(task_id, log_line=f"[metrics] ⚠ {e}")

        # 读取质量指标
        metrics = read_results_json(output_dir)
        if metrics:
            api.report_progress(task_id, log_line=(
                f"[Metrics] ✓ PSNR={metrics.get('psnr')} "
                f"SSIM={metrics.get('ssim')} LPIPS={metrics.get('lpips')}"
            ))
        else:
            api.report_progress(task_id, log_line=(
                "[Metrics] 未找到 results.json 指标（不影响完成，可能未启用 --eval）"
            ))

        # ---------- 阶段5：定位 ply ----------
        stage_timings["download"] = {"start": time.time()}
        api.report_progress(task_id, status="downloading", progress=0,
                            current_step="i18n:step.downloading",
                            stage_timings=stage_timings)
        ply = find_latest_ply(output_dir)
        if ply is None:
            raise RuntimeError(
                f"未找到 ply 模型文件，请检查 {output_dir}/point_cloud/ 目录"
            )
        stage_timings["download"]["end"] = time.time()
        stage_timings["download"]["duration"] = (
            stage_timings["download"]["end"] - stage_timings["download"]["start"]
        )
        api.report_progress(task_id, status="downloading", progress=100,
                            current_step="i18n:step.downloadDone",
                            stage_timings=stage_timings)

        # 构造最终 GPU 显存快照（含峰值）；未采到则保持 None（前端不显示）
        gpu_final: dict | None = None
        if gpu_state.get("peak_gb", 0.0) > 0:
            cur = gpu_state.get("current") or {
                "used_gb": 0, "total_gb": 0, "percent": 0,
            }
            gpu_final = {
                "used_gb": cur["used_gb"],
                "total_gb": cur["total_gb"],
                "percent": cur["percent"],
                "peak_gb": round(gpu_state["peak_gb"], 2),
            }

        # ---------- 阶段6：上传结果 ----------
        stage_timings["oss_upload"] = {"start": time.time()}
        api.report_progress(task_id, status="oss_uploading", progress=0,
                            current_step="i18n:step.oss_uploading",
                            stage_timings=stage_timings)
        oss_url = upload_result_oss(ply, cfg, recon_id)
        # 上传结束，先结算本阶段耗时 —— 必须在上报完成前写入 stage_timings，
        # 否则 report_complete 发出去的 oss_upload 只有 start、没有 duration，
        # 前端会一直显示「待开始」。
        stage_timings["oss_upload"]["end"] = time.time()
        stage_timings["oss_upload"]["duration"] = (
            stage_timings["oss_upload"]["end"] - stage_timings["oss_upload"]["start"]
        )
        if oss_url:
            # 先推满上传进度（仍处于 oss_uploading 阶段），再上报完成转 COMPLETED
            api.report_progress(
                task_id, status="oss_uploading", progress=100,
                current_step="i18n:step.ossUploadDone", stage_timings=stage_timings,
                log_line="[worker] 结果上传完成，释放算力",
            )
            api.report_complete(
                task_id, oss_url=oss_url, metrics=metrics,
                stage_timings=stage_timings, remote_output_folder=output_folder,
                gpu_memory=gpu_final,
            )
        else:
            # 回传服务器（服务器侧若配置 OSS 会自动再上传）
            resp = api.upload_model(task_id, ply)
            log(f"模型已回传服务器: {resp.get('model_url', '')}")
            # 先推满上传进度（仍处于 oss_uploading 阶段），再上报完成转 COMPLETED
            api.report_progress(
                task_id, status="oss_uploading", progress=100,
                current_step="i18n:step.ossUploadDone", stage_timings=stage_timings,
                log_line="[worker] 结果上传完成，释放算力",
            )
            api.report_complete(
                task_id, oss_url=None, metrics=metrics,
                stage_timings=stage_timings, remote_output_folder=output_folder,
                gpu_memory=gpu_final,
            )

        # ---------- 完成 + 清理 ----------
        finished = time.time()
        status.update(task_id, status="completed", progress=100,
                      finished_at=finished,
                      current_step="完成",
                      total_duration=round(finished - started, 2),
                      oss_url=oss_url)
        cleanup_local(task, data_dir, output_dir if output_folder != recon_id else None, cfg)
        log(f"===== 任务 {task_id} 处理完成（耗时 {finished - started:.1f}s）=====")

    except Exception as exc:
        finished = time.time()
        log(f"任务 {task_id} 失败: {exc}", "ERROR")
        try:
            api.report_fail(task_id, str(exc))
        except Exception as e:
            log(f"上报失败也失败了: {e}", "ERROR")
        status.update(task_id, status="failed", progress=100,
                      finished_at=finished, error=str(exc)[:4000],
                      total_duration=round(finished - started, 2))
        # 失败同样清理，释放磁盘
        cleanup_local(task, data_dir, output_dir if output_folder else None, cfg)


# ==================== 主循环 ====================

def main_loop(cfg: dict, args) -> None:
    global _log_file
    # 收到 SIGTERM/SIGINT（如 kill worker）时，连带杀掉正在跑的训练子进程，
    # 避免 train.py 变孤儿继续占用 6009 端口，导致下次训练崩溃
    signal.signal(signal.SIGTERM, _handle_terminate)
    signal.signal(signal.SIGINT, _handle_terminate)
    work_dir = Path(__file__).resolve().parent
    log_path = work_dir / "worker.log"
    _log_file = open(log_path, "a", encoding="utf-8")

    api = make_api(cfg)
    status = LocalStatus(work_dir / "worker_status.json", cfg["worker_id"])

    log("=" * 60)
    if oss_configured(cfg):
        oss = cfg.get("oss") or {}
        log(
            f"溯影 3DGS Worker 启动 | worker_id={cfg['worker_id']} | "
            f"queue=OSS {oss.get('bucket')}/{oss.get('queue_prefix') or 'queue'} | "
            f"poll={cfg['poll_interval']}s"
        )
    else:
        log(
            f"溯影 3DGS Worker 启动 | worker_id={cfg['worker_id']} | "
            f"OSS 未配置，回退 HTTP claim -> {cfg['server_url']} | "
            f"poll={cfg['poll_interval']}s"
        )
    log("=" * 60)

    interval = max(1, int(cfg["poll_interval"]))
    consecutive_empty = 0
    max_idle = args.max_idle or float("inf")

    while True:
        try:
            task = api.claim()
        except Exception as e:
            log(f"领取任务失败（{e}），{interval}s 后重试", "WARN")
            time.sleep(interval)
            continue

        if task is None:
            consecutive_empty += 1
            log(f"无待处理任务（已连续空轮询 {consecutive_empty} 次），{interval}s 后重试")
            if args.once:
                log("--once 模式：无任务，退出")
                break
            if consecutive_empty >= max_idle:
                log("达到 max-idle，退出")
                break
            time.sleep(interval)
            continue

        consecutive_empty = 0
        try:
            process_task(task, api, cfg, status)
        except Exception as e:
            log(f"处理任务出现未捕获异常: {e}", "ERROR")
        if args.once:
            log("--once 模式：处理完成，退出")
            break
        time.sleep(interval)


if __name__ == "__main__":
    cfg, args = load_config()
    main_loop(cfg, args)
