# API 契约

业务接口 Base URL: `/api/v1`；健康检查位于 `/health`。交互式文档：`/docs`（`DOCS_ENABLED=false` 时关闭，生产配置强制关闭）。

## 身份

开发适配器要求：

| Header | 必填 | 说明 |
|---|---|---|
| `X-Tenant-Id` | 是 | 租户 |
| `X-User-Id` | 是 | 用户 |
| `X-Roles` | 否 | 逗号分隔，如 `approver,support` |

租户与用户在服务端从身份上下文读取，**不接受请求体覆盖**。

## 客服

只读问答走 `SupportAgent`，不写入任务台账；退款与工单意图转交 `TaskRuntime`。

客服 Agent 按 **租户 + 用户** 保存会话槽位（订单号、退款金额、待补槽位），所以同一用户可以连续追问而不必重复报订单号。

### POST `/api/v1/support/messages`

```json
{
  "message": "订单 ORD-DEMO-1001 买的是什么商品？桌板材质和承重是多少？",
  "conversation_id": "conv_optional",
  "order_id": "ORD-DEMO-1001"
}
```

`message` 1–2000 字符；`conversation_id` 最长 80；`order_id` 最长 80。响应：

```json
{
  "conversation_id": "conv_0123456789ab",
  "reply": "AeroDesk 电动升降桌 D1（SKU-AD-1001）售价 129.00 CNY。……",
  "intent": "PRODUCT_QUERY",
  "agent_mode": "local",
  "context_cards": [
    {
      "kind": "product",
      "card_id": "PROD-AD-1001",
      "title": "AeroDesk 电动升降桌 D1",
      "subtitle": "SKU-AD-1001 · 家居办公",
      "summary": "双电机升降桌，1200×600mm 竹纤维桌面，四档记忆高度，适合居家办公。",
      "badges": ["24 个月质保", "上海仓现货", "含安装指南"],
      "fields": {
        "product_id": "PROD-AD-1001",
        "sku": "SKU-AD-1001",
        "category": "家居办公",
        "price_cents": 12900,
        "currency": "CNY",
        "stock": 42,
        "warehouse": "上海仓",
        "warranty_months": 24,
        "status": "ACTIVE"
      },
      "sections": [
        {"id": "specs", "title": "规格参数", "preview": "桌面尺寸：1200 × 600 mm……", "chars": 144}
      ],
      "source": "catalog://products",
      "total_chars": 865
    }
  ],
  "task": null,
  "suggestions": ["这款商品的质保范围", "查询订单物流", "退款条件是什么？"],
  "slots": {
    "order_id": "ORD-DEMO-1001",
    "product_id": null,
    "amount_cents": null,
    "pending_intent": null,
    "pending_request": null
  },
  "inherited_slots": ["order_id"],
  "clarification": null,
  "personalization": null
}
```

`intent` 取值：`PRODUCT_QUERY`、`POLICY_QA`、`ORDER_QUERY`、`LOGISTICS_QUERY`、`REFUND_ELIGIBILITY`、`REFUND_STATUS`、`DELIVERY_ISSUE`、`REFUND_REQUEST`、`TICKET_CREATE`、`SELF_ANALYTICS`、`UNKNOWN`。取值来自 `app/agent/intents.yaml` 意图目录，目录新增一行即可启用新意图，不必改代码。

- 只读卡片最多返回摘要与章节预览，正文需按需请求。
- `REFUND_REQUEST` / `TICKET_CREATE` 会创建任务并返回 `task`；退款通常为 `WAITING_APPROVAL`。
- `SELF_ANALYTICS` 走受控 Text-to-SQL（见下），返回 `kind = "analytics"` 的统计卡片；被拒时回复转人工并带 `handoff`。
- 订单与政策卡片按身份过滤，跨租户/跨用户返回 `404`。

可选 Header：

| Header | 说明 |
|---|---|
| `X-Channel-Message-Id` | 与 `POST /api/v1/channels/web/messages` **同一道幂等闸**：带上它，本次投递写进 `channel_messages`，重放读回已存回复并在响应头标 `X-Channel-Deduplicated: true`；不带则一次调用跑一轮、表里不留行。语义、409 与接管规则见下方「Web 渠道」一节 |

会话字段：

| 字段 | 说明 |
|---|---|
| `slots` | 该租户 + 用户当前保存的槽位。`order_id` / `amount_cents` 会被下一轮沿用；`pending_intent` 是被澄清打断、等待用户补齐的意图 |
| `inherited_slots` | 本轮**没有**在消息里出现、而是从上一轮槽位继承的字段（`order_id` / `amount_cents`） |
| `clarification` | 本轮在追问槽位时非空：`reason`（`MISSING_ORDER_ID` / `INVALID_AMOUNT`）、`prompt`、`expected_slot`、`resumable_intent` |
| `personalization` | 本轮按客户档案改过答子时非空：`effects`（原因码）+ `fields`（用过的档案字段名）。没有档案、没产生效果、或 `MEMORY_PERSONALIZATION_ENABLED=false` 时为 `null`。**不含任何档案值本身**，语义见下 |

槽位优先级：**消息里写的订单号 > 请求体的 `order_id` > 上一轮继承的槽位**；金额同理。用户补齐订单号后，上一轮被打断的意图自动续跑。

示例（缺少订单号时）：

```json
{
  "conversation_id": "conv_0123456789ab",
  "reply": "请提供订单号（格式如 ORD-DEMO-1001），我再查这笔退款的状态。……",
  "intent": "REFUND_STATUS",
  "agent_mode": "local",
  "context_cards": [],
  "task": null,
  "suggestions": ["查询订单", "退款条件是什么？", "转人工"],
  "slots": {
    "order_id": null,
    "product_id": null,
    "amount_cents": null,
    "pending_intent": "REFUND_STATUS",
    "pending_request": null
  },
  "inherited_slots": [],
  "clarification": {
    "reason": "MISSING_ORDER_ID",
    "prompt": "请提供订单号（格式如 ORD-DEMO-1001），我再查这笔退款的状态。",
    "expected_slot": "order_id",
    "resumable_intent": "REFUND_STATUS"
  }
}
```

### `personalization`：档案改了哪几处（四个原因码）

客户长期记忆会影响回复，但只允许四种**可枚举**的效果，且全部是模板或形状变化。下面是真实响应（`demo:alice` 先 `PUT /memory` 写入 `{"preferred_name":"张小雅","preferences":{"tone":"concise","product_interests":["TrailLite Pro 摄影背包 28L"]}}`，再问「TrailLite 的参数是什么？」）：

```json
{
  "reply": "你好，张小雅。找到 2 款相关商品：TrailLite 通勤背包 22L、TrailLite Pro 摄影背包 28L。……",
  "context_cards": [{"card_id": "PROD-TL-2003", "…": "…"}, {"card_id": "PROD-TL-2002", "…": "…"}],
  "suggestions": ["这款商品的质保范围", "查询订单物流"],
  "personalization": {
    "effects": ["CARDS_REORDERED", "SUGGESTIONS_TRIMMED", "ADDRESSED"],
    "fields": ["preferred_name", "product_interests", "tone"]
  }
}
```

