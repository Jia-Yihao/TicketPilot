# ADR-003：退款资格由服务端确定性规则判定

- 状态：Accepted
- 日期：2026-10-23

## 背景

模型可以理解“我想退款”，但不能回答“这个订单此刻是否有资格退、最多能退多少”。让模型判断会引入幻觉、不可复现和越权。

## 决策

`RefundPolicy.check(identity, order_id, amount)` 检查：

1. 订单存在且属于当前租户与用户（或 support）
2. 订单状态为 `SHIPPED` / `DELIVERED`，未取消
3. 已签收订单在 30 天窗口内
4. 可退余额 > 0
5. 不存在 `REQUESTED` / `SUCCEEDED` / `UNKNOWN` 的退款记录
6. 申请金额 > 0 且不超过可退余额

返回结构化的 `eligible`、`reasons`、`amount_cents`、`max_refundable_cents`、`policy_version`。

模型不参与上述任何判断；它最多影响 `order_id` 与 `amount_cents` 的槽位抽取，且规则抽取优先。

## 后果

- 拒绝原因可枚举、可测试、可向用户解释
- 政策版本写入审批摘要，政策变化可追溯
- 规则变化需要发版；可通过配置化参数化窗口/阈值，但不允许模型动态改规则

## 备选方案

- 让 LLM 读取政策后判断：拒绝，不可审计
- 只在执行前查一次资格：拒绝，审批期间状态可能变化，执行前仍需复核
