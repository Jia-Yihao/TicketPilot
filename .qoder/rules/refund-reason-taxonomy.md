# 退款原因 / 退款类型与金额构成：项目级口径

本文件是 TicketPilot 的项目级声明，规定「退款原因（refund reason）」、「退款类型
（refund type）」与「退款金额构成」的唯一口径，供后续实现任务（给退款加 `reason` /
`refund_type` 并计入 `action_digest`，即 `docs/下一步规划.md` 的 B-1、B-8）逐字引用。

- 流程仍遵循用户级协作规则（organizer -> executor -> reviewer）；业务事实以
  `docs/spec/项目规格.md` 为准，本文件与其冲突时先改本文件并说明理由。
- 本文件**只声明口径，不改动任何产品代码、测试、迁移、配置、文档**。
- 「当前事实」均以文件:行给出证据；标注「当前代码尚无对应实现」的项是**待实现口径**。

---

## 1. 术语表

同一概念在本项目只有一种叫法。下表右侧的英文名是**实现必须逐字使用的标识符**
（枚举类名、字段名、JSON 键名）。

| 中文术语 | 唯一英文标识符 | 唯一定义 |
|---|---|---|
| 退款原因 | `RefundReason` / `reason` | 客户或坐席发起退款时对**退款缘由**的分类选择，取值必须是 1.1 枚举成员之一。是审批人判断的关键输入。 |
| 退款类型 | `RefundType` / `refund_type` | 本次退款在**业务动作维度**上的分类（全额/部分/运费/价保），取值必须是 1.2 枚举成员之一。`SHIPPING` / `PRICE_PROTECTION` 只作分类标签，不代表任何独立金额项、拆分、分摊或「应退/实退」多口径，实退金额一律走第 3 节可退余额口径（详见 §1.2、第 3 节）。 |
| 原因补充说明 | `reason_note` | 当 `reason=OTHER` 时坐席可填的一段自由文本，用于台账与审批详情，**不进入 `refund_payload` / `action_digest`，不进公开回执**。 |
| 退款摘要 | `action_digest` | 对 `refund_payload` 做规范化 JSON（键按字典序）后取 SHA-256 的十六进制串（`app/services/approvals.py:27-31`）。审批人批准的内容与执行内容必须落在同一摘要上，摘要漂移即失效。 |
| 退款载荷 | `refund_payload` | 构造退款审批与执行台账时被摘要覆盖的字段集合（`app/services/approvals.py::refund_payload`，当前 `:34-66`）。 |
| 未提供 | （显示标签，非枚举成员） | 历史退款记录或字段为 NULL 时在审批详情/回执展示的中文标签「未提供」。**它不是一个枚举值**，不得写入数据库或参与摘要。 |

### 1.0 必须消歧的同名概念（关键口径）

裸名 `reason` 在本仓库已被**多个不同概念**占用。本口径新增的「退款原因」只能由
`RefundReason` / `refunds.reason` / `refund_payload["reason"]` 表达，**不得与下列任一混用**：

| 已有标识符 | 含义 | 证据 | 与「退款原因」的关系 |
|---|---|---|---|
| `approvals.reason` / `decide(reason=...)` | 审批**决定/驳回/失效**原因 | `app/models/approval.py:52`；`app/services/approvals.py::decide`（形参 `reason`，当前 `:128`）、当前 `:142`（失效原因）、`:150`（落库）、`:158`（审计 detail） | 无关，不同概念 |
| `handoff_events.reason`（`HandoffReason`） | **转人工**原因 | `alembic/versions/e2f6a4d81c57_handoff_events.py:30`；`app/models/enums.py::HandoffReason`（当前 `:174-185`） | 无关 |
| `abandon_events.reason`（`AbandonReason`） | **放弃**原因 | `alembic/versions/e2f6a4d81c57_handoff_events.py:91`；`app/models/enums.py::AbandonReason`（当前 `:204` 起） | 无关 |
| `EligibilityResult.reasons` | 资格**不符诊断码**（如 ORDER_CANCELLED） | `app/services/refund_policy.py:22,58-91`；`app/tools/builtin.py:385,392`（`check_refund_eligibility` 输出 schema 的 `reasons`） | 只读诊断，非退款原因 |
| `request_refund` 结果 `reason_code` | 退款**执行结果**原因码 | `app/tools/builtin.py:439`（`request_refund` 输出 schema） | 执行态，非申请原因 |

**消歧约定（可判定口径）**：

- 「退款原因」的**裸名 `reason`** 在本仓库只能出现在唯一三个合法位置：`refunds.reason` 列、`refund_payload` 的 `"reason"` 键、`RefundReason` 枚举类型名。除这三处外，任何地方都不得用**裸名 `reason`** 承载「退款原因」语义；承接退款原因的变量/形参须用带上下文的名字（如 `refund_reason`，见下条实现约束与 §5.3）。
- **合规判定（正向）**：凡以 `RefundReason` / `RefundType` **类型**承载退款原因/退款类型语义者，或落在上述三个合法位置**之内**者，一律合规。判定依据是**值域来源与类型**（这个 `reason` 的取值是不是来自 `RefundReason` 枚举、它的声明类型是不是 `RefundReason`），**不是**「这个名字有没有在下表登记过」。
- **违规判定**：两种情形——(a) 把 `RefundReason` 的值域赋给三个合法位置**之外**的任何 `reason` 标识符；(b) 在三个合法位置**之外新建**一个裸名 `reason` 且其值来自 `RefundReason`。跨位置混用即为违规，且在 diff 中显式可见、可判定——例如把 `RefundReason` 的值传给 `decide()` 的 `reason` 参数，或让下表任一概念复用 `RefundReason` 值域。
- **本表不充当判定器的穷举依据**：下表只是一份「已知同名概念清单」，用于提醒实现者不要把退款原因混进这些既有字段。本仓库裸名 `reason` 远不止下表若干处——**历史读数（本口径撰写时、B-1/B-3 实现前实测）** `grep -rw reason app/ alembic/versions/ --include=*.py` 共 **161 处**（app/ 153 + alembic/versions/ 8）；**本轮复测（2026-10-27 时点读数，实现后）** 同命令共 **204 行**（app/ 193 + alembic/versions/ 11）。除已登记 5 项外还涉及执行取消原因（`app/services/executions.py:45`）、SQL 路由/拒答原因（`app/sql/router.py:48`）、dense 门控原因（`app/retrieval/hybrid.py:55`）以及规则治理、事件抑制、澄清、错误码等多类原因字段。这些既有裸名 `reason` 只要其取值**不来自** `RefundReason` 值域，按上面的正向判定**天然合规**，无需逐一登记，也不会被误判。
- 仍**禁止**把「退款原因」塞进本表任一字段，也禁止本表任一概念复用 `RefundReason` 枚举值域。
- **实现约束（与 §5.3 定死的参数名一致，不得冲突）**：新增的、承接退款原因/退款类型的形参一律用带上下文的名字 `refund_reason: RefundReason`、`refund_type: RefundType`；在边界处（构造 payload / 落库）再映射为口径裸名——即 `refund_payload` 写入键 `"reason"`（值取 `refund_reason.value`）与键 `"refund_type"`（值取 `refund_type.value`），落库列名用 `refunds.reason` / `refunds.refund_type`。「带上下文的名字在边界处映射为口径裸名」是本口径**允许且推荐**的写法；裸名 `reason` 只出现在三个合法位置之内，不构成语义重载。注：`refund_type` 形参名与其 payload 键名同名，属正常（它不在「裸名 `reason` 重载」问题上），实现者**不得**因此改名。

### 1.1 RefundReason 枚举成员（口径已落地）

**历史事实（B-1 实现前）**：口径撰写时 `app/models/enums.py` **尚无** `RefundReason`；
以下为唯一口径，实现须逐字对齐。**实现后**：已落地为
`app/models/enums.py::RefundReason`（当前 `:88-100`），成员与本表逐字一致。
成员名与取值均为大写蛇形（UPPER_SNAKE），值字符串与成员名相同，沿用现有 `StrEnum`
风格（`app/models/enums.py:81-85` 的 `RefundStatus`）。

| 成员 / 值 | 中文唯一名 |
|---|---|
| `QUALITY` | 质量问题 |
| `DAMAGED` | 破损 |
| `WRONG_ITEM` | 发错商品 |
| `NOT_AS_DESCRIBED` | 与描述不符 |
| `NO_LONGER_NEEDED` | 不再需要 |
| `OTHER` | 其他 |

