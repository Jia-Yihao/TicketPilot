# Intent Evaluation — 规格增量

## MODIFIED Requirements

### Requirement: 旧门槛原样保留

系统 SHALL 把 `evals/support_set.json` 的**全量用例**与 `evals/retrieval_metrics.py`
的 `intent_accuracy` 读数继续作为**回归门槛**：新语料的结论不得替换旧门槛，也不得与
旧读数合并。门槛集合的用条数 SHALL NOT 写进规格或清单——它由 `evals/support_set.json`
自己决定，文档只引用「全量用例」。

门槛集合 SHALL 只因**已批准并留下 ADR 的行为变化**而增删或改写用例；每一次增删都
必须在该变更的规格增量里点名，并说明它是新增覆盖还是对既有期望值的改写。

#### Scenario: 旧门槛仍可跑
- **WHEN** 运行 `python evals/retrieval_metrics.py`
- **THEN** 输出仍包含 `intent_accuracy`，分母是 `support_set.json` 的全部用例
- **AND** 新语料的数字只出现在 `evals/intent_metrics.py` 的输出中

#### Scenario: 行为变化改写门槛用例时留痕
- **WHEN** 一次已批准的行为变化（P11：澄清预算从 2 轮改为 3 轮）需要同步修改
  既有用例的期望值
- **THEN** 该用例按新行为改写期望，`docs/spec/项目规格.md` §19.2 的文档漂移表与对应 ADR 记录改写原因
- **AND** 历史轮次在旧条数上产出的读数保持原样，不回溯修改
