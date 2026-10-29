# F-1 单轮反问 — 实施计划（含 T1–T7 的落地证据）

**目标**：规则读不出、而模型给出一个合法候选读数时，服务端问客户一次「您想咨询的是哪一类」；
反问不改路由、不执行写动作、每个会话至多一次（判定域为顺序回合），`AGENT_MODE=rules` 逐字不变。

**口径**：`.qoder/rules/clarify-counter-question.md`（唯一判定口径，第 7 轮独立复查通过，2026-10-29，
提交 `4a4a495`）。本文件只规定**怎么落地**；行为契约在 `specs/counter-question/spec.md`，
取舍在 `proposal.md` 与 `docs/adr/ADR-039-单轮反问.md`。

> **本文件的性质**：F-1 的代码先落盘、openspec 产物后补（T7 是文档 / 证据轮）。因此下面每个任务
> 写的不是「打算怎么改」，而是**已经改了什么 + 证据在哪个文件的哪个位置 + 哪个提交**。
> 凡引用 `文件:行` 均为交付轮实地读盘所得（读法清单见「交付证据」第 3 节），行号以
> `0fbcc32` 的工作树为准。

**架构**：

```text
问句支（question）  support_agent.py::_consult_model:1658-1715
   ├ 上限已用 :1659-1662 ──▶ 今天的固定 gap（_QUESTION_GAP :176-185）逐字返回
   ├ 开关关闭 :1663-1666 ──▶ 同上（连探针的钱都不付）
   ├ 入口守卫 :1667-1673 ──▶ 同上（规则读数非 UNKNOWN，反问无处展示）
   └ 探针 :1674-1676 ──▶ pydantic_agent.py::IntentBrain.probe_question:686-770
                            （复用 _candidates 内核 + IntentAgent.parse；
                              不调 decide_adoption、不产 ModelDecision、四键 None）
         ├ 拒绝 :1677-1681 ──▶ 固定 gap + probe.trace（成本行照落）
         ├ 缺标签 :1691-1696 ──▶ 同上
         └ 成形 :1697-1714 ──▶ CounterQuestion(draft, gate="question", reading_intent)

咨询支（consult）   support_agent.py::_consult_model:1716-1798
   ├ plan() 调用 :1716-1718 ──▶ gap 搬运 :1734-1741 ──▶ ModelDecision :1742-1756
   ├ 采纳门 :1757（source == "pydantic_ai" and routed ∈ ADOPTABLE_INTENTS）
   └ 非采纳 return 点 :1787 之前 :1758-1786
         └ 触发判据 :1768-1775 = redirect_rejected is True + answer is not None
                        + 上限未置位 + plan.intent is UNKNOWN
              数据经 ModelTrace.answer / .candidate_ids（带内、永不序列化）同轮携带（路线 γ）

消费点（handle）    support_agent.py:531-583
   ├ :533 takeover 且非续跑 ──▶ 移交替换（反问不可达）
   ├ :551 missing ──▶ 槽位澄清（反问不可达）
   └ :563 else ──▶ :564 counter_draft is not None and plan.intent is UNKNOWN
                    ├─ 成立 :565-583 反问成为本回合回复 + 恰一条 counter_question.asked
                    └─ 不成立 :584-599 既有 dispatch
阶段七（回合后再判） :601-634  → count_outcome :603 / _shape :620-621（append|replace 消费 :1370-1377）
槽位写回（唯一一次）  :715-731  → counter_question_asked :723 经 conversations.save_slots :731
```

**技术栈**：Python 3.13、FastAPI、Pydantic v2、SQLAlchemy 2、pydantic-ai、pytest、ruff、mypy(strict)。
本变更**不涉及** alembic 迁移（槽位是 JSON dict 列，口径 §5.1 定死）。

**规格路径**：
- `openspec/changes/add-counter-question-2026-10/specs/counter-question/spec.md`

**全局约束**：

1. **采纳面三个词表不得动**：`ADOPTABLE_INTENTS` / `WRITE_INTENTS` / `REDIRECT_KINDS`
   （`app/agent/pydantic_agent.py:39,44,53`）。
2. **写路径零变化**（口径 §7）：业务表行数增量对每批消息逐条相等；受保护锚点
   `tests/test_llm_gap.py::test_model_gate_names_the_three_levels` 与
   `tests/test_security_model_cross.py::test_question_with_a_guard_hit_moves_no_money`
   的**判据形状**不动（后者只有口径 §13.3 预告的 `model_calls` 成本断言按实测改）。