- 来源：`docs/下一步规划.md` B-1 段「建议改动」第 1 条。
- `OTHER`：坐席可补 `reason_note`；客户说不清时允许选 `OTHER`，不得做成必填追问
  （NEXT_STEPS B-1 段「边界」第 2 条）。
- 采集侧以 §5.6 为准（交叉引用，不改变上一条含义）：`OTHER` 只由客户**显式**表达「其他 / 说不清」时选，规则未命中**不得**自动兜底 `OTHER`（§5.6.2）；缺 `reason` 不放行，按 §5.6.3 追问。此处「不得做成必填追问」在采集侧读作「不得造成死锁」——追问话术必须显式给出 `OTHER` 出口（§5.6.3），客户一选即放行，因此「追问」与「允许 OTHER」同时成立，不互斥。「缺即追问」相对 `docs/下一步规划.md` B-1 段「边界」第 2 条原文属**有意的收紧**，属用户裁决，登记见 §7。

### 1.2 RefundType 枚举成员（口径已落地）

**历史事实（B-1 实现前）**：口径撰写时 `app/models/enums.py` **尚无** `RefundType`；
**实现后**：已落地为 `app/models/enums.py::RefundType`（当前 `:103-113`），成员与本表逐字一致。

| 成员 / 值 | 中文唯一名 |
|---|---|
| `FULL` | 全额退款 |
| `PARTIAL` | 部分退款 |
| `SHIPPING` | 运费退款 |
| `PRICE_PROTECTION` | 价保退款 |

- 来源：`docs/下一步规划.md` B-1 段「建议改动」第 1 条。
- 口径要点：`SHIPPING` / `PRICE_PROTECTION` 只作分类标签，不代表任何独立金额项、拆分、分摊或「应退/实退」多口径，实退金额一律走第 3 节可退余额口径；**禁止**为其引入任何独立金额项、拆分、分摊或多口径（措辞与 §1 术语表、§3 一致）。
- 补充说明：此为**本口径的补充判断**——NEXT_STEPS B-1 段「建议改动」第 1 条只列出这两个枚举值、B-8 通篇不提 `refund_type`，二者关系属文档空隙，非原文表述。

---

## 2. 默认值与旧数据回读口径

适用对象：新增到 `refunds` 表的 `reason` / `refund_type` / `reason_note` 列，以及新进入
`refund_payload` 的两个键。**历史事实（B-1 实现前）**：口径撰写时 `refunds` 表无这三列
（旧 `app/models/commerce.py:54-74`，待实现）；**实现后**：`Refund` 类当前在
`app/models/commerce.py:60-90`，三列 `reason` / `refund_type` / `reason_note` 已落在
`:74-82`（`nullable=True`，与 §5.2 一致）。

| 项 | 口径 | 理由 |
|---|---|---|
| 新列可空性 | `reason` / `refund_type` / `reason_note` 在数据库层 `nullable=True`，且**不设服务端默认枚举值**。 | 历史行不能被静默贴上一个可能错误的缘由/类型。 |
| 历史退款读出显示 | `reason`/`refund_type` 为 NULL 时，审批详情与回执显示中文标签「未提供」；**不映射成 OTHER / FULL**。 | 与 1 节「未提供不是枚举值」一致；避免污染分布统计（B-4）。 |
| 新记录约束 | 实现落地后，新创建的退款申请**必须**为非空枚举值 `reason` 与 `refund_type`（应用层校验）。**缺 `reason` 即 422**；**缺 `refund_type` 不拒单**，由服务端按订单当前可退余额口径**派生**后写入（§5.6.6），因此记录上该列**不留空**。`reason_note` 仅 `reason=OTHER` 时可选；**非 `OTHER` 时若提交了 `reason_note`，应用层校验拒绝（422），不得静默忽略**（本口径补充：静默忽略会让坐席以为说明已留存、而审批侧实际看不到，属数据一致性风险）。 | 保证摘要对两字段敏感（第 4 节），且原因分布可用。 |
| refund_payload | 新建审批的 payload **必须**含 `reason`、`refund_type` 两键，值为枚举 `.value` 字符串。 | 审批快照完整性。 |
| 迁移与升降 | 一条新迁移在 SQLite 与 PostgreSQL 上均可 up/down；只加可空列，不回填历史数据。 | up/down 双向可执行、现有退款用例不退化属 NEXT_STEPS B-1 段「验收」第 2 条原文；「只加可空列、不回填历史数据」为**本口径补充**。 |
| 在途审批安全 | 部署前已存在的 PENDING / APPROVED 审批**不 retro-fit**：`verify_binding` 是对**已存储的 `action_payload`** 重算摘要比对（`app/services/approvals.py::verify_binding`，当前 `:186-194`），旧 payload 不含新键、其存量 `action_digest` 也据此计算，因此加键**不会**使旧审批失效或需重算。 | 摘要覆盖的是快照内容，不因 schema 演进而回溯。 |
| action_payload 存储 | 禁止对历史 `approvals.action_payload` 做回填或改写；被摘要覆盖的存储 JSON 必须与生成 `action_digest` 时逐字一致。 | 任何改写都会破坏 `verify_binding`。 |

---

## 3. 金额构成口径

**当前事实（可核对）**：退款金额是**订单可退余额口径**的一笔整数分，**不区分运费、优惠券分摊、税费**。

- `refunds` 只有一个 `amount_cents` 整数列（`app/models/commerce.py:70`）。
- `orders` 只有 `total_cents` / `refundable_cents`（`app/models/commerce.py:29-30`），无运费/优惠券/税费列。
- 金额取值：`amount = requested_amount_cents if not None else order.refundable_cents`；
  仅校验 `>0`、`<= refundable_cents`、`<= max_amount_cents`（`app/services/refund_policy.py:81-91`）。
- 上限来自配置：`refund_max_amount_cents` 默认 `5_000_000`（`app/core/config.py:50`；`app/core/policy.py:95-96`）。
- 币种单一 `currency`，默认 `CNY`（`app/models/commerce.py:31`）。
- 审批摘要只覆盖那一个 `amount_cents`（`app/services/approvals.py::refund_payload`，当前 `:34-66`）。

**当前明确不具备、不得假装具备的能力**：无运费拆分、无优惠券分摊回退、无税费退回、无「实退/应退」多口径。`refund_type=SHIPPING/PRICE_PROTECTION` 只作分类标签，不代表任何独立金额项、拆分、分摊或「应退/实退」多口径，实退金额一律走本节可退余额口径；**禁止**为其引入任何独立金额项、拆分、分摊或多口径（措辞与 §1 术语表、§1.2 一致）。

**未来若要区分（本轮不做，仅登记需动的口径）**：
1. 先引入 `amount_breakdown` 结构，字段建议：`shipping_cents`、`coupon_cents`、`tax_cents`、`refunded_cents`（整数分）。
2. `orders` 或 `refunds` 需要新增对应可退项来源列；资格计算 `RefundPolicy.check` 需产出各项而非单一 `amount_cents`。
3. 需**先声明**哪些 breakdown 字段进入 `refund_payload` / `action_digest`（当前只有 `amount_cents` 进；扩容前必须回本文件补口径）。
4. 迁移同时覆盖 SQLite 与 PG；`amount_cents` 语义变化不得静默改变现有退款用例结果。
以上任一条未落地前，实现**不得**假设任何分摊口径。来源：NEXT_STEPS B-8 段。

---

## 4. 进入 refund_payload / action_digest 的字段范围

`action_digest = canonical_digest(refund_payload)`，对键按字典序序列化后取 SHA-256
（`app/services/approvals.py::canonical_digest`，当前 `:27-31`；写入处
`create_refund_approval` 的 `action_digest=canonical_digest(payload)`，当前 `:102`）。字段归属如下。

### 4.1 参与摘要（必须作为 refund_payload 键）

