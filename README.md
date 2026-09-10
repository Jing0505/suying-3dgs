# 溯影 · 3DGS 自动化重建平台

上传图片，自动完成 3D Gaussian Splatting 重建，生成可交互的 3D 模型。

拉模式任务队列：登录用户直传 OSS → 后端入队 → GPU Worker 轮询领取 → 跑流水线 → 回传结果。账号仅管理员开号，不开放注册。

***

## 核心功能

- **多方式上传**：选择图片、文件夹、ZIP 压缩包

- **步骤式流程**：选文件 → 配置编号 → 参数配置 → 确认提交

- **训练参数**：14 个可调参数（迭代次数、分辨率、致密化阈值等）

- **自动重建**：Worker 轮询领取任务，自动跑 3DGS 流水线（convert → train → render）

- **实时进度**：WebSocket 推送 + 阶段耗时统计

- **阿里云 OSS**：浏览器短期凭证直传，后端只落地址

- **用户鉴权**：JWT 登录、管理员开号、任务按 owner 隔离、每日配额

- **3D 模型查看**：内置渲染器，OSS 直链加载

- **多语言**：中文 / 英文切换

- **调试模式**：无需 Worker 即可模拟完整流程

***

## 快速启动

### 方式 A：Docker（生产部署，推荐）

```bash
# 1. 构建镜像
docker build -t suying-3dgs:latest .

# 2. 创建并启动容器
docker run -d --name suying-3dgs --restart unless-stopped -p 8010:80 \
  --env-file ./backend/.env \
  -v ./backend/storage:/app/storage \
  suying-3dgs:latest

# 3. 验证
curl http://127.0.0.1:8010/api/health
```

日常启动（容器已存在）：

```bash
docker start suying-3dgs
```

修改代码后重建：

```bash
docker rm -f suying-3dgs
docker build -t suying-3dgs:latest .
docker run -d --name suying-3dgs --restart unless-stopped -p 8010:80 \
  --env-file ./backend/.env \
  -v ./backend/storage:/app/storage \
  suying-3dgs:latest
```

仅改 `.env` 不必重建：

```bash
docker restart suying-3dgs
```

### 方式 B：本地开发（venv + Vite）

**后端**（窗口 1）：

```bash
cd backend
python -m venv suying-3dgs
suying-3dgs\Scripts\activate          # Windows
# source suying-3dgs/bin/activate     # Linux/macOS
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

**前端**（窗口 2）：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。

### GPU Worker 部署

把 `backend/worker/` 拷到 GPU 机器，配置 `worker_config.json`：

```json
{
  "worker_id": "gpu-1",
  "poll_interval": 30,
  "project_root": "<gaussian-splatting 项目路径>",
  "data_root": "<数据存储路径>",
  "output_root": "<模型输出路径>",
  "conda_env": "gaussian_splatting",
  "use_gpu": false,
  "oss": {
    "access_key_id": "<与后端 .env 一致>",
    "access_key_secret": "<与后端 .env 一致>",
    "bucket": "<与后端 .env 一致>",
    "endpoint": "<与后端 .env 一致>",
    "queue_prefix": "queue"
  }
}
```

启动：

```bash
cd <worker 目录>
pip install oss2
nohup python3 worker.py > worker.log 2>&1 &
tail -f worker.log
```

> `use_gpu` 在 headless 服务器必须设 `false`（COLMAP 走 CPU，否则 OpenGL 上下文失败）。
> Worker 详细说明见 `backend/worker/README.md`。

***

## 配置

### 后端 `backend/.env`

复制 `.env.example` 为 `.env` 后编辑：

```env
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8010
WORKER_API_KEY=<自定义密钥>
DEBUG_MODE=false
LOCAL_STORAGE_DIR=./storage

# 用户鉴权
JWT_SECRET=<足够长的随机串>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<首次启动播种管理员，已存在则不覆盖>

# 阿里云 OSS（可选，留空则走本地 multipart 兜底）
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_BUCKET=
OSS_ENDPOINT=oss-cn-XXX.aliyuncs.com
OSS_IMAGE_PREFIX=images
OSS_OUTPUT_PREFIX=output
OSS_QUEUE_PREFIX=queue
```

### 管理员账号

- **首次部署**：在 `.env` 设置 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，确保 `storage/users.json` 不存在，启动后端自动播种

- **改密码**：停后端 → 删 `storage/users.json` → 改 `.env` → 重启

- **开号**：管理员登录后，在「用户管理」页操作

### OSS 配置要点

- Bucket 设公共读、禁止公共写

- CORS 允许前端 Origin 的 `POST/HEAD/GET`，Headers 含 `Content-Type`、`Authorization`

- 队列对象由后端/worker 写成 private ACL

- 生产环境 `CORS_ORIGINS` 收紧为具体域名，不用 `*`

***

## 项目结构

```
suying-3dgs/
├── frontend/                  # React 18 + Vite 5 + Tailwind
├── backend/
│   ├── app/                   # FastAPI（任务状态机 + OSS + WebSocket）
│   ├── worker/                # GPU Worker 轮询脚本
│   ├── storage/               # 运行时数据（自动生成）
│   └── .env                   # 后端配置
├── Dockerfile                 # 全栈单镜像（nginx + 前端 dist + uvicorn）
├── entrypoint.sh              # 容器启动脚本
├── nginx.conf                 # 反代 + SPA 回退
└── README.md
```

***

## API 概览

完整 schema 见 `/openapi.json`，Swagger UI：`/docs`，ReDoc：`/redoc`。

| 方法       | 路径                       | 说明          |
| -------- | ------------------------ | ----------- |
| `POST`   | `/api/auth/login`        | 登录（返回 JWT）  |
| `GET`    | `/api/auth/me`           | 当前用户        |
| `POST`   | `/api/admin/users`       | 管理员开号       |
| `POST`   | `/api/oss/upload-policy` | 签发 OSS 直传凭证 |
| `POST`   | `/api/tasks`             | 创建任务        |
| `GET`    | `/api/tasks`             | 任务列表        |
| `GET`    | `/api/tasks/{id}`        | 任务详情        |
| `GET`    | `/api/tasks/{id}/model`  | 下载 PLY      |
| `DELETE` | `/api/tasks/{id}`        | 删除任务        |
| `WS`     | `/ws/tasks/{id}?token=`  | 实时进度        |
| `GET`    | `/api/health`            | 健康检查        |

***

## 架构

```
前端 → OSS 直传图片 → 后端入队 → OSS 私有队列
                                        ↓
                              Worker 轮询领取
                                        ↓
                    convert.py → train.py → render.py
                                        ↓
                              PLY 上传 OSS → 写 status
                                        ↓
                          前端凭 OSS 直链渲染 3D 模型
```

9 态状态机：`pending → uploading → converting → training → rendering → downloading → oss_uploading → completed / failed`。Worker 崩溃卡住的孤儿任务，5 分钟自动回收。

***

## 技术栈

- **前端**：React 18、TypeScript、Vite 5、Tailwind 3、@mkkellogg/gaussian-splats-3d、i18next

- **后端**：Python 3.10+（<3.14）、FastAPI、Uvicorn、oss2、WebSocket

- **Worker**：Python 3.10+、纯标准库（零三方依赖）

- **容器**：Docker、nginx、多阶段构建

