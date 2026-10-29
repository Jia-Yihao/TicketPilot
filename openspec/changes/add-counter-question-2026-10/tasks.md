# F-1 单轮反问 — 任务清单

**本清单的勾选纪律**（`openspec/AGENTS.md` §5）：勾选表示「该里程碑的**实物**已在盘上且有可核对的
文件 + 提交」；表示「已提交审查」，**不表示**已通过独立 reviewer 复审。本轮（T7）是文档 / 证据轮，
因此实现里程碑按提交逐条勾实，**凡依赖 reviewer 结论、依赖未跑门禁、或按本轮边界明确不动的项一律留空**，
并在项后写明为什么留空。例外说明：本轮（T7）产物经独立 reviewer 复审**已于 2026-10-29 进行**并给出
「通过（附一般项）」结论（一般项已由后续返工轮处理），见 6.7；上述纪律本身不变。

## 1. 只读通路（T1–T3）

- [x] 1.1 反问文案 YAML + 封闭键 loader（`app/agent/counter_question.yaml` + `app/agent/counter_question.py`，未知顶层键加载期报错）（计划任务 1）
- [x] 1.2 单轮上限状态位与全量往返（`app/services/conversations.py:42/:59/:85`，**零迁移**）（计划任务 2）
- [x] 1.3 问句门只读问询通路 `IntentBrain.probe_question`（`app/agent/pydantic_agent.py:686-770`）与 `QuestionProbe` / 封闭拒绝原因词表（:447-481）（计划任务 3）
- [x] 1.4 该通路不调 `decide_adoption`、不产 `ModelDecision`、四计数键保持未判定（行号证据：全文件 `decide_adoption` 仅 :580/:637、`replace(` 仅 :661）（计划任务 3）

## 2. 问句档反问（T4）

- [x] 2.1 `_consult_model` 问句支六道判定（上限 :1659-1662 → 开关 :1663-1666 → 入口守卫 :1667-1673 → 探针 :1674-1676 → 拒绝 :1677-1681 → 缺标签 :1691-1696 → 成形 :1697-1714）（计划任务 4）
- [x] 2.2 共用渲染器 `_counter_question_draft`（:188-240，`UNKNOWN` 跳过、`max_options` 截断、任一缺显示名整句不问）与 `CounterQuestion` 只读包装（:243-257）（计划任务 4）
- [x] 2.3 `handle` 消费点：反问只在「有草稿且规则读数为 `UNKNOWN`」时成为回复（:563-583）（计划任务 4）
- [x] 2.4 公开契约可空性：`SupportClarification.expected_slot` / `resumable_intent` 改为 `str | None`（`app/schemas/support.py:57-58`）（计划任务 4）
- [x] 2.5 成本如实落账：`tests/test_llm_gap.py` 的 `len(refunds) + 2` 与 `tests/test_security_model_cross.py` 的 `model_calls`（口径 §13.3 预告项，判据形状未动）（计划任务 4）

## 3. `consult` 档反问（T4b，路线 γ）

- [x] 3.1 `ModelTrace.answer` / `candidate_ids` 两个带内、永不序列化字段（:203-204），仅 `plan` 新路径那一次 `replace` 写入（:661-682；台账复用 :600 与 rules 早退 :603-604 留 `None`）（计划任务 5）
- [x] 3.2 非采纳 return 点之前的形成段 `support_agent.py:1758-1786`，触发判据照抄 `decide_adoption` 的 `redirect_rejected`，叠加开关 / 上限位 / `UNKNOWN` 三条前置（计划任务 5）
- [x] 3.3 采纳 return 点（:1788-1798）与四键搬运（:1734-1741）逐字不变（计划任务 5）

## 4. 审计与一键回退（T5）

- [x] 4.1 `counter_question.asked` 落**消费点**（`support_agent.py:571-583`，`detail` 恰三键）（计划任务 6）
- [x] 4.2 开关 `counter_question_enabled`（`app/core/config.py:122`）与 `.env.example:203-209`（计划任务 6）
- [x] 4.3 回退等价范围的两处分寸：问句支连探针成本都不付（:1663-1666）、`consult` 支不关 `plan` 调用本身（:1769）（计划任务 6）
- [x] 4.4 置位随回合末那一次 `save_slots`：`counter_question_asked=carried... or counter_asked`（:721-723 → :731），被拦轮次不清 carried 位（计划任务 6）

## 5. 矩阵与门禁（T6）

