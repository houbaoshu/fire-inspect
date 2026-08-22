# 消防监督检查 AI 辅助系统

面向消防监督检查人员的私有化 Web 应用。系统围绕“一次检查”整理原始材料、候选事实、证据、知识引用和三类 Word 文书；AI 结果始终需要人工确认。

## 本地运行

要求 Python 3.12+、Node.js 22+，处理真实音视频时还需要 FFmpeg，渲染 Word 预览时需要 LibreOffice。

```bash
cp .env.example .env
uv sync --group dev
uv run alembic upgrade head
uv run python -m backend.app.cli create-admin
uv run uvicorn backend.app.main:app --reload --port 8000
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 `http://localhost:5173`。开发默认使用 SQLite、本地对象存储、同步任务和确定性 Mock AI，因此无需外部服务即可跑通主流程。

## 内网部署

复制 `.env.example` 为 `.env`，修改 PostgreSQL/MinIO 密钥和模型配置后运行：

```bash
docker compose up --build
docker compose exec api python -m backend.app.cli create-admin
```

入口为 `http://localhost:8080`。Compose 使用 PostgreSQL 16 + pgvector、Redis、MinIO、独立 API/Worker 和 Nginx。对象桶不公开，所有资源获取都经过业务权限校验或短时签名地址。

## 核心约束

- AI 提取的事实只能进入候选状态，不能自动确认。
- 无知识原文时明确返回未找到依据，不生成法规或条款。
- Word 导出只修改管理员确认的模板区域，其他区域保持原样。
- 人工编辑的文书字段会锁定；重新生成先返回差异提案，用户接受后才替换。
- 助手只提供回答和导航动作，不执行事实确认、文书修改或正式提交。
- 原始材料与派生结果分离；处理步骤失败和重试不删除原件或其他成功结果。

## 验证

```bash
uv run pytest
uv run ruff check .
cd frontend && npm run build && npm run lint
cd frontend && npm run test:e2e
```

API 文档在后端运行后可通过 `http://localhost:8000/docs` 查看；前端类型可用 `npm run generate:api` 从 OpenAPI 重新生成。
