# 三类在线信号与通知（R11）：项目级口径

本文件**只声明口径**，不改任何产品代码 / 测试 / 迁移 / 配置 / 既有文档（裁定 7：口径先行——本文件经 reviewer 通过、切一次提交之后，实现轮 T2–T6 才允许动手）。流程遵循用户级协作规则。业务事实以 `docs/spec/项目规格.md` 为准；本口径与其冲突时，先改本文件并说明理由，再改代码。

行号声明：文中行号是**本轮工作树实读**的读数，一律**以符号名为准**、行号只作定位提示——`.qoder/rules/*.md` 的行号引用**无机器核对**（锚点守卫只扫 `docs/**/*.md`，见 `docs/code-anchors.yaml` 的扫描范围口径）。每条「当前事实」都给 `文件:行` 或 `文件::符号名` 证据；每条「待实现」以【待实现】显式标注。

**R11 的信号采集与判定零模型调用**（§14.1 已裁定，见 §6 第 2 条）。本文出现的审计动作名、YAML 键名、`signal_id`、`outcome` 值等英文标识符**在口径里定死，实现轮不得改名**。

---

## 0. 本条要修的是什么（R11 之前的现状）

台账原文（`docs/下一步规划.md` §0.2 表行，`:74`，逐字）：

> `| R11 | 三类在线信号与通知 | 只有埋点与回放，没有阈值告警；异常要靠人事后看板 | 三类信号各有阈值（从基线文件读，不写死代码）与通知出口；误报率单独可读；只出动作建议，不改配置 | 待 R4、R9（1.5 天） |`

现状逐条给证据（均为已落地事实）：

1. **有埋点、有回放读数，但没有阈值概念**：`app/api/v1/traces.py::trace_metrics`（`:51-104`）现算 span 健康度（`error_rate`、`p95_latency_ms`），返回的是数字，没有任何「超过一条线」的判定。
2. **有比率，但要人主动去看**：`app/api/v1/handoffs.py::handoff_metrics`（`GET /metrics`，`:60-71`）逐字调用 `HandoffMetrics.collect()`（`app/services/handoff.py:584`）产出 `handoff_rate` 等读数；该链路里不存在「异常」二字。
3. **没有任何告警留痕**：`audit_events` 现无信号类动作行——审计写入先例只有 `handoff.escalate`（`app/services/handoff.py:348-351`）等业务动作；`monitor.signal.breached` / `monitor.signal.reviewed` 两个动作名是【待实现】。
4. **既有配置全是「单条判定」阈值，没有「比率告警」阈值**：`app/core/config.py:57-66`（`escalation_clarification_rounds=3`:57、`handoff_after_failed_attempts=2`:58、`escalation_no_evidence_score=5.0`:59、dense `0.35`:63、fusion `0.0164`:64、rerank `0.35`:65、`session_idle_minutes=20`:66）。
5. **误报率无处可读**：仓内没有「复核」这个动作，也没有它的承载表。
6. **服务端唯一可用的留痕出口**是 `AuditRepository.record`（`app/repositories/audit.py:15-38`，全 keyword-only，`actor_id`/`tenant_id` 必填）。
7. **基线文件是离线读数**：`evals/baseline/business.json`（46 条读数，`_meta.recorded_at = "2026-10-28T12:53:08"` 在 `:7`）没有任何在线组件读取它；`app/**` 对 `evals` 零真实 import（见 §3）。

---

## 1. 术语表

| 中文术语 | 英文唯一标识符 | 定义 | 状态 |
|---|---|---|---|
| 在线信号 | `signal` | R11 的三类比率型运维读数，各由唯一 `signal_id` 标识 | 口径 |
| 无依据率（信号 A） | `no_evidence_rate` | 窗口内无依据转人工事件占可比用户消息的比例 | §2 |
| 转人工率（信号 B） | `handoff_rate` | 窗口内转人工会话占全部会话的比例（既有读数） | §2 |
| 工具失败率（信号 C） | `tool_failure_rate` | 窗口内已落定的工具执行台账中 FAILED 占比 | §2 |
| 阈值 | `threshold` | 信号的比率告警线，`float ∈ (0, 1]`，存 `app/core/online_signals.yaml` | 【待实现】 |
| 基线抄本 | `baseline`（YAML 块） | YAML 内记录的**抄本**（非引用）：源文件路径、读数键、抄的值、抄录日期 | 【待实现】 |
| 已校准 | `calibrated` | `true` ⇒ `baseline` 是真实抄本且有测试兜一致性；`false` ⇒ 占位初值，未经基线校准 | §3 |
| 告警 / 触警留痕 | `monitor.signal.breached`（审计动作名） | 读到比率越过阈值时写入的审计行 | 【待实现】 |
| 复核 | `monitor.signal.reviewed`（审计动作名） | 人工对「某信号在某窗口 breach 过」这一判定的结论留痕 | 【待实现】 |
| 误报率 | `false_positive_rate`（响应字段名） | 误报数 ÷ 已复核数；已复核数为 0 时为 `null`（不落 `0.0`） | §5 |
| 动作建议 | `action_suggestion`（YAML 键） | 触警时随读数带出的**静态**文本，只提示人下一步查什么 | 【待实现】 |
| 再告警间隔 | `realert_minutes`（YAML 键） | 同一信号两条 breached 行之间的最小间隔（幂等去重参数） | §5 |

### 1.0 必须消歧的同名概念（关键口径）

