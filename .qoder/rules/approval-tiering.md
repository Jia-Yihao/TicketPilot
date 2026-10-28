# 审批金额分层（B-3）：项目级口径

> 本文件是 TicketPilot 本轮工作（退款审批按金额分层）的项目级声明，只规定术语、
> 契约、配置项与验收口径。流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md`
> 为准。退款原因 / 退款类型的口径在 `.qoder/rules/refund-reason-taxonomy.md`（B-1），
> 两者冲突时先改口径文件并说明理由，再改代码。
>
> 本文件**只声明口径，不改动任何产品代码、测试、迁移、配置、文档**。
> 「当前事实」均以文件:行给出证据；标注「当前代码尚无对应实现」的项是**待实现口径**。

---

## 1. 术语表

同一概念在本项目只有一种叫法。下表右侧的英文名是**实现必须逐字使用的标识符**
（枚举类名、字段名、JSON 键名、YAML 键名）。

| 中文术语 | 唯一英文标识符 | 唯一定义 |
|---|---|---|
| 审批档位（tier） | `tier` / `tier_id` | 一笔退款按金额落入的**审批规格档**。它回答的是「按什么规格审」，**不回答**「要不要审」（见 §3）。 |
| 档位上限 | `max_cents` | 档位表中一行的金额上界（整数分，**含**）。 |
| 审批角色 | `roles` | 该档位允许做出审批决定的角色集合；本轮只允许 `approver`（§2.3 校验 5）。 |
| 所需审批数 | `required_approvals` | 该档位需要的审批份数；本轮恒为 `1`（单层，§6 边界 1）。 |
| 档位快照 | tier snapshot | 解析出档位后冻结下来的四元组 `tier_id` / `max_cents` / `roles` / `required_approvals`，进 `refund_payload` 与 `action_digest`（§5）。 |
| 未达阈值 | below threshold | `PolicyEngine.requires_approval(amount_cents)` 为**假**，即金额不超过 `refund_approval_threshold_cents`。 |
| 审批路径 | approval path | 未达阈值之外的路径：建审批行 → 任务进 `WAITING_APPROVAL` → 审批通过后执行。 |
| 自动执行 | auto-executed | 未达阈值时的路径：**不起审批行**，直接执行退款，但仍走同一套台账、资格复核、对账与回执。 |
| 执行路径标记 | `approval_path` | 落进任务 `result` 与任务事件的路径标记，取值 `AUTO` / `APPROVAL`，用于把「自动执行」与「审批通过」区分开（§4.3）。 |

### 1.0 必须消歧的同名概念（关键口径）

裸名 `tier` / 「档位」在本仓库不是新概念的唯一载体，也不是别的东西的别名。下表把
「审批档位」与仓库里已经存在的几个近义概念分开；**它们不是一回事，实现不得混用**。

| 已有标识符 | 含义 | 证据 | 与「审批档位」的关系 |
|---|---|---|---|
| `RiskLevel`（`R0`–`R3`） | **风险等级**：一个意图或动作的风险档，与金额无关 | `app/models/enums.py::RiskLevel`（当前 `:116-120`）；`app/agent/intents.yaml` 的 `risk_level: R3`（`REFUND_REQUEST` 行） | 不同概念。风险等级回答「这个动作多危险」，档位回答「按金额该谁批」。**禁止**把 `RiskLevel` 值当档位值，也禁止把档位写进 `risk_level` 字段。 |
| `refund_approval_threshold_cents` | **阈值**：金额超过它就要人批 | `app/core/config.py:51`（默认 `0`）；环境变量 `REFUND_APPROVAL_THRESHOLD_CENTS`（`.env.example:16`） | 不是档位。它是**一个标量**，回答「要不要人批」；档位是**一张表**，回答「按什么规格批」。两者分工见 §3。 |
| `requires_approval`（布尔） | 资格结果里的「要不要人批」 | `app/core/policy.py:92-93`（`PolicyEngine.requires_approval`）；`app/services/refund_policy.py:25`（`EligibilityResult.requires_approval`）、`:98`（写入处）、`:37`（`to_dict()` 序列化键） | 不是档位。它是档位**之外**的判定（§3）；且按 B-1 口径 §4.2，**它不进摘要**（本文件 §5 不改这一条）。 |
| `action_digest` / `refund_payload` | 审批摘要 / 退款载荷 | `app/services/approvals.py:27-31`（`canonical_digest`）、`app/services/approvals.py::refund_payload`（当前 `:34-66`） | 不是档位。档位是**进**载荷的一个键（§5），不是载荷本身。 |
| `RefundType`（`FULL` / `PARTIAL` / `SHIPPING` / `PRICE_PROTECTION`） | **退款类型**：业务动作维度 | B-1 口径 §1.2；枚举已落地 `app/models/enums.py::RefundType`（当前 `:103-113`） | 不同概念。退款类型是「退的是什么形态的钱」，档位是「谁来批」。`PARTIAL` 不等于「低档位」。 |
| `approvals.reason` / `decide(reason=...)` | 审批**决定 / 驳回 / 失效**的原因 | `app/models/approval.py:52`；`app/services/approvals.py::decide`（形参 `reason`，当前 `:128`）、当前 `:142`（失效原因）、`:150`（落库）、`:158`（审计） | 不同概念。与档位无关。 |
| `Identity.roles` / `require_approver` | 运行时的**身份角色集合**与审批人校验 | `app/core/security.py:10-17`（`Identity.has_role`）、`:32-34`（`require_approver`） | 相关但不同：`roles`（档位表里）是**该档位允许的角色集合**（数据），`Identity.roles` 是**某人现在有什么角色**（运行时事实）。两者比对发生在审批决定时（§4.1）。 |

---

## 2. 档位表契约

### 2.1 位置与形状

**定死**：档位表放在**新增文件** `app/core/approval_tiers.yaml`，由**新增 loader**
`app/core/approval_tiers.py` 读取；**不并入** `app/core/config.py`，**不**写成 Python 常量。

理由（均可核对）：

1. **与项目既有的「数据在 YAML、代码只读」同形**：`app/agent/intents.yaml` + `app/agent/intent_catalog.py`
   （`_CATALOG_PATH`，当前 `:58`）、`app/agent/escalation.yaml` + `app/agent/escalation.py`
   （`_CATALOG_PATH`，当前 `:31`）都是同一形状。档位表是**第三张同形表**，不是第四种机制。
2. **不并进 `config.py`**：`app/core/config.py` 是 `pydantic-settings` 的扁平配置（全部标量），
   档位表是**有结构的列表**（多行、有角色、有上限）。塞进去要么把结构压成字符串，要么把
   配置类变成一个解析器——两种都会让「配置」与「数据」混在一处。
3. **放 `app/core/` 而不是 `app/agent/`**：档位是**退款写路径的阈值**，其唯一读取口是策略层
   （`app/core/policy.py` 的模块文档写着「thresholds must come from configuration, not from
   code. This module is that configuration's single reader」）。`app/agent/` 下那两张 YAML
   是**对话侧**的行为表（意图目录、转人工触发表），与退款执行路径无关；把退款审批的
   档位放进对话侧目录会让「谁读它」变得含糊。
4. **不变量 5 的延伸**：`openspec/project.md:59` 不变量 5 写「意图目录（`intents.yaml`）与转人工
   条件（`escalation.yaml`）是数据，不是提示词」。档位表同属「数据」这一类：**代码读取它，
   不在 Python 里硬编码档位，也不在提示词里让模型现编**。

**键结构（定死）**：

```yaml
version: "2026-10-26"
tiers:
  - tier_id: T1
    max_cents: 5000000
    roles:
      - approver
    required_approvals: 1
