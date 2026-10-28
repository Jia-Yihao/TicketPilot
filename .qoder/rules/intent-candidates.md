# 意图识别候选化（P8）：项目级口径

本文件是 TicketPilot 本轮工作（模型输出面收窄到服务端候选集）的项目级声明，只规定
术语、契约、配置项与验收口径。流程仍遵循用户级协作规则；业务事实以 `docs/spec/项目规格.md`
为准。两者冲突时，先改本文件并说明理由，再改代码。

## 1. 术语表

| 术语 | 唯一定义 |
|---|---|
| 候选集（CandidateSet） | `build_candidates()` 在**调用模型之前**算出的「这条消息可能属于哪些意图」，是模型唯一被允许选择的集合。 |
| 候选（Candidate） | 候选集里的一个目录行（`intent_id`），带一份证据列表与一个服务端信度桶。 |
| 证据（evidence） | 服务端自己算出来的事实字符串，形如 `cue:退款` / `slot:order_id` / `rule:LOGISTICS_QUERY` / `context:pending_intent` / `context:last_intent` / `order:DELIVERED`。**只能来自服务端读数**，不接受模型自述。 |
| 信度桶（bucket） | `high` / `medium` / `low` 三档，由证据的**种类数**决定（见 §2.4）。不是概率，不是分数，未校准前**不参与任何判定**。 |
| `NONE` | 模型的一个合法回答，意思是「候选里没有一个是对的」。它不是候选，不进气泡、不进目录。 |
| 越界（out_of_candidates） | 模型给出的 `intent_id` 不在候选集内（也不是 `NONE`）。处理固定：回退规则读数，并在 trace 记 `model_out_of_candidates`。 |
| 采纳面（adoption surface） | 服务端**愿意据此改变路由**的意图集合。本轮不变：对话入口仍只采纳 `REFUND_REQUEST`，其余读数照旧丢弃。 |

## 2. 候选集契约

### 2.1 接口

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    intent_id: str                 # 必须是 intents.yaml 里存在的行
    evidence: tuple[str, ...]      # 稳定顺序，逐字可复算
    bucket: Literal["high", "medium", "low"]

@dataclass(frozen=True, slots=True)
class CandidateSet:
    candidates: tuple[Candidate, ...]
    allow_none: bool               # 本轮恒为 True
    narrowed: bool                 # False = 服务端没有依据，整份目录都在

    def ids(self) -> tuple[str, ...]: ...
    def allows(self, intent_id: str) -> bool: ...
    def bucket_of(self, intent_id: str) -> str | None: ...

