# Plan: 意图路由数据驱动化（F-9 / 阶段 B）

本文是施工说明。读完应当能直接开工，不需要再读对话记录。所有行号是 2026-10-27 的实测值，动手前请先复核。

## 0. 一句话

把写意图（退款 / 工单）的判定从 `app/agent/routing.py` 的顺序 `if-elif` 迁到 `app/agent/intents.yaml` 的 `cues`，让 `catalog.match()` 成为唯一判定器，**判定结果一条都不许变**。

## 1. 现状精读

### 1.1 判定实际分两段，读意图已经由目录决定

`app/agent/support_agent.py:158-187` 的 `classify_support()` 是这个顺序：

```python
spec = get_catalog().match(text, has_catalog_match=..., has_order_id=...)
if spec is not None and spec.nature == "collaboration":   # ① 转人工先行
    return SupportPlan(SupportIntent(spec.intent_id), resolved_order)
if route.intent in {Intent.REFUND_REQUEST, Intent.TICKET_CREATE} and not blocks_write_action(text):  # ② 写意图
    return SupportPlan(SupportIntent(route.intent.value), resolved_order)
if spec is None:
    return SupportPlan(SupportIntent.UNKNOWN, resolved_order)
return SupportPlan(SupportIntent(spec.intent_id), resolved_order)   # ③ 目录覆盖 classify 的读意图
```

**关键事实**：第 ③ 步说明 catalog 命中会**覆盖** `classify()` 给出的读意图。也就是说读意图其实已经是「目录唯一判定」，`classify()` 的读意图分支（`routing.py:303-312`）只影响 `RoutePlan.intent` 这个粗粒度字段，不影响 `SupportPlan`。

**所以本轮的真正范围只有第 ② 步**：写意图的判定仍来自 `route.intent`，而 `route.intent` 的写意图值又是 `routing.py:298-314` 的 `if-elif` 给的。

### 1.2 写意图的两个条件

```python
blocked = blocks_write_action(text)                                   # routing.py:298
if any(word in text for word in _REFUND_ACTION) and not blocked:       # :299 → REFUND_REQUEST
    ...
elif any(word in text for word in _TICKET) and not blocked:            # :301 → TICKET_CREATE
```

词表在 `routing.py:41`（`_REFUND_ACTION`，6 条）与 `:53`（`_TICKET`，5 条）。`blocked` 来自 `blocks_write_action()`，含义是「有疑问词或泛化标记、且没有请求框架」——即**问句不算写请求**。

### 1.3 目录里两行的现状

`intents.yaml:80-96` 的 `REFUND_REQUEST`（`priority: 10`、`write_path: true`）与 `:98-107` 的 `TICKET_CREATE`（`priority: 11`、`write_path: true`）**都没有 `cues`**。同文件的 `HUMAN_HANDOFF`（`:60-78`）有 `cues` 且带 `match_request_only: true`——这是「命中 cue 但仍需是明确请求」的既有先例，本轮要复用这个语义。

## 2. 目标结构

```
catalog.match()  ──写路径行（write_path: true, match_request_only: true）
   │                └→ REFUND_REQUEST / TICKET_CREATE（未来新增的写意图同样走这里）
   ├─读意图行（现状不动）
   └─collaboration 行（现状不动，仍先判）

classify()  ──只保留槽位抽取（order_id / amount_cents / refund_reason / refund_type）
                + 给 RoutePlan.intent 填一个粗粒度值
```

`RoutePlan.intent` 是 6 值枚举（`app/models/enums.py:123`：POLICY_QA / ORDER_QUERY / LOGISTICS_QUERY / TICKET_CREATE / REFUND_REQUEST / UNKNOWN），而目录有 12 行，**反向映射不全覆盖**。所以不要让 `classify()` 整体委托 catalog——读意图分支保持原样，只把写意图分支改成「遍历 catalog 中 `write_path: true` 的行」。这样既满足「加一行写意图即生效」，又不会改变 `RoutePlan.intent` 的取值集合（下游 `runtime.py` 有依赖它的分支）。

### 2.1 可认领集合与「失败可见」

遍历 `write_path: true` 的行必须过一个显式白名单：

```python
# 只有这些 Intent 成员能被 classify 认领；新增写意图行前必须先扩枚举
_WRITE_INTENT_IDS = {Intent.REFUND_REQUEST, Intent.TICKET_CREATE}
_WRITE_INTENT_EXEMPT = {"DELIVERY_ISSUE"}   # 已知不在枚举里，显式豁免
```

