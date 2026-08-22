# ---------- 阶段 1：构建前端 ----------
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---------- 阶段 2：应用镜像（含 ffmpeg，内嵌前端产物） ----------
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /build/dist ./static_frontend
# 前端产物位置通过环境变量指向（默认取仓库内 frontend/dist）
ENV FI_STATIC_DIR=/app/static_frontend
EXPOSE 8000
# 单进程：任务执行器为进程内 asyncio worker（PLAN §1.1.5），多进程需外置 broker，MVP 不引入
CMD ["sh", "-c", "python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