- [x] 5.1 `tests/test_counter_question_matrix.py`（新建，收集 9 条）钉 §12.1 / §12.8 / §12.10 三格（计划任务 7）
- [x] 5.2 六个 F-1 测试文件同批实跑：`82 passed, 1 warning in 15.56s`（命令与逐字输出见 `plan.md` 交付证据第 4 节）（计划任务 7）
- [x] 5.3 三份守卫检查实跑（`-k "not documented_numbers"`）：`24 passed, 1 deselected`；`ruff` 全绿；`mypy app` 149 文件零错误（计划任务 7）
- [ ] 5.4 全量 `pytest -q` 的唯一红项 `test_documented_numbers_match_the_code` 转绿——**留空理由**：文档数字 re-cite 属 R12 收尾轮，本轮按边界不得改守卫清单与任何文档数字（计划任务 7）
- [ ] 5.5 口径 §12.1 的探针 11 句**实测**、62 条支持集、`evals/replay.py --self-check`、PG / Redis 侧用例、`npm run build`——**留空理由**：本轮与实现轮均未运行（口径 §12.11 / §13.5 的处置是「由使用者侧本机模型复跑并如实登记未跑」）（计划任务 7）
- [ ] 5.6 §12.8 的端到端增强（`TestModel intent="NONE"` / 越界读法 / `FailingAgent` 三回合并列）——**留空理由**：本轮不开测试轮，已登记为待增强项（计划任务 7）

## 6. 交付轮产物（T7）

- [x] 6.1 `openspec/changes/add-counter-question-2026-10/proposal.md`（为什么 / 改什么 / 不改什么 / 影响面 / 依赖与顺序）（计划任务 7）
- [x] 6.2 `specs/counter-question/spec.md`（11 条 Requirement，含并发不设上界、消费侧单闸的分层事实、append 叠加下反问仍可见三条如实登记）（计划任务 7）
- [x] 6.3 `plan.md`（任务 1–7 的「改了什么 + 证据 = 文件 + 提交」、范围声明、前后工作树快照、实跑命令逐字输出、冲突登记、待办表）（计划任务 7）
- [x] 6.4 `docs/adr/ADR-039-单轮反问.md`（决策 + 代价 A/B/C + 被否决的方案 + 已知缺口 + 「本轮是文档轮，未复跑」的证据节）（计划任务 7）
- [x] 6.5 代价 A、B、C 与未跑门禁清单按简报硬要求写满且**未使用任何并发量化措辞**（代价 B 同时登记「不得写成反问后必静默」）（计划任务 7）
- [x] 6.6 口径与盘上实物的差异登记（§12 第 8 行锚点漂移、`docs/下一步规划.md:45` 状态列过期、简报要求的两处措辞查无实物），**未同轮修改口径文件**（计划任务 7）
- [x] 6.7 本轮产物经独立 reviewer 复审并已给出结论：2026-10-29 复审完成，结论「通过（附一般项）」，5 项一般项（内部数字 / 引用漂移）已由后续返工轮处理；复审为只读检索与逐行阅读，reviewer 未运行任何测试 / 构建 / lint；按 `openspec/AGENTS.md` §5，勾选在 reviewer 结论之后执行，归档仍属收尾轮 / R12（计划任务 7）
- [ ] 6.8 `docs/guide/接口说明.md` 等既有文档的同步、`docs/下一步规划.md` §0.2 状态列改写、ADR 索引补 ADR-039、`adr_count` 与其余数字 re-cite、说明书重生成、本目录归档与基线合并——**留空理由**：逐条已登记在 `plan.md`「待办：需他处同步（本轮不改）」，分属 F-1 收尾轮与 R12（计划任务 7）

## 7. 不在本轮范围

- **不改任何生产代码与任何测试文件**（本轮唯一写入面是 5 个新建文档文件）。
- **不改三份守卫清单**：`docs/fact-baseline.yaml`、`docs/code-anchors.yaml`、`docs/architecture-guard.yaml`。
- **不改 `docs/下一步规划.md` §0.2 的状态列**、不改任何文档里的测试数字（收集数 / passed 数 / 迁移数 / head）——统一属 R12 收尾轮 re-cite。
- **不执行任何 git 写操作**（`add` / `commit` / `stash` / `checkout` / `reset` / `clean` / `push`），只读 git 命令除外。
- **不动 `.workbuddy/`**；**不归档**本 change 目录（归档属 R12 / 收尾轮）。
- **不把增量写进 `openspec/specs/` 基线**（`openspec/AGENTS.md` §7）。
- **不放开金额采纳、不改 `intents.yaml` 写路径行、不改 `routing.py` 受检 / 豁免集合、不改
  `evals/intent_metrics.py` 与 `evals/intent/**`**（口径 §7.3、§11）。
- **不引入锁 / 版本列 / 条件更新 / 新表 / 新迁移 / 二级开关**（口径 §5.1、§10.3）。
- **不为本项放宽任何升级阈值或信号覆盖面**（口径 §5.2）。
- **不重排、不重写 T1–T6 的实现与提交历史**；本目录只是把已发生的事实写成规格与决策记录。