| 键 | 现状 | 说明 |
|---|---|---|
| `action` | 已有，常量 "REFUND" | 动作类型。 |
| `order_id` | 已有 | 目标订单，租户边界内。 |
| `amount_cents` | 已有 | 实退金额（可退余额口径，第 3 节）。 |
| `currency` | 已有 | 币种。 |
| `resource_version` | 已有 | 订单乐观锁版本，绑定执行时资源状态。 |
| `policy_version` | 已有 | 政策版本，跨版本审批不可混用。 |
| `reason` | 已落地（B-1；`refund_payload` 当前 `:63` 写入键 `"reason"`） | 退款原因枚举值；加入摘要以让「批准的就是这件事」。 |
| `refund_type` | 已落地（B-1；`refund_payload` 当前 `:64` 写入键 `"refund_type"`） | 退款类型枚举值。 |
| `approval_tier` | 已落地（B-3 修订；`refund_payload` 当前 `:65` 写入键 `"approval_tier"`） | 审批档位快照（`tier_id` / `max_cents` / `roles` / `required_approvals`）。见 `.qoder/rules/approval-tiering.md` §5.2：该文件按 NEXT_STEPS B-3「分层结果进 `action_digest`」把本表由八项修订为九项（键的形状见其 §5.1）。 |

- 加入 `reason`/`refund_type` 后，同一订单两条不同 `reason`（或不同 `refund_type`）的退款会产生**不同摘要**，在台账可区分（满足 NEXT_STEPS B-1 段「验收」第 1 条）。
- 因序列化按键排序，新增键的顺序无关；但**增删任一键都会改变摘要**，故新旧审批互不可替换（第 2 节在途审批安全）。

### 4.2 不参与摘要（及原因）

| 字段 | 为何不进摘要 |
|---|---|
| `reason_note` | 自由文本，逐次措辞不稳定，纳入会造成审批快照漂移；且 NEXT_STEPS B-1 段「边界」第 1 条明确不进公开回执。 |
| `status`（RefundStatus）、`provider`、`external_refund_id`、`idempotency_key`、`created_at`、`updated_at` | 均为**执行态**，在审批之后才产生或变化（`app/models/commerce.py:71-89`），不属于「审批人批准的内容」快照。 |
| `product_id` | 本口径不引入；`refunds` 现无此列（`Refund` 类当前 `app/models/commerce.py:60-90`，其中仍无 `product_id`，事实不变），归属 B-2 / P3 表结构设计。 |
| `max_refundable_cents`、`requires_approval`（资格诊断输出） | 资格判定输出，非审批内容；`requires_approval` 的生效与阈值判定属 B-3，**但它本身仍不进摘要**（进摘要的是档位快照 `approval_tier`，见第 4.1 节）。 |

---

## 5. 与后续实现的接口约定（逐字引用）

以下命名、类型、约束在本文件**定死**；实现任务不得改名、不得扩大范围。

### 5.1 枚举（新增，落在 app/models/enums.py）

```python
class RefundReason(StrEnum):
    QUALITY = "QUALITY"
    DAMAGED = "DAMAGED"
    WRONG_ITEM = "WRONG_ITEM"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    OTHER = "OTHER"

class RefundType(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    SHIPPING = "SHIPPING"
    PRICE_PROTECTION = "PRICE_PROTECTION"
```
- 写法：类名大驼峰；成员名 = 值字符串，UPPER_SNAKE；与现有 `StrEnum` 风格一致（`app/models/enums.py:81-85`）。
- 模型层枚举列使用 `SAEnum(..., native_enum=False, length=32, validate_strings=True)`，与 `RefundStatus` 列同构（`app/models/commerce.py:71-73`）。

### 5.2 refunds 表新增列（app/models/commerce.py::Refund）

| 列 | 类型 | 约束 |
|---|---|---|
| `reason` | `SAEnum(RefundReason, native_enum=False, length=32, validate_strings=True)` | `nullable=True`（历史行 NULL；新记录应用层必填） |
| `refund_type` | `SAEnum(RefundType, native_enum=False, length=32, validate_strings=True)` | `nullable=True`（同上） |
| `reason_note` | `String(500)` | `nullable=True`；**不进 payload/摘要/公开回执** |

- **实现后**：三列已按上表落地在 `app/models/commerce.py:74-82`（与 §2 「实现后」一致）。

### 5.3 refund_payload 签名与键（app/services/approvals.py::refund_payload，当前 `:34-66`）

- `refund_payload` 新增两个关键字参数，参数名在此**定死**：`refund_reason: RefundReason`、`refund_type: RefundType`；payload 相应新增键 `"reason": refund_reason.value`、`"refund_type": refund_type.value`（键名仍是口径裸名 `reason` / `refund_type`，与 §1.0「三处合法位置」一致，不得改）。
- **历史事实（B-1/B-3 实现前）**：`create_refund_approval` 的 keyword-only 参数为 `task_id`/`order`/`amount_cents`/`requested_by`/`policy_version`（旧 `app/services/approvals.py:52-59`），尚未含退款原因/类型参数，属当时的待实现口径。**实现后**：`create_refund_approval` 当前在 `app/services/approvals.py::create_refund_approval`（当前 `:76`），签名（当前 `:79-86`）已含 `refund_reason: RefundReason`、`refund_type: RefundType`，并另含 B-3 口径 §5.1 追加的 `approval_tier: dict`；参数名与 `refund_payload`（形参当前 `:39-41`）逐字一致，两值透传给 `refund_payload`。
- 参数命名与 §1.0 自洽：承接退款原因的**形参**用带上下文的 `refund_reason`（不叫裸名 `reason`），仅在 `refund_payload` 的键与落库列名处映射为口径裸名 `reason`；`refund_type` 形参与其 payload 键同名属正常，不属「裸名 `reason` 重载」问题。
- 调用侧须把两值透传下来。两处调用点以「函数名 + 调用语句」为锚（口径撰写时 `runtime.py` / `approvals.py` 正被在途改动修改，纯行号会漂移）：`app/services/approvals.py` 中 `create_refund_approval` 内的 `payload = refund_payload(...)` 调用点（当前 `:88`），以及 `app/services/runtime.py` 中 `_handle_refund`（当前 `:629`）内的 `payload = refund_payload(...)` 调用点（当前 `:725`）。注意：`runtime.py` 中**不存在** `_request_refund`，同名者仅为 `tests/test_refunds.py`、`tests/test_outbox.py` 里的测试辅助函数，不得作为实现锚。

### 5.4 工具入参（app/tools/builtin.py）

- `check_refund_eligibility` 与后续发起退款的入口需能携带退款原因与退款类型，**入参键名一律用带上下文的 `refund_reason`、`refund_type`**，值为枚举 `.value` 字符串，`additionalProperties` 保持关闭。入参键**不得**用裸名 `reason`：那会在 §1.0 三个合法位置之外新建一个承载 `RefundReason` 值域的裸名（§1.0「违规判定 (b)」），详见 §5.6.4；映射为口径裸名只发生在 `refund_payload` 与落库边界。**历史事实（B-1 实现前）**：该 ToolSpec 现参数仅 `order_id`、`amount_cents`（`additionalProperties: False` 旧 `:331`），**无这两个入参**，属待实现。**实现后**：`app/tools/builtin.py` 的 `check_refund_eligibility` ToolSpec（当前 `:349`）`properties` 已含 `refund_reason`（当前 `:362`）/ `refund_type`（当前 `:363`），`additionalProperties: False` 当前 `:366`。
- `request_refund`（执行已审批退款，`app/tools/builtin.py` 当前 `:403-452`）不新增客户可见入参；原因/类型来自审批快照，不在此重复采集。
- 槽位抽取不新增模型权限：`refund_reason` / `refund_type` 由关键词规则映射或客户 / 坐席显式提供，**模型不抽取、不采纳**（维持 ADR-020 边界，`docs/下一步规划.md` B-1 段「建议改动」第 4 条）。此句原先只是抽象话，不足以实现；**采集口径的完整可判定规定见 §5.6**（规则表落在哪个 YAML、键长什么样、未命中怎么办、两处入口的缺值行为与 `refund_type` 的缺省派生、链路命名与模型侧边界）。

### 5.5 展示口径

- 审批详情、回执须展示「退款原因」「退款类型」的中文唯一名（1.1/1.2）；NULL 显示「未提供」（第 2 节）。
- OTHER 的 `reason_note` 只在坐席/审批详情侧显示，不进公开回执。

### 5.6 采集口径：`refund_reason` / `refund_type` 的值从哪里来

