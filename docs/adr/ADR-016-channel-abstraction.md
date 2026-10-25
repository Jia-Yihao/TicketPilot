# ADR-016：渠道抽象——协议差异停在边缘，Agent 只认一种消息

- 状态：Accepted
- 日期：2026-10-24
- 修订：2026-10-24（T8——旧入口与控制台并入同一道幂等闸；见文末「修订」）

## 背景

在此之前，客服 Agent 只有一个入口：`POST /api/v1/support/messages`，请求体是 JSON，
身份来自开发用的请求头。控制台（React）就是这个入口的图形壳。

一旦要接邮件、企业微信、微信公众号，直接往这条路由上挂会出现三个问题：

1. **协议差异会渗进 Agent。** 微信一条消息 200 字、短信 70 字、邮件要有主题和落款；
   如果由 Agent 输出这些，`app/agent/` 就得知道渠道，而渠道是会一直增加的。
2. **身份从请求体里来。** 外部回调的 body 由对方拼，谁都能在 JSON 里写 `user_id`。
   把 body 里的字段当身份，等于把租户隔离交给调用方。
3. **重复回调会重复回复。** 邮件网关、IM 平台都会重投同一条消息；
   重投一次就多一条工单、多发一封回复，是事故而不是 bug。

## 决策

### 1. 三件事停在渠道边缘：验签、归一、按渠道渲染

`app/channels/base.py` 定义适配器的形状：

```python
class ChannelAdapter(Protocol):
    channel: Channel
    capabilities: ChannelCapabilities

    def verify(self, request: InboundRequest) -> None: ...
    def normalize(self, request: InboundRequest) -> MessageEnvelope: ...
    def render(self, response: SupportMessageResponse) -> RenderedMessage: ...
```

`MessageEnvelope` 是系统内唯一入站消息形状（渠道、渠道消息号、会话、`Identity`、文本、
附件元数据、locale、时间戳）。`ChannelGateway` 只做「验签 → 归一 → 交给 Agent → 渲染」，
Agent 收到的仍然是那句话、那个身份，看不见渠道。

### 2. 身份来自服务端配置，不来自 body

`resolve_identity()` 只接受 `tenant:user` 形态的配置映射：

- 邮件：`channel_email_identity_map = {"alice@example.com": "demo:alice"}`
- IM：`channel_im_identity_map = {"zhangsan": "demo:alice"}`

映射里没有的发送者一律 `403 FORBIDDEN`（「No customer mapping for this … sender」）；
配置本身畸形（不是 `tenant:user`）时 `503 DEPENDENCY_UNAVAILABLE`，因为那说明部署配错了，
而不是用户配错了。body 里的 `user_id`、`tenant` 字段被忽略。

### 3. 验签在解析之前

`verify_hmac()` 对 `timestamp + "." + raw_body` 做 HMAC-SHA256，签名与时间戳走
`X-Channel-Signature: sha256=<hex>` / `X-Channel-Timestamp`，允许 `channel_webhook_max_skew_seconds`
（默认 300 秒）的时钟偏移。先验签后 `json.loads`，所以未签名的 body 不会被解析、
也不会进日志。

### 4. 投递幂等是一张表，不是一段内存

`channel_messages` 表以 `UNIQUE (channel, channel_message_id)` 记录每一次入站投递：
请求摘要、状态（`PENDING/COMPLETED/FAILED`）、尝试次数、缓存的回复。

`ChannelMessageStore.claim()` 的分支是这张表的全部语义：

| 情况 | 结果 |
| --- | --- |
| 首次出现 | 认领，写入 `PENDING`，执行后 `complete` 缓存回复 |
| 同 id、同摘要、已完成 | 直接返回缓存的回复（`deduplicated=True`） |
| 同 id、摘要不同 | `409 CHANNEL_MESSAGE_ID_REUSED`——复用消息号是调用方的 bug |
| 同 id、正在处理 | `409 CHANNEL_MESSAGE_IN_FLIGHT`，让重投等一等 |
| 认领超过 `channel_delivery_claim_timeout_seconds` | 允许接管，崩溃的 worker 不会永久锁死一条消息 |

Web 渠道只有在显式带上 `X-Channel-Message-Id` 时才走这张表；不带就是原来的透明转发，
所以控制台行为没有变化。（**这一句在 2026-10-24 被 T8 修订**，见文末「修订」。）

### 5. 渲染矩阵是数据，不是 if-else 散落在业务里

`app/channels/render.py` 的 `CAPABILITIES` 描述每个渠道能做什么，`render_response()`
按能力产出文本：

| 渠道 | 长度 | 形式 |
| --- | --- | --- |
| WEB / APP | 不限 | 卡片、流式 |
| EMAIL | 不限 | 正式：受理编号、`处理时限：{sla_hours} 小时内首次回复`、落款、免责声明、主题 |
| WECHAT / WECOM | 200 | 「详情：1.xxx」索引式，超长按句边界截断 |
| WHATSAPP | 1024 | 纯文本 |
| VOICE | 每句 25 字 | 去掉链接与列表符号，超出即转人工 |
| SMS | 70 | 追加「回复TD退订」 |

