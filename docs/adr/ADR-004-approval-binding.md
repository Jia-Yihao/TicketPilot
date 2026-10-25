# ADR-004：审批与动作参数摘要、资源版本绑定

- 状态：Accepted
- 日期：2026-10-23（2026-10-23 修订：四眼审批与 `CONSUMED` 生命周期）

## 背景

仅记录“某任务已批准”不够：审批后订单金额、状态或可退余额可能变化；也可能出现“批准 100 元，执行 500 元”的偷换。

## 决策

审批创建时计算：

```text
digest = sha256(canonical_json({
  action, order_id, amount_cents, currency,
  resource_version, policy_version
}))
```

并保存 `action_payload`、`action_digest`、`resource_version`、`expires_at`。

执行前：

1. 审批必须属于该任务且状态为 `APPROVED`
2. 审批未过期（过期时标记 `EXPIRED` 并拒绝执行）
3. 重新计算当前订单版本对应的摘要，必须与 `action_digest` 一致
4. 资格复核必须仍通过

任一不满足：审批 `INVALID`、台账 `CANCELLED`、不调用外部服务，转人工复核。

四眼原则：审批人必须具备 `approver` 或 `admin` 角色，必须与任务同租户，且不得与申请人相同——**`admin` 也没有自批例外**。

审批生命周期：

```text
PENDING → APPROVED → CONSUMED
        ↘ REJECTED / EXPIRED / INVALID / CANCELLED
```

- `APPROVED` 表示“决定已作出”，但订单额度仍被占用，防止执行中再创建第二笔退款。
- 外部结果明确成功或明确失败后置 `CONSUMED`，释放订单级额度。
- 外部结果 `UNKNOWN` 时保持 `APPROVED`，直到对账收敛，避免“结果未知 + 新审批”造成重复退款。

同一订单同一动作最多一个 `PENDING`/`APPROVED` 审批，由数据库部分唯一索引保证；详见 ADR-008。

## 后果

- 审批绑定具体动作而非笼统任务
- 审批期间订单变化会安全失败，而不是执行过期决定
- 审批消费后不可复用；重试必须重新走资格校验与审批
- 需要审批人重新发起，用户体验略有代价

## 备选方案

- 仅绑定 task_id：拒绝，无法防参数偷换
- 审批时快照全部订单字段并执行快照：拒绝，会覆盖真实状态变化
