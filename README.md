# 消防监督检查 AI 辅助系统

本仓库是依据 `docs/PRD.md`、`docs/ACCEPTANCE.md` 与三份原始 Word 模板从零实现的单机构内网 MVP。AI 只产生候选内容；事实、问答角色、法规适用性和正式文书均由检查人员确认。

## 本地开发

要求 Python 3.12、Node.js 22、FFmpeg/ffprobe、LibreOffice 与 Poppler。普通测试使用 SQLite 和固定响应，不需要真实 AI 或公网。

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
export FIRE_INSPECT_CSRF_TRUSTED_ORIGINS="http://127.0.0.1:5173"
uv sync --frozen --group dev
uv run python manage.py migrate
uv run python manage.py create_initial_admin
uv run python manage.py runserver
```

`create_initial_admin` 默认交互输入用户名，并通过隐藏输入读取、确认密码。自动化初始化时仅从显式指定的进程环境变量读取密码，例如：

```bash
uv run python manage.py create_initial_admin \
  --noinput \
  --username bootstrap-admin \
  --password-env FIRE_INSPECT_INITIAL_ADMIN_PASSWORD
```

请通过受保护的运行环境预先注入并在使用后清除该变量。命令不读取 `.env`，不提供默认账号或密码，也不会回显密码。

前端在另一个终端启动：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Vite 开发服务器使用 `http://127.0.0.1:5173`，所以上面的 Django 进程必须显式信任该本地 Origin；生产环境仍只配置实际的 HTTPS 同源地址。

系统没有默认账号、默认密码或生产密钥。真实 AI、真实视频和 Windows 文书验收必须通过显式测试入口运行。

## 测试入口

```bash
make fast
make local-acceptance
make real-ai
make windows-docx
make postgres
make browser-e2e
```

- `fast`：后端单元/API/安全/固定 AI/DOCX 结构，以及前端测试和构建。
- `local-acceptance`：显式使用本机授权视频与法规 PDF；不会把视频或关键帧复制进仓库。
- `real-ai`：只有四类端点环境变量齐全并显式授权时运行。
- `windows-docx`：在单位指定 Windows + Word/WPS + 字体环境运行。
- `postgres`：使用显式提供的隔离 PostgreSQL URL 验证双 Worker 领取、唯一约束和备份恢复。
- `browser-e2e`：使用系统 Chrome 访问已部署的 HTTPS 地址，阻断公网请求并验证登录、创建检查、材料上传和深层 SPA 路由刷新。

测试命令会把后端与前端 JUnit 写入 `test-results/`，并由精确测试节点生成
`test-results/acceptance-coverage.json`。报告对每条验收项及每个门槛明确标记
`PASSED`、`FAILED` 或 `NOT RUN`；文件存在本身不算证据，未运行也不能计为验收通过。
各外部门槛所需变量、审批文件和运行步骤见[配置说明](docs/CONFIGURATION.md)。

部署、配置、备份恢复与升级见 [部署说明](docs/DEPLOYMENT.md)、[配置说明](docs/CONFIGURATION.md) 和 [运维手册](docs/OPERATIONS.md)。
