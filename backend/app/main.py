"""
溯影 3DGS 自动化重建平台 - FastAPI 主应用（拉模式任务队列架构）

核心流程：
  1. 登录用户向后端领取短期 OSS Post Policy，浏览器直传图片到 OSS，
     再 POST /api/tasks 只提交公开 URL；后端校验地址后写入 OSS 私有队列。
  2. 3090 worker 轮询 OSS queue/pending 领取任务（不必连本机）；图片走 OSS 直链，
     进度/完成写回 queue/status；本机后台线程同步到 TaskManager 供前端展示。
     OSS 未配置时仍可 POST /api/worker/claim 兜底。
  3. 前端通过 GET /api/tasks、WebSocket 或三份联动记录文件
     （GET /api/queue/tasks | progress | completed）查看状态。

接口一览：
  POST   /api/auth/login                 登录（签发 JWT）
  GET    /api/auth/me                    当前用户
  POST   /api/admin/users                管理员开号
  GET    /api/admin/users                用户列表
  PATCH  /api/admin/users/{id}           禁用/启用用户
  POST   /api/oss/upload-policy          签发短期 OSS 直传凭证
  POST   /api/tasks                      创建重建任务（JSON：OSS 地址；OSS 未配置时 multipart 兜底）
  GET    /api/tasks                      列出当前用户可见任务
  GET    /api/tasks/{task_id}            查询任务状态
  GET    /api/tasks/{task_id}/model      下载/流式传输 ply 模型
  DELETE /api/tasks/{task_id}            删除任务
  WS     /ws/tasks/{task_id}             实时订阅任务进度
  GET    /api/queue/tasks                任务清单记录文件
  GET    /api/queue/progress             过程记录记录文件
  GET    /api/queue/completed            完成归档记录文件
  --- worker 专用（需 Bearer WORKER_API_KEY）---
  POST   /api/worker/claim               领取下一个待处理任务
  POST   /api/worker/progress            上报进度
  POST   /api/worker/complete            上报完成
  POST   /api/worker/fail                上报失败
  POST   /api/worker/upload-model        回传 ply 模型（OSS 未配置时）
  GET    /api/worker/tasks/{task_id}/images/{filename}   下载图片（OSS 未配置时）
  GET    /api/health                     健康检查
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import (
    get_current_admin,
    get_current_user,
    get_optional_user,
    user_from_token,
    ws_current_user,
)
from .config import settings
from .oss_manager import get_oss_manager
from .quota_manager import get_quota_manager
from .task_manager import (
    TaskInfo,
    TaskStatus,
    compute_stage_progress,
    get_task_manager,
)
from .user_manager import User, get_user_manager

logger = logging.getLogger("main")

app = FastAPI(
    title="溯影 3DGS 自动化重建平台",
    description="上传图片，自动完成 3D Gaussian Splatting 重建并返回 .ply 模型",
    version="2.0.0",
)

# ----- CORS：允许前端跨域访问（通过 CORS_ORIGINS 环境变量配置域名）-----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常返回 JSON detail，避免前端只看到 axios 默认 500 文案。"""
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {exc}"},
    )

# 启动时加载持久化的任务，并播种管理员（已存在则跳过）
get_task_manager().load_persisted()
get_user_manager()


def _start_oss_queue_sync() -> None:
    """把本地 pending 补写到 OSS，并启动 status 同步线程。"""
    if settings.DEBUG_MODE:
        return
    oss = get_oss_manager()
    if not oss.is_enabled():
        logger.info("OSS 未启用，3090 仍走 HTTP claim")
        return
    manager = get_task_manager()
    try:
        manager.republish_pending_to_oss()
    except Exception:
        logger.exception("[OSS queue] 启动补写 pending 失败")
    thread = threading.Thread(
        target=manager.oss_sync_loop,
        name="oss-queue-sync",
        daemon=True,
    )
    thread.start()
    logger.info("[OSS queue] 已启动 status 同步线程")


_start_oss_queue_sync()


# ==================== Worker 鉴权 ====================


