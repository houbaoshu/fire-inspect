# 配置说明

应用只读取进程环境变量，不主动读取 `.env`，也不会在日志或 API 中回显密钥。Docker Compose 自身默认会查找 `.env`，生产运行前必须设置 `COMPOSE_DISABLE_ENV_FILE=1`，再由单位的系统服务或容器秘密管理机制把秘密注入父进程。不要把 `docker compose config` 的完整输出重定向到文件或采集进日志；只运行 `docker compose config --quiet` 做语法和必填项校验。

## 基础配置

| 变量 | 说明 |
|---|---|
| `FIRE_INSPECT_ENV=production` | 启用生产启动门槛 |
| `FIRE_INSPECT_SECRET_KEY` | 至少 50 字符的强随机值 |
| `FIRE_INSPECT_ALLOWED_HOSTS` | 逗号分隔的明确内网域名；不能用 `*` |
| `FIRE_INSPECT_PUBLIC_BASE_URL` | 必须为 `https://` 基址 |
| `FIRE_INSPECT_CSRF_TRUSTED_ORIGINS` | 与实际 HTTPS 来源一致 |
| `POSTGRES_ADMIN_PASSWORD` | 仅数据库首次初始化使用的 bootstrap 管理密码；Web/Worker 不得获得 |
| `FIRE_INSPECT_DB_MIGRATION_PASSWORD` | `fire_inspect_migrator` 的密码；只注入迁移和恢复服务 |
| `FIRE_INSPECT_DB_RUNTIME_PASSWORD` | `fire_inspect_runtime` 的密码；只注入 Web、Worker 和只读一致备份任务 |
| `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD` | 非 Compose 或一次性服务使用的离散 PostgreSQL 连接参数；密码无需 URL 编码 |
| `FIRE_INSPECT_DATABASE_URL` | 非 Compose 环境的兼容入口；若使用，用户名和密码中的 URL 保留字符必须百分号编码 |
| `FIRE_INSPECT_DATA_ROOT` | 受控、不可执行且有备份的数据目录 |
| `FIRE_INSPECT_MAX_UPLOAD_BYTES` | 单文件上限，默认 512 MiB |
| `FIRE_INSPECT_MAX_INSPECTION_BYTES` | 单检查材料总量，默认 2 GiB |
| `FIRE_INSPECT_MAX_MATERIALS_PER_INSPECTION` | 单检查材料数量，默认 50 |
| `FIRE_INSPECT_MAX_MEDIA_DURATION_MS` | 单个视频或音频时长上限（毫秒），默认 7,200,000（2 小时），必须为正整数 |
| `FIRE_INSPECT_MAX_CONCURRENT_UPLOADS` | 单应用实例允许的并发上传数，默认 4；使用跨进程文件锁，Nginx 仍另行执行每客户端连接限制 |
| `FIRE_INSPECT_WORKER_CONCURRENCY` | Worker 并发槽，默认 2 |
| `FIRE_INSPECT_JOB_LEASE_SECONDS` | 任务租约秒数，默认 120；应大于一次心跳间隔 |
| `FIRE_INSPECT_MIN_FREE_BYTES` | 就绪检查要求的数据盘最小剩余空间，默认 1 GiB |
| `FIRE_INSPECT_WEB_WORKERS` | Gunicorn Web 进程数，默认 3 |
| `FIRE_INSPECT_WEB_TIMEOUT` | 大文件上传请求硬超时，默认 600 秒 |
| `FIRE_INSPECT_WEB_GRACEFUL_TIMEOUT` | Web 优雅退出等待时间，默认 30 秒 |
| `FIRE_INSPECT_COOKIE_SECURE` | 生产预检必须为真；Compose 默认开启 |
| `FIRE_INSPECT_REQUIRED_FONTS` | 生产 Compose 固定为三份权威模板所需字体族；缺少任一项拒绝启动 |

三组数据库密码分别注入 bootstrap、迁移和运行时职责，不能复用。实现不会把密码拼接进数据库 URL，因此 `@:/?#%+` 等 URL 保留字符可以直接用于强随机密码。数据库密码至少 16 字符且不能使用示例占位值；应用密钥至少 50 字符并需要足够字符多样性。全部秘密都应由受保护的秘密管理器生成、注入和轮换，不要写入 `.env`、Compose 覆盖文件、命令行参数、备份或工单。

数据库首次初始化后，`fire_inspect_migrator` 持有业务数据库和 `public` schema，只在迁移、权限重配置和空环境恢复时使用；`fire_inspect_runtime` 是非超级用户，只获得普通业务表所需 CRUD。永久审计表对运行时角色只开放 `SELECT/INSERT`，同时有数据库触发器拒绝 `UPDATE/DELETE/TRUNCATE`。生产预检会核对当前角色、表所有者、精确权限和防篡改触发器，任一不一致均拒绝启动。

上传限制必须覆盖单位实际素材，但不应简单取消。应用会在写入业务记录前校验并发槽，在媒体探测完成后校验真实时长；超限请求返回可理解错误且不保留半成品。多 Web 进程共享同一数据卷时，并发锁文件位于受控数据目录，因此进程间仍使用同一上限。Nginx 的 `limit_conn` 是额外的每客户端保护，不替代应用的全局上限。

## AI 能力

ASR、LLM、Vision、Embedding 分别使用下列同构变量；把 `<CAPABILITY>` 替换为 `ASR`、`LLM`、`VISION` 或 `EMBEDDING`：

- `FIRE_INSPECT_<CAPABILITY>_PROVIDER=disabled|http`
- `FIRE_INSPECT_<CAPABILITY>_ENDPOINT`
- `FIRE_INSPECT_<CAPABILITY>_MODEL`
- `FIRE_INSPECT_<CAPABILITY>_API_KEY`
- `FIRE_INSPECT_<CAPABILITY>_TIMEOUT`