（只列出与本轮个性化有关的四个字段，其余字段照旧；`effects` 按实际生效顺序给出，`fields` 是排序后的去重字段名。）

同一句问话在没有档案时是 `"找到 2 款相关商品：……"`、卡片 `PROD-TL-2002 → PROD-TL-2003`、追问 3 条、`personalization: null`。

| `effects` 取值 | 读的字段 | 触发条件 | 对响应的影响 |
|---|---|---|---|
| `ADDRESSED` | `preferred_name` | 本会话第一轮（`last_intent is None`；澄清轮也算） | `reply` 前加「你好，{称呼}。」，只出现一次 |
| `CARDS_REORDERED` | `preferences.product_interests` | 兴趣经 `CatalogService.resolve()` 命中目录，且该商品卡已在 `context_cards` 里但不在首位 | 该卡移到第一位，其余相对顺序不变，`reply` 文本不动 |
| `SUGGESTIONS_TRIMMED` | `preferences.tone` | `tone = "concise"` 且 `suggestions` 多于 2 条 | `suggestions` 裁到前 2 条，`reply` 不动 |
| `INTEREST_SUGGESTION` | `preferences.product_interests` | 命中的兴趣既不是本轮已回答的商品，也没有已经作为商品卡出现，且 `suggestions` 里还没有它 | `suggestions` 首位插入「{目录商品名} 的质保范围」 |

契约上的三条硬约束：

- **只报码与字段名，不报值**。`fields` 是 `preferred_name` / `product_interests` / `tone` 这几个字段名，响应体里不会出现 `history_summary`、不会出现客户写的兴趣字符串。`GET /api/v1/traces/{trace_id}` 里对应的 span 是 `phase = generate` / `name = memory.personalize`，属性同样只有 `effects` / `fields` / `matched_product_id` / `first_turn`。
- **客户自由文本不进回复**。`history_summary`、`contact_window`、目录认不出的兴趣字符串一律不被回声；兴趣必须先被 `CatalogService.resolve()` 认出来（只认完整商品名或 `product_id` / `sku`），所以生效的是目录里的规范名。
- **读档案不产生任何写副作用**。Agent 侧只 `select` 两列，不走 `MemoryService`，因此问答轮次不会新增 `customer_profiles` 行、不改 `updated_at`、也不写 `memory.read` 审计——`GET /api/v1/memory` 那条「会写 `memory.read` 审计」的行为仍然只属于客户自己读档案。

`POST /api/v1/channels/web/messages`（控制台入口）返回同一份形状，两条入口共用一条认知路径。`agent_silent = true`（人工接管静默）时不读档案，`personalization` 恒为 `null`。设计与代价见 [ADR-018](adr/ADR-018-memory-in-answer-personalization.md)。

### GET `/api/v1/support/cards/{kind}/{card_id}`

`kind` 取 `product` / `order` / `policy`。返回整张卡片元数据（不含章节正文）。未知 `kind` 返回 `422`；卡片不存在或无权访问返回 `404`。

### GET `/api/v1/support/cards/{kind}/{card_id}/sections/{section_id}`

按需加载单个章节：

```json
{
  "kind": "policy",
  "card_id": "doc_312e1aaa412143ba",
  "title": "退款政策",
  "section": {
    "id": "section-2",
    "title": "退款条件",
    "content": "- 订单状态必须为 SHIPPED（已发货）或 DELIVERED（已签收）。……",
    "chars": 135
  },
  "source": "policies/refund-v3.md",
  "citation": {
    "type": "policy",
    "document_id": "doc_312e1aaa412143ba",
    "version": "v3",
    "title": "退款政策",
    "section": "退款条件",
    "source_uri": "policies/refund-v3.md",
    "effective_from": "2025-01-01",
    "effective_to": null
  }
}
```

商品与订单章节的 `citation` 为 `null`。

## 渠道

`app/channels/` 只负责三件事：验签并把传输身份换成服务端解析出的 `Identity`、按消息 ID 幂等认领、把同一份规范化回复渲染成渠道形状。`app/agent` 与 `app/services` 里没有按渠道的分支；渠道标签只作为溯源数据写进移交包与放弃事件。

### GET `/api/v1/channels`

任何身份可读。返回 8 个渠道的能力矩阵，未启用的渠道也在表里，只是 `enabled=false`：

```json
{
  "channels": [
    {"channel": "WEB", "enabled": true, "supports_stream": true, "supports_rich_card": true,
     "supports_voice": false, "max_length": null, "max_cards": 6,
     "sentence_char_limit": null, "formal": false, "offers_human_handoff": false},
    {"channel": "APP", "enabled": false, "supports_stream": true, "supports_rich_card": true,
     "supports_voice": false, "max_length": null, "max_cards": 6,
     "sentence_char_limit": null, "formal": false, "offers_human_handoff": false}
  ]
}
```

| 渠道 | 流式 | 富卡片 | 语音 | 字数上限 | 卡片数 | 语气 | 主动提示转人工 | 入站 |
|---|---|---|---|---|---|---|---|---|
| `WEB` | 是 | 是 | 否 | 不限 | 6 | 中性 | 否 | `POST /channels/web/messages` |
| `APP` | 是 | 是 | 否 | 不限 | 6 | 中性 | 否 | 未实现 |
| `EMAIL` | 否 | 否 | 否 | 不限 | 0 | 正式 | 否 | `POST /channels/email/webhook` |
| `WECHAT` | 否 | 否 | 否 | 200 | 0 | 中性 | 否 | `POST /channels/wechat/webhook` |
| `WECOM` | 否 | 否 | 否 | 200 | 0 | 中性 | 否 | `POST /channels/wecom/webhook` |
| `WHATSAPP` | 否 | 是 | 否 | 1024 | 3 | 中性 | 否 | 未实现 |
| `VOICE` | 否 | 否 | 是 | 120（正文截到 100 字再追加 14 字转人工提示） | 0 | 中性 | 是 | 未实现 |
| `SMS` | 否 | 否 | 否 | 70（正文 61 字 + ` 回复TD退订`） | 0 | 中性 | 否 | 未实现 |

`APP` / `WHATSAPP` / `VOICE` / `SMS` 只有渲染形状，没有入站适配器，调用其 webhook 返回 404。

渲染规则：`truncate_sentences()` 只在句读（`。！？!?；;`）边界切断，截断结果一定以 `…` 结尾，因此读者总能看出文字被删过；`context_references` 是卡片标题列表，邮件写成「参考资料」编号段落，IM 压成 `详情：1.x 2.y`，超出预算时退化为「详情：回复「详情」查看」，语音与短信不引用卡片。语音渲染先删 URL 与列表符号。

### POST `/api/v1/channels/web/messages`

请求与响应与 `/api/v1/support/messages` 完全一致（同一个 `SupportMessageRequest` / `SupportMessageResponse`）。可选 Header：

| Header | 说明 |
|---|---|
| `X-Channel-Message-Id` | 客户端生成的幂等键；带上它，重复投递读回已存回复并在响应头标 `X-Channel-Deduplicated: true`，不再跑第二轮 Agent |

