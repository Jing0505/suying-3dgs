#!/bin/sh
# 同时启动 uvicorn（后端，容器内部 8010）与 nginx（前端，80）
# 任一进程崩溃则杀掉另一个，容器退出（避免假活）

set -e

# 启动后端 uvicorn（仅监听容器内部）
uvicorn app.main:app --host 127.0.0.1 --port 8010 &
UV_PID=$!

# 启动 nginx（前台运行）
nginx -g 'daemon off;' &
NG_PID=$!

# 轮询监控，任一进程死亡则杀掉另一个并退出
while kill -0 $UV_PID 2>/dev/null && kill -0 $NG_PID 2>/dev/null; do
    sleep 1
done

echo '[entrypoint] one of the processes died, killing the other and exiting'
kill -TERM $UV_PID $NG_PID 2>/dev/null
wait 2>/dev/null
exit 1
