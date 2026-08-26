# 消防监督检查 AI 辅助系统实现方案

> 状态：待确认
>
> 日期：2026-08-24
>
> 适用范围：MVP，从零实现

## 1. 依据、目标与约束

本方案以以下材料为权威输入：

1. [`docs/PRD.md`](./PRD.md)：产品行为、权限、安全和范围基线。
2. [`docs/ACCEPTANCE.md`](./ACCEPTANCE.md)：可观察验收结果和失败场景。
3. [`docs/templates/*.docx`](./templates/)：三类文书版式的唯一权威来源。
4. [`testdata/README.md`](../testdata/README.md) 与 [`testdata/expected/acceptance-cases.json`](../testdata/expected/acceptance-cases.json)：真实样本及业务判分标准。
5. 2026-08-24 已确认的补充口径：共享字段、文书失效、照片分页、日期时间、签名、视频方向、证据时间容差、法规用例、审计最小化和真实 AI 验收规则。

若本方案与 PRD 冲突，以 PRD 为准；文书结构或视觉与原始模板冲突，以原始模板为准；正式完成必须同时满足 PRD、ACCEPTANCE 和已确认补充口径。

### 1.1 产品目标

系统以“一次检查”为业务中心，把材料、转写、事实、证据、法规和三类文书组织成可追溯的人工审核闭环。AI 只生成候选内容，不替代用户确认事实、问答角色、执法判断或正式文书。

### 1.2 技术目标

- 适合单机构、单机内网部署，运行时不依赖公网资源。
- 模块清晰但不拆微服务；以少组件换取可运维性。
- 原始材料、模板和成功结果不可被失败任务覆盖。
- ASR、LLM、视觉、Embedding、OCR 分别配置和替换。
- 权限在 API、文件、任务、AI 上下文和导航动作中保持一致。
- 每阶段测试先行，并持续使用权威样本回归。
- 文书生成以原模板为基底做局部 OOXML 修改，不重新设计版式。

### 1.3 已确认的业务决策

- 单位名称、地址、负责人、联系人、检查时间、检查类型等检查级共享字段只有一个当前来源；文书不得建立覆盖值。从文书页面修改时，实际写回检查。
- 任一检查级共享字段有效变化，使该检查的三类已确认文书全部退回初稿。
- 事实变化只影响实际依赖该事实的文书；选图、逐照片内容只影响照片文书；问答内容和角色只影响询问笔录；模板映射变化只影响使用该模板映射的文书。
- 照片文书一张照片一页，图片在模板留白区域内等比例缩放。正式验收任选三张清晰关键帧，不绑定固定图片哈希。
- 长字段和正文允许自然换行、表格增长和跨页，不裁切，也不强制压缩在单页内。
- 所有签名区保持空白，供线下手签。
- 检查记录编号作为一个完整文本字段，不解析字号结构。
- 检查日期格式为 `YYYY年M月D日`；询问起止时间都输出完整日期时间，跨日时两端均带日期。
- Word 视觉验收以单位指定的 Word/WPS 和字体环境为准（2026-08-26 确认为本机 macOS + Microsoft Word）；首次人工确认通过的确定性生成样本成为后续视觉基线。
- `1.mp4` 按正向横屏显示，忽略异常 90° 旋转矩阵。允许生成浏览器兼容播放副本，但原件和原时间轴保持不变。
- 视频证据跳转允许目标时间前后约 3 秒误差；每个视频至少提供一张清晰关键画面，业务可用性由人工最终判断。
- `1.mp4` 和 `4.mp4` 放在同一次检查内执行跨材料证据定位验收。
- 法规检索补充少量固定正向和“未找到”用例，明确查询词、预期条款和原文。
- 删除后的永久审计只保留操作人、时间、对象、动作、原因和必要的掩码摘要。
- 普通自动化测试使用固定 AI 响应；真实四类 AI 端点仅在正式交付前通过进程环境变量获授权运行一次。

## 2. 总体技术方案

### 2.1 架构风格

采用“模块化单体 Web 应用 + 独立后台 Worker + PostgreSQL + 本机文件存储”。业务模块在同一代码库和数据库中，跨模块一致性由单个数据库事务保证；媒体、AI、知识解析和 DOCX 生成等耗时工作由同代码镜像的 Worker 执行。

不在 MVP 引入 Kubernetes、Redis、Celery、RabbitMQ、Kafka、MinIO、Elasticsearch、独立向量数据库或多 Agent 编排。

选择这一架构的理由：

- 权限、共享字段变更、文书失效和审计需要强事务，模块化单体比跨服务事务可靠。
- 单机内网负载有限，PostgreSQL 持久任务表足以支持两个以上并发任务。
- 原始媒体适合本地受控文件系统，无需对象存储。
- 减少中间件可以降低离线安装、备份、升级和故障恢复成本。

### 2.2 推荐技术栈

| 层次 | 选择 | 用途和理由 |
|---|---|---|
| 后端语言 | Python 3.12 | 媒体、文档、AI 和测试生态成熟，部署稳定 |
| Web 框架 | Django 5.2 LTS 当前补丁版本 + Django REST Framework | 内置用户、会话、密码、CSRF、ORM、事务、迁移和管理能力；Django 5.2 LTS 官方扩展支持至 2028-04 |
| 数据库 | PostgreSQL 17 当前补丁版本 | 行锁、事务、JSONB、全文/模糊检索和可靠备份；官方支持至 2029-11 |
| 检索扩展 | `pg_trgm` + pgvector 0.8.x | 中文子串/模糊召回和可选向量召回；向量与业务数据保持同库 |
| 后台任务 | PostgreSQL 持久任务表 + Django Worker 命令 | 使用租约和 `FOR UPDATE SKIP LOCKED`，不增加消息中间件 |
| 前端 | React + TypeScript + Vite + React Router + TanStack Query | 适合检查工作台、媒体交互、字段编辑和右侧助手；仅构建期需要 Node |
| UI | 本地打包的 Ant Design 或等价组件库 | 表单、表格和后台界面成熟；不得从 CDN 加载 |
| 反向代理 | Nginx | HTTPS、静态资源、上传限制、SPA fallback 和受保护文件内部转发 |
| 媒体 | FFmpeg/ffprobe、OpenCV、Pillow | 探测、解码、兼容转码、抽帧、清晰度/亮度/重复度计算 |
| Word | `python-docx` + `lxml` | 前者处理常规结构，后者精确修改 OOXML、域、关系和格式 |
| PDF/DOCX 解析 | `pdfplumber`、`pypdf`、Poppler、`python-docx` | 避免把文本抽取误当视觉检查；支持双栏定位和逐页来源 |
| HTTP/Schema | `httpx` + Pydantic/JSON Schema | AI 调用、超时控制和结构化输出校验 |
| 文书渲染 | LibreOffice headless + Poppler | 服务端结构与版式预检；不替代指定环境的最终验收 |
| 测试 | pytest、pytest-django、Vitest、React Testing Library、Playwright | 覆盖领域、API、前端和端到端 |
| 依赖锁定 | `uv.lock` + `pnpm-lock.yaml` | 可重复、可离线构建 |
| 部署 | Linux LTS 主机 + Docker Engine/Compose | 预构建镜像离线导入，数据挂载到主机受控目录 |