1. **`Notification`（客户可见）**——`app/models/memory.py::Notification`（`:170-207`）的 docstring 是「A proactive proposal waiting for the customer's yes or no」：`user_id` 指向客户、正文由规则渲染、确认即写出箱行。R11 的运维告警**不是**客户通知，**禁止复用该模型**（裁定 3）。
2. **`HandoffMetrics`（本仓既有会话级比率）**——`app/services/handoff.py::HandoffMetrics`（`:571`）是**计算者**，其 `handoff_rate`（`:661`）是**读数**；信号 B 只复用该读数，不另立分子分母（裁定 2）。
3. **`escalation_no_evidence_score`（单条证据的分数阈值）≠ 信号 A 的 `threshold`（比率阈值）**——前者是 `float`（`app/core/config.py:59`，默认 5.0），与单条 `EvidenceScore.score` 比较（`app/core/policy.py::EscalationThresholds.no_evidence_threshold`:88-99；消费点 `app/agent/actions.py:379`、`:386`）；后者是 0..1 的**比率**告警线。两种数字，不得混用、不得互相顶替。
4. **`CircuitBreaker.failure_threshold`（连续次数阈值）≠ 信号 C 的 `threshold`（比率阈值）**——前者 `int=3`（`app/tools/executor.py:213`），计**连续失败次数**（trip 判定 `:252-259`）；后者是窗口内**失败占比**。C 读台账，不读熔断（裁定 2）。
5. **`HandoffReason.NO_EVIDENCE`（落库枚举值）≠ `empty_reason`（单次检索的空因 span 属性）**——前者是 `HandoffReason` 成员（`app/models/enums.py:174-185`，`NO_EVIDENCE` 在 `:179`），转人工时落 `handoff_events.reason`（写入点 `app/services/handoff.py:336` `reason=signal.reason.value`）；后者是 `retrieve`/`policy.search` span 的属性（`app/agent/actions.py:338-357`，键 `empty_reason` 在 `:346`），分类单次检索为什么空。信号 A 的分子**只读前者**（见 §2-A 被否决备选）。
6. **三个 `reason` 同名列**——`approvals.reason` / `handoff_events.reason` / `abandon_events.reason` 是三个不同业务字段；本口径中「reason」不加限定时**只指** `handoff_events.reason`。
7. **`detail`（审计 JSON 列）不是分类依据**——`app/models/audit.py:23` 的 `detail` 是人读台账、他人可读；本口径一切分类只走具体列（`action`/`outcome`/`resource_id`），**从不解析 detail/attributes JSON**（与 `traces.py` 纪律同向）。

---

## 2. 三类信号契约

通用规则（三类共用，定死）：

- **零模型调用**：采集 = 读库计数，判定 = 与 YAML 阈值比较，全程确定性代码，不触碰 brain/LLM（§14.1 裁定：`docs/下一步规划.md:2589`「用确定性信号替代，不另起一个 agent」——多调一次模型判答案既不必要也不可复现）。
- 窗口参数：`window_minutes`，`Query(ge=1, le=60*24*90)`，不给则全量（与 `traces.py:24,53`、`handoffs.py:65` 既有形态一致）。
- 租户边界：一切查询必带 `identity.tenant_id` 条件；**不因在线信号放宽任何鉴权或租户边界**（裁定 6）。
- 比率输出一律 `round(x, 4)`（`traces.py:99` 先例）。分母为 0 时比率输出 `null`，不输出 0.0 伪读数（信号 B 的零分母 `null` 是 **monitor 出口层映射**，见 §2-B 裁定 11；既有 `/api/v1/handoffs/metrics` 的读数仍为 `0.0` 不变）。
- 每个信号只有**一个真值来源**（裁定 2），分子/分母/时间列/租户列在下表写死；实现轮不得换源。

### 信号 A `no_evidence_rate`（无依据率）

| 项 | 定死内容 |
|---|---|
| 定义 | 窗口内本租户「因无依据转人工」的事件数 ÷ 同窗口内可能走到无依据转人工的 comparable 用户消息数 |
| 唯一真值来源 | `handoff_events` 表的 `reason == HandoffReason.NO_EVIDENCE`（枚举 `app/models/enums.py:179`；写入路径 `app/services/handoff.py:330-347`，其中 `:332` 落 `tenant_id`、`:336` 落 `reason`）——**已落库、已带租户边界的事实** |
| 分子 | 窗口内（时间列 `HandoffEvent.created_at`）、本租户，`reason = "NO_EVIDENCE"` 的行数（等值比较具体列，不解析 JSON） |
| 分母 | 同租户、同窗口内，`ConversationMessage.role = "user"` 且 `ConversationMessage.intent ∈ {"POLICY_QA", "SELF_ANALYTICS"}` 的消息条数；经 `conversation_id` JOIN `conversations` 收租户（证据：`app/models/conversation.py::Conversation.tenant_id`:23、`::ConversationMessage` 的 `role`:43 / `intent`:44 / `created_at`:47） |
| 意图集合的出处与锁定 | 意图集合**当前抄本**自 `app/agent/escalation.yaml:140-143`（`no_evidence` 信号块的 `intents`: `POLICY_QA`、`SELF_ANALYTICS`）；一致性由一条测试现读该 YAML 比对钉死（§9-3）。被触发意图面变了，YAML 抄本与分母口径由测试逼着一起改 |

**被否决备选（写明理由）**：

- 否决「从 span 属性重算无依据率」（`retrieve` span 的 `empty_reason`/`evidence_score`/`hits`，`app/agent/actions.py:338-357`）：`attributes` 是 JSON 列，跨 SQL 方言解析不可靠；且 `app/api/v1/traces.py` 的既有纪律就是从不调用 `TraceSpan.attributes.__getitem__`（即「从不解析 attributes」，查询只 select 具体列 `:64-70`；`handoff_events` 的 `detail`/`bundle`、`trace_spans` 的 `attributes` 均同理）。
- 否决「分母用会话数（`HandoffMetrics` 形）」：A 的分子是**事件级**（一次会话可能多次无依据转人工），用会话数做分母分子分母不同级、不可比，且单会话内多次命中会被稀释。定死为**同级可比的用户消息计数**。

