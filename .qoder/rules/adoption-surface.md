# 采纳面收口与改道计数（R7）：项目级口径

本文件是 TicketPilot 本轮工作（模型读数能被采纳的面收窄到一个常量、并把「改道」与
「被拒改道」分别计数）的项目级声明，只规定术语、契约、配置项与验收口径。流程仍遵循
用户级协作规则；业务事实以 `docs/spec/项目规格.md` §6.5 为准。两者冲突时，先改本文件
并说明理由，再改代码。

## 1. 术语表

| 术语 | 唯一定义 |
|---|---|
| 采纳面（adoption surface） | 服务端**愿意据此改变路由**的意图集合。唯一载体是 `app/agent/pydantic_agent.py` 的 `ADOPTABLE_INTENTS`，R7 起等于 `frozenset({Intent.REFUND_REQUEST})`。 |
| 可采纳（adopted） | 一次模型回答**真的**改变了 `RoutePlan`：规则读数是 `UNKNOWN`、回答是 `Intent` 成员、且该成员 ∈ `ADOPTABLE_INTENTS`。三者同时成立才算。 |
| 写意图（write intent） | 会打开一个任务的意图，等于 `WRITE_INTENTS` = `{REFUND_REQUEST, TICKET_CREATE}`。**只用于分类**，不是采纳面，不得拿它当采纳判据。 |
| 改道（redirect） | 模型读数与规则读数不同。方向由 `redirect_kind()` 分类，与「是否被采纳」是两个独立问题。 |
| 被采纳的改道（`model_redirect`） | 改道且被采纳，即路由真的变了。只能出现在采纳面内。 |
| 被拒改道（`model_redirect_rejected`） | 回答是合法的 `Intent` 成员、方向也构成改道，但没被采纳（规则读数不是 `UNKNOWN`，或该意图不在采纳面内）。 |
| 改道分类（`model_redirect_kind`） | 封闭词表 `REDIRECT_KINDS` = `extra_write` / `missed_write` / `write_to_write` / `read_to_read` / `none`，与 `evals/intent_metrics.py::WRITE_SPLITS` 同名同义。 |
| 错路由（misroute） | 与 gold 标注不符的路由。**只在离线评测里算**，trace 只提供分类，不提供判定。 |
| `NONE` | 模型的一个合法回答，意思是「候选里没有一个是对的」。它不是意图，永不参与采纳，也不计入改道（`redirect_kind` 返回 `none`，原因记在 `model_reason=model_none`）。 |

## 2. 采纳面契约

### 2.1 唯一定义处

```python
ADOPTABLE_INTENTS: frozenset[Intent] = frozenset({Intent.REFUND_REQUEST})
WRITE_INTENTS: frozenset[Intent] = frozenset(
    {Intent.REFUND_REQUEST, Intent.TICKET_CREATE}
)
REDIRECT_KINDS = (
    "extra_write", "missed_write", "write_to_write", "read_to_read", "none",
)

def redirect_kind(rule_intent: Intent, model_intent: str) -> str: ...

def decide_adoption(rule_intent: Intent, answer: str) -> Adoption: ...
```

1. **只有一个定义处**。`ADOPTABLE_INTENTS` 定义在 `app/agent/pydantic_agent.py`，
   两个入口都 `import` 这一个对象：`IntentBrain.plan`（对话入口与任务接口共同经过）
   与 `SupportAgent._consult_model`（对话侧的门）。**禁止**在任一入口旁边再写一份字面量
   集合、也禁止在配置项或环境变量里放宽它——采纳面不是可调参数。
2. **判定只有一个入口**。把回答变成路由意图的地方只有 `decide_adoption()`；
   调用方不得自行写 `if answer == "REFUND_REQUEST"` 之类的等价判断。
   `IntentLiteral` 这个旧别名（记录的是「六个成员都能被采纳」的旧面）已删除，
   不得重新引入第二个名字描述同一件事。
3. **规则读数优先**。只有规则读 `UNKNOWN` 时模型才有机会改变路由；规则读出了什么，
   模型说什么都不能覆盖它。改写与 `NONE` 的回答先被还原成规则读数，再进判定，
   所以它们**不会**被计成 `redirect_rejected`（它们的原因在 `model_reason`）。
4. **采纳只改路由，不改权限**。被采纳的退款读数仍要走资格校验、金额阈值与人工审批；
   采纳面收窄/放宽都不改变任何审批与核验路径。
5. **写意图集合只做分类**。`WRITE_INTENTS` 供 `redirect_kind()` 判断改道方向，
   任何采纳分支都**不得**读它。

### 2.2 改道分类

`redirect_kind(rule_intent, model_intent)` 是纯函数，三条规则：

| 条件 | 返回 |
|---|---|
| `model_intent` 不是 `Intent` 成员（含 `NONE`、含目录里的非枚举行） | `none` |
| `model_intent == rule_intent.value` | `none` |
| 规则是写、模型也是写 | `write_to_write` |
| 规则是写、模型不是写 | `missed_write` |
| 规则不是写（含 `UNKNOWN`）、模型是写 | `extra_write` |
| 规则不是写、模型也不是写（且两者不同） | `read_to_read` |

词表与离线指标同源：`evals/intent_metrics.py` 的 `WRITE_SPLITS` 用的是前三个名字，
同一次判断在 trace 与评测两处**不需要翻译表**。本轮**不改** `evals/intent_metrics.py`
与 `evals/intent/**`（语料与指标口径不动）。

### 2.3 留痕键集（默认值不落盘）

`ModelTrace` 新增四个字段，`as_dict()` 序列化成四个键：