3. **不引入新机制**：无锁、无版本列、无条件更新、无新表、无新迁移、无二级开关。
   状态位随回合末既有的那一次 `save_slots` 落库（口径 §5.1 裁定 (b)，(a) 已否决）。
4. **上限只承诺顺序回合**：交付物与 ADR SHALL NOT 写任何并发次数上界或收敛时点
   （口径 §5.1 竞态段已在第 7 轮**撤回**此前两版量化措辞，§14.2 末行同）。
5. **零新增 trace / span 键**：`clarify_reason` 只扩取值域；两个带内字段跨不出序列化边界。
6. **rules 模式不可达**（口径 §9）：所有反问代码挂在 `support_agent.py:1647-1648` 与
   `pydantic_agent.py:603-604` 两道既有闸门之后。
7. **命令**：`./.venv/Scripts/python.exe -m pytest -o addopts="" <路径>`（`pyproject.toml:53`
   的 `addopts = "-q"` 会吞掉汇总行；裸 `python` 是 Anaconda 的解释器，会给出假失败）；
   控制台读数重定向到 `.tmp/*.txt`。**同一时刻只跑一个 pytest 进程。**
8. **本轮不碰**：任何生产代码、任何测试文件、三份守卫清单
   （`docs/fact-baseline.yaml` / `docs/code-anchors.yaml` / `docs/architecture-guard.yaml`）、
   `docs/下一步规划.md` §0.2 状态列、任何文档里的测试数字、`.workbuddy/`；
   不执行任何 git 写操作；不归档本目录。

---

## 逐任务计划与落地证据

### 任务 1 — 反问文案 YAML 与封闭 loader（T1，已落地）

**改了什么**：新增 `app/agent/counter_question.yaml`（21 行：`version: "2026-10-29"`、
`prompt_template`、12 条 `labels`、`max_options: 3`）与 `app/agent/counter_question.py`
（107 行：`CounterQuestionSpec` :41-48、封闭顶层键集合 `_TOP_FIELDS` :38、
`_reject_unknown` :51、`load_counter_question` :59、进程内缓存 `get_counter_question` :104）。
未知顶层键在加载期报错，不静默忽略（口径 §4.6）。

**证据**：`app/agent/counter_question.py`、`app/agent/counter_question.yaml`、
`tests/test_counter_question_catalog.py`（现盘收集 27 条）——提交 `77cdf0b`。

### 任务 2 — 单轮上限状态位与全量往返（T2，已落地）

**改了什么**：`ConversationSlots` 新增 `counter_question_asked: bool = False`
（`app/services/conversations.py:42`，理由注释 :39-41）、`to_dict` 键 :59、
`from_dict` 缺键取 `False` :85（旧会话行兼容，**零迁移**）。

**证据**：`app/services/conversations.py`、`tests/test_support_conversation.py`
（往返与缺键兼容追加）——提交 `77cdf0b`。

### 任务 3 — 只读问询通路（T3，已落地）

**改了什么**：`IntentBrain.probe_question()`（`app/agent/pydantic_agent.py:686-770`）与
`QuestionProbe`（:458-481，`asks` 是调用方唯一要问的那个事实 :479-481）、
封闭拒绝原因词表 `QUESTION_PROBE_REASONS`（:447-455）。拒绝顺序（逐条实读）：
rules 早退 :713-714 → 调用失败 :721-734 → `NONE` :737-739 → 无候选集 :740-745 →
越界 :746-756 → `UNKNOWN` :757-763 → 与规则读数相同 :764-767 → 合法读数 :770。
该通路**不调 `decide_adoption`**（全文件仅 :580 与 :637 两处调用，均在 `plan` 内）、
不产 `ModelDecision`、不 `replace` 四计数键（全文件唯一的 `replace(` 在 :661，
位于 `plan` 新路径）。

**证据**：`app/agent/pydantic_agent.py`、`tests/test_question_probe.py`（收集 19 条，
含 `test_a_refund_reading_is_returned_as_is_and_never_recorded_as_adopted:159`、
`test_the_probe_source_never_touches_the_adoption_surface:317`、
`test_the_probe_never_replaces_trace_count_fields:323`）——提交 `77cdf0b`。

### 任务 4 — 问句档反问的生成与回合行为（T4，已落地）