不带该 Header 时这路由是透明包装。同一个 ID 换内容 → `409 CHANNEL_MESSAGE_ID_REUSED`；同一 ID 同一内容仍在处理中 → `409 CHANNEL_MESSAGE_IN_FLIGHT`（`retryable=true`，认领超过 `CHANNEL_DELIVERY_CLAIM_TIMEOUT_SECONDS` 后可被接管）。这两条 409 与接管各有用例守着（`test_channels.py` 的 `test_delivery_still_in_flight_conflicts_instead_of_running_twice` / `test_a_stale_claim_is_taken_over_and_completed`）。

**幂等闸只有一道，两条 web 路由共用**：`/api/v1/support/messages` 与 `/api/v1/channels/web/messages` 都经 `ChannelGateway.accept_web`，同一个 `X-Channel-Message-Id` 无论从哪条进来都算同一次投递——先用哪条、后用哪条都一样，第二次读回已存回复并回 `X-Channel-Deduplicated: true`，不会再跑一轮 Agent（`test_one_delivery_id_is_one_guard_across_both_web_entries`）。**没带这个 Header 的调用不去重**，一次请求跑一轮，也不在 `channel_messages` 里留行——服务端无法凭「文本长得一样」判定重复，去重的前提是调用方给出稳定 ID。控制台前端（`web/src/api.ts`）已改为调本路由并自带幂等键：同一句话在同一段会话里重发复用同一个键，改写一个字则换新键。

两条路由的响应体逐字一致（`test_web_channel_matches_existing_support_route`），差别只在「带不带键」。旧路由保留是因为它写在公开契约与既有脚本里，废弃属破坏性变更。

### POST `/api/v1/channels/{channel}/webhook`

`channel` 取 `email` / `wechat` / `wecom`（大小写不敏感）；`web`、`app` 等没有入站回调，返回 404。

验签在解析之前：`X-Channel-Signature: sha256=<hex>` 覆盖 `X-Channel-Timestamp + '.' + 原始请求体字节`，时间戳在签名材料内，旧正文配新 Header 一样失败；允许偏移 `CHANNEL_WEBHOOK_MAX_SKEW_SECONDS`（默认 300 秒）。身份只来自 `CHANNEL_EMAIL_IDENTITY_MAP` / `CHANNEL_IM_IDENTITY_MAP`（`发件人 -> tenant:user`），请求体里自称的 `tenant_id` / `user_id` 无效。

```json
{"message_id": "em-doc-1", "from": "alice@example.com", "subject": "升降桌异响",
 "text": "订单 ORD-DEMO-1001 想问质保范围", "conversation_id": null,
 "locale": "zh-CN", "attachments": [{"attachment_id": "att-1", "filename": "a.png",
 "content_type": "image/png", "size_bytes": 10240}]}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `message_id` | 是 | 幂等键，最长 160；IM 也接受 `msg_id` / `MsgId` |
| `from` | 是 | 发件人；IM 也接受 `from_user` / `openid` / `FromUserName`，必须在映射表里 |
| `text` | 是* | 正文，截到 2000 字；*邮件按 `text` → `body` → `subject` 顺序取第一个非空，全空则 422 |
| `conversation_id` | 否 | 继续同一会话 |
| `attachments` | 否 | 最多 5 个，只留元数据；只有附件没有正文时返回 422 |

响应是渲染结果，不是 `SupportMessageResponse`：

```json
{
  "channel": "EMAIL",
  "channel_message_id": "em-doc-1",
  "conversation_id": "conv_47ce004696eb",
  "text": "您好：\n\n根据《退款政策》v3「适用范围」：……\n受理编号：conv_47ce004696eb\n处理时限：24 小时内首次回复\n",
  "subject": "TicketPilot 售后回复 · conv_47ce004696eb",
  "truncated": false,
  "deduplicated": false,
  "context_references": ["退款政策", "退货政策"],
  "intent": "POLICY_QA",
  "task_id": null
}
```

| 情况 | 状态码 | `error.code` |
|---|---|---|
| 缺签名头、时间戳坏、超出偏移、签名不符 | 401 | `UNAUTHORIZED` |
| 发件人不在映射表里 | 403 | `FORBIDDEN` |
| 渠道已启用但未配密钥 | 503 | `DEPENDENCY_UNAVAILABLE` |
| 映射值不是 `tenant:user` 形状（缺冒号、租户或用户为空） | 503 | `DEPENDENCY_UNAVAILABLE` |
| 渠道未启用、未知渠道名、非 webhook 渠道 | 404 | `NOT_FOUND` |
| 消息 ID 复用换内容 / 仍在处理 | 409 | `CHANNEL_MESSAGE_ID_REUSED` / `CHANNEL_MESSAGE_IN_FLIGHT` |
| 无正文、仅附件、字段超长 | 422 | `VALIDATION_ERROR` |

映射值形状错误是**配置错误**而不是客户端错误，所以回 503 而不是 422/403：宁可让对接方发现「配错了」，也不要让一个租户的 id 被当成另一个租户的用户名落库。两种 503 都发生在 `normalize()` 之前或之中，因此**不会留下 `channel_messages` 行**（认领在归一化之后）。

签名格式是本项目的约定，不是微信 / 企业微信 / 邮件服务商的真实协议；接真实平台必须替换 `app/channels/base.py` 的 `verify_hmac`。

## 客户记忆与主动服务

记忆接口只操作**当前身份自己**的档案（`X-Tenant-Id` + `X-User-Id`），跨用户与跨租户一律 404；`POST /memory/events`、`GET /memory/rules` 与 `PUT /memory/rules/{event_type}` 需要 `support` 角色，否则 403 `FORBIDDEN`（`admin` 角色因 `has_role` 的实现自动通过）。规则接口的边界是**租户级**——它操作本租户的事件规则，不碰任何客户的档案。

### GET / PUT / DELETE `/api/v1/memory`

`GET` 返回档案（没有档案时返回默认视图，**不创建档案行**，但会写一条 `memory.read` 审计事件，`resource_id` 记为 `none`）；`PUT` 是**部分更新**，未出现的字段保持不变，多余字段被 `extra="forbid"` 拒绝。这条 `memory.read` 只属于「客户自己读档案」——问答轮次里 Agent 读档案走 `app/agent/personalization.py` 的只读列查询，不写审计（见上文 `personalization`）：

```json
{"preferred_name": "张小雅", "locale": "zh-CN",
 "preferences": {"tone": "concise", "contact_window": "工作日 19:00-21:00",
   "product_interests": ["升降桌"], "contact_channel": "EMAIL"},
 "history_summary": "", "proactive_opt_in": true, "muted_event_types": [],
 "max_notifications_per_day": 2, "updated_at": "2026-10-24T05:38:48.309432"}