| 字段 | 序列化键 | 类型 | 何时出现 |
|---|---|---|---|
| `adopted` | `model_adopted` | 布尔 | 这一轮做过采纳判定 |
| `redirect` | `model_redirect` | 布尔 | 这一轮做过采纳判定 |
| `redirect_rejected` | `model_redirect_rejected` | 布尔 | 这一轮做过采纳判定 |
| `redirect_kind` | `model_redirect_kind` | 字符串（`REDIRECT_KINDS`） | 这一轮做过采纳判定 |

1. **默认值 `None` 不出现在序列化结果里**。只带代价、没做判定的 trace（`parse()`
   的纯计费行）与 R7 之前写下的台账行，键集**一字不多**——旧读数不得凭空长出四个
   「未采纳」的键。
2. `from_dict()` 按同一规则反解：键不存在 → 字段为 `None`（「没有记录过判定」），
   **不是** `False`（「判过且否」）。`None` 与 `False` 的语义差不得抹平。
3. **搬运而不重算**。对话侧把 `trace` 存进台账、任务侧从台账复用时，四个计数照抄
   存储值，只重新校验**采纳这个动作**（`SupportAgent._consult_model` 的
   `routed.intent not in ADOPTABLE_INTENTS` 仍是门）；复用分支不得用新逻辑覆盖
   已记录的计数。
4. 落点只有既有链路：任务侧 `runtime._run` 把 `model_trace.as_dict()` 并进
   `task.route` span 属性；对话侧落在 `understand` span 的 `model_gap`
   （`model_gap.redirect` / `model_gap.redirect_rejected` / `model_gap.redirect_kind`
   + `model_gap.model` 里的完整一份）。**不新增 span、不新增事件、不新增表**。
5. 留痕里只有计数与分类名，不含证据串与客户原话（与 P8 同一条边界）。

## 3. 边界与禁止事项

1. **两个计数不得相加**。「被采纳的改道」与「被拒改道」是两件事，相加得到的数
   没有语义；任何报表、文档、门禁都不得把它们求和后当「改道数」。
2. **trace 不判对错**。真实错路由需要 gold 标注，只能在离线评测里算；不得从
   `model_redirect` 或 `model_redirect_rejected` 推出「错了几条」。
3. **不扩面**。任何「先放开再观察」的临时放宽（改 `ADOPTABLE_INTENTS`、加环境变量、
   在测试里 monkeypatch 成全集）都只能用于**负验证**，验证完必须原样收回，
   不得随交付物落盘。
4. **不改门禁口令**。不得为了让守卫变绿去改 `docs/fact-baseline.yaml`、
   `docs/code-anchors.yaml`、`docs/architecture-guard.yaml`。
5. **不越权**。采纳面与改道计数都不许绕过鉴权、不许读跨租户数据、不许放宽审批。
6. **任务接口不得因模型读数建单**。这是本轮的硬结论：`/api/v1/tasks` 上规则读
   `UNKNOWN` 时，模型说 `TICKET_CREATE` 也不能变出工单——路由读数是 `UNKNOWN`、
   任务是澄清、结果是 `NEEDS_HUMAN_REVIEW`，台账记 `model_redirect_rejected=true`。
7. **文档口径单一**。README 顶部「模型改变写路径路由 0/16」只覆盖**对话入口**的
   16 条写路径样本；任务接口这一路的同性质结论由
   `tests/test_llm_gap.py::test_the_task_api_cannot_reroute_a_read_request_into_a_write`
   承担，两处不得互相顶替。

## 4. 验收标准（DoD）

1. `IntentLiteral` 在 `app/` 的**生产代码**里不存在（`grep -rn IntentLiteral app/` 无命中）；
   采纳判定只有一个入口 `decide_adoption()`，两个入口读同一个 `ADOPTABLE_INTENTS`。
   仓内另有两处同名命中，都**不属源码口径**，也不得被当作「别名还活着」的证据：
   `evals/intent_metrics.py:64` 是一句提到旧别名的历史注释（本轮明确不改该文件的语料与
   指标口径）；`build/lib/app/agent/pydantic_agent.py:25` 是陈旧的构建产物（`build/` 不是
   源码目录，不参与口径）。
2. 规则读 `UNKNOWN` + 模型读 `REFUND_REQUEST` → 采纳，`model_adopted=true`、
   `model_redirect=true`、`model_redirect_kind="extra_write"`。
3. 规则读 `UNKNOWN` + 模型读 `TICKET_CREATE` → **不采纳**，`plan.intent` 仍是
   `Intent.UNKNOWN`，`model_adopted=false`、`model_redirect=false`、
   `model_redirect_rejected=true`、`model_redirect_kind="extra_write"`。
4. 直接调 `POST /api/v1/tasks`（规则读 `UNKNOWN`、模型读 `TICKET_CREATE`）：
   返回 201、任务数 1、工单数 0、结果 `NEEDS_HUMAN_REVIEW`，`task.route` 属性里
   `model_redirect_rejected=true`、`model_redirect=false`、
   `model_redirect_kind="extra_write"`。该用例必须带上**红色对照**：临时把采纳面
   放宽到五个成员，用例必须失败（工单被建出来），收回后必须通过。
5. `ModelTrace.as_dict()` 在四个字段全为 `None` 时键集与本轮之前逐字一致；
   `from_dict({})` 反解出四个 `None` 而不是四个 `False`。
6. 全量 `pytest`、`ruff check`、`mypy app`、`evals/support_run.py` 与三份守卫
   全绿；收集数变化时同步改文档里的读数并重生成说明书（守卫 24 passed 是硬门禁）。