def build_candidates(
    message: str,
    context: Mapping[str, object] | None,
    route: RoutePlan,
    *,
    catalog: IntentCatalog,
    order_state: str | None = None,
    max_candidates: int = 6,
) -> CandidateSet: ...
```

`build_candidates()` 是纯函数：输入 `(消息, 上下文, 规则读数, 目录, 订单状态, 候选上限)`，输出候选集。
订单状态由调用方**先取好再传入**（`app/agent/support_agent.py` 侧，`_scoped_order_state()`），内核不查库、不调模型。

`max_candidates` 是 **keyword-only** 参数，排在 `order_state` 之后，只数**带内容证据的行**
（`HUMAN_HANDOFF` / `UNKNOWN` 不占额，见 §2.3 第 5 条）。实现里它的默认值绑定在
`app/agent/candidates.py` 的模块常量 `_DEFAULT_MAX_CANDIDATES = 6`，与 `app/core/config.py` 里
`Settings` 的 `intent_candidates_max` 字段（默认 `6`）同值；本处写的是**语义默认值 `6`**。

### 2.2 证据来源（封闭集合，不得新增未登记的来源）

| 来源 | 证据串 | 单独入选 | 说明 |
|---|---|---|---|
| 目录 cue | `cue:<命中的词>` | 是 | 该行 `cues` 里逐字命中消息的词，一行最多记 3 个，按目录顺序。 |
| 规则读数 | `rule:<intent>` | 是 | `classify()` 给出的非 `UNKNOWN` 结论。 |
| 会话上下文 | `context:pending_intent` / `context:last_intent` | 是 | `ConversationSlots` 里被打断的意图与上一轮意图，只让它**点名的那一行**入选。上下文键名逐字是 `pending_intent` / `last_intent`（即 `app/services/conversations.py` 里 `ConversationSlots` 的两个同名字段）；证据串按上下文键逐字生成，**不得**另造别名。 |
| 槽位 | `slot:order_id` / `slot:amount_cents` | **否** | 本轮或上下文里**真实存在**的槽位；缺槽不记。 |
| 订单状态 | `order:<status>` | **否** | 仅在传入了 `order_state` 时记，且只记给「该状态在其 `required_slots` 里要求订单」的行。 |

**生产接线口径**：调用方必须把会话上下文与订单状态**真实传入** `build_candidates`——上下文
字典须带 `pending_intent` / `last_intent` 两个键（值取 `ConversationSlots` 的同名字段，缺任一者
传 `None`），并须传 `order_state`（订单当前状态，缺则传 `None`）。内核不查库、不调模型，
**取不到就等于没有该证据**——不得由内核自己去补取，也不得在调用方拿不到时用默认值冒充。
接线未落地时，`context:` 与 `order:` 两类证据**整体缺席**，候选集只剩 `rule:` / `cue:` / `slot:`。

**入选门槛**：一行进入候选集的条件是它至少有一条**内容证据**（`rule:` / `cue:` / `context:`）。
`slot:` 与 `order:` 是**加强证据**，只附着在已经因内容证据入选的行上，自身不使任何行入选。
理由必须写清楚：订单号存在只说明「凡是要求订单号的意图都可能」，它把六个订单相关行全变成
候选，等于没有信息；一条线索不指向任何一行的证据不能决定模型该看哪些行。

### 2.3 硬性边界

1. **确定性、离线**：不读时钟、不用随机数、不调模型、不查库。同一输入必须逐字产出同一结果，
   候选顺序固定为「目录 `priority` 升序，同优先级按 `intent_id`」。
2. **永远在集合里**：`HUMAN_HANDOFF` 与 `UNKNOWN` 无条件在候选集内（无证据也是候选，桶记
   `low`；若它同时被内容证据命中，按 §2.4 正常判桶）；本轮新增的意图行不因「模型可能想选」
   而绕过目录校验。
3. **不编造，也不假窄**：只要有任何一行**普通行**（非无条件候选行）拿到内容证据，候选集就收窄成
   「有内容证据的行 + 两行兜底」，没有内容证据的普通行不得进候选。若**一行都没有**内容证据
   （规则读数与目录 cue 同时失明），候选集**不收窄**（`narrowed=false`）：把整份目录的**行名**
   交给模型。理由是窄到只剩两行兜底等于没给模型任何选择，而这类句子正是模型补位的场合
   （P1/P2 的全部意义）。这种集合不是短名单：评测与文档必须把它按「无信号」单独统计，
   不得算进收窄覆盖率。此时候选上限也不生效（上限只数带内容证据的行，见本节第 5 条）。
   **无条件候选行（`HUMAN_HANDOFF` / `UNKNOWN`）上的内容证据不触发收窄**：这两行先被分流走，
   不计入「拿到内容证据的行」。若一条句子的 cue 只命中它们，候选集仍按「无信号」处理
   （`narrowed=false`）。346 条语料里命中这条边界的有 **2** 条（`ext-delivery-24`、
   `gap-handoff-10`，cue 都只命中 `HUMAN_HANDOFF`），它们计入 `no_signal` 统计。
4. **不扩大采纳面**：候选化只改「模型能说什么」，不改「服务端据此做什么」。写路径仍由
   规则门 + 必填槽位 + 审批决定；模型选中 `REFUND_REQUEST` / `TICKET_CREATE` 不自动放行。
5. **有上限**：带证据的候选最多 `max_candidates`（默认 6）个，超出按桶（high > medium > low）
   再按 `priority` 截断；`HUMAN_HANDOFF` / `UNKNOWN` 不占用这个上限，也不被截断。
6. **原文不动**：候选与证据只进 prompt 与 trace，不写回 `messages.content`、`tasks.request`
   或回复文本；`tasks` 的幂等键与 `ModelDecision` 的 `digest` 口径不变。

### 2.4 信度桶

| 桶 | 判据（证据的**种类数**，不是条数） |
|---|---|
| `high` | ≥ 2 种，且其中至少一种是 `rule:` 或 `cue:` |
| `medium` | 恰好 1 种，且是 `rule:` 或 `cue:` |
| `low` | 其余全部：只有 `context:`（哪怕再叠加 `slot:` / `order:`）、无条件候选且无证据 |

一行内的证据串顺序固定为 `rule:` → `cue:`（目录顺序）→ `slot:` → `context:` → `order:`。

**模型自报的 `confidence` 不参与桶计算，也不参与任何判定**（这是 F-3 的收口点）。
该字段保留在 `app/agent/pydantic_agent.py` 的 `IntentOutput.confidence` 上，**不进 trace、不参与任何判定**：
`app/` 下没有任何读取点；唯一读它的地方是 `evals/model_distribution.py` 的 `read_case()`（把
`output.confidence` 存入 `Reading`）与 `run()`（收集 `reading.confidence`），把它读出来
做一次分布统计（只记录模型自报值）。文档与断言一律不把它当质量指标。

### 2.5 分层注入

| 层 | 内容 | 是否进 prompt |
|---|---|---|
| L1 | 候选 `intent_id` + 证据串（约 100 token） | 是，每次调用都带 |
| L2 | 候选对应的目录行定义，**六个键**：`intent_id` / `bucket` / `validation` / `required_slots` / `fallback` / `actions`（实现见 `app/agent/candidates.py` 的 `describe_candidates()`） | 否，本轮只在显式要求时组装（`describe_candidates()`），供下一轮接答案路径复用 |
| L3 | 完整目录 12 行的**定义** | 永不注入。无信号时注入的是 12 个**行名**（只有 `intent_id`，
  不含定义、不含 cue 表），这不等于 L3 |

## 3. 模型面契约

1. `IntentOutput.intent` 的类型由 `Literal[6 个]` 改为 **`str`**，取值合法性由
   `IntentBrain` 对着候选集判定；`NONE` 是合法值。形状校验（订单号必须 `ORD-` 开头、
   金额必须正整数分）仍留在 agent 的 output validator 里，可触发一次 `ModelRetry`。
2. **越界不重试**：`intent ∉ 候选 ∧ intent ≠ NONE` 时不发第二次请求，直接回退规则读数并记
   `model_out_of_candidates`。理由是「越界说明集合不对，再问一次也不会对」，也避免多付一次调用。
3. `NONE` 按「模型同意规则也没读出来」处理：不采纳、不建任务，trace 记 `model_none`。
4. `AGENT_MODE=rules` **完全不进候选路径**：闸门判完就在算候选之前返回，零调用、零新增 trace 字段，
   规则模式的回复与本轮之前逐字一致（这是回放自检 `REPLAY DIFF EMPTY` 必须继续成立的前提）。
5. 失败路径不变：模型报错仍是 `rules_fallback`，带成本记录。
6. **开关关闭时的等价范围**（本轮唯一承认的差异，必须写进 ADR）：`INTENT_CANDIDATES_ENABLED=false`
   时不注入候选、不产生 `model_reason`、合法字面量的读数与来源标记与本轮之前一致。差异只在
   「字面量之外的回答」：旧行为是 pydantic 输出校验失败 → `rules_fallback`；`intent` 放宽成
   `str` 之后，这类回答变成「模型答了但不采纳」，路由结果同为规则读数，来源记 `pydantic_ai`。
   这一差异不改任何写路径结果，因为它两者都回退规则读数。
   **成本口径（不是完全等价）**：旧路径下 `intent` 还是字面量时，模型给出字面量之外的回答会先触发
   pydantic 输出校验失败，再走一次 `ModelRetry` 重问——**可能多付一次模型调用**；新路径 1 次请求
   即被接受，只改来源标记。因此这两条路径的**路由结果**相同，**调用次数**可能不同。

## 4. 配置项

新增配置必须有默认值，并写进 `.env.example` 与 `Settings`：

| 环境变量 | 字段 | 默认 | 含义 |
|---|---|---|---|
| `INTENT_CANDIDATES_ENABLED` | `intent_candidates_enabled` | `true` | 总开关；关掉后不注入候选、不产生 `model_reason`（等价范围见 §3.6）。 |
| `INTENT_CANDIDATES_MAX` | `intent_candidates_max` | `6` | 候选上限（`HUMAN_HANDOFF` / `UNKNOWN` 不占额）。 |

生产环境不因它拒绝启动：纯本地计算，无外部端点依赖。

## 5. 评测口径

- 证据脚本 `evals/intent_candidates.py` 在 **P1 两层语料**（`evals/intent/intent_set.json`，
  346 条）上报告：候选覆盖率（gold ∈ 候选）、平均候选数、桶分布、以及「若采纳面不变会拦掉多少越界」。
- **必须分「收窄集」与「无信号集」两个分母**：`narrowed=false` 的那些句子，覆盖率恒为 100%
  （整份目录都在），把它算进收窄覆盖率就是自欺。报告里要单独给出无信号占比——它就是规则+cue
  的失明率，是本轮最有用的一个数。
- 分层报告：`external` 与 `authored_gap` 分开，不合并成一个数字；桶分布**不设通过/失败阈值**
  （未校准前只记录）。
- 不退化门槛：11 句探针（`scripts/llm_gap_probe.py`）与 62 条支持集的 `intent_accuracy` 读数
  必须与本轮之前一致；`AGENT_MODE=rules` 的全部门禁不变。
- **越界率只能来自真实模型读数**：本轮在同一台机器上跑两条路径做 A/B ——
  关闭候选时让模型自由输出 6 个字面量（before），开启时候选内选择（after）。
  没有真实读数时，报告必须显式写成「未测」，不许用假模型或规则回退模拟越界率。
- 未测项如实登记：`medium`/`low` 桶的实际含义未经校准；`HUMAN_HANDOFF` 作为候选是否会诱导
  模型选它，只有 A/B 读数能说明，本轮不下结论。

## 6. 验收标准（DoD）

1. 候选内核有独立的先失败测试：确定性、顺序稳定、证据来源封闭、无条件候选、上限截断、
   没有内容证据的行不入选、**一行依据都没有时不收窄且如实标 `narrowed=false`**、
   订单状态只在传入时记。
2. 模型面测试用**假模型**驱动：越界选择回退规则并记 `model_out_of_candidates`；`NONE` 不建任务；
   桶计算不读模型自报 `confidence`（给 0.99 的自报值仍按证据判桶）；`rules` 模式零调用；
   采纳面未扩大（非退款读数仍被丢弃）。
3. `INTENT_CANDIDATES_ENABLED=false` 时不注入候选、不产生 `model_reason`，合法字面量的读数与
   来源标记与本轮之前一致；「字面量之外的回答」按 §3.6 的差异如实记录。
4. 评测脚本产出前后对比，且 `build_intent_set.py --check` / `intent_metrics.py --enforce` 不退化。
5. 全量门禁绿：`pytest`、`ruff`、`mypy app`、`evals/run.py`、`evals/support_run.py`、
   `retrieval_metrics.py`、`replay.py --self-check`、`alembic check`、`npm run build`、两个守卫。
6. 文档同步：ADR-034、`docs/spec/项目规格.md`、`docs/guide/证据索引.md`、`docs/guide/架构说明.md`、
   `docs/records/智能体平台设想.md`、`docs/下一步规划.md`、`docs/guide/接口说明.md`（trace 字段）、`README.md`、
   `CONTRIBUTING.md`、`.env.example` 与说明书重生成；守卫事实计数同步。

## 7. 明确不做

- 不把模型输出当 gold，不因为候选化把 `confidence` 变成决策输入。
- 不扩写意图采纳面（`TICKET_CREATE` 等仍不由模型读数触发）。
- 不在本轮接 L2 定义注入到答案路径；`describe_candidates()` 只保证「按需可得」，不被调用即不进 prompt。
- 不引入 embedding 或相似度做候选召回——候选只来自目录行的**确定性命中**。