本节把 §5.4 原来那句「模型不采纳」展开成可判定口径。采集方式（关键词规则映射、缺即不放行）是**本口径补充 + 用户裁决**：`docs/下一步规划.md` B-1 段「建议改动」第 4 条只写了「原因 / 类型由规则或客户显式选择提供，**模型不采纳**」这一句抽象边界，**未规定**规则表落在哪个文件、未命中怎么办、缺值时两个入口各自怎么办。以下 5.6.1-5.6.7 是新增口径，不是 NEXT_STEPS 原文要求（登记见 §7）。

#### 5.6.1 规则表位置：独立 YAML `app/agent/refund_reason_rules.yaml`

**定死**：关键词到枚举的映射表落在**新增文件** `app/agent/refund_reason_rules.yaml`，由新增 loader `app/agent/refund_reason_rules.py` 读取；**不并入 `app/agent/intents.yaml`**。理由（均可核对）：

1. `intents.yaml` 的顶层键是**封闭集**：`app/agent/intent_catalog.py` 的 `_TOP_FIELDS = frozenset({"version", "slots", "intents"})`（当前 `:94`），加载时 `_reject_unknown` 对未知顶层键直接 `ValueError`（当前 `:252-257`，调用点 `:264`）。并入 intents.yaml 就必须同时放宽这个守卫，把一个键名打错从「报错」变成「静默接受」，属降低现有防护，超出本口径范围。
2. 职责不同：`intents.yaml` 头注释声明它是「一行一个能力」的意图目录（意图、服务它的动作、必填槽位、风险等级、无依据时的行为、兜底）。关键词到枚举是**某个槽位的取值来源**，不是意图行；塞进去会让「意图目录」同时承载两类事实。
3. 已有同形先例：转人工触发表就是「独立 YAML + 独立 loader」——`app/agent/escalation.yaml` 对应 `app/agent/escalation.py`（`_CATALOG_PATH = Path(__file__).with_name("escalation.yaml")`，当前 `:31`）。新表沿用这个形状，不发明第三种。
4. 与项目不变量一致：`openspec/project.md` 不变量 5「意图目录（`intents.yaml`）与转人工条件（`escalation.yaml`）是数据，不是提示词」。规则表同为数据，代码只读取：不在 Python 里硬编码关键词，也不在提示词里让模型现编。

**单一事实来源的划法**（两份文件各自封闭，不得交叉重复声明）：

- 槽位的**存在性与追问行为**，落在 `intents.yaml`：新增 `slots.refund_reason`，可用键见 `intent_catalog.py` 的 `_SLOT_FIELDS`（`type` / `pattern` / `prompt` / `invalid_prompt` / `missing_reason` / `examples` / `sensitive`，当前 `:83-93`）。**不需要新增合法键，因此不需要改校验器**。同时把 `refund_reason` 追加进 `REFUND_REQUEST` 的 `required_slots` 与 `clarify_prompts`。**历史事实（B-1 实现前）**：`required_slots` 旧 `intents.yaml:76-78`，当时仅 `order_id` / `amount_cents`；`clarify_prompts` 旧 `:84-86`。**实现后**：`slots.refund_reason` 已声明（当前 `intents.yaml:49-55`），`REFUND_REQUEST`（当前 `:80`）的 `required_slots` 当前 `:84-87`（`order_id` / `amount_cents` / `refund_reason`），`clarify_prompts` 当前 `:93-96`。加载器已强制 `required_slots` 与 `clarify_prompts` 引用的槽位必须已声明（当前 `:301-306`，否则 `ValueError`），漏声明在加载期即失败，不会静默退化成「不追问」。追问**话术**因此有两处声明（槽位级与意图级），读取优先级与分叉约束见 §5.6.3，不得以为话术只有一处来源。
- 槽位的**取值映射规则**，落在 `refund_reason_rules.yaml`。**不得**在 `intents.yaml` 里再抄一份关键词表，也不得在 Python 里硬编码关键词。

**键结构（定死）**：

```yaml
version: "2026-10-26"
slot: refund_reason
enum: RefundReason
other_cues: ["其他", "别的", "说不清", "不想分类"]
rules:
  - refund_reason: DAMAGED
    cues: ["摔坏", "破损", "碎了", "压坏", "裂"]
  - refund_reason: QUALITY
    cues: ["质量", "故障", "用不了", "开不了机"]
  - refund_reason: WRONG_ITEM
    cues: ["发错", "错货", "不是我要的"]
  - refund_reason: NOT_AS_DESCRIBED
    cues: ["和描述不符", "与描述不符", "图片不一样"]
  - refund_reason: NO_LONGER_NEEDED
    cues: ["不想要了", "不需要了", "买多了", "多拍"]
```

`other_cues` 的语义与 §5.6.1.1 逐字绑定（**它不是 `rules` 的一行**，是独立于规则表的
「显式其他」识别表）。

加载期必须校验以下各条，违反即报错，**不得静默跳过或降级**：

- 顶层键只允许 `version` / `slot` / `enum` / `other_cues` / `rules`；规则项键只允许
  `refund_reason` / `cues`（沿用 `intent_catalog.py` 里 `_reject_unknown` 的「未知键即打错字」
  形状）。
- `slot` 必须等于 `"refund_reason"`、`enum` 必须等于 `"RefundReason"`，否则报错（防止这张表被复制粘贴到别的槽位上）。
- 每条 `refund_reason` 的取值必须是 §5.1 的 `RefundReason` 成员名，逐字（大写蛇形）。
- **`OTHER` 不得作为任何 `rules` 条目的目标值**，loader 见到即报错。理由见 §5.6.2。
  `OTHER` 的唯一来源是 `other_cues`（§5.6.1.1）与调用方显式提交，**不是规则表的一行**。
- `cues` 为小写子串列表；匹配前对入站文本做小写化，匹配语义与 `intents.yaml` / `escalation.yaml` 的 `cues` 一致（子串命中，不分词、不加权重）。上面列出的关键词是**口径示例、不是穷举**：实现可增补 cue，但**不得**增删枚举成员、不得改变目标成员名、不得引入「正则 / 同义词模型」这类第二套匹配。
- **表序即优先级**：自上而下，首条命中即停并取该条值；多条同时命中不叠加、不打分、不取最长匹配（与 `intents.yaml` 头注 “the first matching row decides” 同形）。
- `rules` 为空、或某条 `cues` 为空，加载期报错。一张永不命中的表会被静默读成「全部缺值」，属最危险的失败模式。
- `other_cues` 为空同样加载期报错（理由同上：无出口的追问是死锁）；`other_cues` 内不得有重复项。
- `refund_type` **不在本表采集**，其来源见 §5.6.5（显式提交）与 §5.6.6（缺值时的服务端派生）。

#### 5.6.1.1 显式「其他」的识别：独立于规则表的一条通路（**G1 落点**）

**定死**：客户**显式**表达「其他 / 说不清 / 不想分类」时填 `RefundReason.OTHER`；该识别
**不经** `rules` 表（§5.6.1 校验已禁止 `OTHER` 作规则目标值），而是走**独立的 `other_cues`
表**，与 `rules` 同文件、同 loader、同加载期校验。

- **数据形状**：`other_cues` 是**小写子串列表**（非空、不得重复），语义与 `rules` 的 `cues`
  相同（子串命中、不分词、不加权）。上面的词表是**口径示例、不是穷举**；实现可增补词，
  但**不得**把它与 `rules` 的 `cues` 混在同一张列表里（那会让「显式其他」与「具体分类」失去
  优先级可判定性）。
- **匹配顺序（定死）**：`other_cues` **优先于** `rules`。命中 `other_cues` → 填 `OTHER`，
  **不再**看 `rules`。
  **理由**：§5.6.2 要防的是「**系统**没识别出来 ⇒ 兜底 OTHER」，而**客户自己说的**「其他」是
  最强的分类信号；让一条顺带命中的关键词（客户说「说不清具体，但好像坏了」）把客户明说的
  「其他」改写成系统的猜测，正是 §5.6.2 要避免的那类污染，只是方向相反。
  **代价登记**：客户既说「其他」又说具体线索时，记录为 `OTHER`（`reason_note` 由坐席按需补），
  具体线索**不保留**；这是「显式优先」的直接代价，实现不得用「两个都记」的方式规避（两个都记
  会让 `reason` 键的值不再唯一）。
