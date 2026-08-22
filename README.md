# 消防监督检查 AI 辅助系统

以"一次检查"为核心：AI 从检查视频/照片中整理**事实 + 证据**，人工确认后检索法规、生成三类文书初稿（监督检查记录表 / 照片文书 / 询问笔录）并填入 Word 模板导出，全程右侧 AI 助手伴随。

实现依据：`docs/PRD.md`（需求基线）+ `docs/PLAN.md`（实现计划，含技术选型论证与阶段验收口径）。

## 快速开始（开发）

前置：Python 3.12+、Node 22+ / pnpm、ffmpeg、Docker（本地 PostgreSQL）。

```bash
# 1. 数据库（pgvector）
docker run -d --name fire-inspect-db -p 5432:5432 \
  -e POSTGRES_USER=fire -e POSTGRES_PASSWORD=fire -e POSTGRES_DB=fire_inspect \
  pgvector/pgvector:pg16

# 2. 配置 AI 端点（五组均可独立配置，见 .env.example）
cp .env.example .env   # 填入内网网关地址与密钥

# 3. 后端
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.seed          # 初始化：测试账号 + 消防法条款 + 三份预置模板
.venv/bin/uvicorn app.main:app --port 8000

# 4. 前端（另开终端；开发热更新）
cd frontend && pnpm install && pnpm dev        # http://localhost:5173（代理 /api → 8000）
# 或构建后由后端统一伺服（生产形态）：
pnpm build                                    # 之后访问 http://localhost:8000
```

测试账号：`admin / admin123`（管理员）、`inspector / inspector123`（检查员）。

## 部署（内网单机，docker compose）

```bash
# 外网机器上构建并导出镜像
docker compose -f deploy/docker-compose.yml build
docker save fire-inspect-app pgvector/pgvector:pg16 | gzip > fire-inspect-images.tar.gz

# 内网机器
docker load < fire-inspect-images.tar.gz
cp .env.example .env   # 配置内网 AI 端点
docker compose -f deploy/docker-compose.yml up -d
```

- 数据持久化：`pgdata`（数据库）+ `appdata`（媒体文件/导出文书）两个卷。
- 零公网依赖：前端产物内嵌 app 镜像；AI 端点全部走 `.env` 配置，可指向任意内网 OpenAI 兼容服务。
- 重置验收环境：进入 app 容器执行 `python -m app.seed --reset`（清库+清文件+重新初始化）。

## 端到端冒烟

```bash
cd backend && .venv/bin/python -m tests.e2e_smoke   # 需服务运行于 :8000，且 testdata/videos/1.mp4 存在
```

覆盖：登录 → 建检查 → 传视频 → 任务链（转写双跑/关键帧/事实提取）→ 事实确认 → 法规检索 → 三类文书生成导出 → XML 级校验（打勾/下划线/照片区块/问答正文）→ 事实变更退回文书 → AI 助手。

## 目录

```
backend/    FastAPI 应用（api/ 路由、domain/ 业务、workers/ 任务执行器、core/ 基础设施）
frontend/   Vue 3 + Element Plus SPA（构建产物由后端伺服，SPA fallback 刷新不 404）
deploy/     Dockerfile.app + docker-compose.yml
docs/       PRD / PLAN / 模板分析 / ASR 验证
testdata/   种子与验收数据（videos/ 不进 git）
```

## 设计要点（详见 docs/PLAN.md）

- **任务状态机**：DB 任务表 + 进程内 worker；转写/抽帧/事实提取逐步独立，失败显示原因可重试，不丢原始材料。
- **转写双跑**（ASR-VERIFICATION §4）：整段转写供事实提取/笔录正文；10s 窗 + 1s 重叠分段供时间锚点回溯。
- **模板识别**：确定性规则引擎（TEMPLATE-ANALYSIS §4.3 六条规则），三份验收模板 100% 覆盖；映射人工确认后固化。
- **填充样式**：笔录/照片文书值带下划线；记录表检查类型 □→☑；照片按帧整块纵向复制；页脚域打开时自动刷新。
- **留痕**：事实修改、文书确认/退回均记录操作人/时间/旧值→新值，可查历史。
- **降级**：AI 端点不可用时任务失败可重试、原始数据完好、人工整理能力保留。

## 与 PLAN 的实施偏差记录

- **数据库迁移**：PLAN §1.1 写 SQLAlchemy + Alembic；MVP 实现为启动时幂等 `CREATE EXTENSION + create_all`（`app/core/db.py: init_db`）。单库单实例内网部署下无迁移编排需求；后续引入多版本升级时补 Alembic。
- **关键帧模糊阈值**：初值 60 在手机实拍视频上误杀全部帧（实测清晰度 5–93），调整为 15 并加"至少保留 min_keep 帧"兜底（`core/config.py`）。
- **询问笔录页脚**：模板实测为 NUMPAGES 域缓存值陈旧（非 TEMPLATE-ANALYSIS 所述字面硬编码），实现为 `updateFields` 强制刷新 + 字面数字兜底替换双保险（`domain/docx_fill.py`）。
