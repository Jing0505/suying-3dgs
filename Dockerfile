# ===== 阶段 1：构建前端 =====
FROM node:20-alpine AS frontend-builder

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ===== 阶段 2：运行时（后端 + nginx 托管前端） =====
FROM python:3.11-slim

# 安装 nginx（带重试）
RUN set -eux; \
    for i in 1 2 3; do \
        apt-get update && \
        apt-get install -y --no-install-recommends ca-certificates nginx && \
        break || sleep 3; \
    done; \
    rm -rf /var/lib/apt/lists/*; \
    # nginx 非 root 运行所需的可写目录
    mkdir -p /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi /var/log/nginx

WORKDIR /app

# 安装后端依赖（清华镜像加速）
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制后端代码
COPY backend/ ./
RUN mkdir -p /app/storage

# 复制前端构建产物
COPY --from=frontend-builder /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf

# 复制启动脚本
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 创建非 root 用户
RUN useradd -r -s /bin/false appuser && \
    chown -R appuser:appuser /app /usr/share/nginx/html /var/lib/nginx /var/log/nginx

EXPOSE 80

USER appuser

# 启动脚本：同时运行 uvicorn + nginx，互相监控
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
