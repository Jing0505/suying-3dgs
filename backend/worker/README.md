# 溯影 3DGS Worker 部署指南

在 **3090 GPU 服务器**（Linux）上部署本目录下的 `worker.py`。生产路径下它**从阿里云 OSS 私有队列领取任务**，不需要连本机、也不需要反向 SSH。

## 一、架构回顾（OSS 队列拉模式）

```
[前端浏览器]
    │ 1. 直传图片到 OSS，POST /api/tasks 只交 URL
    ▼
[服务器 FastAPI]
    │ 2. 校验后写入 OSS queue/pending/{id}.json（private）
    │    本地 tasks.json / progress.json 供前端展示
    ▲
    │ 3. 后台同步 queue/status
[OSS Bucket]
    │    pending / claimed / status
    ▲
[3090 worker.py]
    │ 4. list pending → 原子写 claimed → 删 pending
    │ 5. 拉图片（oss_images 公开直链）
    │ 6. convert → train → render → metrics
    │ 7. ply 直传 OSS output/ → 写 status completed
    │ 8. 本地 worker_status.json + worker.log
```

未填写 `oss.*` 时回退 `POST /api/worker/claim`（需 3090 能访问本机 8010）。

## 二、部署步骤（在 3090 上执行）

### 1. 拷贝脚本

把整个 `backend/worker/` 目录传到 3090，例如：

```bash
scp -r worker user@3090-ip:/home/user/suying-worker/
```

### 2. 生成配置文件

```bash
cd /home/user/suying-worker
cp worker_config.example.json worker_config.json
vim worker_config.json
```

必填项（OSS 队列，推荐）：

| 字段 | 说明 |
|------|------|
| `oss.access_key_id` / `access_key_secret` / `bucket` / `endpoint` | 与服务器 `backend/.env` 一致；**只写在 3090 上，不要提交 git** |
| `oss.queue_prefix` | 默认 `queue`，与 `.env` 的 `OSS_QUEUE_PREFIX` 一致 |
| `project_root` | 3090 上 gaussian-splatting 项目目录（含 convert.py/train.py/render.py） |
| `data_root` | 图片数据根目录 |
| `output_root` | 模型输出根目录 |
| `conda_env` | conda 环境名（当前版本直接用系统 python 跑，保留兼容） |

HTTP 兜底（仅 OSS 未配置时）：

| 字段 | 说明 |
|------|------|
| `server_url` | 服务器地址，如 `http://1.2.3.4:8010` |
| `api_key` | 与服务器 `backend/.env` 里的 `WORKER_API_KEY` 一致 |

可选：

| 字段 | 说明 |
|------|------|
| `poll_interval` | 轮询间隔秒数，默认 30 |
| `use_gpu` | `convert.py` 是否用 GPU（默认 false，用 `--no_gpu`） |
| `keep_files` | 完成后是否保留本地图片/模型（默认 false=自动删除） |

也可以用环境变量覆盖：`WORKER_OSS_ACCESS_KEY_ID`、`WORKER_OSS_ACCESS_KEY_SECRET`、`WORKER_OSS_BUCKET`、`WORKER_OSS_ENDPOINT`、`WORKER_OSS_QUEUE_PREFIX`、`WORKER_SERVER_URL`、`WORKER_API_KEY`、`WORKER_ID`、`WORKER_POLL_INTERVAL` 等。

### 3. 运行

```bash
pip install oss2
# 前台常驻轮询（SSH 断开即停止）
python3 worker.py

# 只处理一个任务后退出（调试用）
python3 worker.py --once
```

### 4. 进程管理：查看 / 停止 / 后台运行

日常维护 worker 进程最常用三条命令（都在 3090 上执行）：

```bash
# 1. 查看 worker 是否在运行（第二行是 grep 自身，可忽略）
ps aux | grep worker.py

# 2. 停止 worker（把 XXX 替换为上一条命令看到的 PID）
kill XXX
#    若卡住杀不掉，再强制杀死：
kill -9 XXX

# 3. 后台运行（SSH 断开 / 关终端都不影响，日志写入 worker_console.log）
nohup python3 worker.py > worker_console.log 2>&1 &
```

> 提示：`nohup ... &` 启动后进程脱离终端，用 `tail -f worker_console.log` 实时看日志，按 `Ctrl+C` 退出 tail 即可（不会停掉 worker）。
> 想"脱离 / 接回同一个终端现场"可用 `screen`/`tmux`；想开机自启 + 崩溃自动重启见下节 systemd。

### 5. （推荐）systemd 服务

创建 `/etc/systemd/system/suying-worker.service`：

```ini
[Unit]
Description=Suying 3DGS Worker
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/user/suying-worker
ExecStart=/usr/bin/python3 /home/user/suying-worker/worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now suying-worker
```

## 三、本地过程记录

worker 运行期间会在同目录生成：

- **`worker_status.json`** — 每个任务的状态：`pending/uploading/.../completed/failed`、进度、开始/完成时间、阶段、报错信息、耗时。与服务器的 `progress.json` 内容呼应（worker 端视角）。
- **`worker.log`** — 完整运行日志（轮询、领取、命令输出、报错）。

## 四、常见问题

| 问题 | 排查 |
|------|------|
| 一直显示「无待处理任务」 | 本机后端是否已把 pending 写到 OSS？看后端日志 `[OSS queue] 已发布 pending`；3090 日志应是 `queue=OSS` 而不是「无法连接服务器」 |
| 启动报需要 oss2 | `pip install oss2` |
| 领取 409 / 领不到 | 是否有另一台 worker 已 claimed；或 `queue_prefix` 与 `OSS_QUEUE_PREFIX` 不一致 |
| 下载图片失败 | 任务 JSON 是否含 `oss_images` 公开直链；Bucket 图片需公共读 |
| 训练失败（CUDA 爆显存等） | 查看 `worker.log`；进度经 OSS status 同步到前端 |
| 结果如何到前端 | worker 直传 ply 到 OSS 后写 status；本机同步后前端加载 OSS 直链 |