- **被否决的备选**（登记，防止实现时回头）：
  1. **`rules` 优先、`other_cues` 次之**——否决：客户明说「其他」却因顺带命中关键词被记成具体
     分类，等于把客户的显式选择替换成系统的猜测，与 §5.6.2 的防污染目标同向相悖。
  2. **在 Python 里硬编码「其他」二字判断**——否决：违反「数据不是提示词 / 不在代码里硬编码
     关键词」（§5.6.1 理由 4）；且无法随运营调整词表。
  3. **另起一张 `refund_reason_other.yaml`**——否决：同一槽位的数据拆两张文件、两个 loader，
     增加「两张表各改一半」的不一致面；同文件不同顶层键已足够可判定。
- **调用方显式提交**（任务 API / 坐席）：`context["refund_reason"] = "OTHER"` 是**合法显式值**，
  直接采用，**不得**被 `other_cues` / `rules` 的任何匹配覆盖；取值**必须**是 §5.1 的合法枚举
  值，否则 **422**（与 §2「新记录约束」同一条事实）。
- **对话侧的优先级**：上下文里已带合法 `refund_reason`（上一轮已确认、客户本轮只是补充别的
  信息）时**沿用该值**，**不重新匹配**；本轮文本命中 `other_cues` / `rules` 只在**该键尚不存在**
  时用于填槽。这样「上一轮已答、这一轮补充」的对话不会把已确认的分类改写掉。
- **与「未命中」的区别（不得混淆）**：`other_cues` 未命中、`rules` 未命中 → **该键不出现**
  （§5.6.2）→ 走 §5.6.3 的追问或 422。**「未命中」永远不等于 `OTHER`。**
- **沿用适用域（生命周期）**：「沿用该值、不重新匹配」只在**本回合退款流程仍开着**时成立。此前写的
  「同一笔退款流程内」是一句无法机械判定的话，独立审查（2026-10-27，rev-b1-r3 的 K7）判其属**声明
  缺位**——放弃路径不建单，现文没有任何规则禁止陈旧值跨流程存活。经编排方补裁，**定死**为一条
  可判定不变量：`ConversationSlots.refund_reason` 仅在「本回合规划意图为 `REFUND_REQUEST`」
  （含经 `_resume_pending` 恢复到 `REFUND_REQUEST`）**或**「**本回合写回的** `pending_intent`
  仍为 `REFUND_REQUEST`」（即与 `refund_reason` 同一次构造里由
  `draft.clarification["resumable_intent"]` 算出的那个值，**不是**上一回合存量的
  `carried.pending_intent`）时保留；两者皆不成立时，随**同一次 `ConversationSlots` 构造**清为
  `None`。
  **为何是写回值而非上一回合存量**（三回合真实回合读数，2026-10-27 在同一
  `conversation_id` 上以 HTTP 实测）：turn1「包裹破损了，我要申请退款，订单
  ORD-DEMO-1001」→ 缺金额追问（`clarification.reason=MISSING_AMOUNT`、
  `resumable_intent=REFUND_REQUEST`），槽位 `refund_reason=DAMAGED`、
  `pending_intent=REFUND_REQUEST`；turn2「订单 ORD-DEMO-1001 到哪里了」→
  `intent=LOGISTICS_QUERY`、无追问、无建单；turn3「我要申请退款，订单 ORD-DEMO-1002，
  金额 50 元」（这句话本身不含任何原因词）→ **没有被追问原因**，第二笔退款直接
  `WAITING_APPROVAL` 建单——这就是污染本体。按「上一回合存量」读，turn2 进入时
  `carried.pending_intent` 恰为 turn1 写的 `REFUND_REQUEST`，于是 turn2 判定「流程仍
  开着」、`DAMAGED` 原样保留，turn3 依旧被陈旧值喂饱，这条不变量**不生效**，与本条自己
  要求的「下一笔退款重新采集、重新追问」直接冲突。只有把第二个条件读成**本回合写回值**，
  turn2 才因为它没有 `draft.clarification` 而同时清掉原因。
  **代价仍然只有那一半**：插问非退款回合即清（与下方已登记的代价句「客户在退款流程中途
  插问一句别的（被读成非退款意图）会被清掉原因」同向，本条订正不新增代价）。
  **单一落点**就是那次构造（`app/agent/support_agent.py:620` 的 `ConversationSlots(...)` 回合
  **写回**构造，`app/` 下 `ConversationSlots(` 直接构造里只有它带 `refund_reason=`（实参在 `:634`）；
  另一处 `ConversationSlots()` 是 `app/services/conversations.py:115` 的**新会话默认构造**、
  无沿用值可清，不构成第二落点；`tests/test_refund_taxonomy.py:806` 另有用例以
  `refund_reason=` 直接构造槽位，属测试形状断言、不是运行期落点候选；`ConversationSlots.from_dict`（`app/services/conversations.py:59`，
  其 `refund_reason=` 实参在 `:67`）是**从存量 JSON 复原**的读取路径，不是本回合的写回落点），
  不新增事件、不新增配置项、不新增转人工分支。规则匹配与写回仍只发生在
  规划意图为 `REFUND_REQUEST` 的回合；退款任务仍**成功建单的回合即清除**会话槽位的 `refund_reason`，
  下一笔退款重新采集、重新追问（与 §1 术语表「**发起退款时**……的分类选择」一致：跨流程沿用等于
  替客户决定第二笔退款的原因，属 §5.6.2 要防的数据污染）。
  **缺陷事实（本条为何必须补，均已逐行核实）**：客户在退款流程中途转话题时，
  `pending_intent` 因本轮没有 `draft.clarification` 被写成 `None`，而 `refund_reason` 因为
  `draft.task` 为 `None` 被**保留**，于是「流程标记已消失、原因却还在」；下一句不带原因的新退款
  请求会在 `classify()` **之前**被注入这个陈旧值，槽位不再追问——与本条自己要求的「下一笔退款
  重新采集、重新追问」直接冲突。
  **被否决的备选**（登记，防止实现或后续审查时回头）：
  1. **(A) 只在转人工时清除**——否决：漏掉不转人工的转话题，即本缺陷的主形态。
  2. **(C) 读 abandon events**——否决：既撞既有边界（`app/agent/support_agent.py:232-233` 的
     `handle()` docstring 明写 handoff bundle 与 abandon events 是「never a decision branch」；
     该句不在 `_detect_abandon` 自身 docstring（`:1235`）里，但 `_detect_abandon` 正是在
     `handle()` 内被调用（调用点 `:480`），所以撞的正是这条边界），又只覆盖闲置超时、覆盖不了转话题。
  3. **(D) 新增 abandon action**——否决：引入新机制，超出 B-1 本轮边界。
  4. **(F) 只在 `_resume_pending` 返回 `resumed` 为真的回合沿用**——否决：会把「建这个 carry 的
     初衷（避免同一流程内重复追问）」当场废掉：客户重述退款、`plan.intent` 已是 `REFUND_REQUEST`
     时 `_resume_pending` 返回假。
  5. **(G) 存活条件读「上一回合留下的 `pending_intent`」（即 `carried.pending_intent`）**——
     否决：它恰好在泄漏形态里为真（转话题那一回合的进入值就是上一回合留下的
     `REFUND_REQUEST`），按它写等于不变量不生效；可核对证据是同一条三回合序列里 turn2
     保留、turn3 不追问。
     注：以上编号字母不连续（A/C/D/F/G，无 B/E）；编号沿革（B/E 曾对应什么候选、裁撤是否未静默删除候选）在无
     提交锚点的脏工作树下**无法独立复核**，此句属自述；可核的是本清单即为当前完整清单、(A)(C)(D)(F)(G) 五条正文逐字可见。
  **代价登记**：客户在退款流程中途插问一句别的（被读成非退款意图）会被清掉原因、下一句重新追问。
  这是**安全方向**的代价——宁可重问，也不替客户决定第二笔退款的原因（与 §5.6.2 要防的数据污染
  同向）。
  **既有已知取舍保留**：同一回合内客户改口不会被新说法覆盖，本条字面口径不变；其对 B-4 原因分布的
  影响写进 ADR（文档轮登记，本轮不改 ADR）。
  **生命周期归属**：本条补写是 2026-10-26 裁决「建单即清」的**补充**，不推翻它；建单回合清除仍然
  成立，本条只是补上「不建单也要清场」那一半。

#### 5.6.2 命中即填槽；未命中**不填**；不得自动兜底 `OTHER`

