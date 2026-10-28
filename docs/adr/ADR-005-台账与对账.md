# ADR-005：副作用执行台账 + 幂等键 + 对账

- 状态：Accepted
- 日期：2026-10-23

## 背景

外部退款调用可能超时，但外部系统已经成功。此时直接重试会造成重复退款；直接宣称失败会误导用户。

## 决策

`tool_executions` 记录每次高风险写操作：

- 唯一 `(task_id, idempotency_key)`
- 状态：`PENDING` → `SENT` → `UNKNOWN` / `RECONCILED` / `FAILED` / `CANCELLED`
- 保存请求摘要、请求载荷、外部请求 ID、响应摘要、错误码
- 本地事务先提交 `SENT`，再调用外部服务，避免长事务持有数据库锁

结果不确定时：

- 台账 `UNKNOWN`，任务 `SUCCEEDED` + outcome `PENDING_CONFIRMATION`
- 对账按幂等键查询权威退款记录
- 找到成功记录 → 应用余额（条件更新，防重复扣减）→ Verifier → `VERIFIED_SUCCESS`
- 找不到 → 保持 `PENDING_CONFIRMATION`，不重复提交；审批保持 `APPROVED`，继续阻塞同一订单的新退款
- 明确失败 → `FAILED`
- 结果明确后审批置 `CONSUMED`，释放订单级开放审批额度

## 后果

- 超时不再等于失败
- 恢复逻辑可测试（`timeout_before` / `timeout_after` / `unknown`）
- 需要后台对账调度（当前未实现，只能手动调用服务方法）
- 第一版没有 Transactional Outbox；复杂多写场景需升级

## 备选方案

- 超时后自动重试：拒绝，重复退款风险
- 依赖数据库事务包住外部 HTTP：拒绝，长事务与连接占用
