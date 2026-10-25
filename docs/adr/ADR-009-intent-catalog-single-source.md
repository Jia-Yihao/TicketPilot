# ADR-009：意图目录为唯一事实源

- 状态：Accepted
- 日期：2026-10-24

## 背景

意图识别此前散落在三处：

- `SupportIntent` 枚举里的一份标签清单；
- `agent/routing.py` 里一大段 `if/elif` 优先级链；
- `agent/actions.py` 里另一份「这个意图由哪个动作服务」的映射。

三份事实源必须保持同步，而同步靠的是人的记忆。任何一处漏改，症状是「某个意图被识别出来了，但没有对应动作」，或者「动作存在，路由却到不了」。

同时，「缺槽位时问什么」「没有依据时怎么答」「这个意图风险多高」这些问题没有落脚点，只能散落在服务代码的注释里，无法被集中审阅。

## 决策

把意图目录移到数据层：`app/agent/intents.yaml`，由代码读取，代码不再重复这些值。

一行意图声明：

| 字段 | 含义 |
|---|---|
| `intent_id` | 意图标签 |
| `priority` | 数字越小越先匹配，第一条命中的行决定意图 |
| `cues` | 子串线索（小写） |
| `match_policy_question` | 消息在问规则而不是在问某一笔订单 |
| `match_catalog` | 消息命中了商品目录 |
| `match_order_id` | 消息带订单号 |
| `match_request_only` | 只在显式请求时匹配；「什么时候可以转人工」是提问，不是请求 |
| `match_analytics_question` | 消息在问自己记录上的数字，且记录集与聚合方式都被点名 |
| `nature` | `collaboration` 行的优先级高于写路径：要人 ≠ 要工单 |
| `action` | 可执行能力；第一个负责执行，其余是声明的升级路径 |
| `required_slots` / `clarify_prompts` | 执行前必须补齐的槽位与逐槽追问话术 |
| `no_evidence` | 没有依据时的回答口径 |
| `fallback` | 动作无法服务该意图时的去处 |
| `risk_level` | `R0`–`R3` |
| `write_path` | 是否进入确定性工作流 |

槽位本身也在同一份文件里声明（类型、正则、提示话术、非法值话术、缺失原因码）。

当前 12 行目录：`HUMAN_HANDOFF`、`REFUND_REQUEST`、`TICKET_CREATE`、`SELF_ANALYTICS`、`REFUND_STATUS`、`REFUND_ELIGIBILITY`、`POLICY_QA`、`DELIVERY_ISSUE`、`LOGISTICS_QUERY`、`PRODUCT_QUERY`、`ORDER_QUERY`、`UNKNOWN`。

路由与分发的规则随之确定：

1. 按 `priority` 升序遍历目录行；
2. 逐行自检，匹配条件按**固定顺序**判定：`match_request_only`（消息是提问而非请求 → 跳过该行）→ `match_policy_question` → `match_analytics_question` → `match_catalog` → `match_order_id` → `cues` 子串命中；任一条件成立即选定该行；
3. 选中的行，其 `action` 决定后续路径；
4. `write_path: true` 的意图进入 `TaskRuntime`，其余走只读路径；
5. 全部行都不命中 → `UNKNOWN`。

## 后果

- 新增一种能力 = 加一行目录 + 实现一个动作；路由与分发代码不动。
- 「缺槽位问什么」「没依据怎么答」在目录里就能审阅，不必读服务代码。
- 风险等级与是否写路径成为目录的显式属性，升级触发表（`agent/escalation.yaml`）据此引用。
- 目录是 YAML，**加载即校验**：拼错的字段名会被拒绝（`Unknown field`），引用了未声明的槽位会被拒绝，重复的意图行会被拒绝，空目录会被拒绝。少写的可选字段落回声明过的默认值，不静默改变语义。
- 目录是数据不是代码，**改目录等于改行为**，因此目录变更必须与 ADR 一起进版本库。

## 备选方案

- **继续用枚举 + if/elif**：省一个文件，但三处事实源继续漂移；新增意图要改至少三个文件。
- **把目录放进数据库**：可运行时编辑，但目录决定的是「代码走哪条路」，运行时可改会让行为不可复现，也失去 code review 的保护。本项目坚持目录随代码版本走。
