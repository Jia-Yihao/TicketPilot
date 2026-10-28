# bench 测量口径草案（`.qoder/rules/benchmark-harness.md` 建议稿）

> 状态：**已被 `.qoder/rules/benchmark-harness.md` 取代（编排方 2026-10-27 裁定）**；引用 bench 测量口径以该文件为准。
> 本稿是草案，已被 `.qoder/rules/benchmark-harness.md` 取代。以下两句原为「待批稿」的流程
> 叙述，现已失效，仅保留作历史定位：批准前 `bench/` 一律不建（分派单 P1 的开工前置）、
> 批准后由本波收尾轮（计划任务 12）落 `.qoder/rules/`——口径文件已落地，不存在「批准后落盘」
> 这一环节；`bench/` 的开工前置一律以口径文件 §0（生效时点与首次提交条件）为准。
> 本稿只规定口径，不含任何实现承诺；与 `docs/spec/项目规格.md` 冲突时先改本稿并说明理由。

## 1. 目录与模块形状

- 仓内独立 `bench/`，**不复制业务逻辑**：取数一律走生产原接口——
  `SupportAgent` / `TaskRuntime` / 检索器（`app/retrieval` 公开面）/ Run API /
  `POST /api/v1/support/messages`。
- 每模块一份，形状固定四件：`fixture`（构造输入）、`run.py`（执行并写读数）、
  `baseline.json`（结果）、`env_fingerprint`（写在 baseline.json 内，见 §3）。
- `bench/validate.py`：形状与指纹校验器，缺项即 FAIL；由显式命令与 CI 步骤运行。
- 严禁把 mock/缓存带进生产：`app/**` 不得 import `bench/**`（方向唯一）；
  bench 里造的假模型/假时钟只活在 `bench/**` 内。

## 2. 门禁归属（编排方定死三条 + 后果声明）

1. **pytest 不收 `bench/`**：`pyproject.toml` 的 `testpaths` 保持 `tests` 不变。
   后果：bench 的回归不进 `python -m pytest` 门禁；「不收」换来的好处是
   **不动 `test_collected` 这条事实基线**（把 bench 收进 pytest 会抬高收集数，触发
   README/PROJECT_SPEC/docx 多处数字同步与负验证 CASES 6 级联——守卫级联既有教训）。
   bench 的「测试」= `bench/validate.py` 自检 + 每模块 run.py 的非零退出码。
2. **ruff 必须覆盖 bench**，但**不改既有命令行**：现清单
   `ruff check app tests scripts evals alembic`（Makefile lint 目标与 CI 均为此句，
   且被文档锚定）。做法=Makefile 新增 `lint-bench: ruff check bench` + CI 新增独立
   步骤 `ruff check bench`；合并进主命令行属口径变更，须与文档同步一起做（收尾轮）。
   理由：不收 bench 等于写不受 lint 的代码；收进主行会即时打爆登记锚点。
3. **mypy 不覆盖 bench**：`python -m mypy app` 范围不变。理由：mypy 的既有口径是
   「生产代码 strict」；bench 是读数生产者，其类型问题污染的是读数不是产品；
   覆盖它会把 `mypy_files` 事实与门禁时长一起抬高，收益不成比例。
   无 make 环境的等价命令照仓库惯例写进 CONTRIBUTING（收尾轮同步）。

## 3. 环境指纹（定死项）

`env_fingerprint` 必含：Python 版本；关键 SDK 版本（pydantic-ai、sqlalchemy、fastapi）；
模型快照名与端点 URL；温度=0；预热轮数；重复次数 ≥5；分位数池化方式（p50/p95 算法）。
另加两条本项目定死项：

- `reasoning_tokens_enabled`：本机 LM Studio 的 reasoning tokens **关不掉**（温度 0
  也压不住，单次约 8–14 秒）。凡含模型调用的延迟读数必须带此字段与一句口径注记：
  「该延迟含推理 token 生成时间，不代表推理引擎能力上限」；不同 reasoning 设置下的
  读数**不得互相引用**。
- `produced_by`：commit 标识 + `git status --short` 摘要（脏树指纹）+ 生成命令全串 +
  时刻。理由（登记在案）：本仓库已吃过一次「A/B 产物没记 commit、无法复核产自哪棵树」
  的亏；无 produced_by 的读数一律按无效处理。

## 4. 回填前契约回归：复用现成两守卫，不另写第三套

任何 bench 驱动的优化回填前必须跑并附当日输出：

1. SQLite / PostgreSQL 逐位一致：`evals/retrieval_metrics.py --database-url ...`
   （双侧同读数）；
2. `evals/replay.py --self-check` 见 `REPLAY DIFF EMPTY`。

不新建第三套契约套件——又多一条要人工同步的口径，违反「同一概念一种叫法」。

## 5. 性能门禁分层

- 微基线（单模块、秒级、无外部端点）：进 PR（CI 步骤跑 `bench/validate.py` +
  选定秒级模块）。
- 端到端 / 过载 / 浸泡：每日或手动，不阻塞 PR；读数进 `docs/guide/证据索引.md` 时**必须标时点**，
  历史读数按日期保留、不静默改现状值（沿用既有口径）。
- 优化一律 flag 化，默认关或默认等价（等价=同一 Golden/support/replay 读数逐字不变）。
- 缓存失效键：索引 mtime+size、模型名、语料 hash；三者任一变化即缓存作废。

## 6. TP 项与 baseline 的分类（定死）

- **不需要 bench 即可验收的纯代码改动**：预算刹车（P9，测试+trace 判定）、
  事件读取有界化（P10，等价性测试判定）、seed 去重（P11，当日全量墙钟前后两读数
  对比即可，属 pytest 基础设施、不是产品性能面）、负验证进门禁（P2）、
  `pending_count` 处置（P3）。
- **必须先有 baseline 才谈优化**：收窄 vs 无信号 A/B（P5）、候选数 M 扫描（P6）、
  预取阈值校准（P7）、以及 **TP-3 若未来做审批队列查询形态改造**——其前置耗时
  baseline 即 P8；没有 P8 读数之前，任何「改查询形态」的提案不得立项。
  （注：复合索引版 TP-3 已被实测否掉——`app/repositories/approvals.py:138-145`
  的 ORDER BY 首键是 case 表达式，普通 btree 服务不了；口径详见变更包 proposal.md。）