def _require_worker_auth(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """
    Worker 接口鉴权：请求头需携带 `Authorization: Bearer <WORKER_API_KEY>`。
    未配置 WORKER_API_KEY 时跳过鉴权（仅建议内网/调试环境）。
    """
    if not settings.WORKER_API_KEY:
        return
    if authorization != f"Bearer {settings.WORKER_API_KEY}":
        raise HTTPException(status_code=401, detail="无效的 worker 凭证")


# ==================== 请求体模型 ====================


class LoginBody(BaseModel):
    username: str
    password: str


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "user"


class PatchUserBody(BaseModel):
    disabled: bool


class UploadFileMeta(BaseModel):
    name: str
    size: int = Field(ge=1)


class UploadPolicyBody(BaseModel):
    reconstruction_id: Optional[str] = None
    files: list[UploadFileMeta]


class CreateTaskBody(BaseModel):
    reconstruction_id: Optional[str] = None
    oss_images: list[str]
    train_params: Optional[dict] = None
    total_bytes: Optional[int] = None


def _sanitize_recon_id(raw: Optional[str]) -> str:
    """重建编号只允许字母数字下划线短横，防止路径穿越。"""
    if not raw or not raw.strip():
        return f"recon_{int(time.time())}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", raw.strip())
    if not cleaned:
        raise HTTPException(status_code=400, detail="重建编号包含非法字符")
    return cleaned[:80]


def _require_task_access(task: TaskInfo, user: User) -> None:
    if not get_task_manager().can_access(task, user.id, user.role == "admin"):
        raise HTTPException(status_code=404, detail="任务不存在")


# ==================== 用户鉴权 ====================


@app.post("/api/auth/login")
async def login(body: LoginBody) -> dict:
    user = get_user_manager().authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    from .auth import create_access_token

    return {
        "token": create_access_token(user),
        "user": user.to_public(),
    }


@app.get("/api/auth/me")
async def auth_me(user: User = Depends(get_current_user)) -> dict:
    return user.to_public()


@app.post("/api/admin/users", status_code=201)
async def admin_create_user(
    body: CreateUserBody,
    _: User = Depends(get_current_admin),
) -> dict:
    try:
        created = get_user_manager().create_user(
            body.username, body.password, body.role
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return created.to_public()


@app.get("/api/admin/users")
async def admin_list_users(_: User = Depends(get_current_admin)) -> dict:
    users = get_user_manager().list_users()
    return {"users": [u.to_public() for u in users], "total": len(users)}


@app.patch("/api/admin/users/{user_id}")
async def admin_patch_user(
    user_id: str,
    body: PatchUserBody,
    admin: User = Depends(get_current_admin),
) -> dict:
    if user_id == admin.id and body.disabled:
        raise HTTPException(status_code=400, detail="不能禁用当前登录的管理员")
    user = get_user_manager().set_disabled(user_id, body.disabled)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user.to_public()


# ==================== OSS 直传凭证 ====================


@app.post("/api/oss/upload-policy")
async def issue_upload_policy(
    body: UploadPolicyBody,
    user: User = Depends(get_current_user),
) -> dict:
    """签发短期 Post Policy。先做配额校验再签发。"""
    if user.disabled:
        raise HTTPException(status_code=403, detail="账号已禁用")
    oss = get_oss_manager()
    if not oss.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="OSS 未配置，本地调试请改用 multipart 创建任务",
        )
    quota = get_quota_manager()
    quota.check_policy_rate(user.id)

    files = [f for f in body.files if _is_image_file(f.name)]
    if not files:
        raise HTTPException(status_code=400, detail="未检测到有效图片")
    for f in files:
        if f.size > settings.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"单文件不能超过 {settings.MAX_FILE_SIZE_MB}MB: {f.name}",
            )
    total_bytes = sum(f.size for f in files)
    quota.assert_can_upload(user.id, len(files), total_bytes)

    reconstruction_id = _sanitize_recon_id(body.reconstruction_id)
    policy = oss.generate_post_policy(user.id, reconstruction_id)
    return {
        "reconstruction_id": reconstruction_id,
        "file_count": len(files),
        "total_bytes": total_bytes,
        **policy,
    }


