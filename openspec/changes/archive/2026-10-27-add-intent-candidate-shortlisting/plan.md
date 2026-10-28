# P8 意图识别候选化 — 实施计划

目标：把模型能说的意图从「6 个字面量」改成「服务端用证据算出来的候选集 + `NONE`」，
越界固定回退规则并留痕，同时给出一个**不读模型自报数字**的服务端信度桶。
口径以 `.qoder/rules/intent-candidates.md` 为准，本文件只规定怎么落地。

架构：`app/agent/candidates.py`（纯函数内核）→ `pydantic_agent.IntentAgent.parse(..., candidates=…)`
（只注入 L1）→ `IntentBrain.plan()`（算候选、校验越界、写 `ModelTrace.reason`）→
`support_agent._consult_model` / `runtime._run`（把 reason 带进 `understand` span 与 `task.routed`）
→ `evals/intent_candidates.py`（P1 语料上的覆盖率 / 候选数 / 桶分布）。

技术栈：FastAPI + SQLAlchemy 2 + PydanticAI（后端）；pytest；评测脚本在 `evals/`。
规格增量：`openspec/changes/add-intent-candidate-shortlisting-2026-10/specs/intent-candidates/spec.md`。

全局约束：

- 内核**确定性、离线**：不读时钟、不用随机、不调模型、不查库；同一输入逐字同一输出。
- 证据来源封闭（`cue:` / `rule:` / `slot:` / `context:` / `order:` 五类），不新增未登记来源。
- 采纳面不变：对话入口仍只采纳 `REFUND_REQUEST`；`AGENT_MODE=rules` 零调用、零新增 trace 字段。
- 越界不重试（`requests == 1`），回退规则读数。
- 桶只按证据**种类数**判；模型自报 `confidence` 不参与计算与判定。
- 每个任务先写会失败的测试并亲眼看过失败（红 → 绿 → 重构），最后跑全量门禁。
- 命令一律用 `./.venv/Scripts/python.exe`，`pytest` 要加 `-o addopts=`（`pyproject` 里 `addopts = "-q"`）；
  Windows 控制台输出中文会乱码，读数重定向到 `.tmp/*.txt` 并设 `PYTHONIOENCODING=utf-8`。

## 任务 1 — 候选内核（先红）

文件：`tests/test_intent_candidates.py`（新建）

断言至少覆盖：

1. 同一输入两次调用逐字一致，且候选顺序 = 目录 `priority` 升序、同优先级按 `intent_id`；
2. cue 命中记 `cue:<词>`，一行最多 3 个且按目录顺序；
3. `classify()` 的非 `UNKNOWN` 结论记 `rule:<intent>`；
4. 只给**真实存在**的槽位记 `slot:order_id` / `slot:amount_cents`，缺槽不记；
   **只有槽位时该行不入选**（槽位是加强证据）；
5. `context` 里的 `pending_intent` / `last_intent` 各记一种 `context:` 证据，且只让点名的那一行入选（桶 `low`）；
6. 未传 `order_state` 时不出现任何 `order:` 证据；传了才给**已因内容证据入选**且要求订单的行记 `order:<status>`，
   只有订单状态时不使任何普通行入选；
7. `HUMAN_HANDOFF` 与 `UNKNOWN` 无条件在候选里、桶为 `low`、不计入上限截断；
8. 没有任何证据的普通意图行不在候选里；
9. 超过 `max_candidates` 时按桶（high > medium > low）再按 `priority` 截断；
10. `allows()` / `ids()` 的形状；`NONE` 不是候选（`allows("NONE") is False`）。

期望签名：

```python
# app/agent/candidates.py
@dataclass(frozen=True, slots=True)
class Candidate:
    intent_id: str
    evidence: tuple[str, ...]
    bucket: Literal["high", "medium", "low"]

@dataclass(frozen=True, slots=True)
class CandidateSet:
    candidates: tuple[Candidate, ...]
    allow_none: bool
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

def describe_candidates(candidate_set: CandidateSet, *, catalog: IntentCatalog) -> list[dict[str, Any]]: ...
```

