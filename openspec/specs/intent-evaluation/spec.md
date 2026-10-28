# Intent Evaluation

## Purpose

用一份可追溯、人工标注的两层 held-out 语料评测意图识别：分层报告规则覆盖与读错，
用四桶代价矩阵计量误判，并只在规则缺口上测量模型。`evals/support_set.json` 的全量用例继续作为回归门槛。

## Requirements

### Requirement: 分层语料与可追溯来源

系统 SHALL 以两层语料评测意图识别：`external`（公开客服语料的真实问法）与
`authored_gap`（为外部语料缺席的意图补写的问法）。每一层单独报告，不得合并成一个准确率。

#### Scenario: 每条外部语料带来源
- **WHEN** 读取 `evals/intent/intent_set.json`
- **THEN** 每条 `layer=external` 的用例都带 `provenance.dataset`、`provenance.file`、`provenance.line`
- **AND** 数据集顶层记录来源 URL、许可证、源文件 SHA256 与样本 SHA256

#### Scenario: 两层不合并
- **WHEN** 运行 `python evals/intent_metrics.py`
- **THEN** 输出分别包含 `layers.external` 与 `layers.authored_gap`
- **AND** 不输出把两层相加的单一准确率作为结论

### Requirement: 人工标签与脱敏

系统 SHALL 只把人工对照意图定义写定的标签当作 gold；外部语料自带的标签与模型输出
一律不得写入 gold。所有入库文本 SHALL 在抽取时拒绝长数字串、URL 与邮箱。

#### Scenario: 标签合法
- **WHEN** 校验数据集
- **THEN** 每条 `gold` 属于 12 个目录意图或 `UNKNOWN`
- **AND** 每条用例带 `label_source: human`

#### Scenario: 采样拒绝可识别信息
- **WHEN** 从外部源抽取一条含 8 位以上连续数字或 URL 的用户消息
- **THEN** 该消息不得进入外部语料文件

### Requirement: 规则覆盖与错误区分

系统 SHALL 只通过公开入口 `classify_support` 测量规则读数，并按层、按意图分别报出
支撑数、非 `UNKNOWN` 覆盖率、正确率、`UNKNOWN` 数与读错数。

#### Scenario: UNKNOWN 与读错分开
- **WHEN** 某条用例的 gold 为 `REFUND_STATUS`，规则输出 `UNKNOWN`
- **THEN** 它计入 `unknown`，不计入 `wrong`
- **AND** 某条输出 `LOGISTICS_QUERY` 的用例才计入 `wrong`

#### Scenario: 每个意图有足够分母
- **WHEN** 汇总 `external` 与 `authored_gap` 两层
- **THEN** 12 个目录意图各至少有 10 条用例
- **AND** `SELF_ANALYTICS` 在 `external` 层缺席这一事实被显式报告，而不是补零或隐藏

### Requirement: 模型只在缺口上被测量

系统 SHALL 只在规则输出 `UNKNOWN` 的子集上调用模型，并单独报告意图正确率、
槽位补位正确率、`order_id` / 金额编造次数、调用次数、token 与延迟。
模型标签空间只有 6 个值；无法表示的 fine intent 计入 `out_of_label_space`，不得算作模型错误。

#### Scenario: 非缺口不问模型
- **WHEN** 规则已经给出非 `UNKNOWN` 的意图
- **THEN** 该用例不产生模型调用
- **AND** 报告写明模型调用数等于缺口子集大小（扣除标签空间外的用例）

### Requirement: 误判代价矩阵

系统 SHALL 把每条用例的结局归入且只归入一类：`correct`、`write_intent_error`、
`read_intent_error`、`false_unknown`。对写意图错判 SHALL 报出样本数、占比，
以及按目录槽位推导的 `clarifications`、`tasks`、`approvals` 与 `amount_exposed_cents`。

#### Scenario: 四类求和等于总数
- **WHEN** 汇总任一层的代价矩阵
- **THEN** 四类计数之和等于该层用例总数

#### Scenario: 金额错误单列
- **WHEN** 一条 gold 为只读意图的用例被读成 `REFUND_REQUEST` 且文本带金额
- **THEN** 该金额计入 `amount_exposed_cents`
- **AND** 报告中该数字与「已实际退款」明确区分

### Requirement: 非循环论证

系统 SHALL 拒绝把 `routing.py` 或 `intents.yaml` 的单个 cue 原样当作评测问法，
并报告外部语料中「包含 gold 意图任一 cue」的比例，作为循环程度的可见读数。

#### Scenario: 无逐字 cue
- **WHEN** 校验数据集
- **THEN** 没有任何一条 `text.strip()` 逐字等于任一 cue
- **AND** 外部层的 `cue_overlap_rate` 单独输出，不参与准确率计算

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