- **命中**：值写入 `task.context["refund_reason"]`（键名定死见 §5.6.4），并进入本轮 `RoutePlan.refund_reason`。
- **未命中**：**该键不出现**。不得写 `None`、空串、`"OTHER"` 或任何占位值；不得「先按 OTHER 建单、之后再改」。
- `OTHER` 只在客户**显式表达**「其他 / 说不清 / 不想分类」时才选，它是一次显式选择，不是规则表的兜底出口。识别机制见 §5.6.1.1（`other_cues` 独立表，**优先于** `rules`）。
- 理由（写进口径，供 reviewer 与后续实现者理解边界来源）：自动兜底 `OTHER` 会把「系统没识别出来」与「客户明说是其他」压成同一个值，直接污染 NEXT_STEPS B-4 段（运营看板的售后原因分布）想要的数据；且 `OTHER` 一旦成为默认值，就随 §4.1 的 `reason` 键进 `refund_payload` 与 `action_digest`，审批人批准的是摘要已经钉死的一个分类，无从发现它是兜底值。
- **与 `docs/下一步规划.md` B-1 段「边界」第 2 条的关系（照原文读准，不得当成互斥）**：原文是「不把原因做成必填的追问——客户说不清时允许 `OTHER`，但审批详情要显示「未提供」」。本口径**保留**其中两条硬事实：`OTHER` 始终是一个合法可选出口（因此追问不造成死锁，见 §5.6.3）、值为 NULL 的历史记录在审批详情显示「未提供」（§2）。本口径**收紧**的是「不把原因做成必填的追问」这一句：按用户裁决缺 `reason` 不放行，因此要追问，收紧理由即上一条。实现者若读到该条原文而想「干脆默认 OTHER」，即违反本节；若反过来把追问做成无出口的死锁，同样违反本节。

#### 5.6.3 缺值时的两处入口：一条规则的两个表现，不是两套真相

统一口径一句话：**新建退款申请必须有 `refund_reason`；缺 `reason` 即不放行**（与 §2「新记录约束」是同一条事实，两处入口都不得自行放宽或降级）。`refund_type` 的**记录**同样必填，但它的**输入**可选——缺值时由服务端派生（规则见 §5.6.6）；**缺 `refund_type` 不是「不放行」的理由，也不得触发追问**。

| 入口 | 当前事实（已核对） | 缺 `refund_reason` 的行为 | 缺 `refund_type` 的行为 |
|---|---|---|---|
| 对话 HTTP：`app/api/v1/support.py`，请求体 `app/schemas/support.py::SupportMessageRequest`，现仅 `message` / `conversation_id` / `order_id`，**无结构化原因位** | 有对话轮次 | 走 `intents.yaml` 的 `required_slots` 机制**追问**，本轮不建退款任务 | **不追问**；运行时按 §5.6.6 派生 |
| 任务 API：`app/schemas/tasks.py::TaskCreateRequest`，现含 `context: dict[str, Any]`，即已存在的自由结构化槽位，原样入库并被台账重读（`app/services/runtime.py::create_task` 的 `context` 形参，及其 `canonical_digest({"request": request, "context": context or {}})`） | 无对话轮次 | 直接 **422**（§2 已定「缺 `reason` 即 422」） | **不再 422**；运行时按 §5.6.6 派生 |

- 对话侧**不新增第二套追问逻辑**，落进既有链路即可（均以函数名 / 键名为锚，行号会漂移）。**历史事实（B-1 实现前）**：`app/agent/support_agent.py` 组装 `context` 处（`context["order_id"]` / `context["amount_cents"]` 那一段）尚无 `context["refund_reason"]`；同文件算缺槽处（构造 `present` 集合后调 `catalog.missing_slots(plan.intent.value, present)`）尚未 `present.add("refund_reason")`；运行时 `app/services/runtime.py::_handle_refund` 当时只有连续三道 `if not plan.order_id` / `plan.amount_invalid` / `plan.amount_cents is None` 各调一次 `self._clarify(...)` 的闸门。**实现后**：`support_agent.py` 已在组装 `context` 处按同形补 `context["refund_reason"]`（当前 `:278` / `:284` / `:367`），缺槽处已补 `present.add("refund_reason")`（当前 `:463`）——**只补 `refund_reason`，不补 `refund_type`**（`refund_type` 不进 `REFUND_REQUEST.required_slots`）；`_handle_refund`（当前 `:629`）原三道闸门（当前 `:638` / `:642` / `:646`）后面已按同形**补上第四道**缺退款原因的 `_clarify` 闸门（当前 `:651-662`，判据是 `plan.refund_reason is None` 或槽位解析不可用 `not slots.ok`），且**没有** `plan.refund_type is None` 闸门（缺类型走 §5.6.6 的派生，不是澄清；派生在运行时当前 `:706-708` 完成）。派发侧无需改：`app/agent/actions.py` 的 `_create_task`（`@action("refund.request")` 的 `refund_request` 即走它）已把 `request.context` 透传给 `runtime.create_task`。
- 追问话术**必须**含「其他 / 说不清」出口，例如：「请说明退款原因，例如质量问题、破损、发错商品、与描述不符、不再需要；说不清或不方便分类的话，回答“其他”也可以继续。」——保证 §1.1 的 `OTHER` 出口不因追问而退化。该话术**声明在两处**：`slots.refund_reason.prompt`（槽位级，兜底）与 `REFUND_REQUEST.clarify_prompts.refund_reason`（意图级，覆盖），两处都写在 `intents.yaml` 里；读取优先级由 `prompt_for()`（`app/agent/intent_catalog.py:168-171`）定死——**优先取意图级覆盖、槽位级作兜底**。2026-10-27 实读比对（PyYAML 现算 `intents.yaml` 槽位级 `:51` 与意图级 `:96` 两处取值）：两处**逐字一致**，因此「生效句」与「兜底句」没有差异。**约束**：若将来两处措辞分叉，**以意图级覆盖为准**，且分叉必须在 `docs/spec/项目规格.md` §19.2 类文档登记，不得静默分叉。
- 任务 API 侧**不追问、不降级**：缺 `refund_reason` 时 **422** 是它唯一的缺值表现，由应用层校验产生（§2「新记录约束」）；**不得**在 API 侧把缺失补成 `OTHER`，也不得因为「调用方是内部系统」而放行。缺 `refund_type` 时**不返回 422**，由服务端派生（§5.6.6）；**不得**在 API 侧把缺失补成 `FULL`。
- **`reason_note` 的唯一合法形态（§2「新记录约束」的两入口落地，G2 落点）**：`reason_note` **只在
  `reason=OTHER` 时**可写。**非 `OTHER` 却提交了 `reason_note` ⇒ 拒绝建单**：
  - 任务 API 入口 → **422**（与「缺 `reason` 即 422」同一档的请求级拒绝）。
  - 对话入口 → **不建退款任务**，按缺槽位同款形态处理（客户自然语言里没有这个位，正常不会
    触发；若 `context` 里带了，只可能来自坐席 / 调用方直传）。
  - **校验只写一处**：判定落在退款写路径的**共用入口**（`app/services/runtime.py::_handle_refund`
    建单前，或与之共用的应用层校验函数），两侧只是把同一个拒绝**翻译**成各自的表现
    （API → 422；对话 → 拒绝建单）。**不得**为某个入口单写一份。
  - **不得静默忽略**：静默忽略会让坐席以为说明已留存、而审批侧实际看不到，属数据一致性风险
    （§2 已登记的理由）。**不得**「先存下 `reason_note`、等 `reason` 确定了再决定」。
  - **对话侧不采集 `reason_note`**：它是坐席侧的自由文本（§1 术语表），不是客户槽位，因此
    **不进** `REFUND_REQUEST.required_slots`，也**不**进 `slots` 声明。
- 「两个表现、一条真相」的可核对依据：两侧读**同一个 context 键** `refund_reason`；对话侧命中后写入的 `context` 与调用方直传的 `context` 在运行时不可区分（同一个 `create_task(context=...)` 形参、同一份入库 JSON、同一个摘要输入）。`refund_type` 两侧同理：显式值从**同一个** context 键读，缺值时走**同一条**派生（§5.6.6）。因此实现**不得**为某个入口加特判（例如「仅对话侧 `refund_reason` 必填」「API 侧可缺省」「对话侧另写一份类型派生」）；加特判就是把一条规则写成两套真相。

