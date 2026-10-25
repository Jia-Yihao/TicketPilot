# 安全与威胁模型

## 信任边界

```text
不可信：用户文本、请求体中的 context、模型输出、第三方渠道回调的整个请求体（含 `from` / `tenant_id` / `message_id`）、客户自己写的档案字段
可信：已验证身份上下文、服务端签名校验与身份映射结果、服务端规则、数据库、审批记录、权威退款记录
```

模型输出只作为槽位建议；它不能调用工具、不能决定资格、不能批准退款。

## 威胁与对策

| 威胁 | 对策 | 代码位置 |
|---|---|---|
| 跨租户读取订单/任务/工单 | 所有查询强制租户匹配；不匹配统一 404 | `services/orders.py`、`runtime._require_visible`、`services/tickets.py` |
| 越权退款 | 退款资格仅基于服务端订单状态与余额 | `services/refund_policy.py` |
| 模型编造订单号/金额 | 规则优先；PydanticAI 只填槽；订单必须真实且属于调用者 | `agent/routing.py`、`agent/pydantic_agent.py` |
| 审批后偷换金额/订单 | 摘要绑定订单、金额、币种、订单版本、政策版本 | `services/approvals.py` |
| 审批后订单被修改 | 执行前重新校验资源版本，不一致则审批 `INVALID` | `RefundExecutionService.execute` |
| 自批 | `requested_by == reviewer` 一律拒绝，**所有角色含 admin 均无例外**；审批人必须属于任务租户 | `ApprovalService.decide` |
| 伪造身份头 | 生产配置强制 `IDENTITY_ADAPTER=oidc` 且 `DOCS_ENABLED=false`；OIDC 适配器未实现时应用拒绝启动，不会带着 Header 身份进入生产 | `core/config.py`、`main.py` |
| 审批长期有效 | TTL（默认 30 分钟），过期置 `EXPIRED` 且不执行；当前为惰性判断，无后台任务（P1） | 同上 |
| 重复退款 | 订单级在途审批唯一约束 + 订单行锁 + 台账原子 claim + 余额条件更新；第二笔并发退款被拒绝 | `models/approval.py`、`repositories/commerce.py`、`repositories/tool_executions.py`、`refunds.py` |
| 超时后重复提交 | 进入 `PENDING_CONFIRMATION`，走对账，不盲重试 | `RefundExecutionService` |
| 副作用被自动重试 | 执行器读 Manifest：有副作用的工具除非声明 `idempotency = "required"`，否则不重试；「结果未知」任何情况下都不重试 | `tools/executor.py::_may_retry` |
| 模型书写金额或单号 | 回执只用工具返回值里 `result_schema` 声明过的字段；缺字段则整条回执不渲染 | `tools/executor.py::render_receipt` |
| 消息重复投递 | `dedupe_key` 唯一约束 + 条件认领；投递是 at-least-once，由下游对账幂等吸收 | `repositories/outbox.py::claim` |
| 消息与业务事实不一致 | 消息与业务事实同事务写入，不会出现"改了却没排消息" | `services/outbox.py::enqueue` |
| 重复创建工单 | `(tenant_id, idempotency_key)` 唯一 + 服务端幂等 | `models/ticket.py`、`services/tickets.py` |
| 过期政策被引用 | 检索过滤 `ACTIVE` + 有效期 | `retrieval/search.py` |
| 统计查询越权读他人数据 | 租户与归属谓词由执行器注入、语句自带即拒绝；表/列/函数白名单；值全部参数化；连接强制只读 | `sql/guard.py`、`sql/service.py` |
| 坐席侧越权看他人案件 | 坐席端点一律要求 `support` 角色；案件按 `tenant_id` 过滤，跨租户返回 404 | `api/v1/handoffs.py::_require_seat`、`services/handoff.py` |
| 伪造入站渠道身份（邮件 / IM 回调自称租户） | HMAC-SHA256 覆盖 `timestamp + '.' + 原始请求体`，时间戳在签名材料内且偏移默认 300 秒；身份只来自服务端 `CHANNEL_*_IDENTITY_MAP`，正文里的 `tenant_id` / `user_id` 一律无效 | `channels/base.py::verify_hmac`、`resolve_identity` |
| webhook 重放制造重复工单 / 退款 | `(channel, channel_message_id)` 唯一 + 内容摘要：同 ID 同内容读回已存回复，换内容 409 `CHANNEL_MESSAGE_ID_REUSED`，仍在处理 409 `CHANNEL_MESSAGE_IN_FLIGHT` | `channels/store.py::claim`、`models/channel_message.py` |
| 猜测未启用渠道的存在 | 未启用与未知渠道返回同一个 404；矩阵只披露能力与 `enabled`，不披露密钥 | `channels/gateway.py::adapter`、`api/v1/channels.py` |
| 客户偏好里塞入 PII | 称呼 / 联系时段 / 每条兴趣命中 PII 直接 422 拒写；`history_summary` 走 `redact()` 掩码后落库，宁可存成掩码也不存原文 | `services/memory.py::_clean_text`、`core/redaction.py` |
| 未经同意的主动触达 | `proactive_opt_in` 默认 `false`，第一道就返回 `NO_CONSENT`；再经退订 / 重复 / 冷却 / 当日额度逐层抑制，任一命中就不出声 | `services/proactive.py::emit` |
| 坐席代客户操作通知 | 通知按 `(tenant_id, user_id)` 定位，跨用户与跨租户 404；`POST /memory/events` 与 `GET /memory/rules` 额外要求 `support` 角色 | `api/v1/memory.py` |
| 主动话术被塞入任意字段 | `context` 是六个键的白名单，越界键不是丢弃而是 422；标量转字符串后截 200 字并强制脱敏；模板引用了缺字段则整条抑制，不渲染半句话 | `services/proactive.py::_context` |
| 记忆删除后仍被读出 | `DELETE /memory` 硬删档案与通知行，并把队列中 `PENDING` / `PROCESSING` 的 `proactive.confirmed` 置 `CANCELLED`；留下的 `memory.delete` 审计事件只含资源 ID 与字段名，不含正文 | `services/memory.py::delete` |
| 客户档案里的自由文本借"个性化"进回复 | 档案只有四种可枚举的模板效果（称呼开头 / 商品卡重排 / 按 `tone` 裁追问 / 把兴趣变成一条追问），没有"把档案拼进提示词"这条路：`preferred_name` 与每条兴趣在**写入时**过 PII 检查（命中 422），`history_summary` 落库前已是掩码文本且根本不在读取列里，兴趣必须被 `CatalogService.resolve()` 解析成真实商品才生效（解析不出就静默丢弃，不回显客户写的字符串）；Trace 里只落原因码与字段名，不落档案值 | `agent/personalization.py`、`services/memory.py::_clean_text`、`services/catalog.py::resolve` |
| 提示注入诱导越权 | 模型无工具权限；写操作过 Tool Gateway；服务端规则兜底；注入模式命中只留痕，不参与规则判定 | `tools/gateway.py`、`core/guard.py` |
| 无来源的金额/时效承诺 | 输出护栏检查金额、时效、百分比与承诺词；无工具/卡片/任务来源的片段替换为「需确认」并追加提示 | `core/output_guard.py` |
| 高频请求拖垮服务 | 租户与 IP 双令牌桶，超限返回 429 + `Retry-After`（进程内预算，多 worker 会放大） | `api/middleware.py` |
| 超大请求体 | JSON 默认 256 KiB、multipart 默认 5 MiB，超限返回 413；SSE 等无 body 的 GET 不受影响 | `api/middleware.py`、`core/config.py` |
| 安全事件不可追溯 | 输入/输出/传输层拒绝统一写 `security_events`（PII 只存掩码摘要与指纹） | `models/security_event.py`、`api/v1/security.py` |
| 错误信息泄漏 | 统一错误信封；500 不返回内部细节 | `core/errors.py` |
| 审计缺失 | 工单、审批、退款、副作用工具均落 `audit_events` | `repositories/audit.py` |