### 信号 B `handoff_rate`（转人工率）

| 项 | 定死内容 |
|---|---|
| 定义 | 逐字采用既有读数 `HandoffMetrics.collect()` 返回字典中的 `handoff_rate` |
| 唯一真值来源 | `app/services/handoff.py::HandoffMetrics`（`:571`）的 `collect()`（`:584`），产出键 `window_minutes`/`conversations`/`handoff_conversations`/`handoff_rate` 等（`:657-674`；`handoff_rate` 在 `:661`） |
| 分子 / 分母 | 即既有实现的 `handoff_conversations`（`:660`）/ `conversations`（`:659`）；`collect` docstring 定死「分母始终是窗口内的会话数，不是历史累计——拿累计做分母，任何一次运营事故都会被时间稀释掉」（`:589-590`） |
| 窗口 / 租户 | 由 `collect(identity, window_minutes=...)` 既有签名承担；既有对外形态先例 `app/api/v1/handoffs.py:60-71`（`response_model=HandoffMetricsResponse`，`app/schemas/handoffs.py:48`） |
| 零分母映射（裁定 11） | 信号 B 仍**逐字**取 `collect()` 既有 `handoff_rate` 读数（裁定 2 不变：不改 `collect()`、无第二套分子分母）；monitor 出口在把 `collect()` 自带会话分母键 `conversations` 同窗口/租户读数判为 0 时，将该信号行 `metric_value` 置 `null`、`breached` 置 `false`。这是**出口层映射，不是第二份事实源**；既有 `GET /api/v1/handoffs/metrics` 该形态保持 `0.0` 不变——`app/services/handoff.py:101-104` 的 `_rate()` 零分母返回 0.0，**不改动** |

**裁定：不得另立第二套分子分母**（裁定 2 逐字）。被否决备选：自己 `SELECT COUNT(handoff_events)`——会造出第二份「转人工率」事实源，两处演化必漂。

### 信号 C `tool_failure_rate`（工具失败率）

| 项 | 定死内容 |
|---|---|
| 定义 | 窗口内本租户已落定的工具执行台账中，FAILED 状态占比 |
| 唯一真值来源 | `tool_executions` 状态分布（`app/models/tool_execution.py::ToolExecution`:15-42；状态词表 `ToolExecutionStatus`：`app/models/enums.py:36-42` = PENDING / SENT / UNKNOWN / RECONCILED / FAILED / CANCELLED） |
| 分子 | 窗口内 `status = FAILED` 行数（时间列 `created_at`，`app/models/tool_execution.py:39`） |
| 分母 | 窗口内**已落定**的行：`status ∈ {SENT, RECONCILED, FAILED, CANCELLED, UNKNOWN}`（排除 `PENDING`——尚未执行，不计入任何一侧）。`UNKNOWN` 进分母不进分子：它是「外部结果未知」（`enums.py:39`），不能声称失败，也不能当它不存在；失败率因此保守偏低，登记为代价 §10-2 |
| 租户边界（硬） | 该表**无 `tenant_id` 列**（`app/models/tool_execution.py:21-30` 逐列核对无此列）；任何按租户的统计**必须 JOIN `tasks`**：`ToolExecution.task_id → tasks.id`，租户取 `Task.tenant_id`（`app/models/task.py:36`）。`tasks.conversation_id` 与本条无关，别混 |
| 与熔断的关系 | 熔断器状态不落库：`CircuitBreaker` 的 state 在内存且每进程一份（`app/tools/executor.py:202-208` docstring「The state is in memory and per process… not treated as a correctness mechanism — it is a load-shedding device」；`app/services/runtime.py:254-257`「One breaker per process」+ `:257` 构造点），只在 tool span 属性里露 `circuit_state`（`app/services/runtime.py:1400-1415`，键在 `:1412`）。**C 读的是失败台账，不是熔断器实时态**——口径必须这样写（裁定 2），代价登记 §10-3 |

被否决备选：读熔断实时态——内存态不落库、跨进程不可得、重启即清零，不可复现；台账才是可复现读数。

---

## 3. 阈值与基线抄本契约（裁定 1 + 裁定 9）

**位置与形状（定死）**【待实现】：

- 新增数据文件 `app/core/online_signals.yaml`；新增 loader `app/core/online_signals.py`（`@lru_cache(maxsize=1)` + 加载期封闭键校验、未知键即报错）。
- **不并入 `app/core/config.py`，不写成 Python 常量，不进 `Settings`**。理由与 `.qoder/rules/approval-tiering.md` §2.1 同形：阈值是**有结构的多行数据**，且「阈值不写死在代码里」是台账原文要求（`docs/下一步规划.md:74`）。
- loader 形状照本仓五处同形先例：`app/agent/intent_catalog.py:332`、`app/agent/escalation.py:154`、`app/core/approval_tiers.py:232`、`app/agent/counter_question.py:104`、`app/agent/refund_reason_rules.py:158`（均 `@lru_cache(maxsize=1)`）；未知键即报错的形态照 `app/agent/intent_catalog.py::_reject_unknown`（`:269-274`，调用点 `:281`/`:284`/`:298`）。

**键结构（封闭集，逐字定死）**：