`intents.yaml` 的 `DELIVERY_ISSUE` 也是 `write_path: true`，但 `Intent("DELIVERY_ISSUE")` 会抛 `ValueError`。处理规则三条：

1. 不在白名单/豁免集的行**跳过**，行为与今天一致（今天 `classify()` 本来就不判它）。
2. 跳过必须**可观测**：加一条测试断言「`write_path: true` 的行集合 ⊆ 白名单 ∪ 豁免集」。新增一行 `write_path: true` 而不在这两个集合里，**测试直接失败**，提示「先扩 `Intent` 枚举，或显式加入豁免集」。
3. **禁止裸 `continue`**。静默跳过会让「加一行即生效」第二次失效，而且失效时没有任何提示——比现在把词表写在代码里更难排查。

## 3. 五处不等价点（必须先读）

以下五处是「按 `priority` 天真排序」会改变判定结果的地方。`priority` 与 `if-elif` 位置不等价，直接迁移会让 A1（逐条不变）失败。

| # | 现象 | 现状依据 | 若照搬会怎样 | 对齐方式 |
|---|---|---|---|---|
| **T1** | **问句门槛丢失**（最高危） | `routing.py:298` 与 `support_agent.py:181-183` 各调一次 `blocks_write_action` | 「什么时候可以转人工」命中 `_TICKET` 的「转人工」→ 被判成 `TICKET_CREATE`，写路径被问句触发 | YAML 两行加 `match_request_only: true`；`classify_support` 第 ② 步的 `not blocks_write_action` **一字不许删** |
| **T2** | **POLICY_QA 与 LOGISTICS_QUERY 顺序倒置** | `classify()` 第 5 步 `_LOGISTICS`（`:307`）先于第 6 步 `_POLICY`（`:309`），同时命中给 `LOGISTICS_QUERY` | 但 `priority`：`POLICY_QA=40` < `LOGISTICS_QUERY=60`，按 priority 排序会给 `POLICY_QA`。「物流政策怎么规定」判定翻转 | 二选一：把 `LOGISTICS_QUERY` 的 priority 调到 <40，或让 `POLICY_QA` 的 cues 收窄到不含物流词。**用 346 条语料验证哪种与现状一致**，不要凭直觉选 |
| **T3** | **`REFUND_ELIGIBILITY` 被降级成 `POLICY_QA`** | `routing.py:303-304` 命中 `_REFUND_ELIGIBILITY` 却赋 `Intent.POLICY_QA` | 交给 catalog 会返回 `REFUND_ELIGIBILITY`（更细，`priority: 30`），下游动作从政策检索变成 `refund.eligibility` | 本轮以现状为准：维持 `POLICY_QA`。若无法在 YAML 层表达，把这条差异单独报批，**不得**默默接受 |
| **T4** | **`_TICKET` 与 `HUMAN_HANDOFF` 的 cue 重叠** | `_TICKET` 含「转人工」「人工客服」；`HUMAN_HANDOFF` 的 cues（`intents.yaml:71-72`）也含这两条 | 若 collaboration 行不再先判，「转人工」会被 `TICKET_CREATE` 抢走，产生建单而不是转人工 | `classify_support` 第 ① 步顺序**不许动**；在契约里写明「collaboration 行优先于写路径」 |
| **T5** | **写意图 cues 不能放宽** | `_REFUND_ACTION` 只有 6 条动作短语（「申请退款」「我要退款」「帮我退款」「退钱」「退款申请」「refund」） | 若把 cues 放宽成「退款」两字，几乎所有涉及退款的句子都会命中写路径 | `REFUND_REQUEST` 的 cues **逐字复制**现有 6 条，先不加词。加词是后续独立事项，不混在本轮 |
| **T6** | **`DELIVERY_ISSUE` 也是 `write_path: true`** | `intents.yaml` 该行 `write_path: true`（`priority: 50`），但 `Intent` 枚举里没有这个值 | 直接 `Intent("DELIVERY_ISSUE")` 抛 `ValueError`，切换那一刻就崩 | 走 2.1 节的白名单 + 豁免集；跳过必须可观测，不许静默 |