**改了什么**：`_consult_model` 的返回元组加一位 `CounterQuestion | None`；问句支
（`support_agent.py:1658-1715`）按「上限 → 开关 → 入口守卫 → 探针 → 拒绝 → 缺标签 → 成形」
六道判定，未成形的每一条都**照旧付了的钱**（trace 照常带回，固定 gap 逐字返回）；
共享渲染器 `_counter_question_draft`（:188-240，`UNKNOWN` 跳过 :216-217、`max_options`
截断 :214-215、任一缺显示名整句不问 :210-211 / :218-220）；常量
`COUNTER_QUESTION_REASON` :166、`_HANDOFF_TAIL` :171、`_QUESTION_GAP` :176-185；
`CounterQuestion` 只读包装 :243-257；`handle` 消费点 :563-583（:564 的双判据）；
公开契约 `app/schemas/support.py` 的 `expected_slot` / `resumable_intent` 改为可空（:57-58）。

**证据**：`app/agent/support_agent.py`（+211/-60）、`app/schemas/support.py`、
`tests/test_counter_question_turn.py`（收集 6 条）、`tests/test_llm_gap.py`（+50，
含口径 §13.3 预告的 `len(refunds) + 2` 成本改判）、
`tests/test_security_model_cross.py`（+10/-10，只改 `model_calls` 成本断言）、
口径文件自身同步 +11（提交内）——提交 `1784c57`（提交信息记录该步独立 reviewer 通过，
「阻塞 0、严重 0、一般 5」）。

### 任务 5 — `consult` 出口的反问（T4b，路线 γ，已落地）

**改了什么**：`ModelTrace` 新增两个**带内、永不序列化**字段 `answer`（:203）与
`candidate_ids`（:204；:199-202 注释写明只有 `plan` 新路径那一次 `replace`（:661-682，
`candidate_ids` 写入在 :675）会赋值，台账复用分支 :600 与 rules 早退 :603-604 留 `None`）；
`as_dict` :237-261 与 `from_dict` :211-235 都不读不写这两个字段。
`_consult_model` 的非采纳 return 点（:1787）之前新增形成段 :1758-1786：触发判据照抄
`decide_adoption` 的 `redirect_rejected`（口径 §3.1，对话层不重写等价逻辑），
并叠加开关、上限位、`plan.intent is SupportIntent.UNKNOWN` 三条前置。
**采纳 return 点（:1788-1798）与台账复用轮逐字不变**：复用 trace 的 `answer is None`，
判据不成立，所以「复用轮不产生反问」这件事在盘上的实物是这一条判据，不是一句口号。

**证据**：`app/agent/pydantic_agent.py`（+28）、`app/agent/support_agent.py`（+73/-20）、
`tests/test_consult_counter_question.py`（收集 7 条；该文件同时是 T5/T6 常量的唯一出处）、
口径文件 +15（§2.2 / §4.1 路线 γ 登记）——提交 `56c3853`（提交信息：路线 γ，独立审查通过）。

### 任务 6 — 审计落消费点 + 一键回退（T5，已落地）

**改了什么**：`AuditRepository(session).record(...)` 写在消费点
`support_agent.py:571-583`（动作名 `counter_question.asked`、`resource_type=conversation`、
`outcome="SUCCESS"`、`detail` 恰 `{gate, reading_intent, option_count}`）；
开关 `counter_question_enabled: bool = True`（`app/core/config.py:122`，注释 :116-121）
与 `.env.example:203-209`；问句支的开关分支 :1663-1666（**连探针的钱都不付**）；
consult 支的开关前置 :1769（**不关 `plan` 调用本身**）；置位表达式
`counter_question_asked=carried.counter_question_asked or counter_asked`（:721-723，
注释写明 best-effort 与「被拦的轮次不清 carried 位」）经 :731 既有的那一次 `save_slots` 落库。

**证据**：`app/agent/support_agent.py`（+86/-11）、`app/core/config.py`（+8）、
`.env.example`（+8）、`tests/test_counter_question_audit.py`（收集 14 条）——提交 `195e09f`。

### 任务 7 — 矩阵收口与交付证据（T6 + T7）

#### 7a T6 用例矩阵（已落地）

补三格此前**没有实物钉住**的 §12 格子（`tests/test_counter_question_matrix.py` 文件头
:1-29 逐条写明钉哪三格、用什么替身、以及「探针 11 句的实跑读数属用户侧，本用例钉的是期望」）。
其中 :14-20 那一段已经把「生产消费侧只有一道闸、只判 `probe.asks`，三分支的**生产者**
判定由 `tests/test_question_probe.py` 钉」写成了原文——本次交付不把它改写成「端到端逐分支钉住」。