```yaml
version: 1
signals:
  - signal_id: no_evidence_rate        # A
    threshold: 0.20                    # 未经基线校准的占位初值（裁定 9）
    calibrated: false
    realert_minutes: 15
    baseline:
      source_file: "none"              # calibrated: false ⇒ 定死字面 "none"
      reading_key: "none"              # calibrated: false ⇒ 定死字面 "none"
      copied_value: null               # calibrated: false ⇒ 定死 null
      copied_at: "2026-11"
      note: "未经基线校准的占位初值"   # calibrated: false ⇒ note 必含「未经基线校准」
    action_suggestion: "查检索与证据判定：核对 policy.search 的空因分布与 no_evidence 判定线，确认知识库版本。"
  - signal_id: handoff_rate            # B
    threshold: 0.55
    calibrated: true
    realert_minutes: 15
    baseline:
      source_file: "evals/baseline/business.json"
      reading_key: "handoff_metrics.handoff_rate"
      copied_value: 0.3654
      copied_at: "2026-11"
      note: "阈值 0.55 高于基线 0.3654，属判断值，不是基线值；基线读数为时点快照（源文件 _meta.recorded_at=2026-10-28T12:53:08）。"
    action_suggestion: "查澄清与门：核对 clarification_rounds 与 escalation 触发分布，确认是否有单信号风暴。"
  - signal_id: tool_failure_rate       # C
    threshold: 0.10
    calibrated: false
    realert_minutes: 15
    baseline:
      source_file: "none"
      reading_key: "none"
      copied_value: null
      copied_at: "2026-11"
      note: "未经基线校准的占位初值"
    action_suggestion: "查熔断与上游：按 tool_name 拆失败分布，核对 timeout 与上游服务健康。"
```

约束逐条：

1. 顶层键封闭：`{version, signals}`。`signals` **恰三行**，`signal_id` 取封闭词表 `{no_evidence_rate, handoff_rate, tool_failure_rate}` 且互不重复。
2. 信号行键封闭：`{signal_id, threshold, calibrated, realert_minutes, baseline, action_suggestion}`；`baseline` 块键封闭：`{source_file, reading_key, copied_value, copied_at, note}`。未知键即 `ValueError`（加载期失败，运行期不兜底）。
3. `threshold: float ∈ (0, 1]`；`realert_minutes: int > 0`（初值 15 定死，见 §5）；`action_suggestion` 为非空字符串。
4. `calibrated` 语义（定死）：`true` ⇒ `baseline` 必须是真实抄本（源文件路径、读数键、抄的值、抄录日期四件齐），且受 §3-6 一致性测试约束；`false` ⇒ `source_file`/`reading_key` 为 `"none"`、`copied_value` 为 `null`、`note` 必含「未经基线校准」。
5. 初值（裁定 9 逐字）：**B = 0.55**，来源是基线读数 `handoff_metrics.handoff_rate = 0.3654`（`evals/baseline/business.json:26`；52 会话 / 19 次转人工）**向上取的判断值**，`calibrated: true`，且必须写明「阈值 0.55 高于基线 0.3654，属判断值，不是基线值」；**A = 0.20、C = 0.10**，`calibrated: false`，YAML 与文档都必须写「未经基线校准的占位初值」（基线文件里没有这两项读数——实核 `evals/baseline/business.json` 无无依据率、无工具失败率读数，这是诚实前提，不得伪装成校准过）。补校准**只改 YAML，不改代码**（§8）。
6. **为什么 app 不读 evals**：`docs/architecture-guard.yaml:30-43` 的 tiers 是 `core`/`observability`/`db`=0、`models`/`schemas`=1、`repositories`/`retrieval`/`sql`=2、`tools`/`agent`/`channels`=3、`services`=4、`api`=5——`evals` **不在分层清单里**，`app/**` 读取 `evals/**` 是向上/跨树依赖；已实测 `app/**` 对 `evals` 零真实 import。所以 YAML 里存**抄本**（copy）而非引用，一致性由 `tests/` 兜（tests 跨树读取不受 tiers 管制）。
7. **抄本一致性测试**（fail-closed）【待实现】：一条测试**现读** `evals/baseline/business.json`，断言 `calibrated: true` 行的 `copied_value == readings[reading_key]`。文件缺失 / 键缺失 / 值不符**一律失败，不得跳过**。先例：`tests/test_metrics_baseline.py` 存在，即「现读基线文件并比对」的形状。

---

## 4. 通知出口契约（裁定 3）

**出口 = 一个只读 HTTP 端点 + 告警留痕写审计**【待实现】，外加一条**补充裁定**（复核写入端点，见下「复核出口」）：

