# benchmark-harness 规格增量

## ADDED Requirements

### Requirement: bench 目录契约
系统 SHALL 提供仓内独立 `bench/` 目录承载性能与分布读数；每个 bench 模块 SHALL 由
`fixture` + `run.py` + `baseline.json` + `env_fingerprint` 四件构成，且 SHALL NOT 复制
业务逻辑——只能通过生产原接口取数（`SupportAgent` / `TaskRuntime` / 检索器 / Run API /
`POST /api/v1/support/messages`）。

#### Scenario: 模块形状可校验
- **WHEN** 遍历 `bench/*/` 目录
- **THEN** 每个模块目录内存在 `run.py`、`baseline.json` 与 fixture 文件
- **AND** 模块代码中对 `app.` 的 import 只出现在调用生产接口处（判定方式：任务 1 的
  显式命令反向核对「`app/**` 不 import `bench/**`」+ reviewer 读 diff；**当前无机器
  守卫**——见下方「机器核对范围」Requirement，不得写成守卫自动核对）

#### Scenario: 不复制业务逻辑
- **WHEN** 审查任一 bench 模块
- **THEN** 不存在与 `app/` 内实现同形的检索/退款/路由算法副本（只允许读配置与调接口；
  判定方式：reviewer 读 diff——`bench/` 不在两个文档守卫的扫描清单，该项**无机器核对**，
  口径文件 §2.5 已登记此缺口）

### Requirement: 环境指纹
每个 `baseline.json` SHALL 记录 `env_fingerprint`，至少含：Python 版本、关键 SDK 版本、
模型快照名与端点、温度=0、预热轮数、重复次数 ≥5、分位数池化方式；并 SHALL 额外记录
两项本项目定死项：(a) `reasoning_tokens_enabled`（本机 LM Studio reasoning tokens 关不掉、
单次约 8–14 秒的事实标注——含模型延迟的读数必须带此口径，不得当作推理引擎能力上限），
(b) `produced_by`（commit 标识 + 工作树脏状态摘要 + 生成命令 + 时刻），使任何读数可复核
产自哪棵树。

#### Scenario: 指纹缺项即无效
- **WHEN** `run.py` 写出的 `baseline.json` 缺少 `reasoning_tokens_enabled` 或 `produced_by` 任一项
- **THEN** 该模块的形状校验（`bench/validate.py`，pytest 不收集）判定 FAIL（用坏样例钉住）

### Requirement: 契约回归复用既有守卫
bench 回填前的契约回归 SHALL 复用既有两守卫——SQLite/PostgreSQL 逐位一致
（`evals/retrieval_metrics.py --database-url`）与 `evals/replay.py --self-check` 的
`REPLAY DIFF EMPTY`——SHALL NOT 另写第三套契约套件。

#### Scenario: 回填前双守卫先跑
- **WHEN** 任何 flag 化优化准备从默认关切换为默认开
- **THEN** 送审材料必须列出上述两条命令与本轮实际输出，缺一不得提交审查（判定方式：
  reviewer 阅读所附输出；**无机器守卫**强制这一条，不得写成守卫自动拦截）

### Requirement: 性能门禁分层与 flag 化
微基线（单模块、秒级）SHALL 可进 PR；端到端/过载与浸泡 SHALL 每日或手动，不阻塞 PR。
一切优化 SHALL flag 化且默认关或默认等价；mock/缓存 SHALL NOT 进入生产代码路径
（bench 侧产物也不得以任何 import 路径可达 `app/` 运行时）。

#### Scenario: 默认配置读数不变
- **WHEN** 优化 flag 全部保持默认且 `bench/` 存在
- **THEN** 既有 Golden / support / replay 读数逐字不变（回归测试钉住）

### Requirement: 机器核对范围（缺口如实登记）
`bench/` 相关口径（`.qoder/rules/benchmark-harness.md`、本变更包 specs 与 `bench/**`
内容）**不在**两个既有文档守卫的机器扫描范围内：符号/路径锚点守卫只认其登记的顶层目录
清单（不含 `bench`）且只扫 `docs/`；数字守卫的现算表也没有任何 bench 相关事实；
`.qoder/rules/**` 与 `openspec/**` 同样不在锚点守卫扫描范围。该缺口已由口径文件 §2.5
登记。本规格上述各 Scenario 的判定方式 SHALL 写为「`bench/validate.py`、各模块 `run.py`
的非零退出码、显式命令 + reviewer 读 diff」，SHALL NOT 表述为存在机器守卫核对。