**证据**：`tests/test_counter_question_matrix.py`（新建 232 行，收集 9 条）——提交 `0fbcc32`。

#### 7b T7 文档 / 证据轮（本轮）

**改了什么**：只新增本变更目录的四个文件与一篇 ADR；**未改任何既有文件**。
三项必须如实登记的代价与两类未跑门禁全部写进 `docs/adr/ADR-039-单轮反问.md`：

- **代价 A（并发）**：上限的判定域是顺序回合；置位是回合末那一次整列覆盖写的一部分，
  `save_slots` 无版本列、无锁（`app/services/conversations.py:147-152`），并发不触发反问的
  回合可把 `True` 抹回 `False`，其后的回合仍可再反问 ⇒ **并发不设上界、不承诺收敛时点**。
  该代价不触及写路径判据、不放宽顺序上限、不引入新机制。
- **代价 B（append 叠加下反问文本仍可见）**：审计 :571-583 与置位 :723 都发生在阶段七
  （:601）与 `_shape`（:620-621 → :1370-1377）之前，所以 `complaint`
  （`app/agent/escalation.yaml:77-81`，`takeover: true` + `reply_mode: append` +
  `continue_task: true`）、`idle_timeout`（:192-201）、`risk`（:151-161）三类追加型命中时
  客户看到的是「反问 + 追加句」，这是**预期行为**，不得写成「反问后必静默」。
  替换型（`repeated_clarification` :187 `reply_mode: replace` 等）与「接管且非续跑」
  在消费点之前（:533-550）就已分流，走不到反问消费处，因此不产生审计行。
- **代价 C（§12.8 的分层事实）**：消费侧只有**一道闸**（`support_agent.py:1677`，
  只判 `probe.asks`；`probe.reason` 在全 `app/` 内**没有任何读取点**——交付轮实测，见「交付证据」第 4 节），
  所以 T6 钉的是「闸不按拒绝原因分叉」，生产者侧三分支由 `tests/test_question_probe.py`
  用真实 `IntentBrain` 钉。端到端逐分支（`TestModel intent="NONE"` / 越界读法 / `FailingAgent`）
  登记为**待增强项**，本轮不开测试轮。

**T7 收尾项的如实状态**（勾选见 `tasks.md`）：口径 §12.1 的探针侧是期望而非实测；
`evals/**`、PG/Redis 用例、`npm run build` 未跑；本轮产物**已于 2026-10-29 经独立 reviewer 复审，
结论「通过（附一般项）」**，一般项（内部数字 / 引用漂移）已由后续返工轮处理，且复审只读代码与文档、
未运行任何测试 / 构建 / lint；因未跑门禁仍在，交付仍不得宣布验收通过；本目录不归档。

---

## 审查重点

1. **不越采纳面**：探针与 consult 反问两段都没有 `decide_adoption` / `ModelDecision` /
   四计数写值路径（行号证据见任务 3 / 5）。
2. **不越展示面**：草稿的 `intent` 永远是规则读数（`_counter_question_draft` :224-240）；
   路由与 `gap["adopted"]` 的搬运（:1734-1741、:1788）不因反问改变。
3. **写路径零变化**：反问分支只产 `resolved=False` 的澄清草稿，永不进 `_dispatch` 的动作执行。
4. **留痕纪律**：审计 `detail` 恰三键，客户原话 / 订单号 / 候选证据 / 模型原文一律不出现。
5. **状态位不是计数器**：上限的判定域与失效面按 §5.1 原文表述，不得回退成任何量化承诺。
6. **rules 模式可达性**：两道闸门（:1647-1648、`pydantic_agent.py:603-604`）必须仍在所有反问代码之前。

---

## 交付证据（T7 证据轮，`openspec/AGENTS.md` §8）

### 1. 范围声明

本轮**只新增**下列文件，未修改、未删除任何既有文件；未执行任何 git 写操作
（无 `add` / `commit` / `stash` / `checkout` / `reset` / `clean` / `push`）；
未运行任何测试用例（只跑门禁读数的命令见第 4 节）；未改三份守卫清单、
未改 `docs/下一步规划.md` §0.2 状态列、未改任何文档里的测试数字、未动 `.workbuddy/`、未归档本目录。

新增文件（本轮唯一写入面）：

```text
openspec/changes/add-counter-question-2026-10/proposal.md
openspec/changes/add-counter-question-2026-10/plan.md
openspec/changes/add-counter-question-2026-10/tasks.md
openspec/changes/add-counter-question-2026-10/specs/counter-question/spec.md
docs/adr/ADR-039-单轮反问.md
```