T1 与 T4 涉及写路径安全，任何一条出错都属于严重缺陷，不是「精度问题」。T6 属于启动期崩溃，也不接受「先跳过再说」。

## 4. 两层门禁与四步实施

每一步单独提交、单独过门禁。**不得**四步合成一次提交，否则 diff 不可归因。

### 4.0 先定门禁口径

| 层 | 对象 | 判据 | 为什么必须有这一层 |
|---|---|---|---|
| **端到端** | `classify_support()` 输出的 `SupportPlan.intent` | 346 条逐条不变 | 这是用户可见的判定。合成顺序（T4 的 collaboration 抢先）只有这一层看得见 |
| **过程** | `classify()` 的写意图分支 vs catalog 的写行 | 差异清单为空 | 这是本轮真正改动的对象，必须干净 |

读路径上 `classify()` 与 `catalog.match()` 的**既有分歧**（T2 的顺序、T3 的降级）不计入差异清单，但要归档成「既有分歧档案」（见 4.5），并证明它们不影响 `SupportPlan`。

只卡过程层不够：某句同时命中写意图 cue 与 collaboration cue 时，`classify()` 给 `TICKET_CREATE`、`classify_support()` 给 `HUMAN_HANDOFF`，过程层看上去「写意图判对了」，端到端其实已经翻转。

### 4.1 步骤 1：影子模式（零行为变化）

- 给 `REFUND_REQUEST` / `TICKET_CREATE` 加 `cues`（逐字复制 `_REFUND_ACTION` / `_TICKET`）与 `match_request_only: true`。
- 新增一个**只读**比对脚本或测试：对 346 条语料同时算 `classify()` 与 `catalog.match()`，输出不一致清单（语料 id、文本、两者判定）。
- 同时存一份**端到端基线**：346 条的 `SupportPlan.intent` 序列，作为后续每一步的对照物。
- **判定逻辑一行不改**。
- 门禁：全量用例仍全绿（此时行为必然未变，用来证明「加 cues 本身不会改变判定」）。

产物：过程层差异清单 + 端到端基线。清单是空的就直接进步骤 3；非空则按 T1–T6 进步骤 2。

### 4.2 步骤 2：消除过程层差异

- 按差异清单逐条对齐：调 priority、收窄 cues、补 `match_request_only`。
- 每条对齐都要在清单里留一行「原因 → 处理」，便于审查。
- 读路径的既有分歧不修，转入 4.5 的档案。
- 门禁：过程层差异清单为空；端到端基线**逐条一致**。

### 4.3 步骤 3：切换判定来源

- `classify()` 的写意图分支改为遍历 catalog 的 `write_path: true` 行：

```python
spec = _match_write_intent(text)          # 新增私有函数，内部走 catalog
if spec is not None and not blocks_write_action(text):
    intent = Intent(spec.intent_id)
elif ...                                   # 读意图分支原样保留
```

- `classify_support()` 第 ② 步的判据可以保持 `route.intent in {...}`（此时 `route.intent` 已由 catalog 给出），也可以改成读 spec——**改哪一种都要保证 T1 的门槛仍在**。
- 若 `routing.py` 引入 catalog 造成循环导入，把这次查询下沉到 `support_agent` 层再传值进 `classify()`，**不要**为了图快改 import 方向（`architecture-guard` 会挡）。
- 门禁：端到端逐条一致 + 全量全绿（收集数仍 **1035**）+ A3 假意图断言（临时注入一行 `write_path: true` 的假意图，判定立刻生效，跑完删除；假意图的 `intent_id` 必须是 `Intent` 已有成员）。**该断言并入既有用例体内，不新增测试函数、不用 `parametrize`、不新建 `test_*.py`**，否则收集数变 1036/1037，`test_collected` 与 `test_py_files` 两条事实同时红，连带 9 份文档 26 处 `1035` 都要改（详见 `dispatch.md` C13）。假意图目录用 `intent_catalog._load(tmp_path / ...)` 构造，不走 `get_catalog()`（`lru_cache`）。
- 同时验证「失败可见」：临时加一行 `write_path: true` 但不在白名单/豁免集里，测试必须**失败**。这一步证明 2.1 节的约束真的在起作用，验证完删除。

### 4.4 步骤 4：收尾

