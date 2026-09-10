"""
任务管理器：维护重建任务的生命周期与进度状态。

任务状态机（拉模式：3090 worker 主动轮询领取）：
  pending        -> 已入队，等待 3090 worker 领取
  uploading      -> worker 已领取，正在拉取图片到本地
  converting     -> 执行 convert.py（特征提取+匹配+三角化）
  training       -> 执行 train.py（高斯泼溅训练）
  rendering      -> 执行 render.py（渲染数据集）
  downloading    -> 正在查找/校验 ply 模型
  oss_uploading  -> 正在上传 ply 到阿里云 OSS
  completed      -> 重建完成，模型可查看
  failed         -> 重建失败

除 WebSocket 推送外，每次状态变化都会同步落盘三份联动记录文件
（storage/queue/ 下）：
  tasks.json     -> 任务清单（全量概要，含状态/进度/时间）
  progress.json  -> 过程记录（每个任务详细进度/阶段耗时/错误）
  completed.json -> 完成归档（仅已结束的任务：成功带结果地址，失败带报错）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

from .config import settings

logger = logging.getLogger("task_manager")

OSS_ORPHAN_TIMEOUT = 300
OSS_SYNC_INTERVAL = 5.0
_OSS_SYNC_FIELDS = (
    "oss_url",
    "error",
    "claimed_by",
    "claimed_at",
    "started_at",
    "heartbeat_at",
    "attempts",
    "finished_at",
    "remote_output_folder",
    "gpu_memory",
)


class TaskStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    CONVERTING = "converting"
    TRAINING = "training"
    RENDERING = "rendering"
    DOWNLOADING = "downloading"
    OSS_UPLOADING = "oss_uploading"
    COMPLETED = "completed"
    FAILED = "failed"


# 重建流程的阶段顺序（用于计算整体进度百分比）
STAGE_ORDER = [
    TaskStatus.UPLOADING,
    TaskStatus.CONVERTING,
    TaskStatus.TRAINING,
    TaskStatus.RENDERING,
    TaskStatus.DOWNLOADING,
    TaskStatus.OSS_UPLOADING,
]


@dataclass
class TaskInfo:
    """单个重建任务的完整信息。"""

    task_id: str
    reconstruction_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0  # 0-100
    current_step: str = "任务已创建"
    log_lines: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    image_count: int = 0
    model_path: Optional[str] = None  # 本地 ply 文件路径
    remote_output_folder: Optional[str] = None  # 3090 上的 output 子目录名
    error: Optional[str] = None
    # 各阶段耗时记录
    # 结构: {"upload": {"start": ts, "end": ts, "duration": seconds}, ...}
    # 阶段名: upload / convert / train / render / download
    stage_timings: dict = field(default_factory=dict)
    # 训练参数（仅包含用户自定义的参数，空值/默认值不传给 train.py）
    # 结构: {"iterations": 30000, "resolution": 1, ...}
    train_params: dict = field(default_factory=dict)
    # 重建质量指标（来自 train.py / render.py 输出的 results.json）
    # 结构: {"psnr": 28.5, "ssim": 0.85, "lpips": 0.12, "eval_mode": true}
    metrics: dict = field(default_factory=dict)
    # GPU 显存占用快照（worker 在训练阶段采样，含 used/total/percent/peak，单位 GB）
    # 结构: {"used_gb": 12.3, "total_gb": 24.0, "percent": 51.2, "peak_gb": 22.1}
    gpu_memory: Optional[dict] = None
    # 阿里云 OSS 公开访问 URL（上传成功后填充）
    oss_url: Optional[str] = None
    # ----- 拉模式队列字段（worker 相关）-----
    # 上传图片的 OSS 公开 URL 列表（OSS 未配置时为空，worker 改从服务器下载）
    oss_images: list[str] = field(default_factory=list)
    # 领取任务的 worker 标识
    claimed_by: Optional[str] = None
    # 被 worker 领取（开始执行）的时间戳
    claimed_at: Optional[float] = None
    # 任务实际开始执行（首次被领取）的时间戳
    started_at: Optional[float] = None
    # worker 最近一次心跳时间戳
    heartbeat_at: Optional[float] = None
    # 领取尝试次数（worker 崩溃重试会累加）
    attempts: int = 0
    # 任务结束（completed/failed）时间戳
    finished_at: Optional[float] = None
    # 创建任务的用户 ID（历史无主任务仅管理员可见）
    owner_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        # 日志只保留最近 200 行，避免前端负载过大
        data["log_lines"] = self.log_lines[-200:]
        # 计算端到端总耗时：核心 5 阶段齐全时求和（oss_upload 可选）
        durations = [
            t.get("duration")
            for t in self.stage_timings.values()
            if t.get("duration") is not None
        ]
        core_stages = {"upload", "convert", "train", "render", "download"}
        if core_stages.issubset(self.stage_timings.keys()):
            data["total_duration"] = sum(durations)
        else:
            # 回退：任务已完成/失败时用结束时间 - 创建时间估算
            end_time = self.finished_at or self.updated_at
            if end_time and self.created_at and self.status.value in {
                "completed",
                "failed",
            }:
                data["total_duration"] = max(0.0, end_time - self.created_at)
            else:
                data["total_duration"] = None
        return data

    def record_stage(self, stage: str, event: str) -> None:
        """
        记录阶段时间戳。

        :param stage: 阶段名（upload / convert / train / render / download）
        :param event: "start" 或 "end"
        """
        if stage not in self.stage_timings:
            self.stage_timings[stage] = {}
        self.stage_timings[stage][event] = time.time()
        # 同时有 start 和 end 时计算 duration
        timing = self.stage_timings[stage]
        if "start" in timing and "end" in timing:
            timing["duration"] = timing["end"] - timing["start"]
        self.updated_at = time.time()
        _manager._notify(self)

    def update(self, status: Optional[TaskStatus] = None,
               progress: Optional[int] = None,
               current_step: Optional[str] = None,
               log_line: Optional[str] = None,
               **extra: Any) -> None:
        """更新任务状态并广播通知。"""
        old_status = self.status
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress = max(0, min(100, progress))
        if current_step is not None:
            self.current_step = current_step
        if log_line is not None:
            self.log_lines.append(log_line)
            # 控制日志总量
            if len(self.log_lines) > 2000:
                self.log_lines = self.log_lines[-1000:]
        self.updated_at = time.time()
        for key, value in extra.items():
            setattr(self, key, value)
        status_changed = status is not None and self.status != old_status
        force_queue = status_changed or self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        )
        _manager._notify(self, force_queue=force_queue)


class TaskManager:
    """全局任务管理器（单例）。负责任务 CRUD 与 WebSocket 订阅。"""

    QUEUE_SYNC_DEBOUNCE = 5.0

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._queue_last_sync = 0.0
        self._queue_dirty = False
        self._queue_timer: Optional[threading.Timer] = None
        self._queue_lock = threading.RLock()

    # ----- 任务 CRUD -----
    def create_task(
        self,
        reconstruction_id: str,
        image_count: int,
        owner_id: Optional[str] = None,
        oss_images: Optional[list[str]] = None,
    ) -> TaskInfo:
        task_id = uuid.uuid4().hex[:12]
        task = TaskInfo(
            task_id=task_id,
            reconstruction_id=reconstruction_id,
            image_count=image_count,
            owner_id=owner_id,
            oss_images=list(oss_images or []),
        )
        self._tasks[task_id] = task
        self._subscribers[task_id] = []
        self._persist(task, force_queue=True)
        return task

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskInfo]:
        return sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )

    def list_tasks_for_user(
        self, user_id: str, is_admin: bool
    ) -> list[TaskInfo]:
        """普通用户只看自己的任务；管理员看全部（含历史无主任务）。"""
        tasks = self.list_tasks()
        if is_admin:
            return tasks
        return [t for t in tasks if t.owner_id == user_id]

    def can_access(self, task: TaskInfo, user_id: str, is_admin: bool) -> bool:
        if is_admin:
            return True
        return task.owner_id == user_id

    # ----- Worker（3090 轮询脚本）队列接口 -----
    def claim_next_task(self, worker_id: str) -> Optional[TaskInfo]:
        """
        领取下一个待处理任务（FIFO：最早创建的 pending 任务优先）。
        领取后任务状态置为 uploading（语义：worker 正在拉取图片），
        并记录领取时间 / worker 标识 / 尝试次数。

        同时回收孤儿任务：uploading 状态超过 ORPHAN_TIMEOUT 秒没有心跳，
        视为原 worker 已死（进程崩 / OOM / 网络断），自动释放回 pending，
        并由本次请求立即接管，避免"卡 uploading 永远跑不完"。
        """
        # 拉图阶段正常应秒级完成；OSS 大图偶尔 1-2 分钟，5 分钟是安全余量
        ORPHAN_TIMEOUT = 300
        now = time.time()

        for task in sorted(
            self._tasks.values(), key=lambda t: t.created_at
        ):
            # ----- 孤儿回收：uploading 超时无心跳 -----
            if task.status == TaskStatus.UPLOADING:
                last_hb = (
                    task.heartbeat_at
                    or task.claimed_at
                    or task.created_at
                    or now
                )
                idle = now - last_hb
                if idle < ORPHAN_TIMEOUT:
                    continue  # 还在跑，别动
                old_worker = task.claimed_by or "?"
                task.claimed_by = None
                task.claimed_at = None
                task.heartbeat_at = None
                task.attempts = (task.attempts or 0) + 1
                task.update(
                    status=TaskStatus.PENDING,
                    progress=0,
                    log_line=(
                        f"[rescue] 原 worker {old_worker} 失联 "
                        f"{int(idle)}秒，强制释放并重新领取"
                    ),
                )
                # 不 continue——下面的 PENDING 领取逻辑会立即接管它

            if task.status != TaskStatus.PENDING:
                continue
            # OSS 直链等待：仅当 OSS 已配置时才需要等直链生成。
            #   - oss_images 非空 → OSS 已上传完成，立即领取
            #   - oss_images 为空 且年龄 < OSS_WAIT_TIMEOUT → 跳过，继续等上传完成
            #   - oss_images 为空 且年龄 >= OSS_WAIT_TIMEOUT → 兜底领取，worker 走服务器下载
            # OSS 未配置时 oss_images 恒为空，直接领取，不做任何等待。
            OSS_WAIT_TIMEOUT = 600  # 10 分钟兜底，防止 OSS 上传失败导致任务永久卡死
            if settings.oss_enabled and not (task.oss_images or []):
                age = now - (task.created_at or now)
                if age < OSS_WAIT_TIMEOUT:
                    continue
            task.claimed_by = worker_id
            task.claimed_at = now
            task.started_at = now
            task.attempts = (task.attempts or 0) + 1
            task.update(
                status=TaskStatus.UPLOADING,
                progress=0,
                current_step=f"i18n:step.workerClaimed|{worker_id}",
                log_line=f"[worker:{worker_id}] 已领取任务，开始拉取图片",
            )
            return task
        return None

    def publish_pending(self, task: TaskInfo) -> None:
        """把 pending 任务信封写到 OSS，供 3090 领取。"""
        if settings.DEBUG_MODE or task.status != TaskStatus.PENDING:
            return
        try:
            from .oss_manager import get_oss_manager
            oss = get_oss_manager()
            if not oss.is_enabled():
                return
            if oss.queue_object_exists(oss.claimed_key(task.task_id)):
                return
            oss.put_queue_json(oss.pending_key(task.task_id), task.to_dict())
            logger.info("[OSS queue] 已发布 pending %s", task.task_id)
        except Exception:
            logger.exception("[OSS queue] 发布 pending 失败: %s", task.task_id)

    def republish_pending_to_oss(self) -> None:
        """启动时把本地仍 pending 的任务补写到 OSS。"""
        pending = [
            t for t in self.list_tasks() if t.status == TaskStatus.PENDING
        ]
        for task in pending:
            self.publish_pending(task)
        ids = ", ".join(t.task_id for t in pending) or "(无)"
        logger.info(
            "[OSS queue] 启动补写 pending 完成，共 %s 个: %s",
            len(pending),
            ids,
        )

    def apply_oss_status(self, data: dict[str, Any]) -> Optional[TaskInfo]:
        """把 worker 写回 OSS 的 status JSON 合并进本地任务。"""
        task_id = str(data.get("task_id") or "")
        task = self._tasks.get(task_id)
        if task is None:
            return None
        status = None
        status_str = data.get("status")
        if status_str:
            try:
                status = TaskStatus(status_str)
            except ValueError:
                status = None
        extra: dict[str, Any] = {}
        for key in _OSS_SYNC_FIELDS:
            if key in data:
                extra[key] = data[key]
        if isinstance(data.get("metrics"), dict) and data["metrics"]:
            extra["metrics"] = data["metrics"]
        if isinstance(data.get("stage_timings"), dict) and data["stage_timings"]:
            extra["stage_timings"] = {
                **task.stage_timings,
                **data["stage_timings"],
            }
        new_progress = data.get("progress") if "progress" in data else None
        new_step = data.get("current_step")
        same_status = status is None or status == task.status
        same_progress = new_progress is None or new_progress == task.progress
        same_hb = data.get("heartbeat_at") == task.heartbeat_at
        same_url = data.get("oss_url") == task.oss_url
        if same_status and same_progress and same_hb and same_url:
            return task
        if isinstance(data.get("log_lines"), list):
            task.log_lines = [str(x) for x in data["log_lines"]][-2000:]
        task.update(
            status=status,
            progress=new_progress,
            current_step=new_step,
            **extra,
        )
        return task

    def apply_remote_claim(self, data: dict[str, Any]) -> Optional[TaskInfo]:
        """HTTP 兜底领取走 OSS 原子 claim 后，把结果套到本地任务。"""
        return self.apply_oss_status(data)

    def _rescue_oss_orphans(self) -> None:
        from .oss_manager import get_oss_manager
        oss = get_oss_manager()
        now = time.time()
        for key in oss.list_queue_keys("claimed"):
            task_id = oss.task_id_from_queue_key(key)
            status_data = oss.get_queue_json(oss.status_key(task_id))
            claimed_data = oss.get_queue_json(key) or {}
            data = status_data or claimed_data
            if not data:
                continue
            last_hb = (
                data.get("heartbeat_at")
                or data.get("claimed_at")
                or data.get("updated_at")
                or now
            )
            try:
                idle = now - float(last_hb)
            except (TypeError, ValueError):
                idle = OSS_ORPHAN_TIMEOUT + 1
            if idle < OSS_ORPHAN_TIMEOUT:
                continue
            old_worker = data.get("claimed_by") or "?"
            data["claimed_by"] = None
            data["claimed_at"] = None
            data["heartbeat_at"] = None
            data["status"] = "pending"
            data["progress"] = 0
            logs = list(data.get("log_lines") or [])
            logs.append(
                f"[rescue] 原 worker {old_worker} 失联 {int(idle)}秒，"
                f"强制释放并重新领取"
            )
            data["log_lines"] = logs[-200:]
            try:
                oss.put_queue_json(oss.pending_key(task_id), data)
                oss.delete_queue_key(key)
                oss.put_queue_json(oss.status_key(task_id), data)
            except Exception:
                logger.exception("[OSS queue] 孤儿回收失败: %s", task_id)
                continue
            self.apply_oss_status(data)
            logger.warning("[OSS queue] 已释放孤儿任务 %s", task_id)

    def sync_from_oss_queue(self) -> None:
        if settings.DEBUG_MODE:
            return
        from .oss_manager import get_oss_manager
        oss = get_oss_manager()
        if not oss.is_enabled():
            return
        self._rescue_oss_orphans()
        for key in oss.list_queue_keys("status"):
            data = oss.get_queue_json(key)
            if data:
                self.apply_oss_status(data)

    def oss_sync_loop(self) -> None:
        while True:
            try:
                self.sync_from_oss_queue()
            except Exception:
                logger.exception("[OSS queue] 同步失败")
            time.sleep(OSS_SYNC_INTERVAL)

    def report_progress(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        current_step: Optional[str] = None,
        log_line: Optional[str] = None,
        stage_timings: Optional[dict] = None,
        gpu_memory: Optional[dict] = None,
    ) -> Optional[TaskInfo]:
        """Worker 上报任务进度。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.heartbeat_at = time.time()
        extra: dict[str, Any] = {"heartbeat_at": task.heartbeat_at}
        if stage_timings:
            # 合并阶段耗时（worker 侧累计的结果）
            for stage, timing in stage_timings.items():
                if isinstance(timing, dict) and timing:
                    task.stage_timings.setdefault(stage, {}).update(timing)
        if gpu_memory is not None:
            extra["gpu_memory"] = gpu_memory
        task.update(
            status=status,
            progress=progress,
            current_step=current_step,
            log_line=log_line,
            **extra,
        )
        return task

    def report_complete(
        self,
        task_id: str,
        oss_url: Optional[str] = None,
        metrics: Optional[dict] = None,
        stage_timings: Optional[dict] = None,
        remote_output_folder: Optional[str] = None,
        gpu_memory: Optional[dict] = None,
    ) -> Optional[TaskInfo]:
        """Worker 上报任务成功完成。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.finished_at = time.time()
        extra: dict[str, Any] = {"finished_at": task.finished_at}
        if oss_url:
            extra["oss_url"] = oss_url
        if metrics:
            extra["metrics"] = metrics
        if remote_output_folder:
            extra["remote_output_folder"] = remote_output_folder
        if gpu_memory is not None:
            extra["gpu_memory"] = gpu_memory
        if stage_timings:
            for stage, timing in stage_timings.items():
                if isinstance(timing, dict) and timing:
                    task.stage_timings.setdefault(stage, {}).update(timing)
        task.update(
            status=TaskStatus.COMPLETED,
            progress=100,
            current_step="i18n:step.completed",
            log_line="[worker] 重建完成，结果已就绪",
            **extra,
        )
        return task

    def report_fail(self, task_id: str, error: str) -> Optional[TaskInfo]:
        """Worker 上报任务失败。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.finished_at = time.time()
        task.update(
            status=TaskStatus.FAILED,
            # progress 保持失败前的位置，不置 100（前端失败态显示失败前进度，
            # 避免"失败的任务进度条却是满格"的误导）
            progress=None,
            current_step=f"i18n:step.failed|{error}",
            error=error,
            log_line=f"[ERROR] [worker] {error}",
            finished_at=task.finished_at,
        )
        return task

    # ----- 订阅 / 通知 -----
    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        if task_id in self._subscribers:
            try:
                self._subscribers[task_id].remove(queue)
            except ValueError:
                pass

    def _notify(self, task: TaskInfo, force_queue: bool = False) -> None:
        """向所有订阅者推送任务更新。"""
        self._persist(task, force_queue=force_queue)
        message = json.dumps(task.to_dict(), ensure_ascii=False)
        dead: list[asyncio.Queue] = []
        for queue in self._subscribers.get(task.task_id, []):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.unsubscribe(task.task_id, queue)

    # ----- 持久化（简单 JSON 落盘，便于重启恢复）-----
    def _persist(self, task: TaskInfo, force_queue: bool = False) -> None:
        try:
            path = settings.LOCAL_STORAGE_DIR / "tasks" / f"{task.task_id}.json"
            self._atomic_write(path, json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        except Exception:
            pass  # 持久化失败不影响主流程
        # 任务 JSON 每次都写；三份 queue 文件防抖，避免 COLMAP 逐行日志阻塞事件循环
        try:
            self._schedule_queue_sync(force=force_queue)
        except Exception:
            pass

    def _cancel_queue_timer_locked(self) -> None:
        if self._queue_timer is not None:
            try:
                self._queue_timer.cancel()
            except Exception:
                pass
            self._queue_timer = None

    def _flush_queue_timer(self) -> None:
        with self._queue_lock:
            self._queue_timer = None
            if not self._queue_dirty:
                return
            try:
                self._sync_queue_files_unlocked()
            except Exception:
                pass
            self._queue_last_sync = time.time()
            self._queue_dirty = False

    def _schedule_queue_sync(self, force: bool = False) -> None:
        with self._queue_lock:
            if force:
                self._cancel_queue_timer_locked()
                self._sync_queue_files_unlocked()
                self._queue_last_sync = time.time()
                self._queue_dirty = False
                return
            now = time.time()
            if now - self._queue_last_sync >= self.QUEUE_SYNC_DEBOUNCE:
                self._cancel_queue_timer_locked()
                self._sync_queue_files_unlocked()
                self._queue_last_sync = now
                self._queue_dirty = False
                return
            self._queue_dirty = True
            if self._queue_timer is None:
                delay = max(
                    0.1,
                    self.QUEUE_SYNC_DEBOUNCE - (now - self._queue_last_sync),
                )
                timer = threading.Timer(delay, self._flush_queue_timer)
                timer.daemon = True
                self._queue_timer = timer
                timer.start()

    @staticmethod
    def _atomic_write(path, content: str) -> None:
        """原子写文件：先写临时文件再重命名，避免并发读半截。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        finally:
            # 清理临时文件：部分环境（如沙箱）会拦截 os.remove，
            # 失败时归档到 trash/，避免 tmp 残留堆积
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    try:
                        trash_dir = path.parent.parent / "trash"
                        trash_dir.mkdir(parents=True, exist_ok=True)
                        os.rename(tmp, trash_dir / os.path.basename(tmp))
                    except OSError:
                        pass

    def _sync_queue_files(self) -> None:
        """
        同步三份联动记录文件到 storage/queue/：
          tasks.json     -> 任务清单（全量概要）
          progress.json  -> 过程记录（详细实时状态，含 跑完/在跑/未跑/报错）
          completed.json -> 完成归档（仅已结束任务，成功带结果地址）
        这三份文件是前端页面与 3090 worker 之间共享的「通道」，
        通过 GET /api/queue/* 暴露给前端，worker 也经 API 读写同一份数据。
        """
        with self._queue_lock:
            self._sync_queue_files_unlocked()

    def _sync_queue_files_unlocked(self) -> None:
        queue_dir = settings.LOCAL_STORAGE_DIR / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        all_tasks = [t.to_dict() for t in self.list_tasks()]

        # 1) 任务清单（初始/总览）
        tasks_file = {
            "updated_at": now,
            "total": len(all_tasks),
            "summary": {
                "pending": sum(1 for t in all_tasks if t["status"] == "pending"),
                "running": sum(
                    1 for t in all_tasks if t["status"] not in ("pending", "completed", "failed")
                ),
                "completed": sum(1 for t in all_tasks if t["status"] == "completed"),
                "failed": sum(1 for t in all_tasks if t["status"] == "failed"),
            },
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "reconstruction_id": t["reconstruction_id"],
                    "status": t["status"],
                    "progress": t["progress"],
                    "current_step": t["current_step"],
                    "image_count": t["image_count"],
                    "train_params": t["train_params"],
                    "created_at": t["created_at"],
                    "started_at": t.get("started_at"),
                    "claimed_at": t.get("claimed_at"),
                    "claimed_by": t.get("claimed_by"),
                    "finished_at": t.get("finished_at"),
                    "oss_url": t.get("oss_url"),
                    "owner_id": t.get("owner_id"),
                    "error": t.get("error"),
                }
                for t in all_tasks
            ],
        }
        self._atomic_write(
            queue_dir / "tasks.json",
            json.dumps(tasks_file, ensure_ascii=False, indent=2),
        )

        # 2) 过程记录（详细实时状态）
        progress_file = {
            "updated_at": now,
            "note": (
                "过程记录：pending=未开始；uploading/converting/training/"
                "rendering/downloading/oss_uploading=进行中；"
                "completed=成功；failed=失败（error 含报错信息）"
            ),
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "reconstruction_id": t["reconstruction_id"],
                    "status": t["status"],
                    "progress": t["progress"],
                    "current_step": t["current_step"],
                    "stage_timings": t["stage_timings"],
                    "total_duration": t.get("total_duration"),
                    "created_at": t["created_at"],
                    "started_at": t.get("started_at"),
                    "claimed_at": t.get("claimed_at"),
                    "claimed_by": t.get("claimed_by"),
                    "heartbeat_at": t.get("heartbeat_at"),
                    "finished_at": t.get("finished_at"),
                    "image_count": t["image_count"],
                    "attempts": t.get("attempts", 0),
                    "error": t.get("error"),
                }
                for t in all_tasks
            ],
        }
        self._atomic_write(
            queue_dir / "progress.json",
            json.dumps(progress_file, ensure_ascii=False, indent=2),
        )

        # 3) 完成归档（仅已结束任务）
        done = [
            t for t in all_tasks if t["status"] in ("completed", "failed")
        ]
        completed_file = {
            "updated_at": now,
            "total": len(done),
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "reconstruction_id": t["reconstruction_id"],
                    "status": t["status"],
                    "metrics": t["metrics"],
                    "stage_timings": t["stage_timings"],
                    "total_duration": t.get("total_duration"),
                    "oss_url": t.get("oss_url"),
                    "model_path": t.get("model_path"),
                    "remote_output_folder": t.get("remote_output_folder"),
                    "created_at": t["created_at"],
                    "started_at": t.get("started_at"),
                    "finished_at": t.get("finished_at"),
                    "error": t.get("error"),
                }
                for t in done
            ],
        }
        self._atomic_write(
            queue_dir / "completed.json",
            json.dumps(completed_file, ensure_ascii=False, indent=2),
        )

    def load_persisted(self) -> None:
        """启动时加载已持久化的任务记录。"""
        task_dir = settings.LOCAL_STORAGE_DIR / "tasks"
        if not task_dir.exists():
            return
        for path in task_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                status_str = data.get("status", "pending")
                try:
                    status = TaskStatus(status_str)
                except ValueError:
                    status = TaskStatus.FAILED
                task = TaskInfo(
                    task_id=data["task_id"],
                    reconstruction_id=data["reconstruction_id"],
                    status=status,
                    progress=data.get("progress", 0),
                    current_step=data.get("current_step", ""),
                    log_lines=data.get("log_lines", []),
                    created_at=data.get("created_at", time.time()),
                    updated_at=data.get("updated_at", time.time()),
                    image_count=data.get("image_count", 0),
                    model_path=data.get("model_path"),
                    remote_output_folder=data.get("remote_output_folder"),
                    error=data.get("error"),
                    stage_timings=data.get("stage_timings", {}),
                    train_params=data.get("train_params", {}),
                    metrics=data.get("metrics", {}),
                    oss_url=data.get("oss_url"),
                    oss_images=data.get("oss_images", []),
                    claimed_by=data.get("claimed_by"),
                    claimed_at=data.get("claimed_at"),
                    started_at=data.get("started_at"),
                    heartbeat_at=data.get("heartbeat_at"),
                    attempts=data.get("attempts", 0),
                    finished_at=data.get("finished_at"),
                    owner_id=data.get("owner_id"),
                    gpu_memory=data.get("gpu_memory"),
                )
                self._tasks[task.task_id] = task
                self._subscribers[task.task_id] = []
            except Exception:
                continue
        # 启动后重建三份联动文件
        try:
            self._schedule_queue_sync(force=True)
        except Exception:
            pass


# 全局单例
_manager = TaskManager()


def get_task_manager() -> TaskManager:
    return _manager


def compute_stage_progress(status: TaskStatus, step_progress: int) -> int:
    """
    根据当前阶段及阶段内进度（0-100）计算整体进度（0-100）。
    每个阶段在整体中占比均匀分布。
    """
    if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        return 100
    if status == TaskStatus.PENDING:
        return 0
    try:
        idx = STAGE_ORDER.index(status)
    except ValueError:
        return 0
    stage_weight = 100 / len(STAGE_ORDER)
    base = stage_weight * idx
    return int(base + stage_weight * (step_progress / 100))
