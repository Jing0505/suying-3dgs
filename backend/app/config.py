"""
溯影 3DGS 自动化重建平台 - 后端配置
通过 .env 文件或环境变量进行配置，详见 .env.example
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（若存在）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    """全局配置项，集中读取环境变量并提供默认值。"""

    # ----- 本地存储 -----
    _raw_local_storage_dir = os.getenv("LOCAL_STORAGE_DIR", "./storage")
    _local_storage_dir = Path(_raw_local_storage_dir)
    if not _local_storage_dir.is_absolute():
        # .env 中的默认值和相对值都需要以后端项目根目录为基准解析，
        # 不应按 VS Code/终端当前工作目录解析。
        _local_storage_dir = (
            Path(__file__).resolve().parent.parent / _local_storage_dir
        )
    LOCAL_STORAGE_DIR: Path = _local_storage_dir.resolve()

    # ----- 重建参数 -----
    USE_GPU: bool = os.getenv("USE_GPU", "false").lower() == "true"
    BACKEND_HOST: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8010"))

    # ----- CORS 跨域：逗号分隔域名列表，* 或空表示允许全部 -----
    _raw_cors = os.getenv("CORS_ORIGINS", "*").strip()
    if not _raw_cors or _raw_cors == "*":
        CORS_ORIGINS: list[str] = ["*"]
    else:
        CORS_ORIGINS = [o.strip() for o in _raw_cors.split(",") if o.strip()]

    # ----- 调试模式：不连接 3090，模拟整个流程 -----
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"

    # ----- Worker（3090 轮询脚本）接入配置 -----
    # 3090 上部署的 worker 调用 /api/worker/* 接口时需要携带的 Bearer Token。
    # 留空则跳过鉴权（仅建议在内网或调试环境使用）。
    WORKER_API_KEY: str = os.getenv("WORKER_API_KEY", "suying-3dgs-worker-2024")

    # ----- 用户鉴权（JWT，账号仅管理员开号）-----
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "168"))
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    # ----- 上传配额（防止公网薅 OSS）-----
    # 单文件大小上限（MB）
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
    # 单任务最多图片张数
    MAX_IMAGES_PER_TASK: int = int(os.getenv("MAX_IMAGES_PER_TASK", "200"))
    # 每用户每天最多创建任务数
    DAILY_TASK_LIMIT: int = int(os.getenv("DAILY_TASK_LIMIT", "20"))
    # 每用户每天最多上传字节数（默认 2GB）
    DAILY_UPLOAD_BYTES: int = int(os.getenv("DAILY_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
    # 上传凭证接口限流：每用户每分钟次数
    POLICY_RATE_LIMIT_PER_MIN: int = int(os.getenv("POLICY_RATE_LIMIT_PER_MIN", "10"))
    # Post Policy 有效期（秒）
    OSS_POLICY_EXPIRE_SECONDS: int = int(os.getenv("OSS_POLICY_EXPIRE_SECONDS", "900"))

    # ----- 阿里云 OSS（上传图片与 ply 模型，生成公开访问链接）-----
    OSS_ACCESS_KEY_ID: str = os.getenv("OSS_ACCESS_KEY_ID", "")
    OSS_ACCESS_KEY_SECRET: str = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    OSS_BUCKET: str = os.getenv("OSS_BUCKET", "suying-3dgs")
    OSS_ENDPOINT: str = os.getenv("OSS_ENDPOINT", "oss-cn-shenzhen.aliyuncs.com")
    # OSS 中图片对象名的前缀目录，如 images/{user_id}/{recon_id}/001.jpg
    OSS_IMAGE_PREFIX: str = os.getenv("OSS_IMAGE_PREFIX", "images")
    # OSS 中模型对象名的前缀目录，如 output/recon_xxx/recon_xxx.ply
    OSS_OUTPUT_PREFIX: str = os.getenv("OSS_OUTPUT_PREFIX", "output")
    # 3090 worker 领取/进度/完成的私有队列前缀（对象 ACL=private）
    OSS_QUEUE_PREFIX: str = os.getenv("OSS_QUEUE_PREFIX", "queue")

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def oss_enabled(self) -> bool:
        """是否已配置可用的 OSS 凭据。"""
        return bool(
            self.OSS_ACCESS_KEY_ID
            and self.OSS_ACCESS_KEY_SECRET
            and self.OSS_BUCKET
            and self.OSS_ENDPOINT
        )


settings = Settings()

# 启动时初始化本地存储目录
settings.LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
(settings.LOCAL_STORAGE_DIR / "uploads").mkdir(exist_ok=True)
(settings.LOCAL_STORAGE_DIR / "models").mkdir(exist_ok=True)
(settings.LOCAL_STORAGE_DIR / "tasks").mkdir(exist_ok=True)
(settings.LOCAL_STORAGE_DIR / "quotas").mkdir(exist_ok=True)
