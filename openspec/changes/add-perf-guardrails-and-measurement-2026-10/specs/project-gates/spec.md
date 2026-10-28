# project-gates 规格增量

## ADDED Requirements

### Requirement: 负验证进门禁
CI SHALL 在每次 push/PR 运行 `python scripts/guard_negative_check.py`，其 6 条 CASES
（5 条架构守卫破坏 + 1 条文档数字漂移，`scripts/guard_negative_check.py:193-230`）
必须「六红一绿」成立；任一破坏注入未被对应守卫逮住，CI SHALL 失败。

#### Scenario: CI 步骤存在且失败可传播
- **WHEN** 查看 `.github/workflows/ci.yml` 的 test job 步骤
- **THEN** 存在独立的 guard_negative_check 步骤，未标 `continue-on-error`
- **AND** 该脚本非零退出即整个 job 失败（读 CI 配置判定）

#### Scenario: 本地等价命令可得
- **WHEN** 协作者无 make
- **THEN** 文档给出等价命令 `python scripts/guard_negative_check.py`（沿用仓库既有惯例）

### Requirement: 文档数字机器核对的覆盖面扩展
对 `docs/guide/评测说明.md`、`docs/guide/架构说明.md`、`docs/guide/接口说明.md`、`docs/adr/*` 中**可由仓库
现算得出**的数字，事实清单 SHALL 扩展登记：每项新事实必须有 COMPUTERS 现算函数
（`tests/test_docs_consistency.py:200-215`）、≥2 处逐字锚点（`:435-443`）、锚点整行唯一
且不含数字（`:267-276`、`:393-395`）、清单与现算函数双向无多余（`:380-388`）。
**时点读数**（p50/p95、耗时、按日期的通过率等历史值）SHALL NOT 进 COMPUTERS——它们
不可现算，进守卫会强迫改写历史读数，违反「时点读数必须标时点、历史读数按日期保留」的
既有口径；此类数字的核对方式改为结构核对，**范围以「本波新增/被改的读数小节」为限**：
同一 `###` 小节内须出现至少一个反引号包裹的生成命令串、且须出现至少一个 `YYYY-MM-DD`
日期串，缺一即结构核对失败；既有历史小节不在该检查范围内（守卫检查结构而非值）。

#### Scenario: 新增事实双向闭合
- **WHEN** 向 `docs/fact-baseline.yaml` 增加一条新事实
- **THEN** `test_manifest_only_points_at_documented_facts` 与
  `test_every_fact_has_at_least_two_claims` 全绿；打错现算名或只登记一处说法即失败

#### Scenario: 时点读数不被误钉
- **WHEN** 审查扩展后的 COMPUTERS
- **THEN** 不存在任何以时点性能读数（延迟分位、历史通过率）为值的现算函数

#### Scenario: 时点读数结构核对（仅本波小节）
- **WHEN** 本波新增或被改的读数小节出现在 `docs/guide/证据索引.md`
- **THEN** 同一 `###` 小节内须出现至少一个反引号包裹的生成命令串、且须出现至少一个
  `YYYY-MM-DD` 日期串，缺一即结构核对失败
- **AND** 既有历史小节不在该检查范围内（2026-10-27 现测：现文件 33 个 `###` 小节，仅四节标题带日期）

### Requirement: pending_count 死代码处置
`app/services/outbox.py` 的 `pending_count()`（全仓零调用方、不带租户边界）
SHALL **删除**；SHALL NOT 新增第三种积压读法。

#### Scenario: 处置后无悬空
- **WHEN** 该任务完成
- **THEN** 全仓 `pending_count` 检索结果为零命中；`GET /api/v1/outbox` 的 `total` 仍出自
  `repo.count_for(...)`（`app/api/v1/outbox.py:63-86`），读数不变
