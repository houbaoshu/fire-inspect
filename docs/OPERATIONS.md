# 运维、备份、恢复和升级

以下命令均从已校验的离线交付包根目录运行。先设置 `COMPOSE_DISABLE_ENV_FILE=1`；生产秘密只通过受保护的父进程环境或容器秘密机制注入，不写入 `.env`、命令历史、仓库、备份或日志。只用 `docker compose config --quiet` 验证 Compose，禁止保存或采集会展开秘密的完整 `docker compose config` 输出。

## 日常检查

- `/health/live`：进程存活；
- `/health/ready`：数据库、数据目录、磁盘余量、FFmpeg、LibreOffice 和 Poppler 就绪；
- `docker compose ps`：`db`、`web`、`worker`、`nginx` 均处于预期状态；
- 管理员任务页：排队时间、尝试次数、安全错误与单步重试；
- 磁盘空间、连续失败、备份龄期和审计链校验；
- `docker compose exec -T web python manage.py verify_audit_chain`：永久审计摘要链校验。

普通日志不得包含密钥、Cookie、完整媒体、完整转写、完整 AI 请求或绝对数据路径。删除审计只保留操作人、时间、对象、原因和掩码摘要。

## PostgreSQL 17 版本门槛

生产数据库服务、`pg_dump` 和 `pg_restore` 必须使用相同的 PostgreSQL 17 主版本。当前 Compose 数据库和应用镜像内客户端均来自 PostgreSQL 17.6；只允许在 17.x 安全补丁版本内升级，不得在未演练时跨主版本备份或恢复。生产启动、备份和恢复脚本都会执行相同的机器门禁，不能只依赖人工观察版本字符串。

```bash
docker compose exec -T db psql -U fire_inspect_bootstrap -d fire_inspect -Atc 'SHOW server_version'
docker compose exec -T web pg_dump --version
docker compose exec -T web pg_restore --version
docker compose exec -T web python manage.py postgres_version_gate
docker compose exec -T web python manage.py production_preflight
```

服务端输出必须以 `17` 开头，两个客户端输出必须包含 `(PostgreSQL) 17`。版本不一致时停止备份、恢复或升级，先更换为 PostgreSQL 17 客户端/服务端组合。

`production_preflight` 还必须确认 Web 正以 `fire_inspect_runtime` 非超级用户连接，审计表由 `fire_inspect_migrator` 持有，运行时对永久审计只有 `SELECT/INSERT`，且 UPDATE/DELETE 与 TRUNCATE 两个数据库防篡改触发器均启用。可由受控管理员只抽查角色属性，不输出任何密码：

```bash
docker compose exec -T db psql -U fire_inspect_bootstrap -d fire_inspect -Atc \
  "SELECT rolname || ':' || rolsuper::text FROM pg_roles WHERE rolname IN ('fire_inspect_migrator','fire_inspect_runtime') ORDER BY rolname"
docker compose exec -T web python manage.py production_preflight
```

两行角色结果都必须以 `:false` 结尾。bootstrap 账号只用于数据库容器初始化和受控灾难处理，不得注入 Web、Worker、备份或日常管理命令。

## 一致备份

`scripts/backup.sh` 会先核验 PostgreSQL 17 门禁，再创建维护标记，阻止新写入和 Worker 领取新任务，并等待现有任务结束；随后生成自定义格式数据库转储、受控文件归档、逐文件 manifest 和 `SHA256SUMS`。数据库转储明确排除 `django_session` 表数据，文件归档明确排除 `.env` / `.env.*`，因此活动会话和进程秘密不会进入备份。脚本先写 `.incomplete-*` 临时目录，只有对三个工件的 SHA-256、归档成员、逐文件路径/大小/哈希全部复核通过后才原子发布为时间戳目录。失败的 `.incomplete-*` 不能视为备份成功。脚本退出时自动移除它创建的维护标记；若标记已存在，脚本拒绝干预其他维护窗口。

