# ADR-008：订单级退款并发防护

- 状态：Accepted
- 日期：2026-10-23

## 背景

原实现的幂等键是 `(task_id, idempotency_key)`，只能防同一任务重复执行。两个针对同一订单的独立退款任务会各自生成审批与台账，并各自通过资格校验：

1. 任务 A、B 同时读到订单可退余额 129 元
2. 两个审批人并发批准
3. 两次外部退款都成功
4. 两次余额写入互相覆盖

2026-10-23 在 PostgreSQL 16 上实测复现：订单总额 129 元，产生两笔 129 元退款，两个任务都返回 `VERIFIED_SUCCESS`，最终余额被后写覆盖为 0。任务级幂等键无法解决跨任务竞争。

## 决策

采用数据库约束 + 行锁 + 条件更新，按以下顺序防护。

### 1. 订单级开放审批唯一约束

```sql
CREATE UNIQUE INDEX uq_approvals_open_per_resource
ON approvals (resource_type, resource_id, action_type)
WHERE status IN ('PENDING', 'APPROVED');
```

- 同一订单同一动作最多一个开放审批；`PENDING` 与 `APPROVED` 都占用额度。
- 仓库层先预检并抛出 `ConflictError("APPROVAL_ALREADY_OPEN")`；并发插入由数据库唯一索引兜底，捕获 `IntegrityError` 后转同样的冲突错误。
- SQLite 与 PostgreSQL 均支持部分唯一索引；迁移 `7d3a9c1e5b42`。

### 2. 执行时订单行锁与在途退款复查

- `get_order_for_update` 使用 `SELECT ... FOR UPDATE` 锁定订单行。
- 锁内查询同订单是否存在其他 `REQUESTED` / `SUCCEEDED` / `UNKNOWN` 退款；存在则作废当前审批、取消台账，返回 `ORDER_REFUND_IN_PROGRESS`，不调用外部服务。
- 订单锁在本地事务提交后释放，不会跨外部 HTTP 调用长期持有。

### 3. 台账原子 claim

```sql
UPDATE tool_executions
SET status = 'SENT', attempt = attempt + 1
WHERE id = :id AND status = 'PENDING'
RETURNING id;
```

只有 `RETURNING` 命中一行（即 `PENDING → SENT` 条件更新的赢家）可以调用外部服务；
输家回滚并转入对账，不会重复发送。

### 4. 余额条件更新

```sql
UPDATE orders
SET refundable_cents = refundable_cents - :amount,
    version = version + 1
WHERE id = :id AND refundable_cents >= :amount;
```

余额不足时更新失败，不会把余额写成负数，也不会覆盖并发赢家的结果。

### 5. 审批状态机增加 `CONSUMED`

```text
PENDING → APPROVED → CONSUMED
        ↘ REJECTED / EXPIRED / INVALID / CANCELLED
```

- 明确成功或明确失败后置 `CONSUMED`，释放订单级唯一约束，允许后续合法退款。
- 外部结果 `UNKNOWN` 时保持 `APPROVED`；对账找到成功记录后消费，找不到则继续阻塞第二笔退款。
- 审批决定本身使用行锁读取，跨租户与自批校验见 ADR-004。

## 后果

- 同一订单的并发退款在数据库层互斥，适用于多进程/多实例（共享同一数据库）。
- 第二笔退款在创建阶段得到 409，或在执行阶段安全失败并转人工复核；不会产生第二笔外部退款。
- 每订单串行化会降低同一订单的并发吞吐，这是可接受的业务代价。
- 结果未知期间订单被持续占用，必须依赖对账收敛；后台对账调度仍未实现（P1）。
- 仍不支持部分退款：已成功的退款会阻塞后续退款（P2，未在本 ADR 中解决）。
- 并发回归目前覆盖单进程双线程 + 真实 PostgreSQL；多实例压测尚未执行。

## 备选方案

- **Redis/应用层分布式锁**：拒绝。引入额外依赖，且锁与数据库事务无法原子绑定，进程崩溃后状态更难推理。
- **审批创建时预扣余额**：拒绝。审批过期、驳回、执行失败都需要补偿，复杂度高于“开放审批唯一 + 执行时条件扣减”。
- **只依赖任务幂等键**：拒绝。键的作用域是任务，跨任务竞争无法覆盖。
- **串行化隔离级别**：拒绝作为首选。影响面过大；当前约束 + 行锁已能精确表达业务不变量。

## 验证

- `tests/test_postgres_concurrency.py`：真实 PostgreSQL 上先断言同订单重复申请被部分唯一索引挡下（第二个任务 `FAILED`、开放审批仍只有一条），再让两个审批人并发审批**同一个任务**，断言恰好一个成功、另一个 `ConflictError`，仅一条退款，余额只扣一次，审批最终 `CONSUMED`，订单版本只递增一次。
- `evals/golden_set.json`：两个退款用例使用不同订单；当前 40 条在 SQLite 上 40/40，上一轮 10 条版本在 SQLite 与 PostgreSQL 均为 10/10。
- `alembic upgrade head` 在 SQLite 与 PostgreSQL 实跑，`alembic check` 无漂移。