## 审批摘要

```text
digest = sha256(canonical_json({
  action: "REFUND",
  order_id,
  amount_cents,
  currency,
  resource_version,
  policy_version
}))
```

`canonical_json` 使用 `sort_keys=True`、紧凑分隔符，确保跨进程稳定。

## 受控统计查询的两道防线

自然语言转 SQL 是本项目新增的、唯一由模型输入驱动的数据出口。它靠两道互相独立的防线收敛：

```text
用户问题
  → 计划（planner）：说不出记录集就 NO_PLAN，不进入 SQL 阶段
  → 构建（builder）：产出 AST，不产生字符串 SQL
  → 守卫（guard）：形状 / 作用域 / 绑定
  → 执行（executor）：只读连接
  → 台账（sql_queries）
```

第一道是守卫（`app/sql/guard.py`）：

1. **形状**——只允许单条 `SELECT`；表、列、函数各有白名单；`LIMIT` 必须存在且不超过 200。
2. **作用域**——`tenant_id` 与 `user_id` 不可被读到（它们不在任何数据集的可读列里，语句一旦提及即 `UNKNOWN_COLUMN`）。谓词由 `scope()` 注入，再由 `require_scope()` 复核其必须是顶层合取项，防止被 `OR` 之类的结构稀释。
3. **绑定**——所有值都是参数；唯一允许进入 SQL 字符串的文本是固定列表里的日期格式（`%Y-%m` 等）。