验证：`pytest tests/test_intent_candidates.py` 失败于「模块不存在」——这一步只允许失败在缺实现，
不许失败在拼写。

## 任务 2 — 候选内核（绿）

文件：`app/agent/candidates.py`（新建）。最小实现让任务 1 全绿；桶规则照抄 `.qoder/rules` §2.4。
`describe_candidates()`（L2）返回 `validation` / `required_slots` / `fallback`，**不在任何 prompt 里被调用**。

验证：新文件测试全绿 + `pytest -o addopts=` 全量不退化。

## 任务 3 — 配置与开关

文件：`app/core/config.py`、`.env.example`

新增 `intent_candidates_enabled: bool = True`、`intent_candidates_max: int = 6`。

测试：`tests/test_intent_candidates.py` 补两条——`get_settings()` 的默认值断言，与
`.env.example` 里存在这两个键（仓库里还没有 `.env.example` 的既有断言写法，本轮就地建立；
参照 `tests/test_query_rewrite.py` 里 `retrieval_query_rewrite_*` 的默认值断言写法）。

## 任务 4 — 模型面收窄（先红）

文件：`tests/test_intent_candidate_model.py`（新建，用假模型，全部离线）

断言：

1. 假模型返回**候选内**的意图 → 走现有采纳逻辑（退款读数仍照旧被采纳，非退款仍丢弃）；
2. 假模型返回**候选外**的意图 → 计划等于规则读数，`ModelTrace.requests == 1`（不重试），
   `ModelTrace.reason == "model_out_of_candidates"`；
3. 假模型返回 `NONE` → 不采纳、不建任务，`reason == "model_none"`；
4. 假模型自报 `confidence=0.99` 而证据只有一种 → 候选桶仍是 `low`（证明桶不读模型说的数）；
5. `INTENT_CANDIDATES_ENABLED=false` → 回复、source、trace 与本轮之前逐字一致（越界的假意图
   在该开关关闭时按旧行为处理）；
6. prompt 里出现候选的 `intent_id` 与证据串，但**不出现**未被选为候选的目录行文案；
7. `AGENT_MODE=rules` → 候选内核不被调用、模型桩件一次不响（沿用 `tests/test_llm_gap.py` 的
   「被调用就失败」桩件写法）。

签名变化：`IntentOutput.intent: str`（不再是 `Literal[6]`）、`IntentAgent.parse(request, context,
candidates: CandidateSet | None = None)`、`ModelTrace.reason: str | None`（序列化键 `model_reason`，
`from_dict` 一起读回）。

## 任务 5 — 模型面（绿）

文件：`app/agent/pydantic_agent.py`

- `INSTRUCTIONS` 增补：只能从给定候选里选一个，或选 `NONE`；不得发明候选之外的意图。
- `parse()` 的 prompt 负载加 `"candidates": [c.intent_id]` 与 `"evidence"`（L1，不带 L2 定义）。
- `plan()`：`mode != pydantic_ai` 或开关关闭时**在算候选之前**返回（旧行为逐字不变）；
  开启时 `build_candidates(...)` → `parse(..., candidates=…)` → 校验：
  `output.intent == "NONE"` → `reason="model_none"`；`not candidate_set.allows(...)` →
  `reason="model_out_of_candidates"` 且回退 `rule_plan`；两者都不额外发请求。
- 复用路径（P2 的 `decision`）：`verifies()` 口径不动；复用命中时若当初的 `model_reason` 非空，
  原样带回，不再重新判定。

## 任务 6 — 留痕到 span 与台账

文件：`app/agent/support_agent.py`、`app/services/runtime.py`

- `_consult_model` 的 gap dict 增加 `"out_of_candidates"` / `"none"` 布尔与 `"candidates"` 数量
  （只记数量，不记证据串——证据串含消息内容）；`understand` span 因此自动带上。