每种能力可指向不同的内网网关和模型。`disabled` 保留人工路径；服务失败时只将对应步骤标记失败。`fixed` 仅供显式启用的非生产验收环境，生产启动拒绝使用。

## 字体

服务端文书预检需要宋体、仿宋、楷体、微软雅黑和方正小标宋的单位授权版本。方正等授权字体不随仓库或镜像分发；部署人员将字体只读放入被 Git 和镜像构建排除的 `deploy/fonts/`，容器挂载到 `/usr/local/share/fonts/fire-inspect/`。字体文件和父目录必须允许容器 UID `10001` 只读访问，不得授予写权限。Compose 显式配置 `FangSong,KaiTi,Microsoft YaHei UI,Microsoft YaHei,SimSun,FZXiaoBiaoSong-B05S` 门禁，Noto CJK 或其他替代字体不能冒充通过；字体缺失时 Web/Worker 拒绝启动并显示缺少的字体族。最终文书仍须在单位指定的 Windows + Word/WPS + 同版本字体环境更新域并逐页检查。

## 正式验收门槛

普通回归不读取 `.env`，也不会自动调用外部服务。以下变量只能通过当前进程环境显式提供；缺少任一项时命令以“NOT RUN”退出，不能算通过。

### 真实 AI

设置 `FIRE_INSPECT_REAL_AI_APPROVED=1`，并为 ASR、LLM、VISION、EMBEDDING 四类能力分别设置 `PROVIDER=http`、`ENDPOINT`、`MODEL` 和 `API_KEY`，然后运行 `make real-ai`。此门槛只保存模型/端点类别、输入哈希和脱敏结论，不保存完整媒体、转写或响应。不得把 `fixed` 用于正式交付结论。

本地没有内网 AI 网关时，可用 `scripts/dev_ai_gateway.py` 作为开发用翻译网关：它监听 127.0.0.1，把内部协议翻译成 DashScope OpenAI 兼容调用（ASR 走 qwen3-asr 系列，按 `ASR_WINDOW_SEC`/`ASR_OVERLAP_SEC` 分窗转写；视觉、事实抽取走多模态/对话模型；Embedding 走 text-embedding 系列）。启动方式：`set -a; source .env; set +a; uv run --frozen python scripts/dev_ai_gateway.py --port 8787`，然后把四类能力的 `ENDPOINT` 指向 `http://127.0.0.1:8787/`、`MODEL` 指向对应阿里模型。该网关只是验收工具，不是部署组件；注意它会将材料音频和关键帧发送到所配置的外部模型服务。

### 指定环境的 Word/WPS（环境变量沿用 windows-docx 名称）

1. 在非敏感输出目录运行 `uv run python scripts/generate_docx_acceptance_fixtures.py --output-dir <目录>`。
2. 把四份 DOCX 和 `manifest.generated.json` 送到单位指定的 Word/WPS 和字体环境（2026-08-26 确认为本机 macOS + Microsoft Word 16.112.1，字体为 FZXiaoBiaoSong-B05S、Songti SC、STFangsong、STKaiti；也可使用 Windows + Word/WPS + 六个 Windows 字体族）；更新全部域，以 100% 缩放逐页检查，并把每一页从该环境导出为 PNG 保存到输出目录的 `rendered/` 子目录。
3. 复制 [windows-docx-approval.example.json](windows-docx-approval.example.json)，将 `os` 填为 `Windows` 或 `macOS`，并填写 Word/WPS 精确版本、字体、复核人、DOCX SHA-256、逐页 PNG 路径及 SHA-256；询问笔录每页还要记录实际显示的当前页和总页数。按示例的 `页码:PNG哈希\n` 顺序计算 `visual_baseline_sha256`，首次审批的逐页图像即为后续确定性样本的视觉基线。
4. 设置 `FIRE_INSPECT_WINDOWS_DOCX_APPROVED=1`、`FIRE_INSPECT_WINDOWS_DOCX_OUTPUT_DIR=<目录>`、`FIRE_INSPECT_WINDOWS_DOCX_APPROVAL_FILE=<审批 JSON>`，运行 `make windows-docx`。测试会重新生成确定性样本，逐页解码并校验 PNG、页脚值和视觉基线哈希，拒绝与本次 DOCX 不绑定的旧审批。

### PostgreSQL 与恢复

`FIRE_INSPECT_POSTGRES_TEST_URL` 必须指向可创建临时数据库的隔离 PostgreSQL 17 实例，例如 `postgresql://<test-user>@<host>/<empty-test-db>`。测试环境必须提供同主版本的 `pg_dump` 和 `pg_restore`。设置变量后运行 `make postgres`；该命令会创建并删除随机命名的恢复库，禁止指向生产实例。

### HTTPS 浏览器端到端

在清洁 Compose 环境初始化一个专用检查员账号后，通过当前进程设置：

- `FIRE_INSPECT_BROWSER_APPROVED=1`
- `FIRE_INSPECT_BROWSER_BASE_URL=https://<内网验收地址>`
- `FIRE_INSPECT_BROWSER_USERNAME=<专用检查员>`
- `FIRE_INSPECT_BROWSER_PASSWORD=<由秘密管理器临时注入>`
- `FIRE_INSPECT_BROWSER_EXECUTABLE=<本机 Chrome/Chromium 可执行文件绝对路径>`

运行 `make browser-e2e`。测试使用系统浏览器，不下载浏览器二进制；忽略仅限验收环境的自签名证书，但强制基址使用 HTTPS。浏览器会中止并报告所有非目标同源的网络请求，禁用 trace/video/screenshot，避免把登录秘密或业务页面写入普通测试工件。测试账号和清洁 Compose 卷在验收后删除。