第二道是数据库（`app/sql/service.py::_execute`）：PostgreSQL 上 `SET TRANSACTION READ ONLY`，SQLite 上 `PRAGMA query_only=ON`。即使守卫出现解析漏洞，这条语句也写不进去。SQLite 的 `query_only` 在用完后被显式关闭并归还连接；关不掉时连接直接作废。

台账只保存**语句本身**与绑定值的摘要（`params_digest`），不保存绑定值；返回给用户的行也经过 `redact_payload` 处理。

## 坐席侧的数据边界

转人工之后，人工坐席能看到的案件比 Agent 更多，所以边界收得更紧：

- 转人工的 7 个端点先过 `_require_seat`，只认 `support` 角色；`POST /api/v1/memory/events`、`GET /api/v1/memory/rules` 与 `PUT /api/v1/memory/rules/{event_type}` 同样只认 `support`；Outbox 的两个端点放宽到 `support` 或 `approver`；
- 案件、队列、放弃事件、指标一律按 `identity.tenant_id` 过滤；
- 跨租户访问按"不存在"处理（404），不返回 403，避免通过状态码枚举他人案件；
- 移交包（`HandoffBundle`）随接管事件一起落库，字段来自服务端会话状态，不含凭据。

## 规则文案的信任边界

主动规则的文案由坐席写入（T10，2026-10-24），它会**逐字进入客户收到的通知**，所以这是一个新的运营侧输入面。边界如下：

- **模板不是自由文本通道**：正文由 `str.format_map` 渲染，占位符只能取 `order_id` / `ticket_no` / `amount` / `summary` / `status` / `product` 六个键；键名在**写入时**就校验（越界 → 422 `RULE_TEMPLATE_UNKNOWN_KEY`），值来自坐席上报的 `context`，落库前经 `redact()` 脱敏。位置参数（`{}`）与不配对花括号同样在写入时拒（422 `RULE_TEMPLATE_INVALID`）——它们本来会在 `emit()` 里抛 `ValueError` 变成 500。
- **同意闸不可拆**：`requires_confirmation` 不在请求体白名单里，出现在请求体中直接 422（`extra="forbid"`），不是静默忽略。ADR-014 承诺的「客户先说要不要，消息才会出去」因此不会被配置面绕开；新建的规则固定 `requires_confirmation = true`。
- **触发器不可配置**：事件类型是代码所有（`ProactiveEventType`，三个值），接口只接受这三个值，**不能新增**。否则「什么事实可以触发一条消息」就被交给了配置面，而新增触发能力需要有消费者。
- **审计不存正文**：每次改动只记 `event_type` / `fields` / `enabled` / `cooldown_minutes` / `template_sha256` / `created`——**不含模板原文**（通知正文可能带订单号与金额，再复制一份进 `audit_events` 等于多一处泄漏面）。422 的错误消息也只回违规的**字段名**，不回整条文案。
- **改动不回溯**：写完之后只影响**下一条**通知，已经生成的通知行逐字不变。
- **规则不能删除**：删掉会让 `notification.rule_id` 指向不存在的行，历史断裂；「不再发」由 `enabled = false` 表达。