### 2. 改动前后的工作树快照

BEFORE（本轮第一次写入之前，实测）：

```console
$ git status --short
?? .workbuddy/
$ git diff --stat
（无输出——工作树与 HEAD 无差异）
$ git rev-parse --abbrev-ref HEAD
master
$ git rev-parse --short HEAD
0fbcc32
```

AFTER（本轮全部写入完成后，实测；与 BEFORE 相比**没有任何已跟踪文件被修改**，
新增面恰好是声明的五个文件——`docs/adr/` 那行的八进制转义是 `core.quotepath` 的显示形式，
实际路径是 `docs/adr/ADR-039-单轮反问.md`）：

```console
$ git status --short
?? .workbuddy/
?? "docs/adr/ADR-039-\345\215\225\350\275\256\345\217\215\351\227\256.md"
?? openspec/changes/add-counter-question-2026-10/
$ git status --short --untracked-files=all
?? .workbuddy/memory/2026-10-27.md
?? "docs/adr/ADR-039-\345\215\225\350\275\256\345\217\215\351\227\256.md"
?? openspec/changes/add-counter-question-2026-10/plan.md
?? openspec/changes/add-counter-question-2026-10/proposal.md
?? openspec/changes/add-counter-question-2026-10/specs/counter-question/spec.md
?? openspec/changes/add-counter-question-2026-10/tasks.md
$ git diff --stat
（无输出——本轮未修改任何已跟踪文件）
$ git rev-parse --abbrev-ref HEAD
master
$ git rev-parse --short HEAD
0fbcc32
```

五个新文件的实测行数：`docs/adr/ADR-039-单轮反问.md` 279 行、`proposal.md` 117 行、
`plan.md` 461 行、`tasks.md` 68 行、`specs/counter-question/spec.md` 206 行
（2026-10-29 返工轮处置一般项后的 `wc -l` 实测；交付轮原文自报本文件为 414 行，
随后续写入漂移、以返工轮实测为准；其余四项数字与盘上实物一致）。

**两点如实限定**（`openspec/AGENTS.md` §8.2 第 1–3 条）：其一，上面的 `??` 清单能证明
「本轮没有改任何已跟踪文件」，但**不能独立复核**「本轮之外没有别的轮次在同一工作树写入」——
`.workbuddy/` 在 BEFORE 就已是未跟踪状态，本轮未动它，也未动测试产物目录
（`git status --short --untracked-files=all` 的清单里，除五个交付文件与 BEFORE 就存在的
`.workbuddy/` 之外没有别的条目，即本轮未新建任何仓内副产物目录）；其二，本节的 AFTER 快照
是在**四个 openspec 文件 + ADR 全部写完之后、把这段快照粘回本文件之前**取的——粘回这一动作
只改本文件（未跟踪），因此快照的三行 `??` 清单与空 `git diff --stat` 在写入前后同值。

五个文件全部写入完成后的最终态复跑（同一工作树，逐字；第 4 节另有一次同批读数，
两次相差只在汇总行的秒数上）：

```console
$ PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest -o addopts="" tests/test_docs_consistency.py tests/test_doc_code_anchors.py tests/test_architecture_guard.py -k "not documented_numbers" -q
........................                                                 [100%]
24 passed, 1 deselected in 1.69s
```

（本 ADR 所在目录 `docs/adr/` 在锚点守卫的扫描面内，故 ADR-039 的每个 `文件.py:NN` 锚点都经过
行号越界检查；`openspec/**` 不在扫描面内，其锚点由交付轮逐条实地读过——见第 3 节读法清单。）

### 3. 交付轮实际读过的范围

代码（逐行读，全部为当前工作树实物）：
`app/agent/support_agent.py`（:112-190、:188-260、:515-634、:700-769、:1362-1386、
:1475-1519、:1630-1799）、`app/agent/pydantic_agent.py`（:39-56、:206-262、:440-482、
:686-770 + `replace` / `decide_adoption` / `from_dict` 调用点全库检索）、
`app/services/conversations.py`（:17-101、:111-160）、`app/core/config.py`（开关与预算字段）、
`app/schemas/support.py`（`SupportClarification`）、`app/agent/escalation.py`（`reply_mode`
解析 :54/:142）、`app/services/escalation.py`（`count_outcome` :109）、
`app/agent/escalation.yaml`（信号表 `takeover` / `reply_mode` / `continue_task` 全量）、
`app/agent/counter_question.py`、`app/agent/counter_question.yaml`、`.env.example:203-209`、
`app/agent/routing.py:237-245`。

