# Golden Set 与评测

本目录的离线评测与闭环工具：

| 套件 | 数据集 | 执行器 | 回答什么问题 |
|---|---|---|---|
| 任务路径 Golden Set | `evals/golden_set.json`（40 条） | `evals/run.py` | 任务链路、工具轨迹与业务语义对不对 |
| 只读路径支持集 | `evals/support_set.json`（61 条） | `evals/support_run.py` | 12 个意图的正常/边界/失败，多轮澄清能否续跑 |
| 检索评测基线 | 同上 17 条标注 | `evals/retrieval_metrics.py` | 政策检索命中得准不准、意图路由判得对不对 |
| 回放 | trace + 消息表 | `evals/replay.py` | 同一版本重放会不会改变结论或轨迹 |
| 分层抽样 | 在线 span / task / handoff | `scripts/sample_traces.py` | 哪些线上会话值得进入下一轮用例 |

检索评测的指标定义、当前基线与它暴露出的路由缺口见 [EVALS.md](EVALS.md)；回放与抽样的口径也在同一文档。

## 现有数据集

`evals/golden_set.json` 目前包含 **40 条任务路径用例**（12 normal / 12 boundary / 16 failure），覆盖 6 个任务意图：

| 意图 | normal | boundary | failure | 常用工具 |
|---|---|---|---|---|
| `ORDER_QUERY` | 1 | 2 | 3 | `get_order` |
| `LOGISTICS_QUERY` | 1 | 2 | 1 | `get_logistics` |
| `POLICY_QA` | 4 | 1 | 1 | `search_policy` |
| `TICKET_CREATE` | 2 | 0 | 0 | `create_ticket` |
| `REFUND_REQUEST` | 4 | 6 | 10 | `check_refund_eligibility` |
| `UNKNOWN` | 0 | 1 | 1 | 无（澄清） |

UNKNOWN 没有 normal 形态：无法归类的问题本身就是失败形态。其余 6 个只读意图（退款状态、退款资格、配送异常、商品咨询、自助统计、转人工）由 `evals/support_set.json` 覆盖；两个数据集的意图并集由 `tests/test_eval_datasets.py` 校验必须等于目录里的 12 条。

每条用例都带 `expected_outcome` 与 `expected_tool`，并可在 `expect` 里声明：

| 断言 | 失败信息定位 |
|---|---|
| HTTP 码 / 任务状态 / `result.outcome` | `result.outcome: expected X, got Y` |
| 回复包含 / 不包含 | `response.contains` / `response.not_contains` |
| 工具序列 | `trace.tools` |
| 工具参数子集 | `trace.<tool>.params.<key>` |
| 降级状态 | `trace.degradation` / `trace.degradation.status` |
| 回执字段来源 | `trace.receipt_from` |
| 证据版本 / 待审批金额 / 订单余额 | 业务字段名 |

> 两个退款用例刻意使用不同订单：订单级唯一约束禁止同一订单同时存在两个开放审批。若共用订单，第二条用例应得到 409，而不是继续等待审批。

执行：

```bash
python evals/run.py                 # 40 条任务路径（临时 SQLite）
python evals/support_run.py         # 61 条只读路径 + 多轮澄清
python evals/replay.py --self-check # 录制→回放，要求 REPLAY DIFF EMPTY
```

Postgres 复跑（需要独立库，避免污染演示数据）：

```bash
docker compose exec db createdb -U ticketpilot ticketpilot_eval
docker compose exec app alembic -x db_url=postgresql+psycopg://ticketpilot:ticketpilot-dev-only@db:5432/ticketpilot_eval upgrade head
docker compose exec app python evals/run.py --database-url postgresql+psycopg://ticketpilot:ticketpilot-dev-only@db:5432/ticketpilot_eval
```

执行器使用临时 SQLite（或显式指定的数据库）、真实种子数据和完整 HTTP 层；不 mock 业务规则。输出逐条 PASS/FAIL，任一失败退出码为 1。2026-10-25 实测 SQLite **40/40**、PostgreSQL 16.15 **40/40**（同一轮全量 pytest 458 passed / 0 skipped）。`tests/test_golden_runner.py` 另外要求执行器拒绝脏库（同一库重跑返回 2，`--reset` 后恢复）。

并发回归不在 Golden Set 中，而由 `tests/test_postgres_concurrency.py` 覆盖：设置 `TICKETPILOT_TEST_POSTGRES_URL` 后，在真实 PostgreSQL 上先验证同订单重复申请被订单级唯一索引挡下（第二个任务 `FAILED`、开放审批仍只有一条），再让两个审批人用栅栏并发审批**同一个任务**，断言仅一个成功、仅一条退款、余额只扣一次、订单版本只递增一次。该用例会创建并删除临时数据库，需显式启用，避免误连业务库。

## 当前用例分布

原计划扩展到 50 条，T3 实际做到 **101 条**：

| 套件 | 数量 | 分布 |
|---|---|---|
| 任务路径 Golden | 40 | 12 normal / 12 boundary / 16 failure；6 个任务意图 |
| 只读路径支持集 | 61 | 34 normal / 20 boundary / 7 failure；12 个意图；12 条多轮 |
| **合计** | **101** | 两个数据集的意图并集 = 目录里的 12 条 |

覆盖面包括：政策版本与有效期、跨租户隔离、订单/物流正常与异常、工单创建、退款资格各分支、金额边界、重复申请、自批与过期、未知意图澄清、注入与越权、输入超长、工具失败与降级链、多轮缺槽 / 乱序补槽 / 取消 / 超时。

## 指标

| 指标 | 定义 | 目标 |
|---|---|---|
| 任务完成率 | 期望任务终态匹配 | ≥ 95% |
| 业务结果准确率 | `result.outcome` 匹配 | ≥ 95% |
| 越权泄漏 | 跨租户数据出现在响应中 | 0 |
| 审批绕过 | 未审批执行 R3 工具 | 0 |
| 引用准确性 | 引用文档版本/有效期正确 | 100% |
| 重复副作用 | 同一幂等键产生两条业务记录 | 0 |
| 恢复一致性 | 超时后对账最终状态正确 | 100% |
| 澄清率 | 无法确定意图时不胡乱执行 | 100% |
| 延迟 | 任务 p50 / p95 | 本地记录，不作硬门槛 |
| 成本 | 每任务模型请求数（规则模式为 0） | 记录并设上限 |

## 实验计划

- **A. 规则路由 vs 规则+PydanticAI**：对比 UNKNOWN 澄清率、槽位补齐率、误路由率、模型请求数
- **B. 审批绑定**：篡改金额/订单版本，验证 100% 失效
- **C. 超时恢复**：`timeout_before` / `timeout_after` / `unknown` 的对账收敛
- **D. 检索版本**：过期 v2 永不进入引用
- **E. 并发幂等**：同一任务重复审批、同一 key 重复建单

## 诚实边界

- 101 条仍由项目自己编写，没有真实用户问法样本；新 bad case 从 `scripts/sample_traces.py` 的队列人工挑选后回灌
- 回放只覆盖可复现的只读会话，写路径与转人工会话不在回放范围内
- 没有真实用户数据，没有真实支付渠道
- 并发回归覆盖单进程双线程 + 真实 PostgreSQL 行锁；多进程/多实例部署尚未单独压测
- 任务创建仍无幂等键，重复 HTTP 请求会生成两个任务（P1，未修复）
- 未做统计显著性检验；样本量扩大后再谈置信区间