## 消息队列的数据边界

事务性 Outbox 里的一行是一次**尚未送达的承诺**，坐席看得到它，因此边界与坐席接口一致：

- `GET /api/v1/outbox` 与 `POST /api/v1/outbox/deliver` 要求 `support` 或 `approver` 角色；
- 队列按 `identity.tenant_id` 过滤，跨租户表现为 0 条而不是 403；
- 队列只暴露消息元数据（事件类型、聚合 ID、重试次数、最后错误），不暴露载荷里的业务内容；
- worker 目前有两个消费者：`refund.pending_confirmation` 走对账，`proactive.confirmed` 走 `ProactiveSender`（T7-4 接线，见 [ADR-017](adr/ADR-017-proactive-outbound-seam.md)）；默认传输 `none` 拒绝一切发送，被拒的行退回 `PENDING` 并把拒绝码写进 `last_error`，耗尽后进死信——宁可把它留成一笔看得见的欠账，也不猜一个投递目标；
- 投递前的认领是条件更新（`WHERE status='PENDING'`），两个 worker 并不会各推一次同一行；推送本身仍是 at-least-once，重复由对账幂等吸收。

## 身份适配器与生产 fail-closed

当前 `app/core/security.py` 读取开发 Header。生产设置必须显式选择已验证身份来源：

```text
ENVIRONMENT=prod
IDENTITY_ADAPTER=oidc                       # dev_headers 会被配置校验拒绝
REFUND_PROVIDER=http                        # mock 会被配置校验拒绝
DOCS_ENABLED=false                          # /docs、/redoc、/openapi.json 关闭
AUTO_CREATE_TABLES=false                    # 必须走 Alembic
DATABASE_URL=postgresql+psycopg://...       # SQLite 会被拒绝
```

但 OIDC 适配器与真实退款渠道尚未实现，`create_app` 会在启动时直接抛 `RuntimeError`。因此当前版本**无法以 prod 模式对外服务**——这是刻意的 fail-closed：宁可启动失败，也不让开发 Header 身份、mock 退款和公开文档悄悄进入生产。

## 日志与密钥

- 不记录 API Key、令牌、`.env` 内容、完整连接串。
- 本次实现未引入真实模型调用；启用 `AGENT_MODE=pydantic_ai` 时需由部署环境注入 `ANTHROPIC_API_KEY`，代码不读取也不打印它。
- 审计事件保存动作、资源、结果和必要的业务摘要，不保存完整支付凭证。

## P0 修复（2026-10-23）

真实场景审计发现的两个 P0 已在代码与迁移中修复，并用 PostgreSQL 实跑验证：

### 1. 订单级并发双退款

- `approvals` 增加部分唯一索引 `uq_approvals_open_per_resource(resource_type, resource_id, action_type) WHERE status IN ('PENDING','APPROVED')`；同一订单同一动作最多一个开放审批。SQLite 与 PostgreSQL 均支持，迁移 `7d3a9c1e5b42`。
- 创建审批先在仓库层预检，并捕获 `IntegrityError` 转 `ConflictError(APPROVAL_ALREADY_OPEN)`；并发插入由数据库兜底。
- 执行时对订单行 `SELECT ... FOR UPDATE`，再次检查是否存在其他在途/成功退款；发现则取消台账、作废审批，返回人工复核。
- 台账发送用条件更新原子 claim：只有 `PENDING → SENT` 的赢家可以调用外部服务，`attempt` 在同一语句内自增。
- 余额扣减用条件更新：`refundable_cents >= amount` 才扣减并递增订单版本，输家不会覆盖赢家结果。
- 审批新增 `CONSUMED` 终态：明确成功或明确失败后消费审批，释放订单级额度；`UNKNOWN` 对账期间保持 `APPROVED`，继续阻塞第二笔退款。