1. **只读端点**：新文件 `app/api/v1/monitor.py`（`APIRouter(prefix="/api/v1/monitor", tags=["monitor"])`），`GET /api/v1/monitor/signals`，query 参数 `window_minutes`（`ge=1, le=60*24*90`，常量名 `MAX_WINDOW_MINUTES`，照 `app/api/v1/traces.py:24`）。响应：三个信号各一行——`signal_id` / `window_minutes` / `numerator` / `denominator` / `metric_value`（`round(...,4)`）/ `threshold` / `breached`（bool）/ `calibrated` / `action_suggestion`；外加顶层 `false_positive_rate` 块（§5）。形态逐条照 `traces.py:51-104` 先例：**select 具体列、Python 侧分类、从不解析 attributes/detail JSON、`round(..., 4)`**。
2. **响应模型**：新文件 `app/schemas/monitor.py`（schemas 层，tier 1，先例 `app/schemas/handoffs.py::HandoffMetricsResponse`:48）。
3. **路由注册**：`app/main.py` 加**一行 `include_router`**（现注册点 `app/main.py:165-176`，共 12 行 include）。**不新建 `app/api/v1/router.py`**——已实核仓内 `app/api/v1/` 无此文件。
4. **鉴权（定死，裁定 3 的上报项）**：用 **`_require_staff` 形**（放行 `admin` 或 `("approver", "support")`，照 `app/api/v1/traces.py:22` 的 `_STAFF_ROLES`、`:27-32` 的实现，失败抛 `ForbiddenError("Staff role required.")`），**不用** `_require_seat`（只放 `support`，`app/api/v1/handoffs.py:42-45`）。**理由**：本条是**运维健康读数**，用途与 `traces` 的 `/metrics`（docstring：「Tenant-scoped span health for the operations dashboard」`:57-58`）同类；`handoffs` 的 seat 门守的是**接案工作队列与案卷**，与本条无关。落地细节：`traces.py::_require_staff` 是模块私有，`monitor.py` 内写同名私有函数（不跨模块 import 私有符号），由一条测试钉死 `monitor._STAFF_ROLES == traces._STAFF_ROLES` 且校验行为等价——两份副本的漂移风险登记 §10-6。
5. **告警留痕**：读到 breach 时经 `AuditRepository.record`（`app/repositories/audit.py:15-38`）写 `audit_events`，契约见 §5。
6. **明确不做主动推送**（无 webhook / 邮件 / IM），**不复用 `Notification` 模型**（`:170-207`，客户可见实体，§1.0-1）。理由（定死）：推送需要外部端点与凭据（新增配置项），而本仓「**生产环境不因新增配置拒绝启动**」是既有纪律；且推送的重试与幂等是另一条未审级联。告警的可观测形态就是「端点可读 + 审计留痕可查」，代价登记 §10-9。
7. **复核出口（补充裁定）**【待实现】：`POST /api/v1/monitor/reviews`，请求体仅 `signal_id`（封闭词表校验）、`conclusion`（`FALSE_POSITIVE` / `CONFIRMED`）、可选 `window_minutes`；鉴权同上 `_require_staff` 形；写入 §5 的 reviewed 审计行。**为什么多这一个端点**：裁定 3 字面只给了「一个只读 HTTP 端点」，但裁定 4/8 的 reviewed 行必须有生产者——没有写入口，误报率永远是 `null`，「误报率单独可读」这条验收标准就不成立。裁定口径冲突时以「验收标准可成立」为准，故补充此写入端点；其代价（OpenAPI 增 1 path + 1 operation，守卫级联加重）登记 §10-5，级联面见 §10-8。

---

## 5. 告警留痕与误报率契约（裁定 4 + 裁定 8）

**两条新审计动作，动作名字面定死**（实现轮不得改名；均 23 字符，满足 `action String(64)`，`app/models/audit.py:19`）：

| 动作名 | 语义 | `resource_type` | `resource_id` |
|---|---|---|---|
| `monitor.signal.breached` | 读到某信号在某窗口 breach 的留痕 | `monitor_signal`（14 字符，满足 `String(32)`，`audit.py:20`） | `signal_id`（满足 `String(64)`，`:21`） |
| `monitor.signal.reviewed` | 对「某信号在某窗口 breach 过」这一判定的人工复核结论 | `monitor_signal` | `signal_id` |

**breached 行**：

- `outcome = "SUCCESS"`（既有词表字面，先例 `app/services/handoff.py:354`；「触警」语义由动作名承担，`outcome String(16)`（`audit.py:22`）只放既有值，不发明新字面）。
- `actor_id` = 触发此次读取的 staff 身份（`identity.user_id`，同 `handoff.escalate` 写法 `handoff.py:349`）；`tenant_id` = `identity.tenant_id`（必填）。
- **幂等定死（裁定 4 的上报项「每窗口恰一条还是幂等」）**：定死为**幂等去重**，不是每窗口恰一条。规则：写前查本租户、同 `resource_id`，若已存在 `action = "monitor.signal.breached"` 且 `created_at >=` 本次读取窗口起点 的行，**或**距最近一条 breached 行不足 `realert_minutes`（YAML 行参数，定死初值 15）——则不重复写。**理由**：端点是现算只读的，看板一次刷新就是 N 次读取，「每窗口恰一条」会在每个窗口重复刷告警；去重窗口既防刷屏也不丢新发事件。**去重查询只用具体列**（`action`/`resource_type`/`resource_id`/`tenant_id`/`created_at`），**从不解析 `detail`**。
- `detail` 允许键集**封闭且写死**：`{signal_id, window_minutes, metric_value, threshold}`。禁入内容（定死）：客户文案、单号、原始 span/属性值——`detail` 是 JSON 列且台账他人可读（`audit.py:23`），纪律同「只记计数，不记单号」。

**reviewed 行（裁定 8）**：

- `outcome` = 复核结论，值域封闭：`FALSE_POSITIVE`（14 字符）/ `CONFIRMED`（9 字符），均在 `String(16)` 列宽内（`audit.py:22`）；实现轮若改字面，必须仍 ≤16 字符，且值域必须收进**一个封闭枚举 / `frozenset` 词表**（值域只有一个产出点，照 `.qoder/rules/task-order.md` §5「新增出口先加枚举值」纪律；不散落多处字符串常量）。**不解析 JSON detail 来分类**——理由：与 `traces.py`「从不解析 attributes」纪律同向，且跨方言安全。
- `actor_id` = 做复核的坐席/运维身份；`tenant_id` 必填；`resource_id` = 被复核信号的 `signal_id`。
- `detail` 允许键集**封闭且写死**：`{signal_id, window_minutes}`。
- **复核不绑定到具体某条告警行**：被复核对象是「某窗口内该信号 breach 过」这一判定（裁定 4/8）。要绑定就得新增表/列——属被否决的级联，代价登记 §10-4。

