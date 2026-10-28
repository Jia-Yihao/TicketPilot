## ADDED Requirements

### Requirement: 意图缺口有固定闭环

系统 SHALL 提供「抽样 → 去重 → 人工确认 → 进评测集 → 重跑指标 → 周期报告」的固定步骤，
每一步有命令与产物。缺口队列 SHALL 以规范化文本的 SHA-256 去重，租户标识只以哈希形式保存。

#### Scenario: 同一句话跨周期只入队一次
- **WHEN** 同一段文本（忽略大小写、空白与标点）在两个周期被抽到
- **THEN** 队列仍只有一条记录，观测次数加一，`last_cycle` 更新为最近周期
- **AND** 记录状态不被自动改写

#### Scenario: 跨租户同一句话合并
- **WHEN** 两个租户各抽到同一句话
- **THEN** 队列只有一条记录，`tenant_hashes` 有两个值
- **AND** 队列文件里不出现任何原始租户 ID

### Requirement: 只有人工确认才能把缺口变成金标

系统 SHALL 允许模型或其他自动流程写入 `proposals`，但 SHALL NOT 据此设置 `gold`、
关闭缺口或写入评测语料。关闭缺口 SHALL 要求显式人工确认、合法标签与非空说明。

#### Scenario: 模型提议不改变队列
- **WHEN** 一条提议被写入某条 OPEN 缺口
- **THEN** 缺口的 `status` 仍是 `OPEN`，`label` 仍为空
- **AND** 评测语料不增加用例

#### Scenario: 缺少人工确认被拒绝
- **WHEN** 调用关闭动作但没有人工确认标记或没有说明
- **THEN** 命令失败，队列逐字不变

### Requirement: 关闭的缺口进入同一份 held-out 语料

系统 SHALL 把人工确认关闭的缺口写入 `evals/intent/gap_closures.jsonl`，并由
`scripts/build_intent_set.py` 合并进现有 `authored_gap` 层；用例 SHALL 带
`label_source=human` 与缺口来源，不得另造一套分母。

#### Scenario: 语料只增加一条
- **WHEN** 关闭一条新缺口并重跑构建
- **THEN** `intent_set.json` 的总用例数加一，重复文本被拒绝
- **AND** `build_intent_set.py --check` 通过

### Requirement: 周期报告可复现

系统 SHALL 生成周期报告，列出新增用例数、`OPEN` / `CLOSED` / `IGNORED` 数量、
两层覆盖率与 `UNKNOWN` 的前后变化，以及旧门槛是否退化。同一输入 SHALL 产出逐字一致的报告。

#### Scenario: 报告分层
- **WHEN** 传入改写前后的指标 JSON
- **THEN** 报告分别给出 `external` 与 `authored_gap` 的覆盖率与 `false_unknown` 变化
- **AND** 不输出把两层相加的单一准确率

#### Scenario: 门槛退化可见
- **WHEN** 改写后的指标触发原有门槛失败
- **THEN** 报告标记 `gate_regression=true` 并列出失败项