```

**默认表（定死）**：上表即默认内容。语义见 §2.2。

### 2.2 默认语义：单层、保持现行为

- 默认表**只有一档** `T1`，`max_cents` 取策略金额上限 `refund_max_amount_cents` 的默认值
  `5_000_000`（`app/core/config.py:50`），`roles: [approver]`，`required_approvals: 1`。
- 语义：**今天所有退款都要 1 个 `approver` 审批**——与本轮之前逐字一致。
- 默认档位表 + 默认阈值 `refund_approval_threshold_cents = 0`（`app/core/config.py:51`）时，
  **每一个正金额退款都走审批路径**，`pending_action.requires_approval` 恒为真，
  既有 Golden 与退款用例的读数**不得变化**。
- 末档 `max_cents` 宜 ≥ 运维实际生效的 `REFUND_MAX_AMOUNT_CENTS`；若末档小于它，
  超出末档的金额按 §2.4 的兜底落到**最后一档**（最高档），不会漏批、也不会降档。

### 2.3 加载期校验（违反即报错，**不得静默跳过或降级**）

1. 顶层键只允许 `version` / `tiers`；未知键报错（沿用 `app/agent/intent_catalog.py` 的
   `_reject_unknown` 形状，`:252-257`）。
2. 档位项键只允许 `tier_id` / `max_cents` / `roles` / `required_approvals`；未知键报错。
3. `tier_id` 非空、**全表唯一**、全大写蛇形（形如 `T1` / `T2_LARGE`）。
4. `max_cents` 必须是 `>= 0` 的整数；表内按 `max_cents` **严格递增**（重复或倒退报错）。
   理由：档位解析靠「首个 `max_cents >= amount` 命中」，顺序含糊就会解析出不同档位。
5. `roles` 非空列表；**本轮只允许 `approver`**；同一档内不得重复。
   理由：审批人校验当前只有 `require_approver`（`app/core/security.py:32-34`）一个口径，
   放别的角色进来没有第二个校验点去兑现它。
   **并做规范化**：装载时把每档的 `roles` **按字典序排序**后再进档位快照。
   理由：`canonical_digest` 只对**键**排序（`json.dumps(payload, sort_keys=True)`，
   `app/services/approvals.py:27-31`），而 `roles` 的**值是列表**——列表的**顺序**会逐字进入
   摘要。于是把同一组角色换个书写顺序（`["approver","reviewer"]` → `["reviewer","approver"]`）
   会让摘要**假漂移**，而审批规格其实一个字都没变。排序在装载期一次做完，快照因此是
   **规范形**：同一组角色只有一种书写方式进摘要。
   当前唯一合法的角色集合是单元素 `["approver"]`，此时排序是恒等变换，**既有读数不变**。
6. `required_approvals` 必须 `== 1`。本轮不做多级会签（§6 边界 1），写别的值属于越界，
   加载期就报错，而不是运行时忽略。
7. `tiers` 为空报错。一张空表会被静默读成「全部落进兜底档」，属最危险的失败模式。

### 2.4 解析规则与兜底

- 解析是**纯函数**：不读时钟、不用随机数、不查库、不调模型。同一金额两次解析结果逐字相同。
- `tier_for(amount_cents)`：取**首个** `max_cents >= amount_cents` 的档（表已按 `max_cents`
  升序，线性扫描即可）。
- **未命中**（金额大于所有档位的 `max_cents`）：取**最后一档**（最高档），并由调用方在
  trace 里记一条 `refund.tier_fallback`（实现后的 span 名为 `refund.tier_fallback`，
  `app/services/runtime.py:715-724`；口径原写作 `tier_fallback`，属实现前的命名预期，
  按代码订正字面，留痕义务不变）。**不得**回落到最低档——把大额退款降成最低档的审批规格
  是最危险的读法。
- 解析结果对外暴露为**档位快照**：`{"tier_id", "max_cents", "roles", "required_approvals"}`，
  `roles` 序列化为**按字典序排序后**的字符串列表（§2.3 校验 5 的规范化）。快照是**冻结值**：
  解析之后档位表再改，已解析出的快照不变（摘要因此可复算）。

---

## 3. 阈值与档位的分工（两个问题，两个来源，不得互相顶替）

| 问题 | 唯一来源 | 口径 |
|---|---|---|
| **要不要人批？** | `PolicyEngine.requires_approval(amount_cents)`（`app/core/policy.py:92-93`），阈值取自 `refund_approval_threshold_cents`（`app/core/config.py:51`） | 金额 **>** 阈值 → 要人批；金额 **≤** 阈值 → 自动执行（§4.2）。 |
| **按什么规格批？** | 档位表 `app/core/approval_tiers.yaml`（§2） | 解析出档位 → 取该档的 `roles` / `required_approvals`。 |

- **档位表的 `max_cents` 不参与「要不要人批」的判定。** 理由：NEXT_STEPS B-3 的「记录时更正」
  点名的是**既有**旋钮 `refund_approval_threshold_cents`（「阈值是有的……真正的问题是写路径
  根本没读这个结果」，`docs/下一步规划.md:2189`）。若让新表接管该判定，等于用一张新表覆盖掉
  旧旋钮，旧旋钮会变成**第二个死旋钮**——那正是 B-3 要消灭的东西。
- **档位表在自动执行路径上照样解析**，并把档位快照进摘要：它记录「这笔金额落在哪一档的
  审批规格上」，是台账事实，与「这次有没有人批」是两件事。
- 两条判定**不得互相覆盖、不得互相推断**：读数时 `requires_approval` 只从资格结果读
  （`eligibility["requires_approval"]`，来源 `app/services/refund_policy.py:37`、`:98`），
  档位只从档位表读。

### 3.1 当前事实（已核对，NEXT_STEPS 的「记录时更正」在此落成证据）

- 阈值**已经算进**资格结果：`app/services/refund_policy.py:98` 写入
  `requires_approval=self.engine.requires_approval(amount)`，`to_dict()`（`:29-39`）把它
  序列化成 `requires_approval` 键。
- **历史事实（B-3 实现前）**：**写路径根本没读它**——`TaskRuntime._handle_refund`（旧
  `app/services/runtime.py:523`）在资格通过后**无条件**建审批（`create_refund_approval`
  旧调用点 `:587-593`），`pending_action.requires_approval` **硬编码 `True`**（旧 `:617`）；
  把阈值调到 10000，退款仍然全部等人批。
  **实现后**：`_handle_refund`（当前 `app/services/runtime.py:629`）读
  `requires_approval = bool(eligibility["requires_approval"])`（当前 `:744`），未达阈值走
  自动执行分支（当前 `:745-787`），达阈值才调 `create_refund_approval`（当前 `:788-797`）；
  `pending_action` 的 `requires_approval` 取自该判定本身（当前 `:824`）。
- **耦合必须登记**：`app/services/escalation.py:286-290`（`_needs_review`）读的是**同一个**
  `PolicyEngine.requires_approval(amount)`，它是 `app/agent/escalation.yaml` 里 `risk` 信号
  （`trigger: risk_level`、`risk_level: R3`、`intents: [REFUND_REQUEST]`、`takeover: false`）
  的触发条件之一。因此**阈值一旦真正生效**，该信号在阈值以下的金额上不再触发，
  对话里追加的那句「这类高风险操作会进入人工审批环节……」（`app/agent/escalation.yaml` 的
  `risk` 信号 `reply`）也不再出现。
  **口径：接受这一联动，不解耦。** 理由：那句话描述的是事实——阈值以下确实不进人审；
  留着它才是撒谎。解耦（给转人工信号单开一个阈值）属于新增配置项，超出本轮范围。
  实现时**必须**在 ADR 里写明这条联动。

---

## 4. 两条路径的行为契约

### 4.1 审批路径（`requires_approval` 为真）

- 行为与本轮之前**一致**：建审批行（`app/services/approvals.py::create_refund_approval`，
  当前 `:76`）→ 任务进 `WAITING_APPROVAL`（`app/services/runtime.py::_handle_refund` 内
  `services.tasks.transition`，当前 `:798-827`）→ 审批通过后执行
  （`app/services/runtime.py::approve`，当前 `:850` 起）。
- **新增**：审批载荷含**档位快照**（§5）；审批人校验除既有 `has_role("approver")`
  （`app/services/runtime.py::approve`，当前 `:859-860`）外，还须持有**该档位 `roles` 中的
  至少一个**——这条已落在 `app/services/approvals.py::_require_tier_role`（当前 `:163`，
  `decide` 内在 `:147` 调用，判据 `:181-184`）。
  默认档位的 `roles` 就是 `["approver"]`，因此默认配置下这条校验与今天等价。
- 四眼原则不变：发起人不能审批自己的申请（`app/services/approvals.py:145-146`），
  `admin` 也无自批例外。

### 4.2 自动执行路径（`requires_approval` 为假）

**必须满足（逐条可判定）**：

1. **不起审批行**：不调用 `create_refund_approval`，`approvals` 表不新增行。
2. **任务不进 `WAITING_APPROVAL`**：从 `RUNNING` 直接进入执行结果对应的终态
   （`SUCCEEDED` / `FAILED`，状态迁移规则照 `_apply_refund_outcome`，
   `app/services/runtime.py::_apply_refund_outcome`，当前 `:1222`；自动路径的调用点在
   `_handle_refund` 内当前 `:749`）。
3. **仍走幂等台账**：先写执行台账行（`app/services/executions.py:18` `create_pending`，
   幂等键 `refund:<task_id>`，`request_digest` = 载荷摘要），与审批路径同一形状。
4. **仍走同一个执行器与同一个权威校验**：`app/services/refunds.py::RefundExecutionService.execute`
   （当前 `:262`）与 `VerificationService.verify_refund`
   （`app/services/verification.py:20-21`），不得另写一条「自动执行专用」的简化执行路径。
   **历史说明（B-3 实现前）**：本条在当时结构下不可直接满足，须先按 §4.2.1 重构（该节是对
   本条的显式授权）。**实现后**：重构已落地，权威执行体是 `refunds.py::_execute_refund`
   （当前 `:299`），审批路径经 `execute` 薄包装、自动路径经 `execute_auto`（当前 `:284`）
   调用**同一份**执行体。
5. **仍做资格复核**：执行前按 `app/services/refund_policy.py:48`（`RefundPolicy.check`）
   重新判定，强度**不得低于**审批路径（审批路径的那次复核当前在
   `app/services/refunds.py:450` `self.policy.check(...)`——已移入两条路径共用的
   `_execute_refund`，同一处）。资格不过 → 不执行，转人工复核，与审批路径同款处理。
6. **绑定校验等价**：审批路径用 `verify_binding`（`app/services/approvals.py::verify_binding`，
   当前 `:186-194`，对**存量载荷**重算摘要）；自动路径没有审批行，改用**台账 `request_digest` 重算比对**
   （台账写入见 `app/services/executions.py:30-38`；自动路径的比对在
   `refunds.py::_verify_ledger_binding`，调用点当前 `:390`）。二者保证同一件事：**执行的就是台账里
   那一件**。自动路径不得跳过这项比对。
   **对账面的归属声明（2026-10-27 用户裁决）**：退款**对账**
   （`app/services/refunds.py::reconcile_task`，当前 `:535`）重建退款原因与退款类型时，
   **审批路径**读审批载荷 `action_payload`，**自动执行路径**（`approval is None`）读
   **执行台账的 `request_payload`**（分支当前在 `:585-592`）。理由：AUTO 的台账载荷与审批载荷
   由同一次 `refund_payload(...)` 产生（`app/services/runtime.py:725-739`），是同一份被摘要覆盖的
   快照，因此**对账仍不重新推断、不重新采集**——AUTO 明明采集过原因的退款不得在对账面读成
   「未提供」。边界：这一回落**只**补 AUTO 的对账面取值，**不**改变本条的绑定比对（仍用
   `request_digest` 重算），也**不**改变存量无键历史载荷显示「未提供」的既有口径
   （§1 术语表 / §2 默认值回读）。
7. **不得放宽工具侧的审批要求**：`request_refund` 的 ToolSpec 仍是 `requires_approval=True`
   （`app/tools/builtin.py` 的 `request_refund` ToolSpec 块，以 spec 名为锚，行号会漂移），`ToolGateway.call_outcome` 的审批校验（`app/tools/gateway.py:65-72`）
   一行不改。自动路径**不经过**该工具入口，而是在服务层复用同一执行器；
   **不得**为了让它跑通而给工具加「跳过审批」开关（那会同时削弱审批路径的闸门）。
8. **留痕**：任务事件 `refund.auto_executed`；任务 `result` 里 `approval_path` 为 `AUTO`
   （见 §4.3）；写一条审计（`action="refund.auto_executed"`，`detail` 含 `task_id` /
   `order_id` / `amount_cents` / `tier_id`）。

### 4.2.1 执行器的结构授权：同一权威执行体、两种入口（**对第 4 条的显式澄清**）

**历史事实（B-3 实现前，当时已核对，构成重构的必要性依据）**：`RefundExecutionService.execute(task_id,
approval_id)`（旧 `app/services/refunds.py:127`）在入口处**硬依赖审批行**——
`self.approvals.require(approval_id)`（旧 `:129`，缺行即 `NotFoundError`）、
`approval.task_id != task_id` → `NotFoundError`（旧 `:135-136`）、
`payload = dict(approval.action_payload or {})`（旧 `:130`，金额与订单号**从审批载荷读**）。
自动执行路径按定义**没有审批行**（第 1 条），因此第 4 条「仍走同一个执行器」在当时结构下
**不可直接满足**：不重构就只能在自动路径上另写一份执行逻辑，而那正是第 4 条禁止的
「另写一条自动执行专用路径」。
**实现后**：重构已按本节落地——权威执行体为 `refunds.py::_execute_refund`（当前 `:299`，
两条路径共用一份）；审批绑定包装器为 `execute`（当前 `:262`：`approvals.require` 当前 `:271`、
审批-任务归属校验当前 `:272-273`、取审批载荷当前 `:281`）；自动执行入口为 `execute_auto`
（当前 `:284`，以 `approval=None` 进入同一执行体，绑定改用台账 `request_digest` 重算比对）。

**因此本节显式授权对 `RefundExecutionService` 做结构重构**，把「审批行」与「执行体」解耦。
授权的是**结构**，不是行为；重构后**不得**产生任何可观察的行为差异（见不变量 5）。

**目标形态**（不强制具体命名，语义必须等价）：

- **权威执行体（payload-level executor）**：一个内部执行函数，接受「退款载荷 + 台账 + 订单 +
  任务」，承载**全部权威校验与写操作**：订单加锁读取、`blocking_refund_except` 并发冲突判定、
  资格复核（`RefundPolicy.check`）、幂等占位（`claim_for_send`）、支付渠道调用、
  **验证与对账**（`VerificationService.verify_refund`）、终态落库与回执。
- **审批绑定包装器（approval-bound wrapper）**：现有 `execute(task_id, approval_id)` 变成薄包装
  ——取审批行 → 校验 `approval.task_id == task_id` 与 `approval.status is APPROVED` →
  `verify_binding(approval, order)` → 取审批载荷 → 调**权威执行体**。
- **自动执行入口**：调用**同一个权威执行体**，绕过审批绑定部分，改用第 6 条的台账
  `request_digest` 重算比对承担「执行的就是台账里那一件」这一保证。

**不变量（重构后必须逐条成立）**：

1. **权威执行体只有一份**：两条路径调用的是**同一个**执行体函数，不是两份相似代码；仓库内
   退款执行的写路径应当只有一处。**不得**出现「自动执行专用」的分支实现。
2. **不得削弱任何权威校验**：订单加锁、并发冲突判定、资格复核、幂等占位、渠道调用、
   验证对账六项，**两条路径强度相同**。自动路径**不得**以「没有审批行」为由跳过其中任何一项。
3. **绑定校验两份都保留**：审批路径的 `verify_binding`（`app/services/approvals.py::verify_binding`，
   当前 `:186-194`，对审批载荷重算）与自动路径的台账 `request_digest` 重算比对（第 6 条）是**两份等价校验**，
   **不是二选一**。
4. **失败与转人工的行为形状相同**：资格不过 / 绑定不过 / 并发冲突，自动路径与审批路径**同样**
   不执行退款、同样写台账取消态、同样转人工复核；只是自动路径**没有审批行可失效**，改为写审计
   与台账错误码。
5. **不得改变审批路径的既有行为**：既有退款用例的读数（回执文案、事件序列、台账状态、
   `action_digest`）**逐条不变**。重构是**结构**改动，不是行为改动。

**明确的禁止**：

- **禁止**在自动路径上复制一份退款执行逻辑（即使只是「少几个分支」「少一次审批状态检查」）。
- **禁止**给工具入口加「跳过审批」开关（与第 7 条同向）。
- **禁止**因为自动路径没有审批行，就把资格复核或绑定比对降级为「可选」。

**审查锚点**（实现者与 reviewer 都要读；实现后位置，行为约定不变）：

- `app/services/refunds.py::RefundExecutionService.execute`（当前 `:262`，重构后为薄包装）、
  `:271`（`approvals.require`）、`:272-273`（审批-任务归属校验）、
  `:386-415`（`verify_binding` 失败分支，含自动路径的台账重算比对分支 `:389-391`）、
  `:450`（资格复核，两条路径共用）。权威执行体 `_execute_refund`（当前 `:299`）、
  自动入口 `execute_auto`（当前 `:284`）。
- `app/services/approvals.py::verify_binding`（当前 `:186-194`）。
- `app/services/executions.py:18`（`create_pending`）、`:30-38`（台账写入与 `request_digest`）。

### 4.3 「自动执行」与「审批通过」的区分（台账 / 事件 / 结果 / 回执）

| 面 | 审批路径 | 自动执行路径 |
|---|---|---|
| 审批行 | 有（`approvals` 表一行，状态 `PENDING` → `APPROVED` → `CONSUMED`） | **无** |
| 执行台账 | 有（`tool_executions` 一行，幂等键 `refund:<task_id>`） | 有（同形） |
| 任务事件 | `approval.requested`（`app/services/runtime.py:828-837`）、`approval.approved`、`approval.rejected` | `refund.auto_executed`（当前 `:750-758`） |
| 任务 `result` | `approval_path` = `APPROVAL` | `approval_path` = `AUTO` |
| 公开回执措辞 | 「已审批通过 / 审批后执行」类措辞 | 「已按规则自动处理」类措辞；**不得**复用「已审批通过」 |
| 客户可见文本里的档位 | **不出现** `tier_id` / `roles` / `required_approvals` / 阈值金额 | 同左 |

- 「执行路径标记」`approval_path` 是**新字段**，取值封闭：`AUTO` / `APPROVAL`。落点：任务
  `result` 顶层与任务事件负载。
- 公开回执**必须**能让客户看出「这笔是自动处理的、还是人工批过的」——这是业务事实，不是内部细节。
  但档位标识与阈值金额是**内部口径**，不进客户可见文本（与 B-1 口径「`reason_note` 不进公开
  回执」同向）。
- 坐席 / 审批侧**必须**能看出档位与审批规格：档位快照在审批载荷里，审批详情页
  （`web/src/pages/ApprovalWorkbench.tsx`）按载荷字段渲染即可。

---

## 5. 档位进摘要

### 5.1 新增键：`approval_tier`

- `refund_payload`（`app/services/approvals.py::refund_payload`，当前 `:34-66`）**新增一个键** `approval_tier`
  （已落地：该键当前在 `:65`），值是**档位快照对象**：

  ```json
  {"tier_id": "T1", "max_cents": 5000000, "roles": ["approver"], "required_approvals": 1}
  ```

- 为什么是**整个快照**而不是只放 `tier_id`：摘要的职责是「批准的就是执行的」。
  若只放 `tier_id`，运维把同一档的 `roles` 或 `required_approvals` 改掉，摘要**不变**，
  「分层变化会让旧审批失效」就落空了。快照里带上规格，改规格即改摘要。
- 键名 `approval_tier` 而不是 `tier`：`tier` 过于泛化，且「档位」在本仓库的近邻概念是
  `RiskLevel`（§1.0）；`approval_tier` 自带语境，不与风险等级、也不与退款类型混淆。
- 键名**不得**是裸名 `reason`（那是 B-1 口径 §1.0 的三个合法位置之一，与本键无关，
  混用即违规）。

### 5.2 与 B-1 口径 §4.1 八键清单的关系（**这是一处显式修订**）

B-1 口径 `.qoder/rules/refund-reason-taxonomy.md` §4.1 把 `refund_payload` 的键列成**封闭的
八项**（**该八项清单是本文件修订之前的版本**；本文件把它修订为九项，见下段）：`action` /
`order_id` / `amount_cents` / `currency` / `resource_version` /
`policy_version` / `reason` / `refund_type`。本文件**修订**该清单为**九项**，新增
`approval_tier`。

- **修订理由**：NEXT_STEPS B-3 段「建议改动」第 3 条写明「分层结果进 `action_digest`（否则审批人
  批准的是另一档）」（`docs/下一步规划.md` 的 **B-3 审批没有金额分层（中高）** 段）。
  档位不进摘要，审批人批准的与执行的就不是
  同一档，B-3 的核心诉求落空。
- **修订方式**：按 `agents.md:15`「冲突时先停下、说明冲突、改口径文件，再改代码」，已在本文件与
  B-1 口径文件（§4.1 表格加一行、§6 加一句指向本文件）**两处最小定点修订**中落地。
- **不影响在途审批**：摘要覆盖的是**生成时**的载荷快照；存量审批的 `action_payload` 不含
  `approval_tier`，其 `action_digest` 据此计算。加上新键**不会**使存量审批失效
  （与 B-1 口径 §2「在途审批安全」同一条理由：`verify_binding` 对**存量载荷**重算，
  `app/services/approvals.py::verify_binding`，当前 `:186-194`）。
- **B-1 §4.2 不变**：资格诊断输出（`max_refundable_cents`、`requires_approval`）**仍不进摘要**。
  进摘要的是**档位快照**，不是「要不要人批」这个布尔量。

### 5.3 档位变化使旧审批失效（执行前的重校验）

- 摘要含档位快照，因此**新造的**审批摘要随档位表变化而变化。
- 但**存量**审批的绑定校验是对存量载荷重算（§5.2），不会自己发现档位变了——重算读的就是
  审批行里那份存量载荷，档位表改了它读不到。因此执行前**必须**另做一次显式重校验。
  **这条重校验的物理位置在退款执行体内部**（`app/services/refunds.py::_execute_refund`，
  判据 `:423`），所以它的**作用对象是已进入执行的那张审批行**——正常状态即
  `ApprovalStatus.APPROVED`：审批路径进入该执行体的唯一入口是薄包装
  `RefundExecutionService.execute`（`app/services/refunds.py:262`），它在 `:275-280` 以
  「台账仍是 `ToolExecutionStatus.PENDING` 而审批状态**不是** `ApprovalStatus.APPROVED`」
  抛 `ConflictError`（读数 `:280`），因此仍处 `PENDING`、尚未进入执行的存量审批
  **不会被这条规则自动触达**，它要等到被批准并进入执行时才接受这次检查。
  另有两道闸佐证这一结论（独立审查 2026-10-27 返工轮实地核验，均为佐证、不改变上句判据）：
  `app/tools/gateway.py:71-72` 工具入口的审批状态闸是**无条件**的——
  `if approval.status is not ApprovalStatus.APPROVED:` /
  `raise ConflictError(f"Approval is {approval.status.value}, not APPROVED.")`，
  经工具入口进来的审批必须已是 `APPROVED`，`PENDING` 审批过不去这道门；
  `app/services/refunds.py:322` 执行体对台账缺失**早退**——`if not order_id or ledger is None:`
  即返回 `VerificationStatus.NEEDS_HUMAN_REVIEW` + `reason_code="MISSING_EXECUTION_CONTEXT"`，
  没有执行台账行就触不到执行体后续各项检查（与「审批路径必有台账、自动路径无审批行」的
  形状互为对照）。
  **本段是措辞订正**（独立审查 2026-10-27）：口径此前把作用对象写成「存量 PENDING 审批」，
  与代码现实不符；订正不改任何代码、不改任何 YAML。
  这次显式重校验的规则：
  - 若 `approval.action_payload` **含** `approval_tier` 键：用**当前**档位表重解析该金额的
    档位，与存量快照比对；**不一致** → 审批**失效**，原因码 `APPROVAL_TIER_CHANGED`，
    不执行退款，转人工复核（处理形状照 `app/services/refunds.py:386-415` 的
    `verify_binding` 失败分支——本条已落在 `_execute_refund` 当前 `:417-447`：
    `approval_tier_drifted` 判定 `:423`、失效与原因码 `:424-426`、审计 `:431-439`）。
    **作用对象**是**已进入执行的那张审批行**（判据 `approval is not None`，`:423`），
    其正常状态即 `ApprovalStatus.APPROVED`；自动执行路径 `approval is None`，这条重校验
    按定义不适用。**失效落库覆盖的状态范围**：`ApprovalService.invalidate()`
    （`app/services/approvals.py:196-202`）对 `ApprovalStatus.PENDING`（`:197-198`，走
    `repo.resolve`）与 `ApprovalStatus.APPROVED`（`:199-202`，直接改状态 + `reason` +
    `resolved_at`）**两种存量状态都能落 `INVALID`**，因此「只能失效 PENDING」同样不成立；
    只是 `PENDING` 审批不会被这条**执行前**规则自动触达（见上），它等到被批准并进入执行时
    才受检。
  - 若**不含**该键（存量审批）：**跳过**这项重校验，既有 `verify_binding` 行为不变。
    理由：跳过才不会误伤存量审批（B-1 口径 §2「在途审批不 retro-fit」）。
- 阈值变化（`refund_approval_threshold_cents`）**不**触发这条失效：它决定的是「要不要人批」，
  已经建出来的审批已经过了那道闸，改阈值不该把它变成废纸。档位变化则改的是「按什么规格批」，
  已建审批的规格可能已不是当前规格，必须失效。

### 5.4 「自动执行」这条路径差异**不进摘要**（登记为已知取舍）

- 摘要回答的是「批准 / 执行的是不是同一件事」；「谁批准的」由**审批行**与**审计**回答。
  因此 `approval_path`（`AUTO` / `APPROVAL`）**不进** `refund_payload`。
- 代价（必须登记）：同一订单、同一金额、同一原因与类型的两笔退款，一条自动执行、一条人工审批，
  载荷摘要**相同**。区分靠 `approvals` 表**有没有那一行**、任务 `result.approval_path`、
  任务事件与审计记录。
- 备选方案（若评审要求「两条路签的东西必须不同」）：在载荷里再加 `approval_required: bool`。
  该方案会改变摘要形状（第十键），并使**自动路径与审批路径的载荷不再同形**，且与 B-1 §4.2
  「`requires_approval` 不进摘要」的既有口径直接冲突——采纳它必须先改 B-1 口径。

---

## 6. 边界（本轮明确不做）

1. **不做多级会签**：`required_approvals` 必须为 `1`（§2.3 校验 6）。表里写 `2` 是加载期报错，
   不是运行时忽略。
2. **不做按客户等级的差异化策略**：档位只看金额，不看客户、租户或历史。
3. **自动执行不降低资格校验强度**：自动路径仍跑 `RefundPolicy.check`，且**不得**新增任何
   「自动路径豁免某条资格规则」的分支（§4.2 第 5 条）。
4. **阈值与档位表改动无需改代码即生效**：改环境变量或改 YAML 后**重启进程**生效；不需要改代码、
   不需要发版。「生效」的定义是**重启后新读数**生效，不承诺热加载。
5. **档位变化会让旧审批失效**（§5.3）——这是**特性**不是缺陷，实现时不得为了「让旧审批还能用」
   而跳过重校验。
6. **不改退款金额计算逻辑**：`RefundPolicy.check` 的 `amount` 取值与校验保持不变
   （`app/services/refund_policy.py:81-91`）。
7. **不引入财务分层**（运费 / 优惠券 / 税费分摊）：属 B-8，见 B-1 口径 §3。
8. **不引入第二套审批角色体系**：`roles` 本轮只允许 `approver`；新增审批角色（如财务主管）
   需另立口径。
9. **不改四眼原则**，不放开自批。
10. **不覆盖退货流程**（`WAITING_RETURN`、`returns` 表）：属 B-2。
11. **不引入 `refunds.product_id`**：属 B-2 / P3。
12. **不改 B-1 的采集口径**：退款原因 / 退款类型的取值通路不在本文件管辖。

---

## 7. 配置项与生效方式

| 项 | 位置 | 默认 | 含义 |
|---|---|---|---|
| `refund_approval_threshold_cents` | `app/core/config.py:51`；环境变量 `REFUND_APPROVAL_THRESHOLD_CENTS`（`.env.example:16`） | `0` | 「要不要人批」的阈值。**本轮让它在写路径首次真正生效**（§3、§4.2）。不改名、不废弃。 |
| 档位表 | `app/core/approval_tiers.yaml`（**新增**） | §2.1 的单层表 | 按金额分层审批规格。 |
| 档位表版本 | 表内 `version` 键 | `"2026-10-26"` | 与 `intents.yaml` / `escalation.yaml` 的日期版本号同风格。 |

- 档位表由 loader 在**进程启动时读取并缓存**（`@lru_cache(maxsize=1)`，与
  `app/agent/intent_catalog.py:315-317`、`app/agent/escalation.py:154-156` 同形；
  实现后的 loader 在 `app/core/approval_tiers.py`，其 `@lru_cache` 当前在 `:232`）。
- 两者都是**纯本地数据**，没有外部端点依赖；生产环境不因它们拒绝启动。

---

## 8. 验收标准（DoD）

逐条可判定，每条都要能被测试钉住。

1. **默认不变**：默认阈值 `0` + 默认档位表下，每一笔退款**仍然建审批行**、任务仍然进
   `WAITING_APPROVAL`、`pending_action.requires_approval` 仍为真；既有 Golden 与退款用例
   读数**逐条不变**。
2. **阈值真正生效**：把阈值设为 `X` 后，金额 `≤ X` 的退款**不起审批行**、直接进终态、
   有执行台账行与 `refund.auto_executed` 事件；金额 `> X` 的退款仍建审批行。
   同一笔金额在阈值上下**行为可测且相反**。
3. **无需改代码即生效**：只改环境变量或只改 YAML（不碰任何 `.py`）并重启，读数即按新配置变化。
4. **档位进摘要**：只改档位表的 `roles` 或 `required_approvals`（`tier_id` 不变），
   新造审批的 `action_digest` **必须**与旧的不同；只改 `max_cents` 但档位解析结果不变时，
   摘要**必须**不变（快照取的是解析结果，不是原始表行）。改的是**角色集合的内容**；
   同一组角色只换**书写顺序**时，摘要**必须不变**（角色列表在装载期按字典序规范化，
   见 §2.3 校验 5）。
5. **档位变化使旧审批失效**：改档位表后，**已进入执行的那张审批行**（正常状态
   `ApprovalStatus.APPROVED`）在执行前被判失效（原因码 `APPROVAL_TIER_CHANGED`），
   退款**未执行**；仍处 `PENDING`、尚未进入执行的存量审批**不会被这条规则自动触达**，
   它等到被批准并执行时才接受这次检查（`invalidate()` 对 `PENDING` 与 `APPROVED`
   两种状态都能落 `INVALID`，见 §5.3）；载荷里**没有** `approval_tier` 键的
   存量审批**不被**这条规则误伤。
6. **自动执行不降低资格校验强度**：构造「资格在请求后变化」的用例，自动路径与审批路径
   **同样**不执行退款、同样转人工复核。
7. **两条路径可区分**：自动执行的任务 `result.approval_path == "AUTO"`、有
   `refund.auto_executed` 事件、公开回执措辞与审批通过**不同**；客户可见文本里
   **检索不到** `tier_id` / `roles` / `required_approvals` / 阈值金额。
8. **加载期校验**：未知顶层键、未知档位键、非法 `max_cents`、`max_cents` 重复或倒退、
   重复 `tier_id`、空 `roles`、非 `approver` 角色、`required_approvals != 1`、空表 ——
   **逐条**报错（测试钉住每一条）。
9. **档位兜底**：金额大于所有档位上限时落到**最后一档**（不是最低档），且记
   `refund.tier_fallback`（span 名按代码订正，见 §2.4；口径原写作 `tier_fallback`）。
10. **工具闸门未被放宽**：`request_refund` 的 `requires_approval` 仍为 `True`，
    `ToolGateway` 的审批校验未被改动（回归断言）。
11. **联动登记**：阈值生效导致 `risk` 转人工信号在阈值以下不再触发、以及追加话术随之消失，
    这条联动写进 ADR 与 `docs/guide/评测说明.md` / `docs/spec/项目规格.md`（文档同步属实现轮任务）。
12. **一个权威执行体**（§4.2.1 不变量 1 / 2 / 3 / 5）：自动路径与审批路径调用**同一个**退款执行
    体；六项权威校验（订单加锁、并发冲突判定、资格复核、幂等占位、渠道调用、验证对账）**强度
    相同**；两份绑定校验**都保留**；审批路径的既有读数**逐条不变**。
    **可判定方式**：同一笔退款在两条路径下的资格不过 / 并发冲突分支产生**同样**的台账状态、
    审计动作与回执文案；且代码里**不存在**只在自动路径生效的执行分支实现。

---

## 9. 与 NEXT_STEPS 的对齐

| NEXT_STEPS 条目 | 本文件对应 |
|---|---|
| B-3 审批没有金额分层（`docs/下一步规划.md` 的 **B-3 审批没有金额分层（中高）** 段） | 第 1 节术语 / 第 2 节档位表契约 / 第 3 节阈值与档位分工 / 第 4 节两条路径 / 第 5 节进摘要 / 第 6 节边界 / 第 8 节验收 |
| B-3 的「记录时更正」：`refund_approval_threshold_cents` 存在但写路径没读 | §3.1（现状证据 + 生效口径） |
| B-3 建议改动 1「让阈值真正生效」 | §3、§4.2 |
| B-3 建议改动 2「配置表（YAML）写 `tiers: [{max_cents, roles, required_approvals}]`」 | §2.1（表形状）、§2.2（默认单层） |
| B-3 建议改动 3「分层结果进 `action_digest`」 | §5.1、§5.2 |
| B-3 建议改动 4「未达阈值的执行同样留痕」 | §4.2 第 8 条、§4.3 |
| B-3 边界「不做多级会签 / 不做按客户等级的差异化策略 / 自动执行不降低资格校验强度」 | §6 第 1、2、3 条 |
| B-1 退款原因与退款类型 | 本文件只与它的摘要键清单（§5.2）与 `RefundType` 消歧（§1.0）交叉引用 |

> **§5.2 是对 B-1 口径的一处修订**，已按 `agents.md:15`「冲突时先停下、说明冲突、改口径文件，
> 再改代码」在 `.qoder/rules/refund-reason-taxonomy.md` 上做了**两处最小定点修订**（§4.1 表格加一行、
> §6 加一句指向本文件），其余内容未动。
>
> §5.4「自动执行不进摘要」与 §5.3「档位变化使旧审批失效」是本文件的**两处判断**，
> 不是 NEXT_STEPS 原文要求：前者沿用 B-1 §4.2 的既有口径，后者是 B-3 验收
> 「分层变化会让旧审批失效（digest 变化）」的落地形式（因为既有绑定校验对存量载荷重算，
> 不会自己发现档位变化）。
>
> §5.3 的**作用对象**已在独立审查（2026-10-27）后按代码现实订正为「已进入执行的那张审批行
> （正常状态 `ApprovalStatus.APPROVED`）」，与 §8 验收第 5 条同一套措辞；本段的
> 「对存量载荷重算、不会自己发现档位变化」不受该订正影响（它说的是绑定校验的重算对象，
> 不是这条重校验的作用对象）。
