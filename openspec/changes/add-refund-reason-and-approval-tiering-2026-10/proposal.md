# B-1 退款原因与退款类型 ＋ B-3 审批金额分层

## 为什么

**B-1：退款申请里没有「为什么退」。**
`refund_payload`（`app/services/approvals.py:34-42`）只有 `action` / `order_id` /
`amount_cents` / `currency` / `resource_version` / `policy_version` 六项，`action_digest`
也只覆盖这六项；`refunds` 表（`app/models/commerce.py:54-74`）没有 `reason`、没有
`refund_type`，也没有 `product_id`。于是客户说「我要退款」和「收到就碎了，我要退」在
台账上完全一样：审批人看到的是「12900 分」，看不到「为什么退」——四眼审批的关键输入缺失；
原因分布、退款率、按原因的时长分析全部做不了（运营看板 B-4 依赖它）。

**B-3：审批没有金额分层，而且阈值这个旋钮是死的。**
`refund_approval_threshold_cents`（`app/core/config.py:51`，默认 `0`）确实经
`PolicyEngine.requires_approval()`（`app/core/policy.py:92-93`）算进了资格结果
（`app/services/refund_policy.py:98`），**但写路径根本没读它**：
`TaskRuntime._handle_refund`（`app/services/runtime.py:523`）在资格通过后**无条件**
`create_refund_approval`（`:587-593`），`pending_action.requires_approval` **硬编码 `True`**
（`:617`）。把阈值调到 10000，退款仍然全部等人批。29 元与 2900 元走同一道 R3 审批，
审批成本与金额无关；更糟的是「改了不生效」比「没有这个旋钮」更危险。

**为什么现在做这一批。**
两者是同一个退款对象的两面，且**共用一条迁移**：B-1 给退款补上「退的是什么、为什么退」，
B-3 给退款补上「按金额该谁批、要不要批」。B-4（运营看板的原因分布）与 P10（审批风险事实）
都等这两项的数据基础；用户已裁决下一批做 B-1 + B-3。

## 改什么

按能力列变更点（细则见规格增量，逐文件步骤见 `plan.md`）：

1. **退款原因枚举**：新增 `RefundReason`（`QUALITY` / `DAMAGED` / `WRONG_ITEM` /
   `NOT_AS_DESCRIBED` / `NO_LONGER_NEEDED` / `OTHER`）。
2. **退款类型枚举**：新增 `RefundType`（`FULL` / `PARTIAL` / `SHIPPING` /
   `PRICE_PROTECTION`），只作分类标签，不引入任何独立金额项。
3. **退款台账补三列**：`refunds` 表新增 `reason` / `refund_type` / `reason_note`，
   **可空、不回填历史数据**；新记录的 `reason` 应用层**必填**（缺即 422）；`refund_type`
   **记录必填、输入可选**——缺值由服务端按订单可退余额口径派生（见第 5、6 条）。
4. **原因采集走关键词规则表**：独立数据文件 `app/agent/refund_reason_rules.yaml` +
   loader；命中即填槽，**未命中不填**，**不得自动兜底 `OTHER`**。
5. **类型不由关键词推断**：`refund_type` 的**显式值**只来自坐席 / 调用方显式提交；**没有**
   显式值时由服务端按订单当前可退余额**派生**（本次实退金额等于 `orders.refundable_cents`
   → `FULL`，否则 → `PARTIAL`）。两个入口走**同一条**派生，不存在两套真相。
6. **缺原因不放行、缺类型由服务端派生**：新建退款申请必须有 `refund_reason`——对话侧走
   目录的必填槽位追问（话术带「其他」出口），任务 API 侧 422；`refund_type` 缺失**不**拒单、
   **不**追问，由服务端派生（见第 5 条）。
7. **原因与类型进审批摘要**：退款载荷新增 `reason` / `refund_type` 两键，同一订单两条
   不同 `reason` 的退款在台账可区分。
8. **审批档位表**：新增数据文件 `app/core/approval_tiers.yaml` + loader，默认单层
   （1 个 `approver` 审批），保持现行为。
9. **阈值真正生效**：退款写路径按资格结果里的 `requires_approval` 分支；未达阈值的退款
   **直接执行**，但仍走同一套幂等台账、资格复核、对账与回执。
10. **档位进审批摘要**：退款载荷新增 `approval_tier` 键（档位快照），使「审批人批准的」
    与「执行的」是同一档。
11. **两条路径可区分**：任务结果带执行路径标记、任务事件与审计可读出「自动执行」，
    公开回执措辞与「审批通过」不同。