测试（读文件与收集数，未复跑用例断言之外的内容）：六个 F-1 测试文件的文件头与用例名，
`tests/test_docs_consistency.py:54-104`（数字守卫的读取面）。

口径与规约：`.qoder/rules/clarify-counter-question.md` 全文（2026-10-29 返工轮 `wc -l` 实测 282 行，重点 §2.2/§2.3、§3、§4、
§5、§6、§7、§8、§9、§10、§11、§12 十四条、§13 五项、§14）、
`openspec/AGENTS.md`（含 §4 文件口径 :50-101、§5 三角色 :102-112、§8 证据轮）、
`openspec/project.md`、`openspec/specs/README.md`、`agents.md`、`.qoder/rules/task-order.md` §3/§4。

参考形状：`openspec/changes/add-refund-reason-and-approval-tiering-2026-10/`
（`proposal.md` / `plan.md` / `tasks.md` / 两份 `specs/*/spec.md`）、
`docs/adr/ADR-034-意图候选收窄.md`、`docs/adr/ADR-036-审批金额分层.md`、
`docs/adr/ADR-038-分数种类与分尺度阈值.md`（含其「证据」节的「文档轮未复跑」写法）。

台账与文档：`docs/下一步规划.md:45`（F-1 行）/:73（R10 行）/:2660（R13 收口段，登记同一红项的
处置归 R12）、`docs/fact-baseline.yaml`（`adr_count` :177-185）、`CONTRIBUTING.md:170`、
`docs/records/智能体平台设想.md:178` / ADR 索引表 :1075-1079、
`docs/guide/接口说明.md`（1216 行，:88-95 / :150-152 / :168-181 / :194-201 / :744-748）、
`docs/guide/证据索引.md:272-305`、`docs/spec/项目规格.md:1434-1441`、
`docs/adr/ADR-024-三级模型闸门.md:25-39`、`scripts/llm_gap_probe.py:84-105`。

### 4. 跑过的命令与逐字输出

交付轮当场读数（2026-10-29，Git Bash + `./.venv/Scripts/python.exe`，**单进程串行**，
每条都带 `-o addopts=""`——漏了它看不到汇总行；裸 `python` 是 Anaconda 的，会给出假失败）。
如实说明两点：其一，下面每条输出都是那次运行的原文，**pytest 汇总行的秒数在不同次之间会浮动**
（第 2 节的最终态复跑与这里的守卫同批读数只差在秒数）；其二，除标注「写入完成后」的那条外，
其余读数取于本轮的写入过程之中，全部为 **SQLite 单机形态**、无 PG/Redis、无真实模型凭据。

```console
$ PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest -o addopts="" tests/test_counter_question_catalog.py tests/test_question_probe.py tests/test_counter_question_turn.py tests/test_consult_counter_question.py tests/test_counter_question_audit.py tests/test_counter_question_matrix.py -q
........................................................................ [ 87%]
..........                                                               [100%]
============================== warnings summary ===============================
tests/test_question_probe.py::test_a_legal_new_reading_comes_back_unadopted
  D:\Code\TicketPilot\.venv\Lib\site-packages\pydantic_graph\_utils.py:67: DeprecationWarning: There is no current event loop
    event_loop = asyncio.get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
82 passed, 1 warning in 15.56s
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" --collect-only -q tests/test_counter_question_catalog.py tests/test_question_probe.py tests/test_counter_question_turn.py tests/test_consult_counter_question.py tests/test_counter_question_audit.py tests/test_counter_question_matrix.py
82 tests collected in 0.49s
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" tests/test_question_probe.py -q
19 passed, 1 warning in 0.53s
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" tests/test_counter_question_matrix.py -q
9 passed in 4.82s
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" tests/test_architecture_guard.py tests/test_docs_consistency.py tests/test_doc_code_anchors.py -k "not documented_numbers" -q
24 passed, 1 deselected in 1.76s
```

```console
$ ./.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic
All checks passed!
```

```console
$ ./.venv/Scripts/python.exe -m mypy app
Success: no issues found in 149 source files
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" -q --collect-only
1207 tests collected in 0.68s
```

```console
$ ./.venv/Scripts/python.exe -m pytest -o addopts="" -q
1 failed, 1200 passed, 6 skipped, 1 warning in 203.02s (0:03:23)
FAILED tests/test_docs_consistency.py::test_documented_numbers_match_the_code
```