- 删除 `_REFUND_ACTION` / `_TICKET` 常量与对应 `if-elif` 分支（确认全仓零引用后再删）。
- 更新 `intents.yaml` 头部注释：让「加一行即可，不改路由代码」（第 8–9 行）**成立**，而不是继续把它留着当不实承诺。
- 文档同步：`docs/项目现状.md` 的 D1 / D2 标收口；`docs/下一步规划.md` §0.2 的 F-9 改完成；`docs/优化规划.md` 阶段 B 标完成。
- 写 ADR-037（接在 ADR-036 之后）：记录「判定器唯一化」的决策、被否决的「classify 全面委托 catalog」方案（理由见第 2 节：枚举反向映射不全覆盖）、`DELIVERY_ISSUE` 的豁免处理。
- **ADR 数量 35 → 36 要同步 6 处**。守卫只绑其中两处，漏掉其余四处会留下「磁盘 36、文档 35」的不一致：

| 文件 | 位置 | 改什么 |
|---|---|---|
| `README.md` | `:13` | `35 篇 ADR` → `36 篇 ADR` |
| `CONTRIBUTING.md` | `:171` | `35 篇` → `36 篇`；括号范围 `ADR-013…ADR-036` → `ADR-013…ADR-037` |
| `docs/下一步规划.md` | `:28` | `35 篇 ADR` → `36 篇 ADR` |
| `docs/spec/项目规格.md` | `:3703` | `35 篇 ADR（…ADR-036）` → `36 篇 ADR（…ADR-037）` |
| `docs/guide/证据索引.md` | `:673` | `**35 篇 ADR**（…ADR-036）` → `**36 篇 ADR**（…ADR-037）` |
| `docs/records/智能体平台设想.md` | `:178` | 同上的篇数与范围 |

- 改了 `docs/spec/项目规格.md` 必须跑 `.venv/Scripts/python.exe scripts/build_manual.py` 重生成 docx，否则说明书同步守卫会红。
- **不要改** `openspec/changes/add-perf-guardrails-and-measurement-2026-10/plan.md:164` 的 `35 篇`——那是另一个变更的历史记录，改了就是伪史。

### 4.5 既有分歧档案

读路径上 `classify()` 与 `catalog.match()` 本来就不一致（T2 的顺序、T3 的降级）。这些分歧**本轮不修**，但必须留档：

- 清单字段：语料 id / 原文 / `classify` 读判定 / `catalog` 判定 / 为什么 `SupportPlan` 不受影响。
- 归档位置：`openspec/changes/add-intent-routing-data-driven-2026-10/known-divergences.md`。
- 作用：下次有人想「顺手修好」这些分歧时，会先看到这份清单，知道那是一次独立的行为变更，不是 bug。没有这份档案，「不修」和「漏了」在审查时无法区分。

### 4.6 直接调内核的用例怎么改

有些用例直接调 `model_gate()` / `classify()`，判定来源变了之后它们需要更新。规则：

- **允许改**：判定来源变了、但端到端结论相同的用例。
- **不允许改**：期望从「不 consult」变成「consult」，或从 `UNKNOWN` 变成某个意图——那是端到端行为变了，必须停下报批。
- **必须补**：一条**生产路径**用例（走 `classify_support`，不是直接调内核），证明这句话仍然不进 `consult`。只改单元期望、不补端到端用例，视同未验证。

## 5. 本轮不做：阶段 A 的边界（F-1）

阶段 A 排在本轮之后，但现在就要知道边界，避免本轮顺手扩大采纳面：

- 采纳面仍只有 `REFUND_REQUEST`；`support_agent.py:1513` 的判据本轮不动。
- `model_gate()` 的 `question` 出口本轮不动。
- 阶段 A 才允许「模型读数 → 一次反问」，且**反问不改变路由、不执行写动作、除 `audit_events` 外无副作用**，`AGENT_MODE=rules` 路径逐字不变。本轮任何改动如果让这些约束提前松动，都属于越界。

## 6. 回滚

每一步都是独立提交，回滚按步骤粒度进行：

| 回滚到 | 操作 | 副作用 |
|---|---|---|
| 步骤 1 之前 | revert 该提交 | 无（YAML 加字段但无人读） |
| 步骤 3 之前 | revert 步骤 2 或步骤 3 | 判定回到 `if-elif`，行为等价 |
| 全部 | revert 整个 change | 无数据迁移，无表结构变化 |

本轮不涉及数据库、迁移、配置项，回滚成本低。
