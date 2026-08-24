# 内网部署说明

## 前置条件

- Linux LTS 主机、Docker Engine 与 Compose 插件；
- PostgreSQL 17 服务端，以及同为 17.x 的 `pg_dump`、`pg_restore` 客户端；Compose 镜像已固定为同一 17.6 补丁版本；
- 内网 DNS 与单位签发的 TLS 证书；
- 独立数据盘和独立备份盘；
- 单位授权的中文字体；
- 预构建镜像归档、镜像 SHA-256/签名和第三方许可清单。

离线交付包还必须包含 `compose.yaml`、Nginx 配置、`scripts/backup.sh`、`scripts/restore.sh`、三份未修改的 `docs/templates/*.docx` 及消防法验收样本。初始化命令运行前，这些权威输入必须存在于应用镜像的 `/app/docs/templates/` 与 `/app/testdata/knowledge/` 对应位置，并通过交付摘要校验。

运行时不从 CDN、在线字体或在线激活服务加载资源。联网构建应在受控构建机完成；正式内网只导入经校验的 OCI 镜像归档。

## 首次部署

1. 校验交付包和镜像摘要，将证书只读放到 `deploy/certs/server.crt` 与 `server.key`，将单位授权字体只读放到 `deploy/fonts/`。Nginx 固定以 UID/GID `101:101` 运行，证书目录需允许该 UID 遍历，私钥只允许该 UID/受控管理员读取；Web/Worker 固定以 `10001:10001` 运行，字体需允许其只读访问。不要为方便而把私钥设为全局可读。
2. 设置 `COMPOSE_DISABLE_ENV_FILE=1`，通过受保护的系统服务或秘密管理器向当前父进程注入 `deploy/environment.example` 所列变量；不要在仓库、`.env`、Compose 覆盖文件或命令行参数保存真实值。必须分别生成 `POSTGRES_ADMIN_PASSWORD`、`FIRE_INSPECT_DB_MIGRATION_PASSWORD` 和 `FIRE_INSPECT_DB_RUNTIME_PASSWORD`，三者不得复用；它们通过离散密码变量传递，支持 `@:/?#%+` 等 URL 保留字符。
3. 运行 `docker compose config --quiet`，只校验语法和必填变量，不打印含秘密的完整解析配置。示例占位值会被生产预检拒绝。
4. 启动数据库、一次性迁移服务和应用：`docker compose up -d db migrate web worker nginx`。`migrate` 使用非超级用户数据库所有者执行迁移和权限收敛，成功退出后 Web/Worker 才会启动；它不是常驻服务。若迁移失败，停止部署并检查 `docker compose logs migrate`，不得让 Web/Worker 改用迁移账号绕过。
5. 核对数据库服务端与应用镜像内客户端都是 PostgreSQL 17.x：

   ```bash
   docker compose exec -T db psql -U fire_inspect_bootstrap -d fire_inspect -Atc 'SHOW server_version'
   docker compose exec -T web pg_dump --version
   docker compose exec -T web pg_restore --version
   docker compose exec -T web python manage.py postgres_version_gate
   docker compose exec -T web python manage.py production_preflight
   ```

6. 检查 `/health/live` 与 `/health/ready`，再运行一次 `docker compose exec web python manage.py create_initial_admin` 交互创建管理员；用户名明文输入，密码通过隐藏输入并二次确认。
7. 使用刚创建的规范化管理员用户名初始化三份权威模板，再初始化消防法测试样本：

   ```bash
   docker compose exec web python manage.py initialize_word_templates --actor <管理员用户名>
   docker compose exec web python manage.py initialize_fire_law
   ```

   第一条命令幂等创建三份模板并通过持久任务完成权威映射识别与确认；`--actor` 必须指向已有管理员。第二条命令校验权威 PDF 哈希、保存原件并排队解析，来源状态始终为“待核验”、有效性为“未知”。在管理员任务页确认知识解析成功后再执行检索验收；失败只能重试该解析任务，不得把未解析文档伪装为可检索，也不得人工改成已确认现行版本来绕过验收。
8. 登录后确认三类模板区域均可查看、消防法样本显示“待核验”，再完成权限、上传、两任务并发和三类文书冒烟测试。

9. 核对四个常驻服务都不是 root，且容器根文件系统为只读：

   ```bash
   docker compose exec -T web id -u       # 10001
   docker compose exec -T worker id -u    # 10001
   docker compose exec -T nginx id -u     # 101
   docker compose exec -T db id -u         # postgres 用户（固定镜像中为 999）
   ```

   Web/Worker 只写 `/data` 和带 `noexec,nosuid,nodev` 的临时内存盘；Nginx 只读访问静态文件、证书和 `/data`；PostgreSQL 只写数据库卷及受限的 socket/tmpfs。数据库不发布宿主机端口。若复用的旧数据库卷权限不属于镜像内 `postgres` 用户，应在停机并完成备份后由运维人员修正卷权限，不能把数据库服务改回 root 绕过。

系统没有默认密码。生产配置缺少强密钥、PostgreSQL、HTTPS Host 或安全 Cookie 时拒绝启动。

数据库 bootstrap 账号只存在于数据库容器初始化环境；迁移服务使用 `fire_inspect_migrator`，Web/Worker/备份使用 `fire_inspect_runtime`。生产预检会拒绝超级用户、职责不匹配、审计表所有者错误或运行时具有审计 `UPDATE/DELETE/TRUNCATE` 权限的部署。运行时只允许读取和追加永久审计，数据库触发器同时阻断更新、删除和截断。

无人值守初始化必须显式提供 `--noinput --username <用户名> --password-env <环境变量名>`，密码只从已注入的进程环境读取。命令不读取 `.env`、不接受默认密码，也不在输出中显示密码。系统已有管理员时命令拒绝再次创建，后续账号维护应使用管理员界面。

## 数据目录

`/data` 包含 originals、derived、knowledge、templates、documents、render-previews、quarantine 和 backups。应用路径仅使用服务端 UUID；容器以非 root 用户运行。生产主机的数据文件系统应额外挂载为 `noexec,nosuid,nodev`，不得与源码或前端静态目录混用。首次创建命名卷后确认根目录属于 `10001:10001` 且仅应用和受控备份人员可写。

## SPA 与文件

Nginx 在容器内监听非特权端口 `8080/8443`，Compose 默认映射到宿主机 `80/443`；需要避让端口时只修改 `FIRE_INSPECT_HTTP_PORT` / `FIRE_INSPECT_HTTPS_PORT`。对 `/` 使用 `try_files ... /index.html`，所以业务路由可直接刷新。`/api/`、Range 播放和下载先经过应用的对象权限检查；`/protected-media/` 为 `internal`，不得暴露数据目录的普通静态 URL。代理关闭响应落盘缓冲，Range、Cookie 和授权响应由应用原样处理。

## 备份、恢复与升级

生产格式备份、隔离空库恢复、精确 ACK、PostgreSQL 17 版本检查和升级命令见 [运维手册](OPERATIONS.md)。恢复脚本只允许面向全新空数据库和空数据目录，不得用于覆盖运行中的生产环境。正式交付前必须实际完成一次恢复演练并保存脱敏结果。

## 交付验收

交付前逐项完成 [ACCEPTANCE](ACCEPTANCE.md) 和 [运维手册的交付前门槛清单](OPERATIONS.md#交付前门槛清单)。Windows 文书审批使用 [审批 JSON 示例](windows-docx-approval.example.json)，文件名、字段和四份文书键必须保持不变；DOCX 与逐页 PNG 哈希、询问笔录页脚值、视觉基线、环境版本、字体与复核人必须填写本次实际值。