唯一红项是文档数字守卫（口径 §12.11 的级联项）。`docs/fact-baseline.yaml` 的
`adr_count`（:177-185）**本身就是被守卫的数**：`CONTRIBUTING.md:170` 与
`docs/records/智能体平台设想.md:178` 都写「37 篇 ADR」，本轮新增 ADR-039 使其变为 38 篇，
因此这条断言**又多加一处漂移**——按 `openspec/AGENTS.md` §8.2 第 4 条（读到与既有文档冲突时
必须逐条点名登记、不在同轮改）与 `.qoder/rules/task-order.md` §4（:47-48「全量基线只跑一次」），
**本轮不改守卫清单、不改数字**，
登记进下面的待办表交 R12。

未跑门禁（显式登记，不得被读者当证据）：

- `evals/**` 全部模型读数：`scripts/llm_gap_probe.py` 的 11 句、`evals/support_set.json` 的
  62 条支持集、`evals/replay.py --self-check`（`evals/replay.py:508` 的 `--self-check` 开关）
  —— 按项目惯例由使用者侧本机模型复跑，本轮**未跑**（口径 §13.5 处置条、§12.11）。
- 需要 `TICKETPILOT_TEST_POSTGRES_URL` / Redis 的 PG 侧用例 —— 本轮**未跑**；
  上面那批读数是 **SQLite** 单机形态。
- `npm run build`（`web/`）—— **未跑**；本轮无前端改动，故该项也无预期位移。
- 本轮新增的四个 openspec 文件与一篇 ADR 的独立 reviewer 复审**已于 2026-10-29 进行**，结论「通过（附一般项）」，一般项已由后续返工轮处理；但复审只读代码与文档、未运行任何测试 / 构建 / lint，上述门禁未跑的状态不因复审而改变。

### 5. 冲突与差异登记（不折中、不同轮修复）

1. **行号漂移债（交 R12 统一重对）**：口径 §12 第 8 行仍引
   `app/agent/support_agent.py:1493-1508` 作为问句 gap 键集的锚点，盘上实物
   `:1493-1508` 现在是 `_attempted_candidates`（:1479-1503）的函数体与 `_outcome` 的开头；
   问句固定 gap 的真实位置是模块级 `_QUESTION_GAP`（:176-185）。同批漂移还有
   `:1491`→`:1647`（模式闸门）、`:1557`→`:1757`（采纳门）、`:1705`→`:1787`（非采纳 return）、
   `:1682-1689`→`:1734-1741`（四键搬运）、`_resume_pending` 与 `_guard_output` /
   `_clarify` 的多处。**口径文件本轮不改**，只在交付证据登记。
2. **口径与盘上事实的另一处不符**：`docs/下一步规划.md:45` 的 F-1 行仍写「未实施：
   `_consult_model` 仍只采纳 `REFUND_REQUEST`……`model_gate` 的 `question` 出口因此不问模型」，
   而 `question` 出口今天已经会问模型一次（`support_agent.py:1674-1676`）。
   该行是 §0.2 状态列，**本轮按边界不得改**，登记交 R12。
3. **简报要求的措辞查无实物**（按「代码 / 盘上实物优先」如实登记）：
   - 「`model_gate` docstring 措辞」——**确有实物且确已过期**：`support_agent.py:123-125`
     仍写「No model reading could be adopted for a read question, so the call would buy nothing」，
     与同一提交链落地的问句支矛盾。本轮不改代码，登记交后续代码轮。
   - 「口径 §3.1『复用轮不构成反问』」——**全仓 `.md` 检索无此字符串**；盘上实现这一语义的
     实物是 `pydantic_agent.py:199-202` 的注释与 `_consult_model:1772` 的
     `answer is not None` 判据（复用轮 :600 的 trace 两字段为 `None`）。本文件按实物表述，
     不引用那句不存在的原文。
   - 「takeover 残留措辞」——**检索无 F-1 遗留的 takeover 过期措辞**（F-1 五个提交未改任何
     takeover 相关文档，`app/agent/support_agent.py:534-535` 与 `:243-257` 的 takeover 表述
     与盘上行为一致）。属简报的转述，未核实为既有实物。
4. **「37 篇 ADR」的漂移**：见第 4 节；本轮只新增文件，不动 `docs/fact-baseline.yaml`
   与两处文档计数。

---

## 待办：需他处同步（本轮不改）

本轮只做 openspec 产物 + 一篇 ADR，**不修改任何既有文档**。下表逐条实地核对过：

