# 澄清候选降级（P11）：项目级口径

本文件是 TicketPilot 本轮工作（缺槽位追问的候选降级）的项目级声明，只规定术语、
契约、配置项与验收口径。流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md`
为准，两者冲突时先改本文件并说明理由，再改代码。

## 1. 术语表

| 术语 | 唯一定义 |
|---|---|
| 槽位追问（clarification） | 意图已确定、必填槽位缺失时向客户要一个字段的那一次回复；由 `app/agent/intent_catalog` 的 prompt 模板生成。 |
| 澄清计数（clarification_count） | 会话内已发生槽位追问的累计次数，存在 `ConversationSlots` 里，由 `EscalationEngine.count_outcome` 递增。 |
| 澄清预算（clarification rounds） | 允许连续追问的最大轮数，取自政策阈值 `clarification_rounds`；用尽后本回合改为转人工。 |
| 候选（candidate） | 调用者本人最近订单的 L1 事实集合，用于第二次追问时让客户点选，`kind=order_candidate`。 |
| 候选降级 | 同一意图第二次仍缺 `order_id` 时，把「再问一遍」换成「列出你自己的最近订单让你点选」。 |
| 已尝试候选（attempted_candidates） | 转人工上下文包里记录的、本轮已向客户展示过的候选，字段与候选卡片逐字一致。 |
| L1 事实 | 候选卡片只允许的四个字段：`order_id`、`product_name`、`status`、`delivered_at`。 |
| 挫败度阈值（frustration_rounds） | 与澄清预算无关的既有阈值，仍为 2，只服务 `frustration` 信号。 |

## 2. 候选契约

### 2.1 读路径

```python
class CommerceRepository:
    def list_recent_orders(
        self, tenant_id: str, user_id: str, limit: int = 3
    ) -> list[Order]: ...

class SupportContextService:
    def order_candidates(self, identity: Identity, *, limit: int = 3) -> list[dict]: ...
```

- 排序：`paid_at` 倒序，同一时刻按 `id` 倒序，保证可复现。
- 作用域：`tenant_id` 与 `user_id` 逐值相等；`support` 坐席角色**不放宽**，
  因为候选要发给正在打字的这个人。
- 上限：3 条，由 `limit` 参数决定，代码不读环境变量。

### 2.2 卡片形状

| 字段 | 值 |
|---|---|
| `kind` | `order_candidate` |
| `card_id` | 订单号 |
| `title` | `订单 <订单号>` |
| `subtitle` | 目录里的商品名；目录不认识该订单时为 `最近订单` |
| `summary` | `<状态中文> · <签收日期或「未签收」>` |
| `fields` | 只有 `order_id` / `product_name` / `status` / `delivered_at` |
| `sections` | 空列表（L2 不预取，仍走 `kind=order` 的订单卡按需展开） |
| `total_chars` | `0` |

### 2.3 硬性边界

1. **不调模型**：候选文案是模板拼接；`rules` 与 `pydantic_ai` 模式输出逐字一致。
2. **不猜订单号**：候选只能来自 `list_recent_orders` 返回的行；无行即无候选。
3. **不越权**：候选不进入坐席队列以外的推送、导出、通知或日志。
4. **trace 最小化**：`generate/support.reply` 只记 `clarify_candidates`（计数），
   禁止出现订单号字符串。
5. **第一次不动**：首次缺槽位的回复与本轮改造前逐字一致（含 suggestions）。
6. **非订单槽位不挂候选**：缺金额、缺商品时不列订单。

## 3. 阈值口径

| 信号 | 阈值名 | 默认值 | 语义 |
|---|---|---|---|
| `frustration` | `frustration_rounds` | 2 | 连续不满轮数，与本变更无关 |
| `repeated_clarification` | `clarification_rounds` | 3 | 追问一次 → 带候选追问一次 → 第三次仍未补上才转人工 |

| 环境变量 | 字段 | 默认 | 含义 |
|---|---|---|---|
| `ESCALATION_CLARIFICATION_ROUNDS` | `escalation_clarification_rounds` | `3` | 澄清预算；设回 `2` 即回到「第二次追问当场转人工」的旧口径 |

计数在阈值处停止增长；转人工后会话进入接管态，Agent 静默，不再产生新的追问。

## 4. 验收标准（DoD）

1. `pytest` 全量通过，且每个新增行为都有先失败后通过的测试。
2. 第一次追问与改造前逐字一致；第二次缺 `order_id` 时给出 ≤3 张本人候选卡片。
3. 第三次仍缺 → `repeated_clarification` 转人工，`bundle.attempted_candidates`
   与卡片字段一致；无候选时该字段为空列表。
4. 跨租户、跨用户订单不出现在候选里；坐席角色也不放宽。
5. 点选候选订单号后，被中断的意图续跑并给出答案（不回落到 `UNKNOWN`）。
6. `ESCALATION_CLARIFICATION_ROUNDS=2` 时行为回到旧口径，可用作前后对比。
7. trace 与日志中检索不到候选订单号。
8. `evals/support_run.py` 全部通过，口径变化逐条写进 `docs/guide/评测说明.md` 与 ADR。