## 不改什么

- **不扩写采纳面**：退款原因 / 类型 / 档位都**不由模型抽取、不由模型采纳**；写路径仍由
  规则门 + 必填槽位 + 审批决定。`refund_type` 的**服务端派生**是**确定性计算**（订单可退
  余额与本次金额的关系），**不是**模型抽取——它不扩大模型的采纳面。
- **不追问退款类型**：对话侧只追问退款原因，**不**追问「全额还是部分」；客户在自然语言里
  分不清这两者，追问会造成死锁。代价是自助链路上产生不了运费退款与价保退款（该代价按
  口径登记进 ADR）。
- **不改退款金额计算逻辑**：退款金额仍是订单可退余额口径的一笔整数分，不区分运费、
  优惠券分摊、税费（B-8）。
- **不引入退货流程**：`WAITING_RETURN`、`returns` 表、寄回 / 验收 / 换货补发属 B-2。
- **不引入 `refunds.product_id`**：属 B-2 / P3。
- **不做多级会签**：所需审批数本轮恒为 1；不做按客户等级的差异化策略。
- **不放宽工具侧审批闸门**：`request_refund` 仍要求已批准审批；自动执行路径不经过该入口，
  而是在服务层复用同一执行器。
- **不改四眼原则**：发起人不能审批自己的申请，管理员也无自批例外。
- **不做「退款原因选择器」UI**：控制台 / 前端的退款原因与退款类型选择器不在本轮范围。
- **不热加载配置**：档位表与阈值改动在**重启进程**后生效（不需要改代码、不需要发版）。
- **不改检索 / 查询改写 / 意图候选化 / 澄清候选**：与本变更无关。

## 影响面

| 面 | 落点 |
|---|---|
| 数据 | `app/models/enums.py`、`app/models/commerce.py`、一条新迁移（`alembic/versions/`） |
| 审批与摘要 | `app/services/approvals.py`（退款载荷与建审批）、`app/services/refunds.py`（落库与执行复核） |
| 退款写路径 | `app/services/runtime.py`（缺 `refund_reason` 的闸门、`refund_type` 的缺省派生、阈值分支、两条路径的留痕） |
| 策略与档位 | `app/core/policy.py`（阈值读数不变）、`app/core/approval_tiers.py` + `.yaml`（新增） |
| 对话侧采集 | `app/agent/refund_reason_rules.py` + `.yaml`（新增）、`app/agent/intents.yaml`、`app/agent/routing.py`、`app/agent/turns.py`、`app/agent/support_agent.py` |
| 工具与接口 | `app/tools/builtin.py`（资格工具入参）、`app/schemas/tasks.py`（缺 `refund_reason` → 422） |
| 展示 | 审批详情（`web/src/pages/ApprovalWorkbench.tsx` 按载荷渲染）、回执文案 |
| 文档 | ADR、`docs/spec/项目规格.md`、`docs/guide/接口说明.md`、`docs/guide/证据索引.md`、`docs/下一步规划.md`、`README.md`、`CONTRIBUTING.md`、`.env.example`、说明书重生成 |

## 依赖与顺序

- **顺序：先迁移后逻辑，B-1 先于 B-3。**
  1. 先做枚举与**一条**迁移（三列、可空、不回填）：列不存在，后面的落库与台账代码没有
     地方写；迁移只加可空列，向下兼容，SQLite 与 PostgreSQL 双向可升降。
  2. 再做 B-1 全链路（枚举 → 采集 → 缺值闸门 → 摘要 → 落库 → 展示）。
  3. 最后做 B-3（档位表 → 档位进摘要 → 阈值生效与自动执行路径）。
- **为什么 B-1 与 B-3 不交错**：两者都要改**同一个**退款载荷构造函数。若交错修改，reviewer
  读 diff 时无法把「原因/类型」与「档位」对摘要的影响分开归因；先做 B-1、后做 B-3，
  每次改动的摘要形状变化是可单独验证的一段。
- **为什么共用一条迁移**：用户裁决 B-3 可与 B-1 共用一条迁移；B-3 本身**不需要**新列
  （档位进载荷与摘要，留痕落在执行台账、任务结果与审计），因此一条迁移只承载 B-1 的三列。
- **对上游的依赖**：无。本变更不依赖 B-2 / B-4 / B-5。
- **对下游的依赖**：B-4（运营看板的原因分布）与 P10（审批风险事实 + 摘要）依赖本变更。