| 位置（实测锚点） | 现状 | 需要改成的方向 | 归属 |
|---|---|---|---|
| `docs/guide/接口说明.md:88-95`、`:168-175` 两处 `"slots"` 示例 | 无 `counter_question_asked` 键 | 补该键（`false` 示例），与 `app/services/conversations.py:59` 对齐 | F-1 收尾（本轮不改） |
| `docs/guide/接口说明.md:152` `clarification` 字段行 | `reason` 只枚举 `MISSING_ORDER_ID` / `INVALID_AMOUNT` / `MISSING_REFUND_REASON` | 补 `COUNTER_QUESTION_INTENT_AMBIGUOUS`（既有键的新增值，零新键） | F-1 收尾 |
| `docs/guide/接口说明.md:176-181`、`:194-201` 两处 `clarification` 示例 | `expected_slot` / `resumable_intent` 均非空 | 补一条反问轮两值为 `null` 的示例（`app/schemas/support.py:57-58` 已放宽为可空） | F-1 收尾 |
| `docs/guide/接口说明.md:744-748` | 「`gate=question` 分支（问句根本不进模型）……该分支没有 `model_gap.model`，因为一次调用都没发生」 | 改为：问句支会问一次；不成形时 gap 键集不变但 `model_gap.model` 在成形分支出现（`support_agent.py:1701-1702`），成本行照落（§13.3） | F-1 收尾 |
| `docs/guide/证据索引.md:272`、`:277`、`:305` | 标题与表项均为「问句不进模型 / 走读路径，不问模型」 | 按问句支现状改写，并保留 rules 模式仍不进模型 | F-1 收尾 |
| `docs/spec/项目规格.md:1434-1441` | 「#### 三级门控：问句不进模型」及其表 | 同上；**改了本节事实数必须同批 `python scripts/build_manual.py` 并提交 docx**（`.qoder/rules/task-order.md:39`） | R12（含说明书重生成） |
| `docs/adr/ADR-024-三级模型闸门.md:25`、`:39` | `question → 不问`；且写明 question 档「所以没问模型」 | 追加指向 ADR-039 的「后续修订」指针（不改历史决策正文，只加指针） | F-1 收尾 |
| `docs/下一步规划.md:45` §0.2 F-1 行状态列 | 「未实施……`question` 出口因此不问模型」 | 状态列改写为已实施（**本轮按边界明确不改**） | R12 / 收尾轮 |
| `docs/records/智能体平台设想.md:1075-1079` ADR 索引表 + `:1100` 注 | 表尾停在 ADR-038，无 ADR-039 行 | 补 `| ADR-039 | 单轮反问——… | 已接受 |` 与同日注 | F-1 收尾 |
| `CONTRIBUTING.md:170`、`docs/records/智能体平台设想.md:178`、`docs/spec/项目规格.md:3800` | 「37 篇 ADR」；`docs/fact-baseline.yaml:177-185` 的 `adr_count` 事实 | 全部 re-cite 为 38 篇（缺 ADR-012），并同批刷新收集数 / 迁移数 / head / 文件数 | **R12**（守卫级联，本轮不得动） |
| 口径 §12 第 8 行等按行号引用的锚点 | `app/agent/support_agent.py:1493-1508` 等已漂移（见冲突登记 1） | 统一 re-cite 或改用符号名 | **R12** |
| `model_gate` docstring `app/agent/support_agent.py:123-125` | 仍写「问句不值得问模型」 | 改为三档现状（`question` 可问一次、只换一次反问、不采纳） | 后续代码轮（本轮不改代码） |
| §12.8 端到端增强 | 消费侧只钉了「闸不分叉」；生产侧三分支各自成用例 | 加端到端：`TestModel intent="NONE"` / 越界读法 / `FailingAgent` 三种回合并列断言 gap 与成本 | 待增强项（本轮不开测试轮） |
| `openspec/specs/` 基线 | 尚无 `counter-question` 基线条目 | 验收通过并归档时把本增量并入基线（`openspec/AGENTS.md` §7：归档前不得写基线） | 归档轮（R12 / 收尾） |
| F-1 测试文件间两套矛盾的跨文件导入惯例：`tests/test_counter_question_matrix.py:42-56` 直接导入其他测试文件的私有名，`tests/test_question_probe.py:32` 明文「跨文件不导入私有常量」 | 两套惯例并存，无守卫、无统一 | 由后续测试轮统一惯例（统一前不得宣称已解决） | 后续测试轮（本轮不统一、不动 `tests/**`） |