```

| 字段 | 规则 |
|---|---|
| `preferred_name` | 字符串，≤40 字，命中 PII 直接 422 拒绝 |
| `locale` | 形如 `zh-CN` / `en`，≤16 字 |
| `preferences` | 只接受 `tone`、`contact_window`、`product_interests`、`contact_channel` 四个键；`tone` 限 `concise` / `detailed`，`product_interests` ≤10 条且每条 ≤40 字并过 PII 检查，`contact_channel` 只能是 `Channel` 枚举名（`WEB` / `APP` / `EMAIL` / `WECHAT` / `WECOM` / `WHATSAPP` / `VOICE` / `SMS`，大小写不敏感、落库前统一大写）。**填地址不是笔误而是 422**：`contact_channel` 存的是"用什么渠道找我"，"送到哪个地址"由服务端配置反查，所以档案里永远不会出现手机号或邮箱。能真正送出去的只有 `EMAIL` / `WECHAT` / `WECOM`，其余渠道选了你也会在投递时收到 `[NO_ADDRESS_SOURCE]`，见下文出站投递 |
| `history_summary` | ≤500 字，**写库前强制脱敏**，落库的就是掩码文本 |
| `proactive_opt_in` | 布尔，默认 `false`；没有它任何主动触达都被 `NO_CONSENT` 挡下 |
| `muted_event_types` | 只能是 `REFUND_SETTLED` / `TICKET_UPDATED` / `DELIVERY_EXCEPTION` 的子集 |
| `max_notifications_per_day` | 整数，1–20，默认 3；计的是滑动 24 小时窗口内**未取消**的通知（`PENDING_CONFIRMATION` / `CONFIRMED` / `SENT`） |

`preferences` 的某个键传 `null` 是**删除这个键**，不是存一个空值——空字符串这四个键都不接受，所以"取消"和"填错"在数据上是两回事。`contact_channel` 被删掉后，主动通知在投递阶段会以 `[NO_CHANNEL]` 被拒，而不是回落到某个默认渠道。

`DELETE` 返回 204，并连带删除该客户的通知、把队列中 `PENDING` / `PROCESSING` 的 `proactive.confirmed` 消息置为 `CANCELLED`。

### GET `/api/v1/memory/export`

```json
{"profile": {"preferred_name": "张小雅", "proactive_opt_in": true},
 "notification_counts": {"CONFIRMED": 1}, "redacted": true}
```

只返回档案与按状态计数的通知，不导出正文；`redacted` 恒为 `true`，表示可见文本已经过脱敏。

### POST `/api/v1/memory/events`（坐席）

坐席提供事实，规则提供措辞：

```json
{"user_id": "alice", "event_type": "REFUND_SETTLED", "event_key": "rf_probe_1",
 "context": {"order_id": "ORD-DEMO-1001", "amount": "129.00"}}
```

```json
{"status": "PENDING_CONFIRMATION", "reason": null,
 "notification": {"notification_id": "ntf_946a703094214bb1", "event_type": "REFUND_SETTLED",
   "status": "PENDING_CONFIRMATION", "title": "退款到账提醒",
   "body": "您的订单 ORD-DEMO-1001 退款 129.00 元已到账，请注意查收。",
   "created_at": "2026-10-24T05:38:48.332621", "confirmed_at": null, "cancelled_at": null}}
```

被抑制时 `notification` 为 `null`，`reason` 取以下之一（判定顺序）：

| 顺序 | `reason` | 触发条件 |
|---|---|---|
| 0 | — （422） | `event_type` 不是三类之一，或 `event_key` 非法 / 命中 PII |
| 1 | `NO_RULE` | 本租户没有该事件类型的启用规则 |
| 2 | `NO_CONSENT` | 没有档案或 `proactive_opt_in=false` |
| 3 | `UNSUBSCRIBED` | 事件类型在 `muted_event_types` 里 |
| 4 | `DUPLICATE` | 同一用户 + 事件类型 + `event_key` 已有通知 |
| 5 | `COOLDOWN` | 距上次同事件通知不足规则的 `cooldown_minutes`（已取消的也算） |
| 6 | `FREQUENCY_CAP` | 滑动 24 小时内已达 `max_notifications_per_day` |
| 6.5 | — （422） | `context` 含白名单外的键或非标量值 |
| 7 | `TEMPLATE_CONTEXT_MISSING` | 模板引用了上下文里没有的字段 |

**顺序会带来一个反直觉现象**：入参校验的第 6.5 步排在六道抑制之后，所以对同一个用户重复投递时，你看到的往往是 `COOLDOWN` 或 `FREQUENCY_CAP` 而不是 422——非法 `context` 被抑制挡住了。要稳定触发校验，请用一个从未被触达过的 `user_id`。真跑时这里踩过一次，因此单独写出来。

`event_key` 是坐席侧的幂等键：1–120 个字符、去除首尾空白，命中 PII 直接 422。

`context` 走**显式白名单**，只接受 `order_id`、`ticket_no`、`amount`、`summary`、`status`、`product` 六个键——越界的键不是丢弃，而是 `422 VALIDATION_ERROR`（「写不进上下文的字段就不该出现在话术里」）。取值一律转成字符串：`bool` / `null` → `true` / `false` / `null`，数字直接 `str()`，字符串先截 200 字再**强制脱敏**落库；嵌套对象与数组拒绝。正文由规则模板渲染后截到 500 字，`title` 不来自规则，而是固定映射（`退款到账提醒` / `工单进展提醒` / `物流异常提醒`），所以坐席改模板只能改正文，改不了标题口径。抑制不写通知表，只留一条 `proactive.suppressed` 审计事件。

规则若把 `requires_confirmation` 置为 `false`，这条链就跳过客户确认：入队时状态直接是 `CONFIRMED`、`confirmed_at` 当场写入、并在同一次请求里投出 `proactive.confirmed` 队列消息。demo 租户的三条种子规则全部是 `true`，所以默认路径停在 `PENDING_CONFIRMATION`。

### GET `/api/v1/memory/notifications`

`?limit=1..100`（默认 50），返回 `{"notifications": [...]}`，只列本人通知、按创建时间倒序；被抑制的事件不在此列（它们只在审计里）。`confirm` 与 `cancel` 直接返回单个 `NotificationView`，不再套一层。

### POST `/api/v1/memory/notifications/{notification_id}/confirm` | `/cancel`

`confirm` 把状态推到 `CONFIRMED` 并在**同一事务**写入一条 `proactive.confirmed` 队列消息（`dedupe_key = proactive:<notification_id>`）；对已经 `CONFIRMED` / `SENT` 的通知重复确认是幂等的，直接返回当前视图。已 `CANCELLED` 的通知确认返回 `409 NOTIFICATION_CANCELLED`，其他非待确认状态返回 `409 NOTIFICATION_NOT_PENDING`。`cancel` 只改状态、对已取消幂等返回，且只对 `PENDING_CONFIRMATION` 有效——一旦确认过就再也取消不了，`409 NOTIFICATION_NOT_PENDING`，此时想撤回队列里的消息只能靠 `DELETE /api/v1/memory`（它把 `PENDING` / `PROCESSING` 的 `proactive.confirmed` 置为 `CANCELLED`）。

### GET `/api/v1/memory/rules`（坐席）

列出本租户规则：`rule_id`、`event_type`、`enabled`、`requires_confirmation`、`cooldown_minutes`、`template`，外加两个**读出来就少踩坑**的字段：

| 字段 | 含义 |
|---|---|
| `required_context` | 这条模板用到的 context 键（去重后排序）。它就是 `emit` 渲染时要凑齐的字段；缺一个，整条规则会被 `TEMPLATE_CONTEXT_MISSING` 静默抑制 |
| `updated_at` | 最后一次改动时间（ISO 8601）；改文案、冷却、启停都会刷新它 |

`requires_confirmation` 只读不可写：它是 ADR-014「客户先说要不要」的闸位。

### PUT `/api/v1/memory/rules/{event_type}`（坐席）

**按 `(tenant_id, event_type)` upsert**——租户 + 事件类型唯一，所以这条 `PUT` 既能改已有规则，也能给种子没覆盖到的租户开一条新规则，不需要改代码重灌。`event_type` 取值固定为代码所有（`REFUND_SETTLED` / `TICKET_UPDATED` / `DELIVERY_EXCEPTION`），**不能新增**，也不接受小写。

请求体只收三个字段，多余字段被 `extra="forbid"` 拒：

```json
{"template": "退款 {amount} 元已到账，订单 {order_id}，有问题直接回复这条消息。",
 "enabled": true,
 "cooldown_minutes": 0}