版本依据：[Django 官方下载页](https://www.djangoproject.com/download/)将 5.2 标为 LTS 并维护至 2028-04；[PostgreSQL 官方版本策略](https://www.postgresql.org/support/versioning/)对 17 的支持期至 2029-11；[pgvector 官方仓库](https://github.com/pgvector/pgvector)说明其支持 PostgreSQL 13 及以上。依赖在实现时锁定精确版本，升级只跟随安全补丁，不自动跨主版本。

### 2.3 运行时拓扑

```text
内网浏览器
    │ HTTPS
    ▼
Nginx
    ├── React 静态资源与 SPA fallback
    ├── /api/* 反向代理
    └── internal 文件/视频流（仅接收 Web 授权后的内部转发）
             │
             ▼
Django + DRF Web
    ├── 会话、CSRF、权限和业务 API
    ├── 检查、事实、证据、知识、模板和文书事务
    ├── 只读 AI 助手上下文
    └── PostgreSQL 任务入队
             │
       ┌─────┴───────────────┐
       ▼                     ▼
PostgreSQL 17          Worker（同一应用镜像）
+ pg_trgm/pgvector     ├── 媒体探测与兼容转码
                       ├── ASR/视觉/LLM/Embedding
                       ├── 关键帧与事实候选
                       ├── PDF/DOCX 知识解析
                       └── OOXML 生成与渲染检查
                                  │
                                  ▼
                           本机受控数据目录
```

生产环境四个常驻服务：`nginx`、`web`、`worker`、`postgres`。Worker 默认两个并发槽并可配置；Web 请求只入队，不等待媒体或 AI 长任务。

## 3. 代码模块

这些模块是同一 Django 项目中的内部边界，不是独立服务：

| 模块 | 主要职责 |
|---|---|
| `accounts` | 自定义用户、检查员/管理员角色、登录、会话、密码策略、停用用户 |
| `inspections` | 检查 CRUD、检查级共享字段、参与人员、共享修订号 |
| `materials` | 流式上传、格式校验、原件、派生资产、受控下载和兼容播放 |
| `jobs` | 持久任务、租约、fencing token、重试、幂等、公开错误和任务面板 |
| `transcripts` | 完整连续转写、时间锚片段、文本编辑、问答候选及人工确认角色 |
| `facts` | 候选/确认/撤回事实、人工补充、版本和业务历史 |
| `evidence` | 媒体时间段、关键画面、照片、转写片段和法规条款证据 |
| `knowledge` | 知识文档、双栏解析、条款切分、词法/向量检索、来源回溯 |
| `templates` | 不可变模板版本、区域识别、置信度、人工映射和确认 |
| `documents` | 三类文书、专属字段、人工保护、依赖、状态、修订、生成和导出 |
| `assistant` | 权限内只读上下文、法规查询、回答和类型化导航动作 |
| `audit` | 活动对象详细历史、长期最小化审计、审计校验 |
| `operations` | 健康检查、初始化、备份、恢复、迁移、一致性检查和验收报告 |

跨模块业务规则通过领域服务调用，不允许视图或后台任务直接绕过。例如共享字段更新必须在同一事务内完成字段写入、共享修订递增、三类文书退回和历史记录。

## 4. 文件与存储设计

### 4.1 文件目录

```text
data/
  staging/
  originals/inspections/<inspection-id>/<material-id>/original
  derived/materials/<material-id>/<revision>/
  knowledge/<document-id>/<version>/
  templates/<template-id>/<version>/
  documents/<document-id>/<revision>/
  render-previews/<artifact-id>/
  quarantine/deletions/<operation-id>/
  backups/
```

- 文件系统路径只使用服务端 UUID 和受控目录，不使用上传文件名拼接路径。
- 数据库只保存相对 `storage_key`；绝对路径不进入 API、普通日志或错误响应。
- 原始材料、知识文档和模板一经校验后只读保存，派生物放在独立目录。
- 所有写入先进入 `staging`，校验成功后在同一文件系统中原子移动。
- Nginx 文件目录设置为 `internal`，外部请求先由 Django 完成对象权限检查，再使用内部转发。视频 Range 请求同样需要会话权限。

### 4.2 上传和校验

1. Web 流式写入 staging，同时计算 SHA-256 和字节数，不把整个文件读入内存。
2. Nginx 与应用同时执行可配置的单文件、单检查、数量和并发限制。初始建议为单文件 512 MiB、单检查 2 GiB、50 份材料，验收前可由单位调整；不得低于权威样本需求。
3. 综合扩展名、声明 MIME、魔数和实际解析结果校验；仅扩展名正确不算通过。
4. MP4/MP3/WAV 使用 ffprobe/解码探测；图片使用 Pillow 解码；DOCX/PDF 检查损坏、压缩炸弹、外部关系、嵌入对象和活动内容。
5. 拒绝宏、可执行嵌入物、路径穿越包项和不受支持格式；原始文件名只作为净化后的显示信息。
6. Worker 中的外部解析进程设置超时、内存、输出数量和临时目录限制。
7. 校验完成后原子发布；任何失败只删除 staging 临时物，不产生“成功”材料记录。

### 4.3 媒体派生物

- 原始视频始终保留，证据只引用原材料 ID 和原始时间轴毫秒值。
- 对浏览器不兼容的 HEVC 等视频生成 H.264/AAC、`yuv420p`、`faststart` 的等长播放副本。
- 生成后比较时长和首尾时间，确保播放副本不改变证据时间基准。
- 同时保存容器旋转元数据和人工可修正的“有效显示方向”。`1.mp4` 验收设置为 0°，转码和抽帧禁用异常的 90° 自动旋转。
- 修改显示方向只重建播放副本和预览，不修改原件、转写时间或证据锚点。

## 5. 主要数据设计

### 5.1 通用约定

- 对外业务 ID 使用 UUID。
- 时间使用 PostgreSQL `timestamptz` 以 UTC 保存，界面和文书按 `Asia/Shanghai` 展示。
- 媒体相对时间统一保存为整数毫秒。
- 可编辑聚合具有 `revision BIGINT`；更新必须提交期望修订号，冲突返回 409。
- 当前业务数据、详细历史和长期审计分离。详细历史可含业务正文并随检查删除；长期审计从写入时就只含掩码摘要。
- 跨对象关系必须属于同一次检查。关键关系冗余 `inspection_id`，通过组合外键、约束或数据库触发器拒绝跨检查引用。

### 5.2 身份和检查

#### `users`

- `id, username, password_hash`
- `role`: `inspector | admin`
- `status`: `active | disabled`
- `created_at, updated_at`

用户原则上停用而不物理删除，以保留历史操作人语义。用户名保存规范化值并建立唯一索引。

#### `inspections`

- `id, owner_id`
- `unit_name, unit_address`
- `responsible_person`
- `contact_name, contact_phone`
- `inspection_at`
- `inspection_type`：七种检查类型单值枚举
- `general_notes`
- `shared_revision`
- `created_at, updated_at`

索引：`(owner_id, inspection_at DESC)`；单位名称和地址可使用 `pg_trgm` 索引。

#### `inspection_personnel`

- `inspection_id, sequence`
- `user_id NULL, name, work_unit, role`

参与人员变更由检查领域服务完成，并递增 `shared_revision`。

检查级共享字段目录固定在代码中。模板映射不能把这些字段重新定义为文书级覆盖字段。

### 5.3 材料、转写和画面

#### `materials`

- `id, inspection_id, kind(video/image/audio/text)`
- `display_name, original_storage_key`
- `mime_type, extension, sha256, byte_size`
- `duration_ms, width, height, codec_info`
- `container_rotation, display_rotation_override`
- `revision, created_by, created_at`

建议对 `(inspection_id, sha256)` 建唯一约束，重复上传返回已存在材料而不是复制。

#### `material_derivatives`

- `id, material_id`
- `kind(playback/audio_extract/keyframe/thumbnail/preview)`
- `storage_key, mime_type, sha256, byte_size`
- `source_timestamp_ms NULL, timeline_offset_ms`
- `source_job_id, created_at`

派生物永远不能替代原材料成为证据源。

#### `transcripts`

- `id, material_id UNIQUE`
- `full_text, language, revision`
- `updated_by, updated_at`

完整连续文本独立保存，不能简单拼接边界可能失真的时间窗文本。

#### `transcript_segments`

- `id, transcript_id, sequence`
- `start_ms, end_ms, text`
- `suggested_role(question/answer/unknown)`
- `confirmed_role NULL/question/answer`
- `text_origin(ai/manual)`
- `role_confirmed_by, role_confirmed_at, revision`

约束：时间非负、结束大于开始、同一转写内顺序唯一；索引 `(transcript_id, start_ms)`。

#### `key_frames`

- `id, material_id, timestamp_ms, derivative_id`
- `blur_score, darkness_score, duplicate_group`
- `usable_status(candidate/rejected/selected)`
- `description, description_origin, revision`

每个视频至少保留一张回退候选；人工可拒绝、选择或改用补充照片。

### 5.4 事实和证据

#### `facts`

- `id, inspection_id, content`
- `review_state(candidate/confirmed/withdrawn)`
- `needs_verification, verification_note`
- `origin(ai/manual)`
- `primary_source_material_id NULL`
- `normalized_content_hash NULL`
- `revision`
- `confirmed_by, confirmed_at`
- `created_by, created_at, updated_at`

AI 候选可按来源材料、输入修订和规范化内容哈希幂等 UPSERT；人工事实不做自动语义合并。

#### `fact_revisions`

保存事实正文和状态的不可变业务版本，供当前检查内的详细历史查看；检查删除时级联删除。

#### 证据关系

不使用完全无约束的通用多态 JSON，拆为：

- `fact_media_ranges(fact_id, inspection_id, material_id, start_ms, end_ms, note)`
- `fact_transcript_links(fact_id, inspection_id, segment_id)`
- `fact_keyframe_links(fact_id, inspection_id, key_frame_id)`
- `fact_photo_links(fact_id, inspection_id, photo_material_id)`
- `fact_clause_links(fact_id, clause_id, applicability_reason, confirmed_by)`

所有媒体证据 API 返回明确的 `material_id` 和原始时间轴毫秒数。前端必须先切换到指定材料，再跳转时间；不能只对当前播放器执行 seek。

### 5.5 知识库

#### `knowledge_documents`

- `id, title`
- `category(law/standard/local_rule/internal/other)`
- `issuer, source_reference, version, publication_date`
- `source_verification(pending/verified/rejected)`
- `validity(unknown/effective/expired)`
- `original_storage_key, mime_type, sha256`
- `revision, uploaded_by, created_at`

上传默认 `source_verification=pending`、`validity=unknown`。内部制度不得被标成正式法律法规。

#### `knowledge_clauses`

- `id, document_id, sequence`
- `chapter, section, clause_identifier, heading, content`
- `page_start, page_end, source_locator JSONB`
- `normalized_text, search_vector`
- `parser_version`

#### `knowledge_embeddings`

- `clause_id, model_fingerprint, dimensions`
- `embedding VECTOR`
- `status, created_at`

Embedding 与条款分表，允许替换不同维度的模型。MVP 知识量较小，按 `model_fingerprint` 过滤后做精确向量检索即可；无需立即建立复杂 ANN 索引。

### 5.6 模板和映射

#### `word_templates`

- `id, document_type, name`
- `active_mapping_revision_id`
- `active, created_by, created_at`

#### `template_versions`

- `id, template_id, version_no`
- `original_storage_key, sha256`
- `validation_result, created_by, created_at`

模板版本不可变；新文件创建新版本。

#### `template_mapping_revisions`

- `id, template_version_id, revision_no`
- `state(draft/confirmed/superseded)`
- `recognizer_version`
- `created_by, confirmed_by, confirmed_at`

已确认映射不可原位编辑，修改时克隆为新草稿。

#### `template_regions`

- `mapping_revision_id`
- `business_field_key, display_name`
- `repeat_scope(single/document_common/per_item)`
- `fill_strategy(label_suffix/table_cell/checkbox/image_region/body/footer_field/preserve_blank)`
- `locator JSONB`
- `style_policy JSONB`
- `confidence, enabled, required, sequence`

`locator` 保存 OOXML 结构路径、相邻固定标签和上下文指纹；只在管理员映射界面显示中文业务名、位置预览、填充方式和置信度，不向普通用户暴露内部定位键。

签名区域固定使用 `preserve_blank`，禁止绑定填充值。

### 5.7 文书

#### `documents`

- `id, inspection_id, document_type(record/photo/interview)`
- `name`
- `template_version_id`
- `state(draft/confirmed)`
- `revision`
- `current_mapping_revision_id`
- `last_generated_revision_id NULL`
- `generator_version, upgrade_available`
- `confirmed_by, confirmed_at`
- `created_at, updated_at`

默认名称为创建时的“单位名称 + 文书类型”。用户手工改名后，单位名称变化不自动重命名，但文书仍按共享字段规则退回初稿。

#### `document_field_values`

- `document_id, business_field_key, value_json`
- `origin(ai/manual/system)`
- `manual_locked`
- `revision, updated_by, updated_at`

只保存文书公共或专属值。若 API 收到检查级共享字段键，必须拒绝局部覆盖并引导调用检查共享字段接口。

字段权威来源：

| 字段类别 | 当前来源 | 变化影响 |
|---|---|---|
| 单位、地址、负责人、联系人、检查时间、检查类型、检查人员、通用备注 | `inspections` / `inspection_personnel` | 三类文书 |
| 记录编号、检查内容 | 监督检查记录文书字段 | 当前记录表 |
| 照片拍摄地点、摄制人、拍摄时间、确认人 | 照片文书公共字段 | 当前照片文书 |
| 单图、反映内容、说明、排序 | `photo_document_items` | 当前照片文书 |
| 询问次数、起止时间、地点、询问/记录人员、被询问人信息 | 询问笔录文书字段 | 当前询问笔录 |
| 最终问答角色、文字、顺序 | `interview_qa_items` | 当前询问笔录 |
| 事实正文和状态 | `facts`，文书只记录依赖 | 实际依赖该事实的文书 |

#### `photo_document_items`

- `id, document_id, sequence`
- `key_frame_id NULL, photo_material_id NULL`，二者恰有一个
- `reflected_content, reflected_content_origin`
- `description, description_origin`
- `revision`

源照片在同一文书内唯一，重复生成使用稳定源 ID UPSERT，不追加重复页。

#### `interview_qa_items`

- `id, document_id, sequence, text`
- `suggested_role, confirmed_role`
- `content_origin(ai/manual)`
- `role_confirmed_by, role_confirmed_at`
- `source_segment_start_id, source_segment_end_id NULL`
- `revision`

最终正文只使用人工确认后的角色和当前文字。AI 重新生成不能覆盖人工角色或人工正文。

#### `document_fact_dependencies`

- `document_id, fact_id, fact_revision`

记录生成或确认时实际引用的事实，用于精确失效。

#### `document_revisions`

- `document_id, revision_no`
- `event_type(generated/edited/confirmed/invalidated/exported)`
- `content_snapshot JSONB`
- `input_revision_vector JSONB`
- `mapping_revision_id, generator_version`
- `created_by, created_at`

快照只用于历史和复现，不可编辑，也不能成为共享字段新来源。

#### `document_state_events`

- `document_id, from_state, to_state`
- `reason_code, reason_summary`
- `caused_by_type, caused_by_id`
- `actor_id NULL, created_at`

稳定原因码至少包括：`SHARED_FIELDS_CHANGED`、`REFERENCED_FACT_CHANGED`、`PHOTO_CONTENT_CHANGED`、`QA_BODY_CHANGED`、`TEMPLATE_MAPPING_CHANGED`、`DOCUMENT_FIELD_EDITED`、`SOURCE_DELETED`。

#### `document_exports`

- `document_id, document_revision_id`
- `storage_key, sha256, page_count`
- `structure_check_result JSONB`
- `render_check_result JSONB`
- `created_by, created_at`

旧导出保留为检查内历史，始终受检查权限保护；界面明确标记当前、旧修订和已失效产物。

### 5.8 任务、AI 运行和审计

#### `processing_jobs`

- `id, task_type, scope_type, scope_id, inspection_id`
- `input_fingerprint, idempotency_key`
- `status(queued/running/succeeded/failed/cancelled/superseded)`
- `attempt_count, max_attempts, available_at`
- `lease_owner, lease_token, lease_expires_at`
- `expected_scope_revision`
- `safe_error_code, safe_error_message`
- `created_by, created_at, started_at, finished_at`

唯一约束：`idempotency_key`，以及适用时的 `(task_type, scope_id, input_fingerprint)`。队列使用 queued 部分索引。

#### `job_attempts`

- `job_id, attempt_no, lease_token`
- `capability, provider_name, model_name, config_fingerprint`
- `started_at, finished_at, outcome, safe_error_code`

不保存密钥、会话令牌、完整提示、完整响应、完整媒体或完整转写。

#### `domain_change_history`

保存活动业务对象的完整旧值、新值和状态变化，供有检查权限的用户查看。它随对应业务对象或检查删除。

#### `audit_events`

- `actor_id/actor_label, occurred_at`
- `object_type, object_id, inspection_id_snapshot`
- `action, reason, masked_change_summary`
- `request_id, previous_hash, event_hash`

数据库应用角色无 UPDATE/DELETE 权限，插入由受控函数完成；使用摘要链和校验命令检测篡改。审计从写入时就不保存完整敏感内容，因此可以在业务删除后长期保留。

## 6. 状态、一致性和失效规则

### 6.1 文书状态机

```text
draft --用户显式确认--> confirmed
confirmed --相关输入有效变化--> draft
```

耗时生成任务的 queued/running/failed 状态属于任务，不扩展文书业务状态。

### 6.2 同步失效矩阵

| 变化 | 应退回的已确认文书 |
|---|---|
| 任一检查级共享字段或参与人员 | 同一检查三类文书 |
| 文书实际引用事实的正文、状态或证据 | 依赖该事实的文书 |
| 未被文书依赖的事实 | 不退回 |
| 照片公共字段、选图、排序、逐图内容 | 照片文书 |
| 询问表头、问答角色、文字、顺序 | 询问笔录 |
| 记录编号、检查内容 | 监督检查记录表 |
| 已确认模板映射 | 使用该模板映射的文书 |
| 文书自身人工字段修改 | 当前文书 |
| 生成规则或模型升级 | 不自动退回；只标记初稿可主动升级，已确认文书不后台生成 |

业务写入、文书退回、原因和历史必须在同一事务中提交，不由后台队列异步补做。

### 6.3 人工内容保护

- 文书专属字段和重复项记录 `origin` 与 `manual_locked`。
- 重新生成默认只更新 AI/system 来源且未锁定的值。
- 用户若要求覆盖人工值，必须逐字段显式确认并产生历史。
- 检查共享字段永远取当前检查值，不存在文书覆盖。
- 已确认文书禁止直接后台生成；用户编辑时先在同一事务中退回初稿。
- 生成结果提交前再次比较文书状态、行修订和输入修订向量。输入已变化的结果标为 `superseded`，不得写回。

## 7. 持久任务与失败降级

### 7.1 任务领取

- Worker 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务，写入租约和随机 fencing token。
- 默认两个并发槽；不同能力可分别设置并发上限、超时和最大尝试次数。
- 租约超时可重新领取；旧 Worker 提交时必须校验 token，失去租约的结果不得发布。
- Web、Worker 和重试接口计算同一幂等键；重复点击返回已有任务。

### 7.2 原子结果发布

1. 任务读取目标和输入修订，形成不可变输入快照。
2. 外部进程或 AI 调用不持有长数据库锁。
3. 文件先写 staging，数据库结果先写新修订。
4. 完成结构/Schema 校验后，在短事务内重新校验对象存在、版本匹配和租约有效。
5. 原子切换当前结果指针并标记成功。
6. 任何失败保留旧成功结果和原材料，清理本次临时物。

### 7.3 独立步骤

以下均为独立任务类型：兼容转码、音频提取、ASR、关键帧、视觉描述、事实提取、知识解析、Embedding、模板识别、文书生成、DOCX 渲染。一个步骤失败不得清空其他步骤结果；恢复后只重试失败步骤。

### 7.4 错误模型

任务保存内部错误分类和对用户安全的公开错误。公开消息不能包含堆栈、绝对路径、端点响应正文、密钥或完整业务内容。空结果、畸形 Schema、部分临时文件和旧结果均不能伪装成成功。

## 8. 媒体、转写、事实和证据流程

### 8.1 视频与播放

- ffprobe 保存编码、时长、帧率、分辨率、音轨和旋转元数据。
- 浏览器播放优先使用兼容副本；下载原件仍经过权限检查。
- 播放副本与原视频保持同起点、同时间轴，证据时间始终存原始毫秒值。
- 前端证据导航包含 `material_id + start_ms + end_ms`，切换正确材料后再 seek。

### 8.2 关键画面

1. 按可配置时间窗口和场景变化生成候选帧。
2. 计算亮度、清晰度和感知哈希重复组。
3. 每个时间窗口保留较优帧，控制候选数量。
4. 若阈值过滤后为空，回退保留全片评分最高的一张。
5. 人工选择、拒绝或上传补充照片；算法指标不替代业务判断。

### 8.3 转写

- ASR 适配器必须输出完整中文文本及带材料 ID、起止毫秒的片段；不支持时间戳的适配器不能在正式验收中标记成功。
- 连续正文与时间锚片段分开保存，避免粗窗口边界直接污染问答正文。
- 用户可以修改文本和语义问答角色；角色是“问/答/未知”，不声称声学身份。
- 片段按时间排序、范围合法且覆盖材料处理范围；证据跳转验收允许约 ±3 秒。

### 8.4 候选事实

- 事实提取使用明确列出的转写、视觉描述和材料版本，返回结构化候选事实和已有证据引用。
- 输出引用必须存在、属于当前检查且时间合法；否则任务失败或候选标为待核实。
- AI 只创建候选事实，不自动确认，不生成最终处罚结论。
- 重试对相同输入幂等，不编辑人工或已确认事实。
- 用户可以确认、撤回、编辑、删除或人工补充，并查看详细版本历史。

## 9. AI 能力适配

### 9.1 能力协议

分别定义：

- `ASRProvider`
- `LLMProvider`
- `VisionProvider`
- `EmbeddingProvider`
- `OCRProvider`（增强能力，MVP 非 P0）

初始实现包括固定响应适配器、通用 HTTP 适配器和禁用/人工模式。业务代码只依赖能力协议，不依赖供应商名称。

### 9.2 配置和秘密

- 每种能力独立配置 endpoint、model、timeout、并发、重试和密钥环境变量。
- 应用只读取进程环境；不在代码、数据库、日志或测试报告中读取或打印本地配置文件内容。
- 数据库只保存非秘密的适配器名、模型名和配置指纹。
- AI 运行记录保存能力、对象 ID/修订、输入/输出摘要、耗时、状态和错误分类，不保存完整请求或密钥。

### 9.3 输出校验和提示安全

- AI 输出先通过 JSON Schema/Pydantic 校验，再校验业务引用和权限。
- 知识文档、模板、转写和用户输入全部作为不可信数据放入提示隔离区，不得覆盖系统指令。
- 助手请求携带当前路由、对象 ID 和可选的用户选中文本；服务端不得信任这些上下文，必须按当前会话重新加载对象并校验访问权限，选中文本还要验证确实属于该对象当前修订。
- 助手没有任何写、确认、删除或导出工具，只能返回文本、引用和类型化导航动作。
- 导航动作仅允许打开材料并跳时、照片、事实、法规段落和文书。服务端和前端都要再次验证目标权限。

## 10. 法规知识实现

### 10.1 入库

1. 管理员上传文本型 PDF/DOCX 和元数据。
2. 原件校验并不可变保存，缺少来源信息时标为“待核验”。
3. PDF 按坐标块、栏和阅读顺序提取；不能直接使用普通逐行文本顺序。DOCX 按标题、段落和表格提取。
4. 识别章、节和“第×条”，保存条款全文、页码和页内定位。
5. 构建规范化文本、`pg_trgm`/全文索引；Embedding 独立排队。
6. 解析版本和索引版本可重建，但不改变原件或有效状态。

现有消防法 PDF 必须通过结构测试确认第 1 至第 74 条可按正确栏序定位；它初始化为“待核验”，不能仅凭内容或文件存在变成已确认现行版本。

### 10.2 检索

三路召回：

1. 条款号、文档标题和类别的结构化精确匹配。
2. 规范化子串、`pg_trgm` 和中文词法召回。
3. Embedding 可用时按同一模型指纹加入余弦相似召回。

固定规则融合候选。LLM 只能解释已召回条款，返回的原文必须是数据库条款正文的可验证子串，条号和来源不能由模型生成。没有候选达到固定正/负用例校准阈值时，返回结构化 `not_found`。Embedding 或 LLM 故障时，精确和词法检索仍可用。

### 10.3 来源回溯

结果包含问题概述、条款号、原文、简短适用理由、文档标题、来源状态和原文件入口。点击法规证据打开保存条款并可跳转原 PDF 页码；DOCX 至少打开原件和保存的条款文本。

## 11. 模板识别和 Word 生成

### 11.1 模板安全与版本

- 三份原始模板按 ACCEPTANCE 的 SHA-256 初始化并在每次模板测试前后校验。
- 管理员上传创建新的不可变模板版本，不修改文件本体。
- 拒绝宏、外部模板关系、嵌入执行对象、压缩炸弹和损坏包。
- 映射绑定精确模板版本；已确认映射不可原位编辑。

### 11.2 识别器

识别 OOXML 段落、run、表格、合并单元格、固定中文标签、普通空格、字面复选框、图片占位文本、正文锚点和页脚域。

填充策略包括：标签后插入 run、填充空单元格、替换字面占位、替换复选框字符、锚点后插入正文、复制整页重复区块、图片区域和页脚域。

每个候选区域显示稳定中文业务名、位置预览、填充方式和置信度。管理员可以改名、禁用、补充、修正和确认。生成前重新核对锚文本和上下文；结构不匹配时失败并要求修正，不能猜位置继续写。

识别器遇到不支持的文本框、嵌套对象、异常合并单元格或其他未知结构时，必须保留模板原件，展示具体对象位置和“不支持/需人工映射”的可操作提示；不得崩溃、静默忽略或生成看似成功的错误文书。

### 11.3 生成通则

- 每次从原模板副本开始，局部修改必要 OOXML 节点。
- 保留纸型、页边距、固定文字、字体、字号、表格、段落和页眉页脚。
- 内容采用自然段落和可增长表格行，不使用会裁切长文本的固定高度。
- 签名锚点永远不填。
- 输出先写临时 DOCX，完成 OPC/关系、结构和渲染检查后原子发布。
- 失败不替换上一份成功导出。

### 11.4 监督检查记录表

- 编号以完整文本写入编号值区。
- 日期统一格式化为 `YYYY年M月D日`。
- 七个字面检查类型中恰好一个 `☑`、其余六个 `□`。
- 单位、地址、负责人、联系人和检查内容填入原表格对应空白区域。
- 现场检查的一般说明按已确认映射填入原模板备注/说明区域，不另造版面。
- 普通值不增加下划线。
- 保留原 5×5 合并表格、A4 纸型和固定文字；行允许自然增高和跨页。
- 两处签名段落保持空白。

### 11.5 照片文书

- 以原始单页证据区块为母版，一张照片复制一页，页间显式分页。
- `image_region` 映射保存由模板留白计算并经管理员预览确认的最大宽高。
- 图片在该区域内等比例缩放、居中，不裁切、不拉伸、不遮挡文字。
- 删除字面 `[此处插入照片]`，每页恰有一个实际图片关系。
- 反映内容、说明按照片独立；拍摄地点、摄制人、拍摄时间、确认人从照片文书公共字段重复填入每页。
- 标签 run 不下划线，值 run 加下划线。

### 11.6 询问笔录

- 询问起止时间都输出完整日期时间，跨日不省略日期。
- 表头填充值加下划线，固定标签保持不下划线。
- 每个“问：”和“答：”作为独立自然段，使用人工确认角色和当前正文，并按模板规则带下划线。
- 长表头和正文自然换行、跨页，不使用固定高度或文本框。
- 原模板页脚同时含复杂 `PAGE` 域和 `fldSimple NUMPAGES` 域；生成器保留并校正两者，设置打开时更新域，并在服务端渲染后校验缓存显示值，不能沿用原模板缓存的 1/4。
- 签名位置保持空白。

### 11.7 文书检查

每次生成执行三层检查：

1. OOXML 结构：包、关系、纸型、页边距、表格、run 格式、图片、字段代码和签名留白。
2. 服务端渲染：在安装所需字体的隔离环境中用 LibreOffice 转 PDF、Poppler 转逐页 PNG，检查页数、空白页、越界、图片和页脚。
3. 发布门槛：在单位指定的 Word/WPS 和字体环境中实际打开、更新域、保存并逐页人工检查。

当前非 Windows 环境如果缺少中文字体，渲染必须明确失败，不能把标点和表格可见误报为视觉通过。

## 12. API 与前端设计

### 12.1 API 原则

- 版本前缀 `/api/v1/`。
- 会话 Cookie + CSRF，不把长期访问令牌放在浏览器存储。
- 对可编辑资源使用期望 revision/`If-Match`。
- 创建任务、重试、生成、确认、导出和删除接受 `Idempotency-Key`。
- 领域命令返回稳定错误码和安全提示。

### 12.2 主要 API 边界

| API | 职责 |
|---|---|
| `/auth/*` | 登录、退出、当前用户 |
| `/inspections/*` | 检查 CRUD、共享字段、参与人员和检查历史 |
| `/inspections/{id}/materials/*` | 流式上传和材料列表 |
| `/materials/{id}/download|play` | 权限内原件下载和 Range 播放 |
| `/materials/{id}/jobs/{step}` | 提交和查询独立处理步骤 |
| `/jobs/{id}/retry` | 只重试失败步骤 |
| `/transcripts/{id}/*` | 完整文本、片段和角色编辑 |
| `/inspections/{id}/facts/*` | 事实、状态、版本和证据 |
| `/knowledge/*` | 管理员文档管理及权限内检索 |
| `/templates/*` | 管理员模板和映射版本 |
| `/inspections/{id}/documents/*` | 创建、列表和状态 |
| `/documents/{id}/fields|photo-items|qa-items` | 文书公共/专属内容 |
| `/documents/{id}/generate|confirm|export` | 显式文书命令 |
| `/assistant/query` | 只读回答、引用和导航动作 |
| `/admin/audit` | 管理员全局最小化审计 |

### 12.3 前端页面

- 登录。
- 检查列表、创建和概况。
- 检查工作台：材料、转写、事实与证据、法规、三类文书、历史。
- 管理员：用户、知识、模板映射、任务和审计。
- 主要业务页面右侧固定只读 AI 助手。

共享字段在文书页面明确标注“检查共享字段”。编辑前提示将使三类文书退回初稿，提交调用检查 API。文书专属字段显示来源和人工锁定状态。

长任务使用短轮询和可见的 queued/running/succeeded/failed/superseded 状态；MVP 不引入 WebSocket。所有前端路由由 Nginx `try_files` 回退到 `index.html`，保证直接访问和刷新不出现 404。

## 13. 权限、安全与删除

### 13.1 统一权限

建立集中式 `InspectionAccessPolicy`：

- 检查员查询始终限制为 `owner_id=current_user.id`。
- 管理员可访问全部检查并管理用户、模板和知识库。
- 材料、转写、事实、证据、任务、文书和文件都从所属检查反查权限。
- 嵌套写入不相信客户端提交的 `inspection_id`。
- 明确管理员接口对检查员返回 403；未登录返回 401；无权猜测的业务对象可返回 404。
- AI 上下文只通过同一授权查询服务构建。

### 13.2 会话和生产配置

- Cookie：`Secure`、`HttpOnly`、`SameSite=Lax`；所有修改请求启用 CSRF。
- 密码使用 Django 推荐强哈希，优先 Argon2。
- 首个管理员通过一次性命令交互创建，无默认账号、默认密码或开发密钥。
- 生产环境缺少强随机密钥、HTTPS、可信 Host 或安全 Cookie 时拒绝启动。
- 登录失败限速并写最小化安全审计。

### 13.3 文件和解析安全

- 容器非 root 运行，数据目录不可执行。
- 校验路径穿越、Windows 盘符/UNC、NUL、Unicode 混淆、双扩展名和伪 MIME。
- 对 DOCX ZIP 项数量、解压总量和压缩比设置上限。
- 禁止宏、外部关系、嵌入执行对象和不受控子进程。
- 错误响应不返回堆栈、密钥、会话令牌、完整业务正文和服务器绝对敏感路径。

### 13.4 详细历史与永久审计

- `domain_change_history` 为当前业务回溯服务，可保存事实、字段和状态的完整旧值/新值，受检查权限保护，并随检查删除。
- `audit_events` 为永久安全审计，从一开始只保存必要掩码摘要；普通业务删除不影响它。
- 手机号、证件号等敏感字段按类型掩码；完整媒体、完整转写、完整 AI 请求和密钥从不进入永久审计或普通日志。

### 13.5 删除流程

1. API 返回影响预览，用户二次确认并填写原因。
2. 锁定对象，立即撤销访问，取消排队任务并使运行任务租约失效。
3. 将精确目标目录原子移动到同盘隔离区。
4. 数据库事务删除业务数据、派生数据和详细历史，并写最小化删除审计。
5. 事务失败则恢复隔离文件；成功后由幂等清理任务永久删除隔离目录。
6. 物理文件清理完成前保持 `deleting`；清理失败进入 `delete_failed` 并可安全重试。只有数据库目标和隔离目录中的目标文件都已删除后，API/UI 才报告删除成功。

删除单个材料时，删除其转写、画面、播放副本和派生结果。已经人工确认或人工创建的事实不静默删除：移除缺失证据、标记待核实，并使依赖文书退回初稿。删除整个检查时删除全部业务数据和文件，仅保留最小化审计。

## 14. 可观测性、备份、恢复和升级

### 14.1 可观测性

MVP 不部署独立 ELK 或 Prometheus 集群，提供：

- `/health/live` 和 `/health/ready`。
- PostgreSQL、数据目录、磁盘空间、FFmpeg、LibreOffice 和配置预检。
- 结构化 JSON 日志及 request/job correlation ID。
- 本地日志轮转。
- 管理员任务面板：队列时间、运行时间、失败原因、尝试和重试。
- 磁盘不足、任务积压、连续失败和备份过期告警。
- 可选 `/metrics` 供单位已有监控拉取。

业务审计与运行日志彻底分离。

### 14.2 备份

为保证数据库与文件一致，单机备份采用短暂维护窗口：

1. 禁止新写入并等待当前 Worker 写任务结束。
2. 使用 `pg_dump` 导出数据库。
3. 归档 originals、derived、knowledge、templates、documents 和必要渲染产物。
4. 生成应用版本、数据库迁移号、文件清单和 SHA-256 manifest。
5. 写入权限受限、加密的独立磁盘或内网备份目录。
6. 不备份进程秘密、AI 密钥或活动会话。

默认保留建议为 7 个日备份和 4 个周备份，可配置。删除墓碑/删除审计纳入恢复检查，避免从旧备份恢复后使已删除检查重新对外可见。

### 14.3 恢复和升级

恢复流程：校验 manifest → 恢复 PostgreSQL → 恢复文件 → 执行迁移 → 数据库/文件双向一致性检查 → 启动 → 登录、文件、任务和文书冒烟测试。

升级前必须成功备份；停止 Worker 领取新任务后执行迁移。迁移失败不启动新版本。升级不自动重新处理已有文书，已确认文书保持不变；初稿只在用户主动操作时使用新规则。正式交付前至少完成一次“备份—清空测试环境—恢复—端到端冒烟”的演练。

### 14.4 离线交付包

- 预构建并签名/校验的 OCI 镜像归档。
- Compose 文件、Nginx 配置、初始化/备份/恢复/升级脚本。
- Python/前端依赖锁文件和第三方许可清单。
- 数据目录权限和容量检查工具。
- 字体检测清单；方正小标宋等授权字体不提交仓库，由单位提供并安装到服务端渲染环境和 Windows 验收环境。
- 内网部署、配置、备份、恢复、升级和故障处理手册。

## 15. 实现顺序

每一阶段遵循“先写失败测试 → 实现最小闭环 → 运行本阶段及全部既有回归”。阶段退出条件未满足时不进入下一阶段。

| 阶段 | 内容 | 退出条件 |
|---|---|---|
| 0. 验收骨架 | 权威文件哈希、模板不可变、固定时钟/UUID、临时数据库/目录、固定 AI、测试分类、覆盖清单 | 普通测试一键离线运行；真实 AI 和 Windows 验收只能显式启用 |
| 1. 高风险可行性门槛 | 两视频解码/兼容代理/方向、双栏 PDF 条款顺序、三模板识别、最小 OOXML 生成、短/长询问笔录和三图三页 | 关键媒体、PDF 和 DOCX 风险在目标环境验证可行；原件哈希不变 |
| 2. 账户与检查领域 | 登录、角色、检查 CRUD、共享字段、参与人员、乐观锁、详细历史和永久审计 | A/B 检查员、管理员、未登录权限矩阵通过 |
| 3. 文件与任务基础设施 | 流式上传、格式校验、受控下载、任务表、Worker、租约、幂等、两任务并发和删除框架 | 两任务不串数据；重复提交不重复；崩溃后可恢复 |
| 4. 媒体流水线 | 探测、兼容播放、音频、转写、关键画面、视觉适配器和独立状态 | 原件保留；每视频至少一帧；任一步失败可单独重试 |
| 5. 事实与证据 | 候选/确认/撤回/人工补充、角色确认、证据关系、历史和精确失效 | 两视频同检查时材料不跳错，时间误差约 ±3 秒 |
| 6. 知识库 | 元数据、双栏解析、条款切分、词法/向量检索、待核验和 not_found | 固定正向/负向用例通过，引用可回溯原文 |
| 7. 模板管理 | 三模板强制识别、映射编辑/确认/版本、未知形态降级 | 必需业务区域齐全，中文名称稳定，原件不变 |
| 8. 文书领域和生成 | 字段来源、人工保护、同步失效、三类 OOXML、结构/渲染检查、历史产物 | 三类结构断言和长内容测试通过；失败不替换旧产物 |
| 9. 完整前端和助手 | 检查工作台、媒体/证据交互、文书编辑、管理员页面、只读助手、SPA 路由 | 外网阻断仍完整工作；助手不写、不越权；路由刷新无 404 |
| 10. 运维和安全收尾 | 失败注入、日志脱敏、生产启动校验、备份恢复、升级、磁盘/性能 | 安全、降级、恢复和升级演练通过 |
| 11. 正式验收 | 固定响应 E2E、真实四类 AI 一次、Windows Word/WPS 逐页检查和视觉基线 | ACCEPTANCE 全项有通过证据后才可声明 MVP 完成 |

高风险可行性阶段只形成测试和可丢弃的技术验证，不恢复历史实现，也不提前固化未经测试的业务架构。

## 16. 测试方案

### 16.1 测试入口

建议最终提供四个明确入口：

1. `fast`：后端单元、API/数据库集成、安全、固定 AI、前端单元和 OOXML 结构；每次提交运行。
2. `local-acceptance`：使用本地真实视频和法规 PDF，AI 可以固定响应；素材缺失时报告“未运行”，不得算通过。
3. `real-ai`：显式授权后读取进程环境变量，运行真实 ASR、LLM、视觉和 Embedding。
4. `windows-docx`：单位指定的 Word/WPS + 字体环境的发布视觉门槛（名称为历史沿用）。

一键测试输出 JUnit、验收覆盖报告和脱敏工件清单。真实视频和关键帧不进入普通 CI 工件或仓库。

### 16.2 单元测试

- 共享字段唯一来源、修订和三文书同步失效。
- 事实依赖、照片专属和问答专属失效矩阵。
- 人工字段合并和明确覆盖规则。
- 事实状态转换和非法转换。
- 日期、跨日时间和完整编号格式。
- 材料/证据时间区间和跨检查关系校验。
- 文件名、MIME、大小、数量和压缩包边界。
- 任务幂等键、租约、fencing token 和过期结果拒绝。
- AI Schema、空响应、畸形 JSON 和引用校验。
- 双栏排序、条款切分、召回阈值和 not_found。
- 模板锚点、中文业务名和填充策略。
- OOXML 节点、run 下划线、复选框、图片比例和页脚域。

### 16.3 API、数据库、文件和并发集成测试

- 检查员 A/B、管理员、未登录用户对列表、详情、猜测 ID、文件、Range、任务、历史、导出和助手的完整权限矩阵。
- 删除后业务行、详细历史和精确磁盘文件消失，长期审计仅保留掩码摘要。
- 上传或生成失败不留下可下载半成品。
- 相同任务重复提交只产生一个逻辑任务和一份结果。
- 两个 Worker 同时处理不同材料，不串 inspection/material/storage_key。
- 租约过期旧 Worker 不能写回。
- 共享字段、事实、选图、问答和映射变化产生正确文书状态及原因。
- 无关变化、后台轮询和重复回调不能改变已确认文书。

并发测试使用真实 PostgreSQL 多连接，不使用单连接事务模拟。

### 16.4 安全测试

- URL 编码路径穿越、绝对路径、Windows 盘符/UNC、NUL、Unicode 混淆、双扩展名和伪 MIME。
- 损坏 MP4、图片、音频、PDF、DOCX；DOCX 解压数量、体积和外部关系。
- Cookie `Secure`/`HttpOnly`/`SameSite`，修改请求 CSRF。
- canary 密钥、Cookie、手机号、证件号和绝对路径触发异常后，扫描日志、错误响应、审计摘要和测试报告。
- 知识文档或转写包含提示注入文本时，仍被作为数据；助手不能写入或越权。
- Playwright 阻断所有公网请求，验证无 CDN、在线字体、远程脚本和在线激活依赖。

### 16.5 失败与降级测试

为 ASR、LLM、视觉、Embedding 和 DOCX 生成分别提供超时、鉴权失败、限流、畸形响应和进程异常 failpoint：

- 只将对应步骤标记失败并显示安全原因。
- 原件、人工内容和其他步骤成功结果保持。
- 恢复后只重试失败步骤。
- 重试不产生重复事实、任务、画面或照片页。
- 空值、旧结果和部分临时文件不能显示成功。
- DOCX 原子发布失败时，上一成功导出仍可用且标记旧修订。

### 16.6 DOCX 结构测试

通用：

- 有效 OPC/ZIP，关系和 Content Types 完整，无宏、外部资源、悬空关系或调试定位键。
- 纸型、页边距、固定文字、字体、字号、表格和页眉页脚来自模板。
- 原模板哈希前后不变。
- 签名区域保持空白。
- 长内容无固定高度裁切。

监督检查记录表：

- 编号完整文本、日期格式正确。
- 单位、地址、负责人、联系人和检查内容在正确节点。
- 恰好一个 `☑` 和六个 `□`。
- 普通值不下划线；原 5×5 合并表格保持。

照片文书：

- 三张照片生成三页，每页一个证据区块和一个实际图片。
- 不残留 `[此处插入照片]`。
- 逐图内容独立，公共字段三页一致。
- 标签不下划线，值下划线。
- OOXML `cx/cy` 与原始像素比例一致且不超过映射图片区域。

询问笔录：

- 表头值和独立问答段落按规则下划线。
- 最终角色和文字来自人工确认内容。
- 固定短夹具生成少于四页，长夹具生成超过四页。
- 页脚同时存在 `PAGE` 和 `NUMPAGES` 域，并从最终渲染逐页核对 `第 N 页 共 M 页`。
- 长地址、长单位、跨日时间和长正文无结构破坏。

### 16.7 DOCX 视觉测试

1. 固定 Windows 构建、Word/WPS 版本、宋体/仿宋/楷体/微软雅黑/方正小标宋版本、DPI 和导出方式。
2. 使用虚构、非敏感、确定性夹具生成三类文书和询问笔录短/长样本。
3. 在 Word/WPS 更新域、保存并逐页导出图片。
4. 业务人员以 100% 缩放检查缺字、裁切、重叠、异常空白、表格、图片、页码、下划线和签名留白。
5. 首次确认的 DOCX、逐页图片和环境清单成为后续基线。
6. 后续在同一环境执行页数、尺寸和像素差异；差异页必须人工复核。
7. 真实关键帧文书只存受限本地工件，不能提交包含敏感画面的视觉基线。

### 16.8 真实 AI 与真实视频验收

- 只有显式启用且四类端点环境变量齐全时运行；配置缺失显示“未运行”。
- 不读取或打印本地配置文件，不记录密钥、完整请求、完整媒体或完整转写。
- 把 `1.mp4` 与 `4.mp4` 放入同一次检查。
- `1.mp4` 播放副本正向横屏，证据仍引用原材料和原时间轴。
- 每视频至少一张候选；自动检查可解码、尺寸、亮度/清晰度下限，业务价值由人工确认。
- 按 acceptance-cases JSON 复核必须语义和禁止推断，不以另一个生成模型作为唯一裁判。
- 分别点击两视频事实证据，确认材料正确且播放器时间与目标相差不超过约 3 秒。
- 正向法规用例核对条款、原文和来源；负向用例明确未找到。
- 运行记录只保存模型/端点类别、时间、输入文件哈希、测试版本和脱敏结论。

### 16.9 端到端流程

固定 AI E2E 和正式真实 AI E2E 均执行：

登录 → 创建检查 → 同时上传两视频 → 等待独立处理 → 编辑转写并确认角色 → 确认事实 → 分别关联两视频证据 → 检索法规 → 生成三类文书 → 修改人工字段 → 重新生成并验证人工内容保留 → 确认文书 → 导出 → 修改共享字段使三类文书退回 → 再确认后修改选图或问答，验证只影响对应文书。

同时验证检查员 B 无法从列表、直链、文件 URL 或助手访问检查员 A 数据；检查员调用管理员接口得到 403；业务路由刷新无 404；助手导航由前端执行且无写能力。

## 17. 验收覆盖矩阵

| ACCEPTANCE | 自动化证据 | 最终人工/环境证据 |
|---|---|---|
| §2 自动化门槛 | 四类测试入口、固定 AI、结构测试、覆盖报告 | 检查测试报告完整性 |
| §3 权限与安全 | 401/403/404、IDOR、文件、助手、恶意文件和日志 canary | 抽查生产 TLS 与日志配置 |
| §4 媒体和事实 | 固定 AI 精确断言、本地真实素材输出、材料/时间证据链 | 核心语义、禁止推断、关键帧可用性 |
| §5 法规知识 | 双栏解析、分类、正/负查询、来源定位、待核验和提示注入 | 业务方确认固定条款原文 |
| §6 模板识别 | 三模板字段清单、置信度、映射编辑、原件不变 | 管理员界面确认中文名称和区域 |
| §7.1 记录表 | OOXML 节点、复选框、下划线和模板几何 | Windows 逐页视觉 |
| §7.2 照片文书 | 三页三图、比例、来源、无占位符 | 三张关键帧清晰且无遮挡 |
| §7.3 询问笔录 | 问答、下划线、短/长页数和页脚域 | Word/WPS 更新域并逐页核对 |
| §8 状态保护 | 失效矩阵、人工保护、幂等和后台稳定性 | UI 原因提示抽查 |
| §9 失败降级 | 五类 failpoint、局部重试和原子发布 | 操作入口和错误文案检查 |
| §10 交付门槛 | 清洁环境 E2E、路由、备份恢复和升级 | 真实 AI 一次、Windows 文书和完整走查 |

机器可读覆盖清单应把每条验收项映射到测试文件、测试层级、夹具和最近结果；“未运行”不能计为通过。

## 18. 需要补充的测试夹具和外部输入

这些不是新的产品决策，但必须在对应阶段补齐：

- `knowledge-cases.json`：查询词、正向/负向、预期条款号、原文或原文哈希、来源定位和允许的理由要点。
- 五类能力固定响应集：成功、空结果、畸形、超时、鉴权失败、限流和部分响应。
- 普通 CI 使用的非敏感小型 MP4/JPEG/PNG/MP3/WAV/文字，以及损坏、伪 MIME、超限和恶意文件名样本。
- 三种比例的非敏感图片，用于照片 DOCX 的确定性结构和视觉回归。
- 询问笔录少于四页、超过四页、超长字段、跨日时间和交替问答夹具。
- 七种检查类型参数化数据。
- 内部制度 DOCX/PDF、缺来源文档、提示注入文档和损坏知识文件。
- 未知模板夹具：内容控件、文本框、下划线字符、嵌套表格和损坏 DOCX。
- 检查员 A、检查员 B、管理员和未登录会话夹具。
- 日志脱敏 canary：AI 密钥、Cookie、手机号、证件号和绝对路径。
- 单位最终指定的 Windows、Word/WPS 精确版本和字体版本。
- 首次 Windows 视觉验收后形成的确定性基线样本和环境清单。

Windows 精确版本和法规固定用例不阻塞主体开发，但阻塞最终视觉基线和法规检索签字。

## 19. 主要风险与控制

| 风险 | 控制 |
|---|---|
| 模板无占位符且 run 级格式复杂 | 精确版本映射、锚点指纹、局部 OOXML、原件哈希和结构测试 |
| 中文字体或查看器差异导致分页漂移 | 固定 Windows/Word/WPS/字体环境，服务端只作预检，首次确认样本冻结基线 |
| 询问笔录页码缓存错误 | 保留 PAGE/NUMPAGES 域、开启字段更新、服务端和 Windows 逐页核对 |
| 双栏法规抽取顺序错误 | 按坐标和栏排序，固定条款结构测试与原文定位 |
| HEVC 浏览器兼容和异常旋转 | 等长 H.264 播放副本、有效方向覆盖、原材料时间轴证据 |
| AI 幻觉或供应商不可用 | 能力协议、Schema/引用校验、候选状态、固定响应、人工路径和独立重试 |
| 重试/并发产生重复或旧结果覆盖 | 幂等键、租约、fencing token、输入修订和原子发布 |
| 共享字段和文书状态不一致 | 同一事务写入共享字段、退回文书、原因和历史 |
| 文件/数据库删除不一致 | 精确目标、同盘隔离、事务恢复和幂等清理 |
| 删除后审计泄露敏感正文 | 详细历史随业务删除；永久审计从源头只存掩码摘要 |
| 单机磁盘或升级故障 | 磁盘预警、短暂停写一致备份、manifest、恢复演练和迁移失败拒绝启动 |

## 20. 完成定义

只有同时满足以下条件，才能声明 MVP 完成：

- 所有普通自动化测试和机器可读验收覆盖报告通过。
- 权威模板和真实素材哈希保持不变。
- 权限、安全、并发、幂等、失败降级和删除审计测试通过。
- 两段真实视频在同一检查内完成材料和约 ±3 秒时间证据定位验收。
- 法规固定正向和未找到用例通过，测试 PDF 仍标“待核验”。
- 三类 DOCX 结构和服务端渲染检查通过。
- 单位指定 Windows + WPS/Word + 字体环境完成全部页面人工检查，并保存首次视觉基线。
- 获授权的真实 ASR、LLM、视觉和 Embedding 各至少成功运行一次。
- 从全新环境完成 ACCEPTANCE §10 的完整端到端流程。
- 内网部署、配置、备份、恢复、升级说明齐全，并实际完成一次恢复演练。

本方案确认前不进入业务代码实现；确认后仍按阶段测试先行推进，每阶段以可重复验收证据而非功能演示作为退出条件。