**误报率公式（裁定 4，定死）**：

```
false_positive_rate = count(action=monitor.signal.reviewed 且 outcome=FALSE_POSITIVE) ÷ count(action=monitor.signal.reviewed)
（同租户、同窗口；分子分母都是具体列等值比较）
```

已复核数为 **0** 时返回 **`None`（JSON `null`），不落 `0.0`**——「没复核过」与「复核过且全对」必须可分。

---

## 6. 红线（裁定 5，机器可判）

1. **在线信号只出动作建议，不许自动改提示词或阈值**（台账原文：`docs/下一步规划.md:2658`「红线：在线信号只出动作建议，不许自动改提示词或阈值」）。动作建议**只能**是 YAML 里的静态字段（`action_suggestion`）——**不是模型生成、不是运行时拼接业务数据**（实现上即：响应里的 `action_suggestion` 逐字等于 YAML 值，测试用字节相等断言）。
2. **「R11 的信号采集与判定零模型调用」为口径硬条款**——依据 §14.1 裁定表（`docs/下一步规划.md:2589`，逐字：「monitor agent | 用确定性信号替代，不另起一个 agent | 只有埋点与回放，没有阈值告警；多调一次模型判答案既不必要也不可复现 | R11」）。任何人把 R11 实现成「调模型判断答案质量」即违反本口径。
3. **机器可判形式**【待实现】：
   - 测试断言以下六个文件的**字节不变**（R11 实现轮改动不得触碰）：`app/agent/intents.yaml`、`app/agent/escalation.yaml`、`app/core/approval_tiers.yaml`、`app/agent/counter_question.yaml`、`app/agent/refund_reason_rules.yaml`、`app/core/online_signals.yaml`（后者由 loader 测试只读）。
   - **源扫描断言**：`app/**` 内不存在任何写配置 / 写 YAML 的路径——`app/**` 源码不出现 `yaml.safe_dump`，不出现写模式 `open(...)` 指向 `*.yaml`/`*.yml`；运行期对 config / agent / core 各 YAML 只读不写。

---

## 7. 边界与禁止事项（裁定 6 逐条 + 台账三处对齐）