先确认 PostgreSQL 版本门槛和 `/health/ready`，再从交付包根目录运行：

```bash
docker compose run --rm -T \
  -e FIRE_INSPECT_BACKUP_DIR=/data/backups \
  web backup
```

最后一行输出本次目录，例如 `/data/backups/20260824T120000Z`。该目录必须同时包含：

- `database.dump`；
- `files.tar.gz`；
- `manifest.json`；
- `SHA256SUMS`。

将时间戳目录复制到权限受限、加密的独立备份盘，并在独立盘再次校验：

```bash
export FIRE_INSPECT_BACKUP_ID=20260824T120000Z
docker compose cp \
  "web:/data/backups/$FIRE_INSPECT_BACKUP_ID" \
  /secure-backup/fire-inspect/
cd "/secure-backup/fire-inspect/$FIRE_INSPECT_BACKUP_ID"
sha256sum -c SHA256SUMS
```

`/data/backups` 只是生成时的暂存位置，不替代独立备份盘。备份不包含 AI 密钥、活动会话或进程环境。建议至少保留 7 个日备份和 4 个周备份，并定期验证可读性。

## 在空库执行恢复演练

`scripts/restore.sh` 只用于隔离的新环境或灾备切换前的新环境，不执行生产原地覆盖。它会在任何恢复写入前核验 PostgreSQL 17 服务端和客户端、精确三个外层校验项、manifest 结构及归档成员；链接、设备、路径穿越、未列入 manifest 的文件和 `.env*` 都会被拒绝。它要求：

1. 目标 PostgreSQL 17 数据库没有任何业务表；
2. `FIRE_INSPECT_DATA_ROOT` 为空目录；
3. 备份目录完整且 `SHA256SUMS` 可通过；
4. 显式设置 `FIRE_INSPECT_RESTORE_ACK=RESTORE_IN_EMPTY_TEST_ENVIRONMENT`。

ACK 只是防误操作门槛，不会绕过空库、空目录或哈希校验。不要对生产 Compose 项目名或现有数据卷执行以下演练。

使用新的 Compose 项目名创建独立数据库和数据卷：

```bash
export COMPOSE_PROJECT_NAME=fire-inspect-restore-drill-20260824
export FIRE_INSPECT_RESTORE_SOURCE_HOST=/secure-backup/fire-inspect/20260824T120000Z
docker compose up -d db
```

先核对服务端和客户端均为 17.x，再确认数据库业务表计数为 `0`、数据目录检查无输出：

```bash
docker compose exec -T db psql -U fire_inspect_bootstrap -d fire_inspect -Atc 'SHOW server_version'
docker compose run --rm --no-deps migrate pg_restore --version
docker compose exec -T db psql -U fire_inspect_bootstrap -d fire_inspect -Atc \
  "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"
docker compose run --rm --no-deps migrate find /data -mindepth 1 -maxdepth 1 -print
```

然后把只读备份目录挂载为 `/restore` 并传入精确 ACK：

```bash
docker compose run --rm -T \
  --volume "$FIRE_INSPECT_RESTORE_SOURCE_HOST:/restore:ro" \
  --env FIRE_INSPECT_RESTORE_SOURCE=/restore \
  --env FIRE_INSPECT_RESTORE_ACK=RESTORE_IN_EMPTY_TEST_ENVIRONMENT \
  migrate restore
```

脚本依次校验版本和备份包、机器确认空数据库/空数据目录、恢复数据库、安全展开文件、逐文件核对 manifest、执行并复核迁移、生产预检和 `check --deploy`。归档先提取到目标数据卷内的受限临时目录，验证通过后才发布；任一步失败都不得把该环境切换为正式服务。若数据库恢复后续步骤失败，该隔离恢复环境必须整体废弃并重新创建，禁止把半恢复环境原地修补成生产环境。

恢复成功后启动其余服务并复核：

```bash
docker compose up -d web worker nginx
docker compose exec -T web python manage.py migrate --check
docker compose exec -T web python manage.py production_preflight
docker compose exec -T web python manage.py verify_audit_chain
docker compose ps
```