- `runtime._run` 的 `route_attributes` 与 `task.routed` 载荷带上 `model_reason`，
  使坐席侧看得出「这次回退是因为越界」而不是「模型没答上来」。
- 测试：`tests/test_intent_candidate_trace.py`（新建，2–3 条）——越界那轮的 trace 属性里有
  `model_out_of_candidates`，且属性里不出现候选证据串。

## 任务 7 — P1 语料上的候选评测

文件：`evals/intent_candidates.py`（新建）、`tests/test_intent_candidate_eval.py`（新建）

脚本对 `evals/intent/intent_set.json` 的 346 条逐条 `build_candidates(message, {}, classify(message))`，
按层（`external` / `authored_gap`）输出：候选覆盖率（`gold ∈ 候选`）、平均候选数、桶分布、
以及 `HUMAN_HANDOFF` / `UNKNOWN` 作为候选的占比。`--json` 出机器可读结果。
测试钉住：分层不合并成一个数字、桶分布不设通过/失败判定、空语料拒绝出报告、报告形状稳定。

## 任务 8 — 不退化门槛

命令：`scripts/llm_gap_probe.py`（11 句探针，本机 LM Studio）、`evals/support_run.py`（62 条）、
`evals/run.py`（40 条）、`evals/replay.py --self-check`、`evals/retrieval_metrics.py --channels bm25,hybrid`。

要求：支持集与探针读数与本轮之前一致；`REPLAY DIFF EMPTY` 成立（规则模式零变化是前提）。
越界率需要真实模型 A/B：`evals/intent_candidates.py --with-model` 在同一条码上跑
`INTENT_CANDIDATES_ENABLED=false`（before，模型自由输出）与 `=true`（after，候选内选择），
报告越界次数与请求数。**跑不动就把这一项写成「未测」，不许用假模型充数。**

## 任务 9 — ADR 与文档同步

文件：`docs/adr/ADR-034-意图候选收窄.md`（新建，含被否决方案：
「让模型输出全部 12 行 + 用 confidence 卡阈值」「越界时 ModelRetry 再问一次」）、
`docs/spec/项目规格.md`（新 §6.2.1 候选内核与桶、§14.1 测试清单与计数、§19.1 收尾读数、
附录 A/B/C、§25 守卫计数）、`docs/下一步规划.md`（§0 规模事实、§1.1、新增 §7.10、§9.1 第 8 行、
§9.10 排序结论）、`docs/records/智能体平台设想.md`（规模事实、层1/层6、§9.4 ADR 索引、进度行）、
`docs/guide/架构说明.md`、`docs/guide/接口说明.md`（trace 属性）、`docs/guide/证据索引.md`（本轮实跑读数）、
`docs/guide/评测说明.md`、`docs/guide/范围与边界.md`、`README.md`、`CONTRIBUTING.md`、`docs/fact-baseline.yaml`
（新计数）与 `docs/spec/TicketPilot项目说明书.docx` 重生成。

## 任务 10 — 全量门禁与独立审查

```bash
./.venv/Scripts/python.exe -m pytest -o addopts=            # 全量
./.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m alembic check
cd web && npm run build
./.venv/Scripts/python.exe -m pytest tests/test_architecture_guard.py tests/test_docs_consistency.py
```

审查重点（交给 reviewer 时点名）：

1. 采纳面是否真的没扩大（非退款读数仍被丢弃、写路径审批不变）。
2. `rules` 模式是否逐字未变（候选内核不可达、无新增 trace 字段、回放仍空 diff）。
3. 越界是否**只发一次**请求（多付一次调用就是把「不重试」写成了假话）。
4. 桶是否完全不看模型自报 `confidence`。
5. trace 里是否只有候选数量，没有证据串（证据串含消息内容，等于把原文搬进 trace）。
6. 越界率若没有真实 A/B 读数，文档是否写成「未测」而不是编一个数字。