验证：`tests/test_postgres_concurrency.py` 在真实 PostgreSQL 上先断言同订单重复申请被订单级索引挡下（第二个任务 `FAILED`、开放审批仍只有一条），再让两个审批人并发审批**同一个任务**，断言恰好一个成功、另一个抛 `ConflictError`（HTTP 409），只有一条退款记录、余额只扣一次、订单版本只递增一次；`refund-waits-for-approval` 与 `refund-approved-and-verified` 使用不同订单，当前 Golden Set 在 SQLite 与 PostgreSQL 16.15 上均为 **40/40**（2026-10-25）。

### 2. 审批权限与自批

`ApprovalService.decide` 现在按固定顺序校验：

1. 审批人必须具备 `approver` 或 `admin` 角色
2. 审批属于该任务（跨任务 404）
3. 审批所属任务与审批人同租户（跨租户 404，避免枚举）
4. 审批仍为 `PENDING`，且未过期
5. `requested_by != reviewer.user_id`——**admin 也不能自批**

### 3. 生产 fail-closed

`environment=prod` 时，配置校验拒绝 SQLite、`AUTO_CREATE_TABLES=true`、`IDENTITY_ADAPTER=dev_headers`、`REFUND_PROVIDER=mock`、`DOCS_ENABLED=true`；`create_app` 对尚未实现的 OIDC/HTTP 适配器直接拒绝启动。`/docs`、`/redoc`、`/openapi.json` 在生产配置下不可达。

## 已知缺口