#### 5.6.4 链路命名：`refund_reason`，在边界处映射为裸名 `reason`（与 §1.0、§5.3 逐字自洽）

`task.context` 里**不得**放一个键叫 `reason`：那是在 §1.0 三个合法位置**之外**新建一个裸名 `reason`，且其值来自 `RefundReason` 值域，正中 §1.0「违规判定 (b)」。因此链路各处一律用带上下文的名字，映射点唯一：

| 位置 | 标识符（定死） | 依据 |
|---|---|---|
| `task.context` 键 | `"refund_reason"` | §1.0 违规判定 (b) |
| `RoutePlan` 字段 | `refund_reason: RefundReason \| None`（`app/agent/routing.py::RoutePlan` 是 frozen dataclass；**历史事实**：口径撰写时现字段仅 `intent` / `order_id` / `amount_cents` / `amount_invalid`；**实现后**：当前 `:158-164` 已含 `refund_reason` / `refund_type` 两字段） | 同上 |
| `Turn` 字段（跨轮载体） | `refund_reason: RefundReason \| None`（`app/agent/turns.py::Turn`；**历史事实**：口径撰写时现字段仅 `conversation_id` / `order_id` / `amount_cents` / `product_id`；**实现后**：当前 `:82-89` 已含 `refund_reason` / `refund_type` 两字段） | 同上 |
| 规则 YAML 键 | 顶层 `slot: refund_reason`、规则项 `refund_reason:` | 同上（YAML 数据键同样不用裸名） |
| ToolSpec 入参键 | `refund_reason`（`app/tools/builtin.py` 的 `check_refund_eligibility` spec；**实现后**：`properties` 已含 `refund_reason` / `refund_type`（当前 `:362-363`），`additionalProperties: False` 保持关闭（当前 `:366`），见 §5.4） | §1.0；见 §5.4 修订 |
| 形参 | `refund_reason: RefundReason` | §5.3 已定死，不得改写 |
| payload 键 | `"reason"` | §1.0 三处合法位置之一 |
| 落库列 | `refunds.reason` | 同上 |

- 映射点唯一：只在构造 `refund_payload` 与落库处把带上下文的名字映射为口径裸名 `"reason"` / `refunds.reason`，即 §5.3「带上下文名字在边界处映射为裸名」与 §1.0 实现约束已经写好的同一条路径。**本节不另立第二套映射规则，也不新增映射点。**
- `refund_type` 各处**同名**：`context["refund_type"]`、`RoutePlan.refund_type`、`Turn.refund_type`、形参 `refund_type`、payload 键 `"refund_type"`、列 `refunds.refund_type`。它不在「裸名 `reason` 重载」这个有问题的名字上，§1.0 末注与 §5.3 已确认形参与键名同名属正常，实现者**不得**因此改名或给它加前缀。

#### 5.6.5 模型侧明确不动（裁决 1 的落地与理由）

- `app/agent/pydantic_agent.py::IntentOutput`（当前 `:47-52`，字段为 `intent` / `order_id` / `amount_cents` / `confidence` / `decision_summary`）**不新增** `refund_reason` / `refund_type`——实现后仍不含这两个字段，事实已核对；提示词不新增任何「抽取退款原因 / 退款类型」的要求。
- 两处采纳位都不得为这两个值开补位：模型结果缓存复用分支（`RouteSupportClassifier` 里 `decision.verifies(...)` 为真时的那段，形状是 `order_id = rule_plan.order_id or decision.model_order_id`、`amount_cents` 仅在 `is None` 时取 `decision.model_amount_cents`）与候选集采纳分支（`candidate_set.allows(answer)` 闸之后那段同样的 `or` / `is None` 形状）。两处均**不得**出现 `rule_plan.refund_reason or ...` 这类补位。
- **为什么金额敢让模型补位、原因不敢**（这段判断是本边界的来源，后续不得反向套用）：`amount_cents` 下游有**两道确定性闸**——资格校验（`app/services/refund_policy.py::RefundPolicy.check` 的 `>0`、`<= refundable_cents`、`<= max_amount_cents`）与人工审批（摘要覆盖，§4.1），抽错的金额会被闸拦下；退款原因是**分类语义**，没有任何确定性闸能判定「客户这句话算质量问题还是破损」。模型一旦补位且被采纳，错值直接进 `refund_payload` 与 `action_digest`，审批人看到的是摘要已钉死的一个分类，无从发现。故采集走关键词规则映射：模型不抽取、不采纳，这是 `docs/下一步规划.md` B-1 段「建议改动」第 4 条「模型不采纳」的具体化。
- 与 `app/agent/routing.py::classify` 的既有读法同形：它已是「文本抽取 + `context.get(...)` 兜外部来源」的形状（`order_id` 与 `amount_cents` 两处）。`refund_reason` 沿用同一形状，取值通路只有两条——关键词规则命中，或客户 / 调用方显式提供；**不存在第三条（模型）通路**。
- `refund_type` 同理**不由关键词推断**：它是业务动作维度（全额 / 部分 / 运费 / 价保）的分类，客户自然语言几乎不区分「全额」与「可退余额正好全额」，误判同样没有确定性闸能发现。显式值**只**来自**坐席 / 调用方显式提交**（任务 API 的 `context["refund_type"]`）；**没有**显式值时由服务端按订单当前可退余额**派生**（§5.6.6），**对话侧不追问 `refund_type`**。执行侧不重复采集（§5.4）：值取自审批快照，`app/services/refunds.py::RefundExecutionService.execute`（当前 `:262`；口径早前写的类名 `RefundExecution` 是实现前的简称预期，实际类名为 `RefundExecutionService`）读 `approval.action_payload`（当前 `:281`；该列是 JSON，天然容新键）。

#### 5.6.6 `refund_type` 的缺省派生：输入可选、记录必填

**定死**：`refund_type` 的**输入**可选，**记录**必填。缺值时由**服务端派生**：既不拒绝、也不追问、更不默认成 `FULL`。

**派生规则（按顺序，先到先得）**：

1. **显式值优先**：上下文里已带合法 `refund_type`（坐席 / 调用方提交）时直接采用该值，**不派生、不覆盖**。
2. **否则派生**：按本次退款金额与**订单当前可退余额**的关系派生——`amount_cents == orders.refundable_cents` → `FULL`；否则 → `PARTIAL`。

**锚点是订单当前可退余额 `orders.refundable_cents`**（`app/models/commerce.py:30`），**不是** `max_refundable_cents`。理由（当前事实，均已核对）：

- `orders.refundable_cents` 是**订单事实**（订单的可退余额列）；`EligibilityResult.max_refundable_cents` 是**资格判定的输出**（`app/services/refund_policy.py:24`，写入处 `app/services/refund_policy.py:97`）。
- 二者**当前取值相同**（`app/services/refund_policy.py:97` 取的就是 `order.refundable_cents`），但**同一取值不等于同一语义**：前者是事实，后者是判定结论（会随策略与订单状态变化）。派生必须锚在**事实**上，否则「退款类型」会随策略配置漂移，而它描述的是「这笔退款与订单可退余额的关系」。
- **口径更正登记**：本口径早前版本把该锚点写成「被 `refund_max_amount_cents` 封顶的 `max_refundable_cents`」，与代码现实不符——`max_refundable_cents` 取值就是 `order.refundable_cents`，**不受**金额上限封顶影响。此处按代码现实更正。

**派生发生在运行时**：在退款写路径**运行时**派生，不在采集侧、也不在调用侧——订单已加载、`RefundPolicy.check` 已跑完（金额已过 `>0` / `<= refundable_cents` / `<= max_amount_cents` 三道校验，`app/services/refund_policy.py:81-91`），此时才知道本次实退金额与订单可退余额的关系。**实现后**：该派生已落地在 `app/services/runtime.py::_handle_refund` 内的 `derive_refund_type(...)` 调用（当前 `:706-708`）。

**两个入口同一条派生**：对话入口与任务 API 入口走**同一个**派生点、同一套锚点，**不存在两套真相**；实现不得为某个入口单写一份派生逻辑，也不得让某个入口跳过派生。

**确定性与模型边界**：派生是**纯函数**（不读时钟、不用随机、不调模型、不查库），同一笔订单 + 同一笔金额派生出同一个 `refund_type`。它**不是**模型抽取，与 §5.6.5「模型不抽取、不采纳」**不冲突**：那条边界约束的是「模型不得提供这两个值」，不是「服务端不得确定性地算出这个值」。

