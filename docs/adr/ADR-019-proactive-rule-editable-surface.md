# ADR-019：主动规则的文案与阈值开写接口，坏模板在写入时就被拒绝

- 状态：Accepted
- 日期：2026-10-24
- 关联：[ADR-014](ADR-014-customer-memory-proactive-consent.md)（客户记忆与主动确认，本 ADR 收的是它后果段里点名的「没有规则编辑 API」那一条）、[ADR-017](ADR-017-proactive-outbound-seam.md)（出站接缝：规则改的是**进正文的那句话**，投递面完全没动）、[ADR-016](ADR-016-channel-abstraction.md)（渠道抽象：同一份文案进不同渠道由渲染矩阵降级）、[ADR-004](ADR-004-approval-binding.md)（审批的四眼原则：规则文案**没有**这一层——这是本 ADR 明写的差异）、`docs/NEXT_STEPS.md` §3 T10（前置核实与实测）

## 背景

T5 落了 `proactive_rules` 表与三条种子规则，T7-4 让确认过的通知真的能走出去，但**规则本身到今天只有一个 `GET`**：

```text
文案 / 冷却 / 启停  →  改 app/seed.py  →  python scripts/seed.py 重灌
```

运营连"看"都得 curl，改一句话要走代码评审与重灌库。这条链上唯一还缺的就是配置面（`docs/NEXT_STEPS.md` 的 G10）。

**开这个口的真实风险不是"能不能改"，而是"改错了会怎样"。** 模板由 `emit()` 用 `Formatter().parse` 取字段、再用 `str.format_map` 渲染（`app/services/proactive.py:279-286`）。写方案时以为已经清楚的两种坏形态，实测之后**只有一种是对的**，另一种比想象的更糟：

| 提交的文案 | `Formatter().parse` 给的字段名 | 今天（无写入口时）的后果 |
|---|---|---|
| `{amount}` | `amount` | 正常渲染 |
| `{foo}` | `foo` | 不在白名单 → 这条规则**永远**被 `TEMPLATE_CONTEXT_MISSING` 抑制，客户收不到任何东西，也没有告警 |
| `{order_id.upper}` | `order_id.upper` | 同上（属性访问在 `format_map` 之前就被白名单挡掉了，所以**不是**渲染出 `<built-in method upper …>`；那个串是 `Formatter().format` 才会产生的，见下方「实测的两条更正」） |
| `{}` | **`''`（空串）**，不是 `None` | 被 `if field` 过滤掉 → 检查通过 → `format_map` 抛 **`ValueError: Format string contains positional fields`** → 一次 500 |
| `{ 不配对` | — | `parse` 阶段 `ValueError`（写入时同样会拒绝） |
| `{amount!r}` / `{amount:>10}` | `amount` | 转换与格式说明**允许**，渲染正常 |

两条实测更正，都是"读代码看不出来"的那种：

1. **`Formatter().parse` 会把字符串尾部的字面量报成一个字段名为 `None` 的元组**，所以 `None` 的含义是"这里没有占位符"，而不是"位置参数"。写第一版校验时按"`None` = 位置参数"来判，结果是**所有合法文案一律 422**——这个 bug 只有真跑应用才会暴露，正则与静态检查都是绿的。位置参数的真实标记是**空串**。
2. **同一个 `{}` 在不同调用路径抛不同异常**：`Formatter().format("{}")` 抛 `IndexError: tuple index out of range`，而生产路径 `template.format_map(...)` 抛 `ValueError`。所以"会炸"是确定的，"炸成什么"取决于调用方式——不能靠捕获单一异常类型来兜。

结论：这两类文案都不该被允许存进数据库。配置面的反馈速度必须是**保存时拒绝**，而且拒绝理由要带上是哪个字段。

## 决策

### 1. 写接口挂在事件类型上，语义是 upsert

```text
PUT /api/v1/memory/rules/{event_type}
```

`proactive_rules` 上已有 `UniqueConstraint(tenant_id, event_type)`（`app/models/memory.py:66-69`），所以"PUT 到事件类型上"天然是 upsert——**不需要新表、不需要迁移**（本轮业务表仍是 23 张，迁移仍是 12 条）。选 PUT 而不是 PATCH：目标是"把这个事件类型的规则调成这个样子"，客户端提交的是意图而不是补丁，而没提交的字段由 `exclude_unset` 保持不动。

并发写同一条由唯一约束兜住，输的一方拿 `409 RULE_ALREADY_EXISTS`。

### 2. 只有三个字段可写，`requires_confirmation` 明确禁止

`ProactiveRuleUpdateRequest`（`app/schemas/memory.py`）用 `extra="forbid"`，只声明 `template` / `enabled` / `cooldown_minutes`：