1. **不新增迁移、不新增表、不新增列、不新增依赖**（`alembic/versions/` 维持单 head `ea6c2b48d701`）。
2. **不改 `web/**`**（措辞定死，含 `web/src/**` 前端源码与 `web/dist` 构建产物）。事实依据：`console_pages` 事实组（`docs/fact-baseline.yaml:217-224`）以 `(\d+) 个控制台页面` / `(\d+) 个业务页面` 两个 pattern 计数的是 `docs/records/智能体平台设想.md` 里的**控制台/业务页面数量**——只有增删 `web/src/pages/` 下的页面才会移动它的读数；**给新端点加响应字段、或新增审计动作名，都不会打红它**（前版把这条机制写错了，本轮更正）。本轮与实现轮均不增页面、不碰 `web/**`，故 `console_pages` 不在本轮打红清单（级联面以 §10-8 为准）。
3. **不改三份守卫清单**：`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、`docs/architecture-guard.yaml`。清单头部规则（`docs/fact-baseline.yaml:1-24`）：清单只写「哪个文件哪一行 + 用哪个正则抠数字」，**不写数字本身**；锚点不得含数字；改清单不走 ADR、改事实要走 ADR。
4. **不改 `evals/intent_metrics.py` 与 `evals/intent/**`**；**不改 `ADOPTABLE_INTENTS`**（不扩采纳面）。
5. **不因在线信号放宽任何鉴权或租户边界**；`_require_staff` 形即既有最严形态照搬，不另造角色。
6. **`evals/**` 的模型读数由用户侧本机模型复跑**；本轮与实现轮都不跑，并在交付物中如实登记「未跑」。
7. 不新建 `app/api/v1/router.py`；路由注册只在 `app/main.py` 加一行 include（§4-3）。
8. 不复用 `Notification` 模型；不做推送；不写 §5 两个动作名与 `monitor_signal` 之外的新审计动作/资源类型。
9. 不写入 `audit_events.detail` 任何客户文案、单号、原始读数（键集封闭，§5）。
10. 不产生模型调用（§6-2）；`monitor` 端点调用链不得触达 brain / LLM / 证据打分之外的任何生成路径。
11. 本文对 `.qoder/rules/*.md` 的行号引用无机器核对（锚点守卫只扫 `docs/**/*.md`）；引用既有口径时以符号/章节名为准。

---

## 8. 配置项与生效方式

- 新增配置**只有** `app/core/online_signals.yaml` 一项。无新环境变量、无 `Settings` 字段、不动 `.env.example`（裁定 1/6 推论：不进 `config.py`/`Settings` 即不进环境面）。
- 改 YAML 后**重启进程**生效，**不承诺热加载**——loader 是 `@lru_cache(maxsize=1)`，每进程缓存一份（与 `.qoder/rules/approval-tiering.md` §7、`docs/guide/接口说明.md:1165`「改环境变量或改 YAML 后重启进程生效……不需要发版；不承诺热加载」同措辞口径）。
- A/C 补校准（裁定 9）：**只改 YAML 数值与 `baseline` 块**（`calibrated: false → true`、抄本四项填充），不改代码；加载期校验（§3 约束 1-4）保证形状不可能改错方向。

---

## 9. 验收标准（DoD，逐条可判定，括号内为验证方式）

1. loader `app/core/online_signals.py` 用 `@lru_cache(maxsize=1)`、加载期封闭键校验、未知键即报错（测试：正例加载 + 每类未知键/越界值构造反例断言 `ValueError`；不许走 `lru_cache` 缓存 getter 构造失败样例，照既有先例形态：`tests/test_approval_tiers.py:123-146` 的反例用 `_write(tmp_path, ...)` 写 YAML 后**直接**调 `load_approval_tiers(path)`（定义 `app/core/approval_tiers.py:177`），绕过 `@lru_cache(maxsize=1)` 的 `get_approval_tiers`（`app/core/approval_tiers.py:232`）——行号为定位提示、可能漂，以符号为准；R11 同形 = 反例直接调 `load_online_signals(tmp_path/...)`，不经缓存 getter）。
2. `app/core/online_signals.yaml` 恰三行，`signal_id` 为 `no_evidence_rate`/`handoff_rate`/`tool_failure_rate`，阈值 0.20 / 0.55 / 0.10，`calibrated` false/true/false（测试：加载结果逐键断言；本文件 §3 为字面依据）。
3. 信号 A 分母的意图集合与 `app/agent/escalation.yaml` 的 `no_evidence` 块 `intents` 一致（测试：现读该 YAML 比对；漂移即 fail）。
4. 抄本一致性：测试现读 `evals/baseline/business.json` 断言 `copied_value == readings[reading_key]`；文件缺失 / 键缺失 / 值不符一律失败、不得跳过（fail-closed 行为测试：三种坏情形各断言失败）。
5. 信号 A 分子只读 `handoff_events.reason` 具体列；实现全链不出现对 `attributes`/`detail`/`bundle`/`payload` 的取值判定（源码扫描 + 行为测试）。
6. 信号 C 的租户统计必须经 `tasks` JOIN；跨租户测试证明 A 租户的读数不受 B 租户数据影响（行为测试，各信号至少一条跨租户隔离用例）。
7. 端点形态：只读 `GET /api/v1/monitor/signals` + `POST /api/v1/monitor/reviews` 两个 operation；鉴权拒绝非 staff（403 用例）；窗口参数 `ge=1, le=60*24*90`（越界 422 用例）；比率为 `round(...,4)` 口径（OpenAPI 快照 + 行为测试）。`app/main.py` 的 `include_router` 恰从 12 增至 13（源码断言）；不存在 `app/api/v1/router.py`（存在性断言）。
8. 误报率语义：窗口内 `reviewed=0` ⇒ `false_positive_rate` 为 `null`；有复核且全对 ⇒ `0.0`；有复核有 false ⇒ `round(f,4)`（三条行为测试，`null` 与 `0.0` 必须都出现且可分）。
9. breached 幂等：同窗口反复 GET 至多一条 breached 行；跨过 `realert_minutes` 再 breach 有新行（时间推进的行为测试两条）。
10. 红线机器可判：六 YAML 字节不变测试 + `app/**` 无 YAML/配置写路径的源码扫描测试 + 响应中 `action_suggestion` 逐字等于 YAML 值（字节相等断言）。
11. 零模型调用：「R11 的信号采集与判定零模型调用」检索在本文件命中；monitor 端点调用链测试中放模型桩并断言不被触发。
12. A/C 未经校准可检索：YAML 两行 `calibrated: false` 且 `note` 含「未经基线校准」；本文件 §3 约束 5 含「未经基线校准的占位初值」字样（grep 断言）。
13. §7 边界逐条零违例：实现轮 diff 不含迁移 / 表列 / 依赖 / `web/**` / 三份守卫清单 / `evals/**`（审查 diff 清单化核对）。
14. 零分母行为测试（裁定 11）：构造分母为 0 的窗口/租户 ⇒ monitor 信号 B 行 `metric_value` 为 `null` 且 `breached` 为 `false`；同条件下断言既有 `GET /api/v1/handoffs/metrics` 的 `handoff_rate` 读数不变、仍为 `0.0`——两层读数各有断言（行为测试至少两条；信号 A/C 零分母另断言 `null`，§2 通用规则）。

---

## 10. 代价与取舍登记（逐条）

1. **A/C 占位阈值可能长期不误触发或误触发**：0.20 / 0.10 未经基线校准（裁定 9），需要 R12 之后的基线轮补校准；补校准只改 YAML、不改代码（§8）。
2. **C 把 `UNKNOWN` 计入分母不计入分子**：工具失败率**保守偏低**——「外部结果未知」既不能声称失败、也不能当不存在（§2-C）。
3. **C 读失败台账、不读熔断实时态**：告警滞后至少一个读取节奏（熔断状态不落库、每进程一份，实时态根本不可读，§2-C）。
4. **误报率不绑定具体告警行**：复核的是「窗口 × 信号」的判定，不是某条 breached 行；要绑定须新增表/列——被否决的级联（§5）。
5. **补充裁定加了第二个端点**：裁定 3 字面「一个只读端点」不足以承载裁定 4/8 要求的 reviewed 生产者，故加 `POST /api/v1/monitor/reviews`（§4-7）；代价是 OpenAPI 多 1 path + 1 operation、写端点带来鉴权面（按 `_require_staff` 形处理，不放宽）。
6. **`_require_staff` 两份私有副本**（`traces.py` 一份、`monitor.py` 一份）：角色集合漂移风险由 §9-7 的一致性测试钉住；不跨模块 import 私有符号是本仓既有形态。
7. **抄本可能与基线漂**：由 fail-closed 一致性测试兜（§3 约束 7）；漂了测试即红，不允许静默。
8. **守卫级联只登记、不修改**（裁定 6）：R11 落地会打红的事实组——`openapi_paths`（`docs/fact-baseline.yaml:27`，**+2 path**：`GET /api/v1/monitor/signals` 与 `POST /api/v1/monitor/reviews` 各一条路径）、`openapi_operations`（`:48`，**+2 operation**；两个事实组在 `docs/spec/项目规格.md` 各有**两条** `in_manual: true` claim——「OpenAPI 里共」段与「本轮收尾读数」段各一，即每组各两条合计四条随数字漂，还欠说明书 docx 重生成）、`app_py_files`（`:128`，新增 `monitor.py`/`online_signals.py`/`schemas/monitor.py`）、`mypy_files`（`:138`）、`test_py_files`（`:149`）、`test_collected`（`:163`）。`console_pages`（`:217-224`）**不入本轮打红清单**——它数的是 `docs/records/智能体平台设想.md` 的控制台/业务**页面数量**（pattern `(\d+) 个控制台页面` / `(\d+) 个业务页面`），只有增删 `web/src/pages/` 下的页面才会移动读数，加端点字段与加审计动作名都不触发；本轮与实现轮均不增页面（§7-2），故不登记。**读数统一由 R12 收尾轮 re-cite；本轮与实现轮禁止为变绿改三份守卫清单**（`.qoder/rules/task-order.md` §3 级联口径）。
9. **无推送 ⇒ 告警不主动到人**：不刷新看板就没人知道触警——是选择不是缺陷（§4-6），代价是发现延迟取决于读取频率。
10. **出口层 `null` 与既有端点 `0.0` 并存（裁定 11）**：同一「零分母」事实在 monitor 信号行是 `metric_value: null`，在 `GET /api/v1/handoffs/metrics` 仍是 `handoff_rate: 0.0`。这是**出口层映射，不是第二份事实源**（信号 B 读数仍逐字取自 `collect()`，`_rate()` 不改）；消费方不得把两者混用或互相断言等价——`null` 语义是「本出口不输出伪读数」，`0.0` 是既有端点的历史字面。断言职责见 §9-14。

---

## 11. 与来源的对齐表

| 来源（逐字要点） | 本口径落点 | 对齐说明 |
|---|---|---|
| `docs/下一步规划.md:74`（§0.2 R11 行）「三类信号各有阈值（从基线文件读，不写死代码）与通知出口；误报率单独可读；只出动作建议，不改配置」 | §2（三类信号）、§3（阈值不写死代码）、§4（通知出口）、§5（误报率单独可读）、§6（只出动作建议、不改配置） | 「从基线文件读」落实为：**app 不直读 evals**（架构分层），阈值存 YAML、基线值以**抄本 + fail-closed 一致性测试**钉住——这是对台账措辞的架构约束适配，不是放宽 |
| `docs/下一步规划.md:2658`「三类信号各有唯一动作：无依据率升高 → 查检索与证据判定；转人工率异常 → 查澄清与门；工具失败率 → 查熔断与上游」「阈值不写死在代码里，先从基线文件读，误报率单独记」「红线」 | §3 YAML 示例的三条 `action_suggestion`（逐字对应三条动作）、§8、§5、§6 | 动作建议的三条文案与台账的「唯一动作」一一对应；红线原文进 §6-1 |
| `docs/下一步规划.md:2589`（§14.1 裁定表）「monitor agent \| 用确定性信号替代，不另起一个 agent……多调一次模型判答案既不必要也不可复现」 | 顶部引言、§2 通用规则、§6-2、§9-11 | R11 **不新增 agent、零模型调用**，以确定性读数替代 |

### 11.1 需他处同步（裁定 10——**R12 明确欠账**，本轮与实现轮均不改）

1. ADR 新条目（新增配置面 / 接口面属「改事实」，按 `docs/fact-baseline.yaml:23-24` 头部规则要走 ADR）。
2. `docs/guide/接口说明.md`：`GET /api/v1/monitor/signals` 与 `POST /api/v1/monitor/reviews` 的端点与响应字段说明。
3. `docs/spec/项目规格.md`：阈值表（三个 `signal_id` 与 `threshold`/`calibrated` 现值）。
4. README：若其接口清单引用了 monitor 面，则同步。
5. `docs/下一步规划.md` §0.2 的 R11 行状态（须待 R11 实现收口后按证据改写，状态只能由证据给出——`.qoder/rules/task-order.md` §1）。
6. 说明书 docx 重生成（`in_manual: true` 各条 claim 随 OpenAPI 数字变化，§10-8）。
7. 裁定 11（monitor 出口层零分母 `null` 映射）随本条第 1 项走 ADR 登记；`docs/guide/接口说明.md` 同步时须写清两层区分——monitor 信号面 `metric_value` 可为 `null`，`/api/v1/handoffs/metrics` 面恒为浮点 `0.0`（§2-B、§9-14、§10-10）。

### 11.2 与既有口径文件的交叉（只登记，不修订）

- `.qoder/rules/benchmark-harness.md` §1.0：`evals` / `bench` / `scripts` 三分——与本口径 §3 约束 6「app 不读 evals、tests 可读基线文件」同向。
- `.qoder/rules/approval-tiering.md` §2.1（YAML + loader 同形先例的形状）与 §7（改表后重启进程生效、不承诺热加载）——本口径 §3、§8 直接沿用其措辞。
- `.qoder/rules/score-kinds-and-scales.md` §1.2（阈值族消歧）——本口径 §1.0-3 与它同为「分数阈值 ≠ 比率阈值」这条线的锚点。
- `.qoder/rules/task-order.md` §3（文档事实数的级联）——本口径 §10-8 的「只登记哪几处会红、R12 统一 re-cite」照它执行。
- `.qoder/rules/adoption-surface.md` §3 第 4 条（不改三份守卫清单）——本口径 §7-3 与其一致。