**对话侧不追问 `refund_type`**：`refund_type` **不进** `REFUND_REQUEST.required_slots`，对话侧**只**追问 `refund_reason`。**不得**追问「全额还是部分」——客户在自然语言里分不清「全额」与「可退余额正好全额」，追问会造成死锁。`refund_type` 对客户**不可见**。

**任务 API 的 422 判据随之收窄**：缺 `refund_reason` → **422**；缺 `refund_type` → **不再 422**（§5.6.3）。

**禁止默认 `FULL`**：缺 `refund_type` 时**不得**一律取 `FULL`。`FULL` 的口径是「本次实退金额等于订单当前可退余额」（把可退的钱一次退完）；若不区分金额而一律取 `FULL`，一笔只退 50 分、而可退余额是 10000 分的退款会被标成「全额退款」，把两类事实压成同一个值，直接污染 B-4 想要的原因 / 类型分布。

**代价登记（必须写进 ADR）**：`SHIPPING` / `PRICE_PROTECTION` **只能**由坐席 / 调用方显式提交；客户自助发起、缺 `refund_type` 的退款**一律**落到 `FULL` 或 `PARTIAL`，即**自助链路上产生不了运费退款与价保退款**。这是本裁定的已知代价，实现**不得**用「猜一个 `SHIPPING`」之类的方式补齐。

**被否决的备选（登记，防止实现时回头）**：

1. **给 `refund_type` 也建一张关键词规则表**——否决：客户几乎不区分「全额」与「部分」，关键词命中极不可靠；且 `SHIPPING` / `PRICE_PROTECTION` 不是客户的语言。
2. **追问客户「全额还是部分」**——否决：同上；且会让自助退款**永远走不通**（说不清 → 澄清 → 再问 → 死锁），`OTHER` 那种出口对「全额 / 部分」并不成立。
3. **把对话侧发起的退款全部转人工**——否决：自助退款是主线能力；派生规则已能在不牺牲可退余额口径的前提下给出确定的类型。

#### 5.6.7 需他处同步（本口径**只登记，不在本轮改那些文件**）

- `docs/guide/接口说明.md`：**已同步（2026-10-27 收尾轮逐条核对，本条不再是待办）**。原待办的五项现均已落地——对话侧追问话术与 `prompt_for` 归因（当前 `docs/guide/接口说明.md:175-186`）、`context` 的 `refund_reason` / `refund_type` 两键与缺值/非法值行为、缺 `refund_reason` 的 422、非 `OTHER` 带 `reason_note` 的 422（当前 `:886-888`）、`refund_type` 缺值由服务端按 `orders.refundable_cents` 派生的说明（当前 `:906-911`）。本节标题是「需他处同步」，故保留此条作为**已落地留痕**，不再登记为欠账。
- `docs/spec/项目规格.md`：**已同步（B-1/B-3 收尾轮 T-R0-5a 落地，本条不再是待办）**。追问话术的两处声明与读取优先级（意图级 `REFUND_REQUEST.clarify_prompts.refund_reason` 覆盖、槽位级 `slots.refund_reason.prompt` 兜底、`prompt_for()` 优先取意图级、两处现值逐字一致、将来分叉以意图级为准且必须登记）已落在两处：§19.2「文档漂移清单」本轮（2026-10-27）表格中记录 `REFUND_REASON_MISSING` 归源订正的那一行（当前 `docs/spec/项目规格.md:4109`），以及附录 A.4 的 `REFUND_REASON_MISSING` 行（当前 `docs/spec/项目规格.md:4865`）。本节标题是「需他处同步」，故保留此条作为**已落地留痕**，不再登记为欠账。
- `app/agent/intents.yaml` 的 `version` 字段：新增槽位与 `required_slots` 变更时同步 bump（口径撰写时现值 `version: "2026-10-24"`；**实现后**：已随 B-1 落地 bump，现值 `version: "2026-10-26"`，当前 `intents.yaml:31`），与 `escalation.yaml` 的日期版本号风格一致。
- `app/agent/intent_catalog.py`：**无需**改校验器——`refund_reason` 槽位用的全是 `_SLOT_FIELDS` 现有合法键，规则表在独立文件（§5.6.1）。若实现时发现必须放宽 `_TOP_FIELDS` 才能落地，即视为越界，须退回本口径重新裁定。

---

## 6. 非目标（本文件不覆盖）

- **不覆盖退货流程**：`WAITING_RETURN`、`returns` 表、寄回/验收/换货补发属 B-2。
- **不做金额分层拆分**：`refund_approval_threshold_cents` 生效、`tiers` 分层属 B-3；本文件仅声明摘要对 `reason`/`refund_type` 敏感，不定义 tier。
- **B-3 的档位口径在本文件之外**：见 `.qoder/rules/approval-tiering.md`（审批档位、阈值生效、两条路径）。该文件修订了本文件 §4.1 的键清单（新增第九键 `approval_tier`），其余内容仍以本文件为准。
- **不引入财务口径 amount_breakdown**：运费/优惠券/税费拆分属第 3 节「未来若要区分」，本轮不做。
- **不改检索 / 查询改写**：与本口径无关（另见 `.qoder/rules/retrieval-query-rewrite.md`）。
- **不改前端 web/**：控制台展示改造不在本口径。
- **不做「退款原因选择器」UI**：控制台 / 前端给坐席用的退款原因、退款类型选择器不在本口径，也不在本轮范围。§5.6 只规定服务端与 YAML 侧的采集；若确需 UI，另立后续任务（连同 `docs/guide/接口说明.md` 的字段说明一起同步，见 §5.6.7）。
- **不改现有金额计算逻辑**：`RefundPolicy.check` 的 `amount` 取值与校验保持不变。
- **不引入 product_id**。

---

## 7. 与 NEXT_STEPS 的对齐

| NEXT_STEPS 条目 | 本文件对应 |
|---|---|
| B-1 退款没有原因与退款类型（NEXT_STEPS B-1 段） | 第 1 节术语/枚举、第 2 节默认值、第 4 节进摘要、第 5 节接口 |
| B-8 金额不区分运费/优惠券/税费（NEXT_STEPS B-8 段） | 第 3 节（声明「当前不区分」+ 未来口径） |
| B-3 审批金额分层（NEXT_STEPS B-3 段） | 第 4.2 / 第 6 节：`requires_approval` **不进**本摘要；**但档位快照 `approval_tier` 进**摘要（第九键，见第 4.1 节与 `.qoder/rules/approval-tiering.md` §5.1/§5.2） |
| B-1 的**采集方式**（关键词规则映射；缺 `reason` 即不放行；缺 `refund_type` 由服务端派生） | **§5.6**：属**本口径补充 + 用户裁决**，NEXT_STEPS 原文**未规定** |

> **§5.6 的采集口径不是 NEXT_STEPS 的要求**。B-1 关于采集只有一句抽象边界（B-1 段「建议改动」第 4 条：「原因 / 类型由规则或客户显式选择提供，**模型不采纳**」），没有规定规则表落在哪个文件、键长什么样、未命中怎么办、缺值时两个入口各自怎么办；§5.6 把这些定死，来源是用户裁决，引用时不得写成原文要求。
>
> 其中「缺 `reason` 即不放行、对话侧追问」相对 `docs/下一步规划.md` B-1 段「边界」第 2 条「不把原因做成必填的追问——客户说不清时允许 `OTHER`」是一处**有意的收紧**，理由：自动兜底 `OTHER` 会污染 B-4 想要的原因分布（§5.6.2）。`OTHER` 仍是合法出口，且追问话术必须给出该出口（§5.6.3），故与该条后半句不冲突。若要回到该条的宽松读法，必须先改本文件并说明理由，实现不得自行择一。
>
> §5.6.6 的 `refund_type` 缺省派生同样是**本口径补充 + 用户裁决**（NEXT_STEPS 原文未规定缺值时怎么办）；它带来的代价（自助链路上产生不了 `SHIPPING` / `PRICE_PROTECTION`）见 §5.6.6，并按该节要求写进 ADR。
>
> §5.6.7 登记的 `docs/guide/接口说明.md` 字段说明与 `intents.yaml` 版本号 bump 属后续任务，本轮不改那些文件。