```

| 字段 | 约束 | 说明 |
|---|---|---|
| `template` | 字符串，非空，≤ **400** 字 | 400 而不是 500：`emit` 渲染后正文截到 500 字，写入时留余量，**不让存进去的文案被静默截尾** |
| `enabled` | 布尔 | 停用即 `emit` 抑制 `NO_RULE`；重新启用即恢复 |
| `cooldown_minutes` | 整数 0–1440 | 0 表示无冷却 |

`requires_confirmation` **出现在请求体里直接 422**（`VALIDATION_ERROR`，`details.errors[].type == "extra_forbidden"`），不是静默忽略——它是 ADR-014 的同意闸，做成开关等于允许运营侧把闸拆掉。**新建**的规则固定 `requires_confirmation = true`。

**写时校验模板**（这是把写面放出来的意义）：占位符必须落在 `order_id` / `ticket_no` / `amount` / `summary` / `status` / `product` 六个键内，且必须是纯键名。

| 拒绝 | 错误码 | 触发条件 |
|---|---|---|
| 未知 context 键 | 422 `RULE_TEMPLATE_UNKNOWN_KEY` | 引用 `{foo}` 之类；`details.allowed` 给出六个合法键 |
| 花括号不配对 / 非法占位符 | 422 `RULE_TEMPLATE_INVALID` | 例如 `{ 不配对` |
| 位置参数 `{}` | 422 `RULE_TEMPLATE_INVALID` | `{}` 在渲染时会抛 `ValueError: Format string contains positional fields`，所以写时就拒 |
| 带属性或下标的占位符 | 422 `RULE_TEMPLATE_INVALID` | `{order_id.upper}` / `{amount[0]}` 必须是纯键名 |
| 模板超长 | 422 `VALIDATION_ERROR` | > 400 字 |
| 空模板 / 空请求体 | 422 `VALIDATION_ERROR` | 新建规则还必须带 `template`，否则没有话可说 |
| 未知事件类型 | 422 `UNKNOWN_PROACTIVE_EVENT` | `details.allowed` 给出三个合法事件类型 |
| 并发写同一条 | 409 `RULE_ALREADY_EXISTS` | 唯一约束兜底 |

**任何一条校验失败，规则行逐字不变**（校验全部发生在落库之前）。

每次写入记一条 `proactive.rule.update` 审计，`detail` 只有 `event_type` / `fields` / `enabled` / `cooldown_minutes` / `template_sha256` / `created`——**不含模板原文**（通知正文可能带订单号与金额，再复制一份进审计等于多一处泄漏面）；`template_sha256` 足以回答「当时生效的是哪一版」。

**改动只影响下一条通知**，已经生成的通知（`PENDING_CONFIRMATION` / `CONFIRMED` / `SENT`）不会被回溯改写。规则**不能删除**：「不再发」由 `enabled = false` 表达——删掉会让 `notification.rule_id` 指向不存在的行。

设计与取舍见 [ADR-019](adr/ADR-019-proactive-rule-editable-surface.md)。

## 自助统计（受控 Text-to-SQL）

问句不会变成 SQL 文本再交给模型改写。链路固定为：规划器把问句写成**结构化查询计划**（数据集、度量、分组、时间窗、状态），构造器把计划写成 AST，守卫逐条检查 AST，执行器再注入租户与本人范围。计划写不出来的问题一律拒绝，不会被猜一个查询。

| 环节 | 位置 | 作用 |
|---|---|---|
| 规划器 | `app/sql/planner.py` | 问句 → 查询计划；不认识的问句返回 `None` |
| 构造器 | `app/sql/builder.py` | 计划 → SELECT 语句与参数 |
| 守卫 | `app/sql/guard.py` | 只放行受限 SELECT，其余按稳定原因码拒绝 |
| 执行器 | `app/sql/service.py` | 注入租户/本人范围、绑定参数、只读连接执行、落台账 |

数据集限 `orders` / `refunds` / `tickets`；度量限 `count` / `sum` / `count_and_sum`；分组限 `status` / `month` / `day`；时间窗限今天/昨天/本周/上周/本月/上月/今年/去年/最近 N 天/最近 N 个月。超出范围的问题按 `NO_PLAN` 拒绝。

### POST `/api/v1/analytics/query`

```json
{"question": "按月统计我的订单笔数"}
```

响应 `200`：

```json
{
  "query_id": "sql_8e3dd1cc6af640b0",
  "question": "按月统计我的订单笔数",
  "plan": {
    "dataset": "orders",
    "metric": "count",
    "group_by": "month",
    "status": null,
    "time_range": null
  },
  "sql": "SELECT STRFTIME('%Y-%m', created_at) AS bucket, COUNT(*) AS metric FROM orders WHERE tenant_id = :tenant_id AND user_id = :user_id GROUP BY STRFTIME('%Y-%m', created_at) ORDER BY bucket ASC LIMIT 200",
  "columns": ["bucket", "metric"],
  "rows": [{"bucket": "2026-10", "metric": 2}],
  "row_count": 1,
  "duration_ms": 0
}
```

`sql` 是为了让开发者看清实际执行了什么而回显的语句：租户与本人范围由执行器注入，所有取值以参数绑定，不出现在文本里。

被拒绝时返回 `422`，错误码 `SQL_REFUSED`，`details.reason` 是稳定原因码：

```json
{
  "error": {
    "code": "SQL_REFUSED",
    "message": "The question could not be answered from your records.",
    "retryable": false,
    "request_id": "req-...",
    "details": {"reason": "NO_PLAN"}
  }
}
```

拒绝原因码（`app/sql/errors.py` 的 `REASON_CODES`，共 18 个）覆盖语法与结构问题、越权范围问题与未知表列，例如 `NO_PLAN`、`NOT_SELECT`、`MULTI_STATEMENT`、`SUBQUERY`、`SET_OPERATION`、`STAR_SELECT`、`UNKNOWN_TABLE`、`UNKNOWN_COLUMN`、`SCOPE_MISSING`、`SCOPE_NOT_TOP_LEVEL`、`PARAM_NOT_ALLOWED`、`INLINE_LITERAL_NOT_ALLOWED`、`LIMIT_MISSING`、`LIMIT_TOO_LARGE`。原因码是稳定契约：新增走追加，不改旧值的含义。

### GET `/api/v1/analytics/queries`

查询本人（坐席为全租户）的统计台账。参数 `dataset`、`status`（`SUCCEEDED` / `REJECTED`）、`limit`（1–200，默认 50）、`offset`（≥0，默认 0）。返回 `items`、`total`、`limit`、`offset`，单条包含 `question`、`plan` 各字段、`sql_text`、`row_count`、`duration_ms`、`status`、`rejected_reason`、`trace_id`、`created_at`。

### GET `/api/v1/analytics/queries/{query_id}`

返回单条统计台账。别人的记录返回 `404`，不泄露存在性。

台账同时记录成功与拒绝：拒答也有 `query_id`、`status = "REJECTED"` 与 `rejected_reason`，所以"哪些问题答不了"是可统计的，而不是消失在对话里。

台账只保留 SQL 文本与参数摘要，**不保存结果行**；`row_count` 是执行时的行数。控制台的统计卡片据此展示计划、SQL 与行数，但不会重新执行查询。

## 转人工（坐席侧）

客服侧说"已转人工"只是话术；这一组接口是坐席真正接手用的。所有接口都要求 `support` 角色、且按租户隔离：别的租户的坐席看到的是"没有这条"，而不是别人的客户。

### GET `/api/v1/handoffs`

待办队列。参数 `status`（可重复，如 `?status=HANDOFF_PENDING&status=QUEUED`；缺省返回所有未结状态 `HANDOFF_PENDING` / `HUMAN_ACTIVE` / `QUEUED` / `RELEASED` / `NOTED`）、`queue`（队列名）、`limit`（1–200，默认 50）、`offset`。返回 `items`、`total`、`limit`、`offset`。

每条 `items` 都带 `channel`，记录这个案件**从哪个渠道找上门**（`WEB` / `EMAIL` / `WECHAT` / `WECOM`…，缺省 `WEB`）。渠道是溯源数据，不参与判断：走 webhook 的邮件工单不会在队列里伪装成网页来访，坐席一眼能看出该回邮件还是该在控制台打字。

### GET `/api/v1/handoffs/{event_id}`

单个案件的**上下文移交包**：原始问句、意图、已确认的槽位、已执行的只读动作与证据、转人工原因、接管状态机、`channel` 与 `bundle.channel`。坐席不需要回头翻对话就能继续。

### POST `/api/v1/handoffs/{event_id}/claim` | `/release` | `/close`

- `claim`：坐席接手案件，状态转 `HUMAN_ACTIVE` 并记录持有人；已被他人接手时返回 `409`。
- `release`：把会话交还给 Agent，状态转 `RELEASED`，可带 `{"note": "..."}`。
- `close`：结案，状态转 `CLOSED`，可带 `{"resolution": "..."}`，缺省 `RESOLVED`。

三个动作都返回更新后的移交包详情。

### GET `/api/v1/handoffs/metrics`

接管指标，按租户与会话计算：本租户会话数、转人工会话数与 `handoff_rate`、Agent 自结率 `auto_resolution_rate`、未结案件数 `open_cases`，以及按队列 / 原因 / 触发信号 / 小时与放弃渠道 / 意图的分布。回答"Agent 到底替人省了多少事"，同时不让某个租户的量影响另一个租户。

可选参数 `window_minutes`（1–129600）：会话按 `updated_at` 落在窗口内统计，转人工 / 放弃事件按 `created_at` 落在窗口内统计；`open_cases` 也只数窗口内的案件。缺省统计全部历史；响应回显 `window_minutes`。

### GET `/api/v1/handoffs/abandons`

被放弃的转人工记录（客户转人工后无人接手）。参数 `limit` / `offset`，返回 `items` 与 `total`；每条含 `channel`，与 `/handoffs/metrics` 的 `abandon_by_channel` 对得上。渠道同样只是溯源：`EMAIL` 的放弃说明邮件那边没人回，而不是网页出了 bug。

## 链路追踪

### GET `/api/v1/traces/metrics`

租户级 span 健康度，供运营看板使用。要求 `support` 或 `admin` 角色。参数 `window_minutes`（1–129600，按 `started_at` 过滤；缺省统计全部历史）。

返回 `window_minutes`、`trace_count`、`spans`、`error_spans`、`error_rate`（0–1）、`p95_latency_ms`、`by_kind`、`by_status`、`by_hour`。错误率按 **span** 计算，一条长 trace 里的单步失败不会被平均掉；P95 用最近秩（nearest-rank）取整。

### GET `/api/v1/traces/{trace_id}`

返回一次对话或一次任务的 span 树：`trace_id`、`conversation_id`、`spans[]`（`span_id`、`parent_span_id`、`kind`、`name`、`status`、`latency_ms`、`attributes`、`started_at`、`ended_at`）。

可见性：同租户的坐席或 `admin` 看本租户全部；客户只看自己的会话与任务。跨租户、越权访问都返回 `404`。

对话响应里的 `trace_id` 可直接用来查这一轮的检索、规划与执行过程。

## 安全事件

输入护栏、输出护栏与传输层拒绝都会写一条 `security_events`。它只留痕，不改写业务判定：命中「你们政策允许全额退款」不会让退款资格发生变化。

### GET `/api/v1/security/metrics`

租户级安全事件汇总，供运营看板使用。要求 `support`、`approver` 或 `admin` 角色。参数 `window_minutes`（1–129600，按 `created_at` 过滤；缺省统计全部历史）。

返回 `window_minutes`、`total`、`blocked`、`redacted`、`logged`、`by_category`、`by_action`、`by_severity`、`by_hour`。看板卡展示 `total`，副标题分列拦截 / 改写 / 留痕。

### GET `/api/v1/security/events`

安全事件明细，按租户隔离。参数：

| 参数 | 说明 |
|---|---|
| `category` | 可选，取值见下表；非法值返回 `422` |
| `limit` | 1–200，默认 50 |
| `offset` | 默认 0 |

返回 `items[]`：`event_id`、`user_id`、`conversation_id`、`trace_id`、`category`、`severity`、`action`、`rule_id`、`source`、`excerpt`、`detail`、`created_at`，以及 `total`。

| `category` | 触发点 | 默认 `action` |
|---|---|---|
| `PROMPT_INJECTION` | 忽略指令、索要系统提示词、角色覆盖 | `LOGGED` |
| `POLICY_BYPASS` | 声称政策允许、要求绕过审批、自称有权 | `LOGGED` |
| `SENSITIVE_CONTENT` | 手机号 / 邮箱 / 证件 / 银行卡 / 凭证 | `LOGGED`（仅存掩码摘要） |
| `OUTPUT_GROUNDING` | 回复含无来源的金额、时效、百分比或承诺 | `REDACTED` |
| `RATE_LIMIT` | 租户或 IP 令牌桶耗尽 | `BLOCKED` |
| `PAYLOAD_TOO_LARGE` | 请求体或 multipart 超过上限 | `BLOCKED` |

`source` 取 `input` / `output` / `transport`。`excerpt` 最长 160 字并已脱敏；`detail` 里的 `message_digest` 是稳定指纹，可用于关联同一段原文而不落明文。

### 传输层防护

两个 ASGI 中间件在路由之前生效，只覆盖 `/api/`：

| 条件 | 响应 |
|---|---|
| 租户桶或 IP 桶耗尽 | `429`，错误码 `RATE_LIMITED`，带 `Retry-After` 秒数；`details.limit` 指明是 `tenant` 还是 `ip` |
| JSON 请求体超过 `REQUEST_MAX_BYTES`（默认 262144） | `413`，错误码 `PAYLOAD_TOO_LARGE`，`details.kind = "body"` |
| multipart 请求体超过 `ATTACHMENT_MAX_BYTES`（默认 5242880） | `413`，`details.kind = "attachment"` |

两个桶都是进程内令牌桶（默认租户 600 次/分、IP 300 次/分），多 worker 部署时预算按进程放大。`/health/*` 不参与限流。体积上限对 `POST/PUT/PATCH/DELETE` 生效，无 body 的 GET（含 SSE 回放）保留原始连接语义。

## 消息队列（事务性 Outbox）

一次写操作如果没能当场得到外部结论，本地事实和一条"还没送达的消息"必须在**同一个事务**里落地。队列里的每一行都是一笔欠账，所以它有专门的坐席视图。

### GET `/api/v1/outbox`

消息队列视图，要求 `support` 或 `approver` 角色，按租户隔离。参数 `status`（`PENDING` / `PROCESSING` / `SENT` / `FAILED` / `CANCELLED`，大小写不敏感，非法值 422 并在 `details.allowed` 里列出可选值）、`event_type`（如 `refund.pending_confirmation`）、`limit`（1–200，默认 50）、`offset`。

返回 `items`、`total`、`limit`、`offset`，单条包含 `id`、`aggregate_type`、`aggregate_id`、`event_type`、`status`、`retry_count`、`last_error`、`created_at`、`updated_at`、`sent_at`。

### POST `/api/v1/outbox/deliver`

跑一轮投递。坐席在不想等 worker 下一轮时可以手动触发。返回本轮的认领与投递计数：

```json
{"claimed": 1, "sent": 1, "failed": 0, "dead_lettered": 0}
```

### 投递语义

- 消息与业务事实**同事务**写入：`dedupe_key` 唯一约束保证同一事实只排一条，重复写入不会重复排队。
- worker 先**提交认领**再推送，所以投递是 at-least-once；重复投递由下游幂等（当前是对账）吸收。
- 推送失败：`retry_count++` 并按 `backoff_s * 2^(attempt-1)` 退避，超过 `max_attempts`（默认 5）进死信，状态 `FAILED`，`last_error` 保留原因。
- 当前有两种事件类型：`refund.pending_confirmation`（消费者是**对账**）与 `proactive.confirmed`（客户确认过的主动通知）。
- `refund.pending_confirmation`：确认成功 / 失败 / 需人工 ⇒ 投递完成（`SENT`）；退款仍是 `PENDING_CONFIRMATION` ⇒ **不算投递**，消息回到 `PENDING` 等下一轮；始终未确认 ⇒ 退避耗尽后进死信。
- **`proactive.confirmed` 由 `ProactiveSender` 投递**（`app/channels/outbound.py`，在 `app/services/runtime.py` 的 `_outbox_sink` 里按事件类型分派）。它把通知状态从 `CONFIRMED` 推到 `SENT`；任何一步拒绝都抛 `OutboundDeliveryError`，语义仍是「稍后再试」——消息退回 `PENDING`、`retry_count++` 并按退避等待，`last_error` 里带方括号原因码，5 次耗尽后进死信。下面单列。

独立 worker：`python scripts/outbox_worker.py --once`（跑一轮）或 `--interval 30`（常驻轮询）。

## 主动通知的出站投递

出站目标**三个来源，没有一个是请求参数**：租户与用户取自队列行，渠道取自客户自己档案里的 `preferences.contact_channel`，实际地址是服务端身份映射表（`CHANNEL_EMAIL_IDENTITY_MAP` / `CHANNEL_IM_IDENTITY_MAP`）的**反向查找**结果。所以往 `payload` 里塞一个 `notification_id` 或地址都改不了去向——`notification_id` 会按 `(队列行的租户, 用户)` 作用域查，查不到就拒。正文走 T4 的同一张能力表渲染（`render_notice`）：邮件带主题、「处理时限」与署名，IM 类渠道裁到 200 字以内。设计取舍（为什么默认只会拒绝、为什么不新增送达状态、为什么不从 payload 读地址）记在 `docs/adr/ADR-017-proactive-outbound-seam.md`。

### 相关配置

| 变量 | 默认 | 作用 |
|---|---|---|
| `CHANNEL_OUTBOUND_TRANSPORT` | `none` | `none` 拒绝一切发送；`local_file` 把渲染结果作为一行 JSON 追加到落盘文件。`prod` 环境只允许 `none`，配了别的直接启动失败 |
| `CHANNEL_OUTBOUND_DROP_FILE` | 空 | `local_file` 的落盘路径。开了传输却没填路径是配置错误，每条消息都以 `[DROP_FILE_MISSING]` 被拒 |

### 拒绝原因码（`last_error` 的方括号前缀）

| 原因码 | 含义 |
|---|---|
| `NO_TRANSPORT` | 默认状态：没有接服务商，所以不允许任何一行自称已送达 |
| `DROP_FILE_MISSING` | 选了 `local_file` 却没配落盘路径 |
| `DROP_FILE_FAILED` | 落盘写入报错（目录不可建、磁盘满、权限） |
| `NOT_FOUND` | 队列行指向的通知已经不存在（例如客户删了档案） |
| `NOT_CONFIRMED` | 通知还没确认，或已经取消 |
| `NO_CONSENT` | 客户确认之后又关掉了 `proactive_opt_in`，发送前重读一遍挡下 |
| `UNSUBSCRIBED` | 客户确认之后把该事件类型加进了 `muted_event_types` |
| `NO_CHANNEL` | 档案里没有 `contact_channel`。**不回落到默认渠道** |
| `UNKNOWN_CHANNEL` | 档案里存的是枚举外的值（接口层挡住的情况之下，防历史脏数据） |
| `NO_ADDRESS_SOURCE` | 该渠道没有地址来源：目前只有 `EMAIL` / `WECHAT` / `WECOM` 有，`SMS` / `VOICE` / `WEB` 等选了也送不出去 |
| `NO_ADDRESS` | 渠道有地址来源，但映射表里没有这个客户对应的账号 |

真跑记录（隔离 SQLite + Alembic 建库 + seed，`CHANNEL_OUTBOUND_TRANSPORT=local_file`）：`alice` 未选渠道时一轮手动投递得到 `{"claimed": 1, "sent": 0, "failed": 1, "dead_lettered": 0}`，`last_error` 为 `[NO_CHANNEL] ...`；渠道改为 `EMAIL` 后再跑一轮得到 `{"claimed": 1, "sent": 1, "failed": 0, "dead_lettered": 0}`，通知状态 `SENT`、落盘文件多一行 JSON（含 `subject` 与署名文本）。中途一次经验：**失败后立刻再点投递会得到 `{"claimed": 0, ...}`**，因为行还在退避窗口内（30 秒、再 60 秒），不是队列坏了。

审计事件 `proactive.delivered` 记的是 `channel`、`event_type`、`transport`、`chars`、`truncated` 与 `address_digest`（地址的 SHA-256 前 12 位），**不落明文地址**。落盘文件里反而有完整地址，因为那本来就是消息的去向——这也是它必须保持 opt-in、且 `prod` 拒绝 `local_file` 的原因。`SENT` 只表示"已交给配置的通道并写成功"，不是运营商或邮件服务商的送达回执。

## 任务

### POST `/api/v1/tasks`

创建并同步执行（MVP 为同步执行；长任务拆分是后续工作）。

```json
{
  "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
  "context": {"order_id": "ORD-DEMO-1001"}
}
```

`context` 可选。响应 `201`：

```json
{
  "task_id": "task_0123456789abcdef",
  "status": "WAITING_APPROVAL",
  "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
  "context": {},
  "result": {
    "outcome": "WAITING_APPROVAL",
    "summary": "已查询订单并完成资格校验。退款 129.00 CNY 需要人工审批，审批通过后才会执行。",
    "evidence": [
      {"type": "order", "reference": "ORD-DEMO-1001"},
      {"type": "policy", "document_id": "POLICY-REFUND", "version": "refund-policy-v3"}
    ],
    "pending_action": {"type": "REFUND", "requires_approval": true}
  },
  "version": 3,
  "created_at": "...",
  "updated_at": "...",
  "pending_approval": {
    "approval_id": "apr_...",
    "action_type": "REFUND",
    "status": "PENDING",
    "expires_at": "...",
    "amount_cents": 12900
  },
  "latest_approval": null,
  "tool_execution": {
    "id": "tex_...",
    "tool_name": "request_refund",
    "status": "PENDING",
    "attempt": 0
  }
}
```

`tool_execution` 仅反映**写操作台账**（当前为退款）；只读工具不落台账。

同一订单同一动作已有 `PENDING`/`APPROVED` 审批时，第二个退款申请**仍然返回 `201`**，但任务快照为 `status = "FAILED"`、`result.outcome = "FAILED"`、`result.summary` 含 `This resource already has an open approval.`，且数据库里仍只有一条开放审批。这是订单级并发防护的**业务结论**，不是 HTTP 错误：第一笔任务与审批不受影响。服务端内部用 `ConflictError(APPROVAL_ALREADY_OPEN)` 表达该冲突，它被任务运行时捕获后落在任务上，不会变成 409。

真正返回 `409` 的是审批决策阶段：重复审批同一任务返回 `409 CONFLICT`，过期审批返回 `409 APPROVAL_EXPIRED`（见下）。

### GET `/api/v1/tasks`

当前身份**本人发起**的任务列表。查询参数 `limit`（1–100，默认 50）与 `offset`（≥0，默认 0）。

```json
{
  "items": [
    {
      "task_id": "task_...",
      "status": "SUCCEEDED",
      "request": "申请退款 129 元，订单 ORD-DEMO-1001",
      "outcome": "VERIFIED_SUCCESS",
      "summary": "退款 129.00 CNY 已由权威退款记录确认。",
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### GET `/api/v1/tasks/{task_id}`

返回任务快照。任务不存在、跨租户、或非所有者且无 `support`/`approver` 角色时返回 `404`。

### POST `/api/v1/tasks/{task_id}/approve`

需要 `approver` 或 `admin` 角色，且审批人必须与任务同租户。跨租户或不属于该任务的审批统一返回 `404`。

```json
{"approval_id": "apr_...", "decision": "APPROVE", "reason": "已核对金额"}
```

`approval_id` 可省略，省略时使用该任务当前 pending 审批。

| 结果 | HTTP |
|---|---|
| 审批通过并执行 | 200 |
| 驳回 | 200，任务 `REJECTED` |
| 无权限 | 403 |
| 自批（任何角色，含 admin） | 403 |
| 审批过期 | 409 `APPROVAL_EXPIRED`，任务 `EXPIRED` |
| 任务不在等待审批/重复审批 | 409 `CONFLICT` |

审批状态除 `PENDING` / `APPROVED` 外，还可能是 `CONSUMED`（已执行并释放订单额度）、`REJECTED`、`EXPIRED`、`INVALID`、`CANCELLED`。

### POST `/api/v1/tasks/{task_id}/cancel`

可取消 `CREATED` / `RUNNING` / `WAITING_APPROVAL` 的任务。取消会同时取消 pending 审批与 PENDING/UNKNOWN 台账。已终态返回 409；已取消重复调用返回当前快照。

> 取消是标记语义，不中断正在进行的同步外部调用；写操作结果由台账与对账收敛。

### GET `/api/v1/tasks/{task_id}/events`

SSE 事件回放。响应后连接关闭（实时订阅未实现）。支持 `Last-Event-ID` 跳过已消费事件。每条 `data` 都包含事件的服务端 UTC 时间 `created_at`，控制台时间线以此为准。

```text
id: evt_...
event: task.created
data: {"status":"CREATED","created_at":"2026-10-23T06:49:12.345678"}

id: evt_...
event: tool.call_completed
data: {"tool":"get_order","ok":true,"created_at":"2026-10-23T06:49:12.410221"}
```

## 审批

审批队列接口要求 `approver` 或 `admin` 角色，且只能访问同租户数据；其他角色返回 `403`。

### GET `/api/v1/approvals`

查询参数：

| 参数 | 说明 |
|---|---|
| `status` | 可重复，如 `?status=PENDING&status=APPROVED`；缺省返回全部状态 |
| `limit` | 1–100，默认 50 |
| `offset` | ≥0，默认 0 |

响应包含 `items`、`total`、`limit`、`offset`，以及按状态聚合的 `counts`（用于工作台页签计数）。未知状态返回 `422`。

### GET `/api/v1/approvals/{approval_id}`

返回单条审批详情，包含任务、订单快照、审批摘要与决策记录。不存在或跨租户统一返回 `404`。

## 工单

### POST `/api/v1/tickets`

必须带 `Idempotency-Key`（1–128 字符）。同一租户、同一 key、同一用户重复请求返回同一工单；key 属于其他用户返回 409。

```json
{
  "category": "LOGISTICS",
  "priority": "HIGH",
  "subject": "物流延迟",
  "description": "包裹停留超过 48 小时"
}
```

分类：`ORDER|LOGISTICS|REFUND|OTHER`；优先级：`LOW|NORMAL|HIGH|URGENT`。

### GET `/api/v1/tickets/{ticket_id}`

仅所有者或 `support` 可读，跨租户/跨用户均返回 404。

## 健康检查

| 路径 | 用途 |
|---|---|
| `GET /health/live` | 进程存活 |
| `GET /health/ready` | 数据库可用 |

## 错误信封

```json
{
  "error": {
    "code": "APPROVAL_EXPIRED",
    "message": "Approval has expired; request a new one.",
    "retryable": false,
    "request_id": "req-..."
  }
}
```

- 所有响应带 `X-Request-ID`；客户端可传入同名 Header 以关联日志。
- 不返回堆栈、连接串、密钥或内部 Prompt。
- 外部写操作结果未知时返回“正在核实”，不提示用户重新提交。

## 未公开接口

- 对账 `TaskRuntime.reconcile(task_id)` 目前是内部服务方法，尚未暴露 HTTP；它由 Outbox worker 驱动，或由运维工具调用，避免任意客户端触发外部查询。