- **`requires_confirmation` 不是被忽略，是被拒绝**（422 `extra_forbidden`）。它是 ADR-014「客户先说要不要，消息才会出去」那条承诺的实现位；写接口允许关它，等于允许运营侧把同意闸拆成一个复选框。要做这个开关得先有独立的送达与撤回设计（ADR-017 的余项），所以本轮在契约层封死，界面上则把它渲染成只读徽标。
- 新建规则时它恒为 `True`（`_new_row()`），没有例外路径。
- 事件类型**不能从界面新增**：`ProactiveEventType` 只有三个值（`app/models/enums.py:138`），`emit()` 第一步就用 enum 拒绝未知类型。把事件类型做成可配置等于把"什么事实可以触发一条消息"交给配置面，而新增触发能力需要先有产生那个事件的人。界面只能在三个已知事件上改文案与阈值——这是「可配置」的边界，不是遗漏。

### 3. 模板校验发生在写入时，错误码分两类

`ProactiveService._template()`（`app/services/proactive.py`）按顺序拒：

| 情形 | HTTP | `code` | 消息里带什么 |
|---|---|---|---|
| 空 body / 没有可改字段 | 422 | `VALIDATION_ERROR` | 「No rule fields were supplied.」 |
| 未知事件类型 | 422 | `UNKNOWN_PROACTIVE_EVENT` | `details.allowed` 列出三个 |
| 引用白名单外的键（`{foo}` / `{order_id.upper}` / `{amount[0]}`） | 422 | `RULE_TEMPLATE_UNKNOWN_KEY` | 键名 + `details.allowed` 六个键 |
| `{}` 位置参数 | 422 | `RULE_TEMPLATE_INVALID` | 点名"positional placeholder" |
| 属性 / 下标形式的占位符 | 422 | `RULE_TEMPLATE_INVALID` | 带出错的原始串 |
| 花括号不配对 | 422 | `RULE_TEMPLATE_INVALID` | — |
| 超过 400 字 | 422 | `VALIDATION_ERROR` | 带上限 |
| `cooldown_minutes` 越界 / 类型不对（含 `true`） | 422 | `VALIDATION_ERROR` | 带上 `0..1440` |
| 并发撞唯一约束 | 409 | `RULE_ALREADY_EXISTS` | — |

两个数字是有意错开的：**写入上限 400，渲染截断线 500**。存一条 480 字的文案会在渲染时被 `[:500]` 静默切尾，句子断在半个词上——留余量比"允许但会截"更诚实。

错误消息**只回字段名与上限，不回整条文案**：文案里可能出现订单号与金额，而错误响应会被日志与前端错误面板原样带出去（这条纪律与 ADR-018「只报码不报值」、T2「422 不回显提交值」是同一条）。

任何一条拒绝都**不会留下半个行**：`get_session` 在 `finally` 里 `session.close()`，未提交的 `session.add()` 随之回滚。这一点没有靠推理，`test_rejected_create_persists_nothing` 断言了"新建被拒后该租户的规则数不变"。

### 4. 只读视图把"这条规则要 emit 带什么"摆到界面上

`ProactiveRuleView` 补两字段：`required_context`（模板实际引用的字段名，排序去重）与 `updated_at`。在此之前，运营要看规则只能看一串自己写的文案，**看不出这条规则需要哪些 context**——而缺字段的后果是静默抑制，只有真发一次事件撞出 `TEMPLATE_CONTEXT_MISSING` 才知道。把依赖显示出来，是把一类"没人知道的坏配置"变成"看一眼就知道"。

### 5. 审计存 `template_sha256`，不存文案

`proactive.rule.update` 的 detail 恰好六个键：

```json
{"event_type": "REFUND_SETTLED", "fields": ["cooldown_minutes", "template"],
 "enabled": true, "cooldown_minutes": 0,
 "template_sha256": "cf9fecd6…", "created": false}
```

足够回答"当时生效的是哪一版"，又不会让可能含订单号与金额的通知正文成倍落进 `audit_events`。用例直接断言请求体里放的标记串不在 detail 的序列化结果里。

### 6. 改动不回溯，且没有删除

- 已生成的通知（`notifications.body` 在 emit 时就落库）**不会被改写**；改模板只影响下一条通知。这是事实而不是卖点——界面上把这句话写在保存成功后的提示里，免得运营以为能批量订正已经发出去的话。
- **不提供 DELETE**：`notification.rule_id` 会指向已不存在的行，历史断裂；"不再发"由 `enabled=false` 表达（emit 抑制为 `NO_RULE`）。

## 后果

