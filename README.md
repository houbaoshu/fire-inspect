# 消防监督检查 AI 辅助系统

这是依据 [`docs/PRD.md`](docs/PRD.md) 实现的 MVP V1。系统以“一次检查”为核心，将原始材料、候选事实、证据、法规引用和三类文书放在同一工作流中。

## 当前可运行闭环

1. 使用服务端会话登录，管理员创建检查人员账户。
2. 创建一次检查，填写或稍后补充单位、地址、时间和检查人员。
3. 上传视频、音频、照片或 TXT 说明；后台使用 FFmpeg 提取带时间点的转写与关键帧。
4. AI 将 transcript 整理为可核对的询问问答候选，并生成其他带有效证据 ID 的候选事实；用户可修改、确认、排除或人工补充。
5. 管理员上传 PDF、DOCX、TXT 知识文档；系统进行混合检索，回答保留原文、定位和来源文件。
6. 使用预置或用户上传的 DOCX 模板生成消防监督检查记录、照片类文书、询问笔录。
7. 在浏览器中编辑结构化字段。事实或基础信息变化时只标记相关字段，不覆盖人工内容。
8. 人工确认后导出可编辑 DOCX，并记录关键操作人和时间。

系统内置一份明确标记为“业务参考材料”的演示知识文档。它只用于验证检索链路，不是正式法规。

## 本地开发

要求：Python 3.12–3.14、Node.js 22+、FFmpeg/ffprobe。

```bash
uv sync
cp .env.example .env
```

开发环境默认可改用 SQLite、本地文件存储和同步任务，最少配置如下：

```dotenv
DATABASE_URL=sqlite:///./data/fire-inspect.sqlite3
STORAGE_BACKEND=local
STORAGE_ROOT=data/storage
TASK_MODE=inline
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=change-me-now
```

分别启动后端和前端：

```bash
uv run uvicorn app.main:app --app-dir backend --reload
cd frontend && npm install && npm run dev
```

打开 `http://localhost:5173`。首次登录使用 `.env` 中的管理员账号；部署前必须修改默认密码。

如果没有配置 AI 服务，系统仍会安全地提取媒体元数据、关键帧并支持人工事实、知识检索和文书导出，但不会伪造转写或候选事实。

## 私有部署

```bash
cp .env.example .env
# 修改密码、Cookie 和获准 AI 服务配置
docker compose -f deploy/docker-compose.yml up --build
```

打开 `http://localhost:8080`。部署包含 PostgreSQL + pgvector、Redis/RQ、MinIO、FastAPI worker 和 React/Nginx。API 容器启动时自动执行 Alembic 迁移；PostgreSQL 使用 pgvector 与 `pg_trgm` 进行混合排序。

生产要求：

- 设置强随机 `BOOTSTRAP_ADMIN_PASSWORD` 和 `S3_SECRET_KEY`；启用 HTTPS 后设置 `SESSION_COOKIE_SECURE=true`。
- 正式视频和业务文档不得发送到未经授权的第三方。AI 配置应指向获准的内网或外部 OpenAI-compatible 服务。
- `AI_CHAT_BASE_URL`、`AI_TRANSCRIPTION_BASE_URL`、`AI_EMBEDDING_BASE_URL` 可分别指向聊天、转写和向量服务的 API 版本根路径；未设置时回退到兼容配置 `AI_BASE_URL`。
- 阿里云语音识别支持 `qwen-audio-3.0-asr-flash`（业务空间 `/api/v1` 地址）和 `qwen3-asr-flash`（业务空间 `/compatible-mode/v1` 地址）；系统会根据模型选择原生 ASR 或兼容聊天接口。`qwen3-asr-flash-filetrans` 是异步文件接口，当前不支持。
- 新处理的音视频会自动从转写中提取问答候选；已处理材料可在“材料与证据”中点击“提取问答”补做。问答必须人工确认，确认后会自动刷新未人工编辑的询问笔录字段。
- 当前数据库向量列固定为 384 维，`AI_EMBEDDING_DIMENSIONS` 与向量模型输出必须设为 `384`。
- 正式投用前，用有权来源法规替换演示参考材料，并接入单位的正式 Word 模板。

## Word 模板约定

DOCX 使用单个文本片段内的 Jinja 占位符：

```text
{{ organization }}
{{ inspection_date }}
{{ facts }}
```

上传后系统扫描并校验字段。用户可配置字段显示名称、类型和生成说明，但不能在系统内改变占位符代码。AI 只生成字段值，模板的字体、表格、页边距和其他版式保持由 Word 决定。

三个经过渲染检查的技术样例位于 [`templates/sample`](templates/sample)。模板生成器为 [`scripts/create_sample_templates.py`](scripts/create_sample_templates.py)。

## 验证

```bash
uv run ruff check backend scripts
uv run pytest
cd frontend && npm run build
```

测试覆盖：用户隔离、AI 事实证据约束、知识引用回溯、短 MP4 关键帧提取、事实变更后的字段过期保护、三类模板注册、字段配置、人工编辑、单张及多张照片插入和 DOCX 导出。

## 目录

```text
backend/app/
  core/       配置、数据库、会话安全、文件存储
  modules/    REST API 与访问边界
  services/   AI、媒体、知识检索、文书和审计逻辑
  workers/    RQ 后台任务
frontend/src/ React 管理端
templates/    预置技术样例和演示参考材料
deploy/       私有部署配置
```

## 明确暂缓

语音法规问答、文书侧 AI 助手、任意无占位符 Word 自动识别、扫描版法规 OCR、实时执法记录仪、外部业务系统、SSO/审批流、复杂 RBAC、法规自动同步和多 Agent 均未纳入本版。