# ==================== 健康检查 ====================


@app.get("/api/health")
async def health_check() -> dict:
    """健康检查接口，返回后端状态与队列统计。"""
    manager = get_task_manager()
    tasks = manager.list_tasks()
    running_states = {
        TaskStatus.UPLOADING,
        TaskStatus.CONVERTING,
        TaskStatus.TRAINING,
        TaskStatus.RENDERING,
        TaskStatus.DOWNLOADING,
        TaskStatus.OSS_UPLOADING,
    }
    queue_stats = {
        "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
        "running": sum(1 for t in tasks if t.status in running_states),
        "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
        "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
    }
    return {
        "status": "ok",
        "debug_mode": settings.DEBUG_MODE,
        "architecture": (
            "pull-queue (3090 worker polls OSS queue)"
            if get_oss_manager().is_enabled() and not settings.DEBUG_MODE
            else "pull-queue (3090 worker polls server)"
        ),
        "oss_enabled": get_oss_manager().is_enabled(),
        "oss_queue_enabled": (
            get_oss_manager().is_enabled() and not settings.DEBUG_MODE
        ),
        "queue_stats": queue_stats,
        "storage_dir": str(settings.LOCAL_STORAGE_DIR),
    }


# ==================== 任务接口 ====================


@app.post("/api/tasks")
async def create_task(
    request: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """
    创建重建任务。

    生产路径（OSS 已配置）：JSON { reconstruction_id, oss_images, train_params }，
    后端只校验并落地址。
    本地兜底（OSS 未配置）：仍接受 multipart 图片/ZIP，写入 storage/uploads/。
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        raw = await request.json()
        body = CreateTaskBody.model_validate(raw)
        return await _create_task_from_oss_urls(request, user, body)

    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=415, detail="请提交 JSON 或 multipart 表单")

    if get_oss_manager().is_enabled():
        raise HTTPException(
            status_code=400,
            detail="OSS 已启用，请直传 OSS 后只提交文件地址",
        )
    return await _create_task_from_local_upload(request, user)


async def _create_task_from_oss_urls(
    request: Request, user: User, body: CreateTaskBody
) -> JSONResponse:
    oss = get_oss_manager()
    if not oss.is_enabled():
        raise HTTPException(status_code=503, detail="OSS 未配置，无法按地址创建任务")

    reconstruction_id = _sanitize_recon_id(body.reconstruction_id)
    urls: list[str] = []
    seen: set[str] = set()
    for raw_url in body.oss_images:
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        url = raw_url.strip()
        try:
            oss.validate_image_url(url, user.id, reconstruction_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    total_bytes = int(body.total_bytes or 0)
    get_quota_manager().assert_can_upload(user.id, len(urls), total_bytes)

    parsed_train_params = body.train_params if isinstance(body.train_params, dict) else {}
    manager = get_task_manager()
    task = manager.create_task(
        reconstruction_id,
        len(urls),
        owner_id=user.id,
        oss_images=urls,
    )
    task.train_params = parsed_train_params
    task.update(
        current_step="i18n:step.queued",
        log_line=(
            f"[queue] 任务已入队（{len(urls)} 张 OSS 图片），等待 3090 从 OSS 领取"
        ),
    )
    manager.publish_pending(task)
    get_quota_manager().record_task(user.id, total_bytes)
    return _enqueue_response(request, task, urls)


async def _create_task_from_local_upload(
    request: Request,
    user: User,
) -> JSONResponse:
    form = await request.form()
    reconstruction_id = _sanitize_recon_id(
        str(form.get("reconstruction_id") or "") or None
    )
    train_params = form.get("train_params")
    if train_params is not None:
        train_params = str(train_params)
    images = [v for k, v in form.multi_items() if k == "images"]
    zip_file = form.get("zip_file")

    parsed_train_params: dict = {}
    if train_params:
        try:
            parsed_train_params = json.loads(train_params)
            if not isinstance(parsed_train_params, dict):
                raise ValueError("train_params 必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=422,
                detail=f"train_params JSON 解析失败: {e}",
            )

    upload_dir = settings.LOCAL_STORAGE_DIR / "uploads" / reconstruction_id
    if upload_dir.exists():
        backup = upload_dir.with_name(
            f"{upload_dir.name}_old_{int(time.time())}"
        )
        try:
            upload_dir.rename(backup)
        except OSError:
            pass
    upload_dir.mkdir(parents=True)

    image_count = 0
    total_bytes = 0

    if zip_file is not None and hasattr(zip_file, "filename") and zip_file.filename:
        zip_bytes = await zip_file.read()
        total_bytes += len(zip_bytes)
        with zipfile.ZipFile(zip_bytes) as zf:
            for member in zf.namelist():
                if member.endswith("/") or not _is_image_file(member):
                    continue
                fname = Path(member).name
                target = upload_dir / fname
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                image_count += 1

    for img in images:
        if not hasattr(img, "filename") or not img.filename or not _is_image_file(img.filename):
            continue
        safe_name = Path(img.filename).name
        target = upload_dir / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = await img.read()
        if len(content) > settings.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"单文件不能超过 {settings.MAX_FILE_SIZE_MB}MB",
            )
        with open(target, "wb") as f:
            f.write(content)
        total_bytes += len(content)
        image_count += 1

    get_quota_manager().assert_can_upload(user.id, image_count, total_bytes)

    manager = get_task_manager()
    task = manager.create_task(
        reconstruction_id, image_count, owner_id=user.id
    )
    task.train_params = parsed_train_params
    task.update(
        current_step="i18n:step.queued",
        log_line=f"[queue] 任务已入队（{image_count} 张本地图片），等待 3090 worker 领取",
    )
    manager.publish_pending(task)
    get_quota_manager().record_task(user.id, total_bytes)
    return _enqueue_response(request, task, [])


def _enqueue_response(
    request: Request, task: TaskInfo, oss_images: list[str]
) -> JSONResponse:
    if settings.DEBUG_MODE:
        asyncio.create_task(_run_debug_worker(task.task_id))
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse(
        status_code=201,
        content={
            "task_id": task.task_id,
            "reconstruction_id": task.reconstruction_id,
            "image_count": task.image_count,
            "status": task.status.value,
            "oss_images": oss_images,
            "oss_uploading": False,
            "server_base_url": base_url,
            "message": "任务已创建并入队，等待 3090 算力处理",
        },
    )


@app.get("/api/tasks")
async def list_tasks(user: User = Depends(get_current_user)) -> dict:
    """列出当前用户可见的重建任务。"""
    manager = get_task_manager()
    tasks = manager.list_tasks_for_user(user.id, user.role == "admin")
    return {
        "tasks": [t.to_dict() for t in tasks],
        "total": len(tasks),
    }


@app.get("/api/tasks/{task_id}")
async def get_task(
    task_id: str, user: User = Depends(get_current_user)
) -> dict:
    """查询单个任务的详细状态。"""
    task = get_task_manager().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_task_access(task, user)
    return task.to_dict()


@app.get("/api/tasks/{task_id}/model")
async def get_model(
    task_id: str,
    token: Optional[str] = None,
    user: Optional[User] = Depends(get_optional_user),
) -> FileResponse:
    """下载/流式传输 ply。查看器无法带 Header，因此也接受 ?token=。"""
    actor = user
    if actor is None and token:
        actor = user_from_token(token)
    if actor is None:
        raise HTTPException(status_code=401, detail="请先登录")
    task = get_task_manager().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_task_access(task, actor)
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"任务尚未完成，当前状态：{task.status.value}",
        )
    if not task.model_path or not Path(task.model_path).exists():
        raise HTTPException(status_code=404, detail="模型文件不存在")
    return FileResponse(
        task.model_path,
        media_type="application/octet-stream",
        filename=f"{task.reconstruction_id}.ply",
    )


@app.delete("/api/tasks/{task_id}")
async def delete_task(
    task_id: str, user: User = Depends(get_current_user)
) -> dict:
    """删除任务及其相关文件。"""
    manager = get_task_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_task_access(task, user)
    # 清理本地文件（删除失败不阻断：可能被占用或环境限制）
    upload_dir = settings.LOCAL_STORAGE_DIR / "uploads" / task.reconstruction_id
    if upload_dir.exists():
        try:
            _safe_archive(upload_dir, f"upload_{task.reconstruction_id}")
        except OSError:
            pass
    if task.model_path:
        model_path = Path(task.model_path)
        if model_path.exists():
            try:
                _safe_archive(model_path, f"model_{task.task_id}")
            except OSError:
                pass
    task_file = settings.LOCAL_STORAGE_DIR / "tasks" / f"{task_id}.json"
    if task_file.exists():
        try:
            _safe_archive(task_file, f"task_{task_id}.json")
        except OSError:
            pass
    # 从内存移除
    manager._tasks.pop(task_id, None)
    manager._subscribers.pop(task_id, None)
    manager._sync_queue_files()
    return {"message": "任务已删除", "task_id": task_id}


def _safe_archive(path: Path, tag: str) -> None:
    """归档（重命名）文件/目录代替删除：部分环境会拦截 unlink/rmtree，
    但 rename 不受影响。归档物保留在 storage/trash/ 下便于追溯。"""
    trash_dir = settings.LOCAL_STORAGE_DIR / "trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    target = trash_dir / f"{tag}.{int(time.time() * 1000)}"
    path.rename(target)


# ==================== 三份联动记录文件 ====================


async def _read_queue_file(name: str) -> dict:
    path = settings.LOCAL_STORAGE_DIR / "queue" / name
    if not path.exists():
        return {"updated_at": None, "tasks": [], "total": 0, "note": "尚无任务"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="记录文件解析失败")


def _filter_queue_payload(payload: dict, user: User) -> dict:
    if user.role == "admin":
        return payload
    visible = {
        t.task_id
        for t in get_task_manager().list_tasks_for_user(user.id, False)
    }
    filtered = dict(payload)
    tasks = [t for t in payload.get("tasks", []) if t.get("task_id") in visible]
    filtered["tasks"] = tasks
    filtered["total"] = len(tasks)
    return filtered


@app.get("/api/queue/tasks")
async def queue_tasks(user: User = Depends(get_current_user)) -> dict:
    """任务清单记录文件（按用户过滤）。"""
    return _filter_queue_payload(await _read_queue_file("tasks.json"), user)


@app.get("/api/queue/progress")
async def queue_progress(user: User = Depends(get_current_user)) -> dict:
    """过程记录记录文件（按用户过滤）。"""
    return _filter_queue_payload(await _read_queue_file("progress.json"), user)


@app.get("/api/queue/completed")
async def queue_completed(user: User = Depends(get_current_user)) -> dict:
    """完成归档记录文件（按用户过滤）。"""
    return _filter_queue_payload(await _read_queue_file("completed.json"), user)


# ==================== Worker 专用接口（3090 轮询脚本调用） ====================


@app.post("/api/worker/claim")
async def worker_claim(
    request: Request,
    worker_id: str = Form(default="anonymous-worker"),
    _: None = Depends(_require_worker_auth),
) -> JSONResponse:
    """
    Worker 领取下一个待处理任务（FIFO）。
    OSS 已启用时走私有队列原子领取（与 3090 相同路径）；否则走本机内存队列。
    领取成功返回任务完整信息；无任务时返回 {"task": null}。
    """
    manager = get_task_manager()
    oss = get_oss_manager()
    task = None
    if oss.is_enabled() and not settings.DEBUG_MODE:
        claimed = oss.claim_next(worker_id)
        if claimed is None:
            return JSONResponse(status_code=200, content={"task": None})
        task = manager.apply_remote_claim(claimed)
        data = task.to_dict() if task is not None else claimed
    else:
        task = manager.claim_next_task(worker_id)
        if task is None:
            return JSONResponse(status_code=200, content={"task": None})
        data = task.to_dict()
    if task is None and not data:
        return JSONResponse(status_code=200, content={"task": None})
    base_url = str(request.base_url).rstrip("/")
    data["image_download_base"] = (
        f"{base_url}/api/worker/tasks/{data.get('task_id')}/images"
    )
    return JSONResponse(
        status_code=200,
        content={"task": data, "server_base_url": base_url},
    )


@app.post("/api/worker/progress")
async def worker_progress(
    task_id: str = Form(...),
    status: Optional[str] = Form(default=None),
    progress: Optional[int] = Form(default=None),
    current_step: Optional[str] = Form(default=None),
    log_line: Optional[str] = Form(default=None),
    stage_timings: Optional[str] = Form(default=None),
    gpu_memory: Optional[str] = Form(default=None),
    _: None = Depends(_require_worker_auth),
) -> dict:
    """Worker 上报任务进度（阶段 / 百分比 / 日志 / 阶段耗时 / GPU 显存）。"""
    try:
        parsed_status = TaskStatus(status) if status else None
    except ValueError:
        raise HTTPException(status_code=422, detail=f"未知状态: {status}")
    parsed_timings: Optional[dict] = None
    if stage_timings:
        try:
            parsed_timings = json.loads(stage_timings)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="stage_timings 必须是 JSON")
    parsed_gpu: Optional[dict] = None
    if gpu_memory:
        try:
            parsed_gpu = json.loads(gpu_memory)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="gpu_memory 必须是 JSON")
    task = get_task_manager().report_progress(
        task_id=task_id,
        status=parsed_status,
        progress=progress,
        current_step=current_step,
        log_line=log_line,
        stage_timings=parsed_timings,
        gpu_memory=parsed_gpu,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task_id": task_id, "status": task.status.value}


@app.post("/api/worker/complete")
async def worker_complete(
    task_id: str = Form(...),
    oss_url: Optional[str] = Form(default=None),
    metrics: Optional[str] = Form(default=None),
    stage_timings: Optional[str] = Form(default=None),
    remote_output_folder: Optional[str] = Form(default=None),
    gpu_memory: Optional[str] = Form(default=None),
    _: None = Depends(_require_worker_auth),
) -> dict:
    """Worker 上报任务成功完成（OSS 结果地址 / 指标 / 阶段耗时 / GPU 显存）。"""
    parsed_metrics: Optional[dict] = None
    if metrics:
        try:
            parsed_metrics = json.loads(metrics)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="metrics 必须是 JSON")
    parsed_timings: Optional[dict] = None
    if stage_timings:
        try:
            parsed_timings = json.loads(stage_timings)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="stage_timings 必须是 JSON")
    parsed_gpu: Optional[dict] = None
    if gpu_memory:
        try:
            parsed_gpu = json.loads(gpu_memory)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="gpu_memory 必须是 JSON")
    task = get_task_manager().report_complete(
        task_id=task_id,
        oss_url=oss_url,
        metrics=parsed_metrics,
        stage_timings=parsed_timings,
        remote_output_folder=remote_output_folder,
        gpu_memory=parsed_gpu,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task_id": task_id, "status": task.status.value}


@app.post("/api/worker/fail")
async def worker_fail(
    task_id: str = Form(...),
    error: str = Form(...),
    _: None = Depends(_require_worker_auth),
) -> dict:
    """Worker 上报任务失败（含报错信息）。"""
    task = get_task_manager().report_fail(task_id, error)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task_id": task_id, "status": task.status.value}


@app.post("/api/worker/upload-model")
async def worker_upload_model(
    task_id: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(_require_worker_auth),
) -> dict:
    """
    Worker 回传 ply 模型（当 OSS 未配置 / 上传 OSS 失败时使用）。
    服务器保存到 storage/models/{recon_id}.ply，生成模型下载地址。
    """
    task = get_task_manager().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not file.filename or not file.filename.lower().endswith(".ply"):
        raise HTTPException(status_code=422, detail="仅支持 .ply 文件")
    model_path = (
        settings.LOCAL_STORAGE_DIR / "models" / f"{task.reconstruction_id}.ply"
    )
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with open(model_path, "wb") as f:
        content = await file.read()
        f.write(content)
    task.update(
        model_path=str(model_path),
        log_line=f"[worker] 模型已回传服务器: {model_path.name}",
    )
    # 服务器侧 OSS 已启用时，自动把回传的模型上传 OSS 生成公开直链
    oss_manager = get_oss_manager()
    if oss_manager.is_enabled():
        try:
            oss_url = await asyncio.get_running_loop().run_in_executor(
                None,
                oss_manager.upload_ply,
                model_path,
                f"{settings.OSS_OUTPUT_PREFIX}/{task.reconstruction_id}/{task.reconstruction_id}.ply",
            )
            task.update(
                oss_url=oss_url,
                log_line=f"[OSS] 服务器已上传模型: {oss_url}",
            )
        except Exception as exc:
            logger.warning("[%s] 服务器上传模型到 OSS 失败: %s", task.task_id, exc)
            task.update(log_line=f"[OSS] 服务器上传模型失败: {exc}")
    return {
        "ok": True,
        "task_id": task_id,
        "model_path": str(model_path),
        "model_url": f"/api/tasks/{task_id}/model",
    }


@app.get("/api/worker/tasks/{task_id}/images")
async def worker_list_images(
    task_id: str,
    _: None = Depends(_require_worker_auth),
) -> dict:
    """
    返回任务图片文件名清单（OSS 未配置时 worker 据此逐个下载）。
    """
    task = get_task_manager().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    upload_dir = (
        settings.LOCAL_STORAGE_DIR / "uploads" / task.reconstruction_id
    )
    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail="图片目录不存在")
    names = sorted(
        f.name for f in upload_dir.iterdir()
        if f.is_file() and _is_image_file(f.name)
    )
    return {"images": names, "count": len(names)}


@app.get("/api/worker/tasks/{task_id}/images/{filename}")
async def worker_download_image(
    task_id: str,
    filename: str,
    _: None = Depends(_require_worker_auth),
) -> FileResponse:
    """
    供 worker 下载图片（OSS 未配置时的回退方案）。
    仅允许读取该任务 reconstruction_id 目录下的图片。
    """
    task = get_task_manager().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 防路径穿越：只允许纯文件名
    safe_name = Path(filename).name
    if safe_name != filename or not _is_image_file(filename):
        raise HTTPException(status_code=400, detail="非法文件名")
    image_path = (
        settings.LOCAL_STORAGE_DIR
        / "uploads"
        / task.reconstruction_id
        / safe_name
    )
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(image_path, media_type="application/octet-stream")


# ==================== WebSocket 实时进度 ====================


@app.websocket("/ws/tasks/{task_id}")
async def task_websocket(websocket: WebSocket, task_id: str) -> None:
    """WebSocket 推送任务实时进度。需登录（query token=JWT）。"""
    user = await ws_current_user(websocket)
    if user is None:
        await websocket.close(code=4401, reason="请先登录")
        return
    manager = get_task_manager()
    task = manager.get_task(task_id)
    if task is None or not manager.can_access(task, user.id, user.role == "admin"):
        await websocket.close(code=4404, reason="任务不存在")
        return

    await websocket.accept()
    queue = manager.subscribe(task_id)

    # 立即推送当前状态
    try:
        await websocket.send_text(json.dumps(task.to_dict(), ensure_ascii=False))
    except Exception:
        manager.unsubscribe(task_id, queue)
        return

    try:
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(message)
                # 任务结束则关闭连接
                data = json.loads(message)
                if data.get("status") in ("completed", "failed"):
                    await asyncio.sleep(1)
                    break
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.unsubscribe(task_id, queue)


# ==================== 调试模式：内置模拟 worker ====================


async def _run_debug_worker(task_id: str) -> None:
    """
    调试模式：在服务器内部模拟一个 3090 worker，
    走与真实 worker 完全相同的状态流转（claim -> 拉图 -> 阶段推进 -> complete），
    便于前端联调而不依赖 3090。
    """
    manager = get_task_manager()
    # 模拟 worker 轮询间隔（等待领取）
    await asyncio.sleep(1.0)
    claimed = manager.claim_next_task("debug-worker")
    if claimed is None:
        return

    stages = [
        # (状态, 阶段key, 描述)
        (TaskStatus.UPLOADING, "upload", "i18n:step.uploading"),
        (TaskStatus.CONVERTING, "convert", "i18n:step.converting"),
        (TaskStatus.TRAINING, "train", "i18n:step.training"),
        (TaskStatus.RENDERING, "render", "i18n:step.rendering"),
        (TaskStatus.DOWNLOADING, "download", "i18n:step.downloading"),
        (TaskStatus.OSS_UPLOADING, "oss_upload", "i18n:step.oss_uploading"),
    ]
    stage_timings: dict = {}
    debug_metrics = {
        "psnr": 28.5341,
        "ssim": 0.8521,
        "lpips": 0.1287,
        "source": "debug_mode/results.json#ours_30000",
        "eval_mode": True,
    }
    # 模拟 GPU 显存占用（本机调试用，便于前端确认显存卡片位置；非真实数据）
    debug_gpu = {
        "used_gb": 18.5,
        "total_gb": 24.0,
        "percent": 77.1,
        "peak_gb": 21.3,
    }
    for status, stage_key, step in stages:
        manager.report_progress(
            task_id=task_id,
            status=status,
            progress=compute_stage_progress(status, 0),
            current_step=step,
            log_line=f"[debug-worker] 模拟阶段: {step}",
            gpu_memory=(debug_gpu if status == TaskStatus.TRAINING else None),
        )
        stage_timings[stage_key] = {"start": __import__("time").time()}
        for i in range(0, 101, 10):
            await asyncio.sleep(0.25)
            manager.report_progress(
                task_id=task_id,
                status=status,
                progress=compute_stage_progress(status, i),
                gpu_memory=(debug_gpu if status == TaskStatus.TRAINING else None),
            )
        stage_timings[stage_key]["end"] = __import__("time").time()
        stage_timings[stage_key]["duration"] = (
            stage_timings[stage_key]["end"] - stage_timings[stage_key]["start"]
        )
        manager.report_progress(
            task_id=task_id,
            status=status,
            progress=compute_stage_progress(status, 100),
            stage_timings=stage_timings,
            gpu_memory=(debug_gpu if status == TaskStatus.TRAINING else None),
        )

    # 生成一个假 ply（极小合法文件），供模型下载/查看
    task = manager.get_task(task_id)
    if task is not None:
        local_ply = (
            settings.LOCAL_STORAGE_DIR / "models" / f"{task.reconstruction_id}.ply"
        )
        local_ply.parent.mkdir(parents=True, exist_ok=True)
        local_ply.write_bytes(
            b"ply\nformat ascii 1.0\nelement vertex 3\n"
            b"property float x\nproperty float y\nproperty float z\n"
            b"end_header\n0 0 0\n1 1 1\n-1 0 1\n"
        )
        task.update(model_path=str(local_ply))

    # 模拟 OSS 结果地址
    oss_url = None
    if get_oss_manager().is_enabled():
        oss_url = get_oss_manager().build_url(f"{task.task_id}.ply")
    manager.report_complete(
        task_id=task_id,
        oss_url=oss_url,
        metrics=debug_metrics,
        stage_timings=stage_timings,
        remote_output_folder=f"debug_output_{task.reconstruction_id}",
        gpu_memory=debug_gpu,
    )
    manager.report_progress(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        progress=100,
        current_step="i18n:step.debugCompleted",
        log_line="[debug-worker] 调试任务完成（模拟）",
    )


# ==================== 工具函数 ====================


def _is_image_file(filename: str) -> bool:
    """判断文件名是否为支持的图片格式。"""
    return filename.lower().endswith(
        (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
    )


# ==================== 静态文件服务（可选）====================

# 挂载 models 目录，便于前端通过 URL 直接访问 ply（superspl.at 等在线查看器需要）
models_dir = settings.LOCAL_STORAGE_DIR / "models"
models_dir.mkdir(exist_ok=True)
app.mount("/static/models", StaticFiles(directory=str(models_dir)), name="models")