- 「换租户/换话术要改代码重跑种子」不再是事实：`GET /memory/rules` 之外多了 `PUT`，控制台加了第 10 页（通知规则，坐席/管理员可见，客户进页面得到与运营看板同款的拒绝面板）。非 `demo` 租户今天可以自己建第一条规则——实测 `acme` 用 PUT 建出 `rule_acme_refund_settled` 并成功 emit。
- **两种"静默的坏配置"从此在写入时就被拒绝**：永远被抑制（白名单外的键）与 emit 抛 `ValueError` 变 500（`{}`）。九种拒绝形状各有用例，且断言失败后规则行逐字不变。
- **没有双人复核。** 审批有四眼（ADR-004：审批人不得与申请人相同，`admin` 也没有自批例外），规则文案没有——一个人就能改所有客户收到的话。这是当前事实，文档不许写成"配置有复核"。界面顶部注释与本页都直接写明这一点。
- **没有版本表与一键回滚。** 审计里只有 sha256，能确认"是哪一版"，不能自动恢复；改错了要对着 sha256 人工打回去。要做版本表需要先决定"回滚是否也要复核"，那是另一期。
- **没有按租户灰度**（ADR-012 的空位，编号仍不回收）：`PUT` 一落就是全租户生效。
- 模板仍然只吃那六个 context 键，**客户档案里的任何东西都不进模板**——与 ADR-018 同一条纪律：客户自由文本不进客户收到的内容。想引用档案字段，需要先在事件侧把它变成结构化 context，那是新事件而不是新配置。
- 规则改动**不进 `evals/` 的 101 条评测集**：那里断言的是认知路径的终态与轨迹，出站文案由用例 + 真实 HTTP 守住（同 ADR-017 的口径）。`evals/` 里既不建规则也不改规则。
- 规模读数（本轮全部实跑）：OpenAPI `40 路径 / 43 操作` → **41 / 44**；`tests/test_proactive_rules.py` 21 条，全量 **447 passed / 3 skipped**（收集 450，33 个测试文件）；mypy strict **134** 个文件（本轮不加 `app/` 模块）；ruff 干净；Alembic 无漂移、无新表；`npm run build` 通过；九条门禁全绿；真实 HTTP **52 项断言 0 失败**（`127.0.0.1:8135` + 干净库 `.tmp/t10_live.db`）；浏览器走查在真实 DOM 上验过保存生效、三种拒绝的原文回显、以及客户身份拿不到表单（且**没有发出任何规则请求**）。
- 新增的 21 条用例**尚未在 PostgreSQL 上复跑**（本机装有 Docker，上一轮的 PostgreSQL 16.15 一次性容器就是用它跑出 `323 passed` 基线的；这一轮收尾时守护进程没有运行，没有再起容器——缺的是执行，不是环境）。这一轮尤其需要它：`409 RULE_ALREADY_EXISTS` 靠的是唯一约束，而 SQLite 与 Postgres 在冲突语义上正是最需要对照的地方。累计待 PG 复跑的是 T2 起新建的 9 个测试文件，当前共 **141 条**（含本轮新增的 21 条）。

> **修订（2026-10-25）**：上面这条「尚未在 PostgreSQL 上复跑」是 2026-10-24 收尾当天的读数。次日已用本机 Docker 起 PostgreSQL 16.15 临时容器补跑全量：**458 passed / 0 skipped（109.92 秒）**，T2 起新建的 10 个测试文件全部覆盖（§107 记的 141 条 + `test_llm_gap.py` 7 条），本轮 21 条中含 `409 RULE_ALREADY_EXISTS` 的用例——唯一约束在 Postgres 上的冲突语义已被真跑验证，不再是待办。同轮在独立 `eval_pg` 库上跑通 11 条迁移（head `c3b8e5a71d29`）与 `alembic check`（无漂移），Golden 40/40、支持集 61/61。§107 的规模读数也随之更新：SQLite 全量 **455 passed / 3 skipped**（收集 458，34 个测试文件，增量来自 T10 之后新增的 `test_llm_gap.py`）；OpenAPI 仍是 41 路径 / 44 操作。**仍未执行**：四通道检索指标脚本（`scripts/retrieval_metrics.py`）的输出本身没有在 PG 上产出过——PG 上跑的是 `test_retrieval_eval.py` 的断言，不是那张指标表。

## 备选方案

- **只做界面、不改后端（把 `seed.py` 的内容渲染成只读页）**：运营能"看"但不能改，且坏文案照样存得进去。否决——G10 的痛点是"改"。
- **`PATCH /rules/{id}` 而不是 `PUT /rules/{event_type}`**：语义更"补丁"，但 `id` 是种子命名的实现细节（`rule_demo_refund_settled`），让调用方先查 id 才能改；而且对没有规则的租户，PATCH 要变成 POST + 一套独立校验，两个面。否决。
- **允许 `requires_confirmation`，前端不给复选框**：接口能关、界面不能关，等于埋一个"知道 curl 的人能拆掉同意闸"的后门。否决——契约层拒绝才是拒绝。
- **写时不校验，发送时才校验（捕获 `ValueError` 转成抑制码）**：把 500 变成 `TEMPLATE_CONTEXT_MISSING`，看起来更稳，实际是把配置错误降级成"消息安静地不发了"——正是本 ADR 要消灭的那个状态。否决。
- **文案存整份进审计（"可追溯性最强"）**：`audit_events` 会成倍囤积可能含订单号与金额的正文，而它已经带着 `actor_id` 与时间戳，是一份会被长期保留的表。否决，改用 sha256。
- **把模板改成 Jinja2（支持条件与循环）**：表达力强，但白名单校验、注入面与"渲染失败会怎样"全部要重做，而且本项目所有通知都是"一句话"。在没有多语言、没有条件分支需求之前，`str.format_map` 的失败模式更可枚举。否决，需要条件文案时再开新 ADR。