还必须人工完成登录、检查列表、受控下载与 Range 播放、任务提交与重试、法规来源定位、三类文书查看/导出的冒烟测试；核对数据库与文件、删除审计和已删除对象不可见性。正式交付前至少保存一次“生产格式备份—全新空库/空目录恢复—端到端冒烟”的脱敏记录。

## 升级

升级只跟随已审核的应用版本和 PostgreSQL 17 安全补丁，不自动重新处理现有文书。

1. 校验新 OCI 镜像摘要/签名、依赖锁、迁移清单和第三方许可；运行 `docker compose config --quiet`，由启动预检拒绝占位秘密，不输出解析后的秘密值。
2. 按上一节完成并校验一致备份。
3. 停止对外入口、Web 和 Worker，防止升级期间出现新写入：

   ```bash
   docker compose stop nginx web worker
   ```

4. 导入已校验的新镜像后，先用一次性容器执行迁移和启动门槛：

   ```bash
   docker compose run --rm migrate
   docker compose run --rm --no-deps web python manage.py production_preflight
   docker compose run --rm --no-deps web python manage.py migrate --check
   docker compose run --rm --no-deps web python manage.py verify_audit_chain
   ```

5. 任一命令失败都不要启动新版本；保留日志和备份，在另一个空环境验证恢复或修正迁移。
6. 全部通过后启动并检查：

   ```bash
   docker compose up -d web worker nginx
   docker compose ps
   docker compose exec -T web python manage.py migrate --check
   docker compose exec -T web python manage.py production_preflight
   docker compose exec -T web python manage.py verify_audit_chain
   ```

7. 复核 `/health/live`、`/health/ready`，再执行登录、权限、文件、任务、法规和三类文书冒烟。
8. 抽查升级前已确认文书仍保持确认状态且没有后台生成任务；初稿只在用户主动操作时采用新规则。

## 交付前门槛清单

只有以下项目均有“通过”证据时才能交付；`NOT RUN` 或跳过不能算通过：

- `make fast`：后端、前端、固定 AI、DOCX 结构和机器可读验收覆盖全部通过；
- `make local-acceptance`：权威素材哈希、两段真实视频同检查处理、正确材料与约 ±3 秒证据定位、固定响应端到端通过；
- `make postgres`：PostgreSQL 17 并发领取、fencing token、生产格式备份及空库恢复通过；
- `make real-ai`：获得明确授权后，ASR、LLM、Vision、Embedding 四类真实端点各至少成功一次；
- `make windows-docx`：指定 Windows + Word/WPS + 授权字体环境更新域并逐页签字，审批 JSON 同时绑定重新生成的 DOCX、逐页 PNG 哈希、视觉基线和询问笔录页脚值；
- `make browser-e2e`：对清洁 Compose 的 HTTPS 地址执行真实 Chrome 登录、检查创建、文字材料上传、深层 SPA 路由刷新和公网请求阻断；
- 检查员 A/B、管理员和未登录权限矩阵，以及文件、助手、管理员接口和日志脱敏通过；
- ASR、LLM、Vision、Embedding 和文书生成失败降级及单步重试通过；
- 三份权威模板和真实素材哈希保持不变，消防法测试样本仍显示“待核验”；
- 本节备份、全新空库/空目录恢复、升级后检查和完整业务冒烟均实际演练；
- 内网 TLS、数据目录权限、独立加密备份盘、日志轮转、授权字体及运维责任人已落实。

每次门槛运行后都要归档 `test-results/` 中对应的 JUnit 和
`acceptance-coverage.json`。覆盖报告必须把验收项绑定到实际执行的精确测试节点；
状态为 `NOT RUN`、测试节点已不存在、JUnit 失败或只有源码文件而没有测试结果时，均不能签为通过。

Windows 审批文件从 [windows-docx-approval.example.json](windows-docx-approval.example.json) 复制并逐项填写；不得复用哈希不一致的旧审批。