截断只在句边界发生（`truncate_sentences`），避免出现半句话。
`GET /api/v1/channels` 把这张矩阵原样输出，前端与运维看到的是同一份事实。

### 6. 生产 fail-closed

`Settings.validate_production_safety`：生产环境开启 `CHANNEL_EMAIL_ENABLED` 或
`CHANNEL_IM_ENABLED` 时，必须同时提供 webhook 密钥（`SecretStr`）和非空的
sender→identity 映射，否则应用拒绝启动。默认两个渠道都是关的，
`CHANNEL_WEB_ENABLED` 为真。

## 后果

- 新增一个渠道 = 一个适配器 + 一条能力记录 + 一条映射配置；`app/agent/` 与
  `app/services/` 零改动，这条是 T4 的验收标准，`tests/test_channels.py` 里用
  邮件与 IM 两条真实回调验证了同一套 Agent 路径。
- 重投不再产生第二次回复，且幂等落在数据库里，多进程共享同一张表。
- 长度与形式差异导致的截断有单一实现点，改一处即可复算全部渠道。
- 代价与边界，如实记下：
  - **签名格式是本项目自定的**（`timestamp.body` + HMAC-SHA256）。真实微信/企业微信
    是各自的加密协议（`signature/timestamp/nonce` + AES），接生产前必须替换适配器里的
    `verify`，归一与幂等层可以原样保留。
  - 附件只记录元数据（文件名、类型、大小），没有下载与解析；`Attachment` 是给
    将来接对象存储的实现留的形状。
  - 渲染只处理文本，不生成图片、不发送——`render()` 与 `render_notice()` 都只返回渲染结果。
    2026-10-24 起出站接在同一个点上：`app/channels/outbound.py` 用这份能力表渲染通知再交给
    传输实现，渠道知识没有第二次长出（见 [ADR-017](ADR-017-proactive-outbound-seam.md)）。
  - `channel_messages` 缓存的是回复文本与 payload，不缓存外部平台的送达回执。

## 备选方案

- **每个渠道各写一条路由，各自拼回复**：改动最小，但渠道知识会渗进业务层，
  N 个渠道 × M 种回复形式在第一次改版时就会爆炸。
- **在 Agent 输出后加一个模板引擎**：解决了渲染，没解决身份与幂等——而这两个才是会造成
  事故的。
- **用平台自带的会话去重（微信 msgid 天然唯一）**：外部平台的去重窗口和我们的不是同一个，
  且我们无法观察它是否真的去重；把幂等寄托在别人身上，事故时拿不到证据。

## 修订（2026-10-24，T8）

**变了什么**：上面「决策 4」末尾那句「不带就是原来的透明转发，所以控制台行为没有变化」描述的
是一个**入口分裂**状态——幂等表只由 `POST /api/v1/channels/web/messages` 写，而控制台与所有既有
调用方走 `POST /api/v1/support/messages`，那条路由直接调 `SupportAgent.handle`。后果是同一句话在
旧入口可以重复受理，`X-Channel-Deduplicated` 在界面上永远观察不到（`docs/NEXT_STEPS.md` 风险表
B10 与 T4 偏差 2 记的就是这件事）。

现在两条 web 路由都经 `ChannelGateway.accept_web()`，**幂等闸只有一道**：

| 调用方式 | 行为 |
| --- | --- |
| 任一条路由 + `X-Channel-Message-Id` | 认领 `channel_messages`；重放读回已存回复并回 `X-Channel-Deduplicated: true`；同 id 换内容 409；在途 409；认领超时可被接管 |
| 任一条路由 + 不带该 Header | 一行都不写，一次调用跑一轮——与修订前完全一致 |

**没变的部分**：响应契约（同一个 `SupportMessageResponse`，`test_web_channel_matches_existing_support_route`
继续逐字比对）、身份来源（仍是请求头适配器，body 自称的身份无效）、渲染（网页 JSON 不做渠道裁剪）、
以及「Web 渠道幂等需要显式键」这条决策本身。

**为什么保留旧路由而不是废弃**：它是 `docs/API.md` 与既有脚本的公开入口，废弃属于破坏性变更；
收口的目标是「同一个键只算一次投递」，不是「所有调用自动去重」——服务端无法凭文本相似判定重复。

**证据**：`tests/test_channels.py` 新增 6 条（跨入口同一键、旧入口带键去重、旧入口不带键不留痕、
旧入口换内容 409、在途 409 且零新任务、陈旧认领被接管且 `attempts` 变 2），全量 408 passed / 3 skipped；
23 条真实 HTTP 断言 + 真实浏览器「丢掉响应再原话重发」走查（同 `task_id`、任务总数不变）。
控制台前端改为调渠道路由并自带幂等键，因此「已接入 1 / 8」那行的 `COMPLETED` 计数会随网页对话增长——
这是幂等表第一次被人点到。