- 开发 Header 身份仍是本地默认；真实 OIDC/JWT 适配器与真实退款渠道尚未实现，因此 prod 模式目前只能验证“拒绝启动”，不能对外服务
- 无任务创建幂等键：同一文本请求执行两次会生成两个任务（P1）
- 限流是**进程内**令牌桶（默认租户 600 次/分、IP 300 次/分），多 worker 部署时预算按进程放大，跨进程配额需要 Redis
- 体积上限按整个请求体计（JSON 256 KiB、multipart 5 MiB），不是解析后的单文件大小；附件类型白名单与 EXIF 剥离未实现
- 输入护栏只识别已知注入/绕过模式并留痕，不拦截；输出护栏按「任务/卡片/工具」粗粒度判定来源，有来源时整段放行，不做逐句引用绑定
- 审批过期仍在操作时惰性判断，无后台过期扫描；退款对账已由 Outbox worker 驱动，但 worker 需常驻进程或手动触发，未接入调度器与告警
- 无真实支付渠道签名/回执验证；无密钥轮换与 KMS 集成
- 同步执行写操作期间取消不能中断外部调用
- 并发回归覆盖单进程双线程 + 真实数据库行锁；多进程/多实例部署尚未单独压测
- 未配置 CORS / TrustedHost / TLS
- Tool Gateway 未校验实际调用参数与审批 `action_digest` 一致；当前退款 handler 读取审批载荷，风险受控，但新增副作用工具会继承该缺口（P2）
- 退款规则仍把已 `SUCCEEDED` 的订单当作阻塞，部分退款无法继续；`SHIPPED` 订单不受 30 天窗口约束（P2）
- 统计查询路径**不含模型**：计划由确定性启发式产出，识别不了的问法一律 `NO_PLAN` 转人工。这是覆盖面限制而非漏洞，但意味着"自然语言任意转 SQL"目前不成立；将来若引入模型规划，模型也只许产出结构化计划，不得直接产出 SQL 字符串
- 统计查询没有租户级配额与频次上限，也没有 `sql_queries` 台账的保留期与清理策略（同一进程内反复提问会持续写库）
- 守卫的自研 SQL 解析器只覆盖单条 `SELECT`；若将来放开 `WITH`、子查询或 `UNION`，白名单与作用域复核必须同步扩展，不能只放宽解析器
- 渠道验签是本仓库自定的 `sha256=HMAC(timestamp + '.' + raw_body)`，**不是**微信 / 企业微信 / 邮件服务商的真实协议；不校验来源 IP、没有回调地址登记、没有平台送达回执，`CHANNEL_*_SECRET` 的轮换也没有工具支持
- 网页的两个入口已并入同一道幂等闸（T8，2026-10-24）：`POST /api/v1/support/messages` 不再直调 Agent，与 `POST /api/v1/channels/web/messages` 共用 `ChannelGateway.accept_web`，同一个 `X-Channel-Message-Id` 无论从哪条进来都只受理一次。**残留的去重边界是「调用方不给键」**：不带该 Header 的提交一次跑一轮 Agent 且不留 `channel_messages` 行，所以重放防护取决于客户端是否生成稳定幂等键——控制台前端已自带（同一句话在同一段会话里复用），直连 API 的脚本与第三方默认没有
- 认领发生在归一化之后，因此"同一 `message_id` 反复验签失败"不落库、没有计数，只能靠日志与全局限流观察；签名密钥泄露即可伪造任意发件人身份的入站消息
- 主动通知的出站投递**地址只来自服务端配置**：渠道取自客户档案的 `contact_channel`（渠道名，填地址一律 422），地址是 `CHANNEL_*_IDENTITY_MAP` 的反向查找，队列 `payload` 里的任何指向都改不了去向；未同意（`NO_CONSENT`）、已静音（`UNSUBSCRIBED`）、未选渠道（`NO_CHANNEL`）都在发送前重读并拒绝，**不回落到默认渠道**。默认 `CHANNEL_OUTBOUND_TRANSPORT=none` 拒绝一切发送，`prod` 启动时拒绝除 `none` 之外的任何值，所以"没配置就自称送达"这条路是堵的
- `SENT` **不是送达事实**：它的含义是"渲染后的内容已交给配置的 transport 并写成功"，没有运营商或邮件服务商的回执，因此不能作为"客户已收到"的证据。审计事件 `proactive.delivered` 只记 `address_digest`（SHA-256 前 12 位）与字符数，不落明文地址；但演示用的 `local_file` 落盘文件**里有完整地址与正文**，它是 PII，必须放在与工作目录同等的安全边界内、按日志对待，不能进版本库（`.tmp/` 已忽略）
- 目前只有 `none` 与 `local_file` 两个传输实现，**没有真实服务商协议**：接 SMTP / 微信 / 企业微信是 `OutboundTransport` 的第四个实现，不改队列语义；`EMAIL` / `WECHAT` / `WECOM` 之外的渠道没有地址来源，选了也只能得到 `[NO_ADDRESS_SOURCE]`
- 记忆档案是硬删（`DELETE` 删行），但 `audit_events` 里的 `memory.read` / `memory.update` / `memory.delete` 与 `security_events` 不受该删除影响；审计留存期与合规清理策略仍未定义（与下面的留存缺口同源）
- 档案进答案（T9，2026-10-24）是一条**只读、无审计**的覆盖层：`agent/personalization.py` 用 `select(具体列)` 只取 `preferred_name` / `preferences`，不实例化 ORM 实体、不给没档案的客户建行、不碰 `updated_at`，也不写 `memory.read`——`MemoryService.get()` 是为"客户自己查看档案"设计的，让它在每轮问答里跑一遍会把合规事件变成噪音。代价写在 [ADR-018](adr/ADR-018-memory-in-answer-personalization.md)：**写进过档案的客户，其历史 trace 不再逐字可回放**；无档案客户的回复逐字不变，所以 101 条评测与回放自检仍是硬断言
- 客户级配额缺失：`PUT /api/v1/memory` 与 `POST /memory/events` 共用全局限流桶，没有"每客户每日写次数"这类独立上限
- **规则文案没有双人复核**（T10，2026-10-24）：审批那一层有四眼（[ADR-004](adr/ADR-004-approval-binding.md)，`admin` 也不能自批），但**规则文案这一层没有**——一个 `support` 角色就能改掉本租户所有客户收到的主动通知口径（文案、冷却、启停），没有第二个人过目、没有审批留痕以外的复核。这一层还缺：规则版本表与一键回滚（审计里只有 `template_sha256`，改错了手打回去）、按租户灰度与阈值外置。取舍与理由见 [ADR-019](adr/ADR-019-proactive-rule-editable-surface.md)
- 坐席端点无独立审计事件：接管/释放/关闭写入 `audit_events`，但"谁看过哪个案件"没有留痕（P2）
- 生产前仍需渗透测试、依赖审计与数据留存合规评估
