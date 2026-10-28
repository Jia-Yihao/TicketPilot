# F-8 移交包携带客户档案摘要 — 实施计划

**目标：** 把客户自己写下、已脱敏的档案摘要带进转人工的移交包（只进移交包），并在包里标明
来源与实际生效的长度上限；空时不占位，既有键逐字不变。

**架构：** 新增一个只读出口（记忆服务里的模块级函数，按租户与用户取档案摘要**一列**，不建行、
不改更新时间、不写审计）→ 移交包落库前在**唯一**的写点补摘要（先脱敏、后截断，截断用与本会话
要点摘要同一个上限、同一个函数）→ 移交包多出三个键（有摘要才有）→ 控制台案件详情区条件渲染。

**技术栈：** Python 3.13 + FastAPI + SQLAlchemy 2 + Pydantic v2（后端）；pytest；React 19 +
TypeScript + Vite（控制台）。无新增依赖、无新增迁移、无新增配置项。

**规格：** `.qoder/rules/handoff-profile-summary.md`（项目级口径，唯一契约来源）与
`openspec/changes/add-handoff-profile-summary-2026-10/specs/handoff-bundle/spec.md`
（行为契约增量）—— 执行者两份都要读。

## 全局约束

- 键名定死：`history_summary` / `history_summary_source` / `history_summary_max_chars`，
  不得另造别名；`transcript_digest`（本会话要点）与 `slots`（结构化槽位）是**别的**概念，
  不得互相替代，也不得从会话槽位 / 消息历史 / 任务记录 / 意图轨迹拼「这个客户之前说过什么」。
- `history_summary_source` 恒为字面量 `"customer_profiles.history_summary"`。
- 整形顺序固定：**先脱敏、后截断**。脱敏用与档案读出口同一套规则（幂等，读一次脱敏一次是安全的）；
  截断用移交服务里既有的 `_clip()`，上限 = `handoff_digest_max_chars`（默认 400）。
- `history_summary_max_chars` 记**实际生效的上限**，不是原文长度。
- 装配移交包的既有入口**保持纯函数**：不接会话、不查库，签名与形状不变，既有产出键逐字不变。
- 读取纪律：只 select 具体列，**不实例化 ORM 实体**、不建行、不碰 `updated_at`、不写审计。
- 读取的是**对话客户本人**的档案（会话调用方），不是坐席的档案。
- 硬边界：档案摘要**只**进移交包——不进客户回复、不进提示词、不进任务上下文、不进追踪属性；
  队列行摘要（triage 面）仍只带本会话要点；答案路径的覆盖层与四种既有效果**一个字都不改**。
- 截断上限取自既有配置 `handoff_digest_max_chars`，**不新增配置项**。
- 本轮**不改** `docs/**`、`README.md`、`CONTRIBUTING.md`（文档同步留到收口轮），**不提交、不推送**。
- 命令一律用 `./.venv/Scripts/python.exe`；`pytest` 要加 `-o addopts=`；Windows 控制台输出中文
  会乱码，读数前设 `PYTHONIOENCODING=utf-8` 并把输出重定向到 `.tmp/*.txt` 再读。
- `ruff` 必须用 `.venv` 里那个（0.17.2）；系统全局 ruff 版本更新，会误报 `UP045`。

## 审查重点

1. **空时不占位**：没有档案行、或摘要是空串 / 纯空白时，移交包里**没有**那三个键
   （不写 `null`、不写空串、不写 `0`）——落到任务 3 的测试。
2. **截断记的是上限不是长度**：超长时正文长度 == 上限且以 `…` 结尾，`history_summary_max_chars`
   == 上限；未超长时正文与原文逐字相同——落到任务 2 与任务 3 的测试。
3. **脱敏在截断之前**：把一段含手机号 / 邮箱 / 身份证 / 卡号的原文**直接写进档案行**
   （绕开写入路径的脱敏），包内必须检索不到原文——落到任务 2 的测试。
4. **不留痕、不建行**：一次转人工后审计里的档案类事件没有新增行，档案行的 `updated_at` 不变
   ——落到任务 2 与任务 3 的测试。
5. **租户与用户边界**：换一个用户、换一个租户都读不到别人的摘要——落到任务 3 的测试。
6. **既有键逐字不变**：没有档案时移交包的键集合与本轮之前**完全一致**；有档案时只多出三个键
   ——落到任务 1 与任务 3 的测试。

### 任务 1：规格与测试骨架（先红）

**文件：**
- 新建：`openspec/changes/add-handoff-profile-summary-2026-10/proposal.md`（已写）
- 新建：`openspec/changes/add-handoff-profile-summary-2026-10/specs/handoff-bundle/spec.md`（已写）
- 新建：`openspec/changes/add-handoff-profile-summary-2026-10/plan.md`（本文件）
- 新建：`openspec/changes/add-handoff-profile-summary-2026-10/tasks.md`
- 新建：`tests/test_handoff_profile_summary.py`

**接口：**
- 上游依赖：无（本任务只写测试与文档）。
- 对外产出：测试文件里用到的既有公开面——`POST /api/v1/support/messages`、`GET /api/v1/memory`、
  `PUT /api/v1/memory`、`GET /api/v1/handoffs/{event_id}`、`GET /api/v1/traces/{trace_id}`、
  `app.models.memory.CustomerProfile`、`app.models.audit.AuditEvent`。

- [x] 步骤 1：写失败测试（10 条验收逐条钉住）

```python
# tests/test_handoff_profile_summary.py
def test_a_profile_summary_reaches_the_handoff_bundle(client, app) -> None:
    _write_profile(client, ALICE, history_summary="上周问过发票抬头")
    body = _message(client, ALICE, "转人工")
    bundle = _case(client, body["handoff"]["event_id"])["bundle"]
    assert bundle["history_summary"] == "上周问过发票抬头"
    assert bundle["history_summary_source"] == "customer_profiles.history_summary"
    assert bundle["history_summary_max_chars"] == 400
```

其余 9 条见规格增量的 Scenario 列表，逐条对应：

| # | 测试函数 | 钉住的验收 |
|---|---|---|
| 1 | `test_a_profile_summary_reaches_the_handoff_bundle` | 有摘要 ⇒ 三个键都在 |
| 2 | `test_the_bundle_masks_the_summary_again` | 直接写库塞手机号 / 邮箱 / 身份证 / 卡号，包内检索不到原文 |
| 3 | `test_an_overlong_summary_is_clipped_to_the_digest_limit` | 长度 == 400、以 `…` 结尾、`history_summary_max_chars == 400` |
| 4 | `test_a_short_summary_is_not_clipped` | 未超长时正文与原文逐字相同 |
| 5 | `test_a_customer_without_a_profile_leaves_the_three_keys_out` | 无档案 ⇒ 三个键都不出现 |
| 6 | `test_a_blank_summary_leaves_the_three_keys_out` | 纯空白 ⇒ 三个键都不出现 |
| 7 | `test_the_summary_never_reaches_the_reply_the_trace_or_the_model_input` | 不进回复 / 不进追踪 / 不进模型输入面 |
| 8 | `test_reading_the_summary_leaves_no_audit_row_and_no_profile_write` | 审计无新增 `memory.%`、档案 `updated_at` 不变 |
| 9 | `test_another_user_or_another_tenant_cannot_read_the_summary` | 换用户 / 换租户都读不到 |
| 10 | `test_the_existing_bundle_keys_are_unchanged` | 无档案时键集合逐字不变；有档案时只多三个键 |

- [x] 步骤 2：运行，确认按预期失败

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py > .tmp/f8_red.txt 2>&1`
Expected: FAIL —— 10 条全部失败，失败原因是移交包里**没有** `history_summary` 键
（`KeyError` / `assert ... in bundle`），不是拼写错、不是导入错。

- [x] 步骤 3：确认失败原因只在「缺实现」

Expected: 失败信息里出现的是 `bundle["history_summary"]` 取不到 / `KeyError: 'history_summary'`；
若出现 `ImportError` 或 `AttributeError` 指向本仓模块名，先修测试本身再继续。

### 任务 2：后端读取出口与整形

**文件：**
- 修改：`app/services/memory.py`（新增**模块级**函数 + `__all__`）
- 测试：`tests/test_handoff_profile_summary.py`（本任务新增的用例：第 2、3、4 条 + 直接写库的辅助）

**接口：**
- 上游依赖：`app.core.security.Identity`、`app.models.memory.CustomerProfile`、
  `app.observability.redaction.redact`（三者仓内已有）。
- 对外产出：

```python
# app/services/memory.py（模块级函数，不进 MemoryService 类）
def load_history_summary(session: Session, identity: Identity) -> str | None:
    """按租户与用户逐值相等取该客户档案的摘要列；没有就返回 None。"""
```

语义：只 select `CustomerProfile.history_summary` **一列**（不实例化实体）；返回值**已脱敏**；
无档案行 / 值为空或纯空白 ⇒ 返回 `None`；**不写审计、不建行、不碰 `updated_at`、不 commit**。

- [x] 步骤 1：写失败测试（第 2、3、4 条）

```python
def test_the_bundle_masks_the_summary_again(app, client) -> None:
    _write_raw_summary(app, ALICE, "手机 13800138000 邮箱 a@b.com 卡号 6222021234567890123")
    body = _message(client, ALICE, "转人工")
    bundle = _case(client, body["handoff"]["event_id"])["bundle"]
    for raw in ("13800138000", "a@b.com", "6222021234567890123"):
        assert raw not in bundle["history_summary"]
    assert "***" in bundle["history_summary"]
```

`_write_raw_summary` 用 `app.state.session_factory()` 直接 `UPDATE customer_profiles`，
绕开写入路径自带的脱敏，用来证明**包这一侧**真的又脱敏了一次。

- [x] 步骤 2：运行，确认按预期失败

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py -k "mask or clip or short" > .tmp/f8_red2.txt 2>&1`
Expected: FAIL —— 移交包里取不到 `history_summary`。

- [x] 步骤 3：在 `app/services/memory.py` 实现 `load_history_summary(session, identity) -> str | None`

```python
def load_history_summary(session: Session, identity: Identity) -> str | None:
    row = session.execute(
        select(CustomerProfile.history_summary).where(
            CustomerProfile.tenant_id == identity.tenant_id,
            CustomerProfile.user_id == identity.user_id,
        )
    ).one_or_none()
    if row is None:
        return None
    summary = redact(row.history_summary or "")
    if not summary.strip():
        return None
    return summary
```

`__all__` 增补 `"load_history_summary"`。

- [x] 步骤 4：运行，确认通过

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py -k "mask or clip or short" > .tmp/f8_green2.txt 2>&1`
Expected: 仍 FAIL（整形发生在落库前，任务 3 才接上），但失败点从「键不存在」变成
「移交包还没接上读取」——即第 2、3、4 条要等任务 3 才转绿。

### 任务 3：移交包字段与落库接线

**文件：**
- 修改：`app/agent/handoff.py`（`HandoffBundle` 三个字段 + `to_dict()` 条件写入）
- 修改：`app/services/handoff.py`（`HandoffService.escalate` 落库前补摘要）
- 测试：`tests/test_handoff_profile_summary.py`

**接口：**
- 上游依赖：任务 2 的 `load_history_summary(session, identity) -> str | None`；
  移交服务里既有的 `_clip(text, limit) -> str` 与 `PolicyEngine.escalation.digest_max_chars`。
- 对外产出：`HandoffBundle` 新增三个字段（默认值 `None` / `""` / `0`），
  `to_dict()` 在 `history_summary` 非空时写入三个键。

- [x] 步骤 1：写失败测试（第 1、3、4、5、6、8、9、10 条）

见任务 1 的表格；第 3 条断言 `len(bundle["history_summary"]) == 400` 且 `endswith("…")`，
第 8 条断言审计里的 `memory.%` 事件数量与档案 `updated_at` 都不变。

- [x] 步骤 2：运行，确认按预期失败

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py > .tmp/f8_red3.txt 2>&1`
Expected: FAIL —— 移交包里没有那三个键。

- [x] 步骤 3：在 `app/agent/handoff.py` 加三个字段与条件写入

```python
# HandoffBundle 末尾（全部带默认值）
    history_summary: str | None = None
    history_summary_source: str = ""
    history_summary_max_chars: int = 0

# to_dict()：先建 payload，再按需补三个键
    payload: dict[str, Any] = { ...既有键逐字不动... }
    if self.history_summary:
        payload["history_summary"] = self.history_summary
        payload["history_summary_source"] = self.history_summary_source
        payload["history_summary_max_chars"] = self.history_summary_max_chars
    return payload
```

`HandoffBundle` 是 `@dataclass(frozen=True, slots=True)`，**保持不可变**；新包用
`dataclasses.replace()` 产出。

- [x] 步骤 4：在 `app/services/handoff.py` 的 `HandoffService.escalate` 里补摘要

在构造 `HandoffEvent` **之前**：

```python
bundle = self._with_history_summary(identity, bundle)
```

并新增私有方法（模块级常量 `_HISTORY_SUMMARY_SOURCE = "customer_profiles.history_summary"`）：

```python
def _with_history_summary(self, identity: Identity, bundle: HandoffBundle) -> HandoffBundle:
    raw = load_history_summary(self.session, identity)
    if raw is None:
        return bundle
    limit = self.policy.escalation.digest_max_chars
    summary = _clip(redact(raw), limit)
    if not summary:
        return bundle
    return replace(
        bundle,
        history_summary=summary,
        history_summary_source=_HISTORY_SUMMARY_SOURCE,
        history_summary_max_chars=limit,
    )
```

新增 import：`from dataclasses import replace`、`from app.observability.redaction import redact`、
`from app.services.memory import load_history_summary`。

- [x] 步骤 5：运行，确认通过

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py > .tmp/f8_green3.txt 2>&1`
Expected: PASS —— 10 条全绿。

- [x] 步骤 6：跑不退化回归

Run: `./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff.py tests/test_personalization.py tests/test_memory.py > .tmp/f8_regress.txt 2>&1`
Expected: PASS —— 既有移交包键、答案路径的四种效果、档案读写逐字不变。

### 任务 4：控制台条件渲染

**文件：**
- 修改：`web/src/types.ts`（`HandoffBundle` 接口三个**可选**字段）
- 修改：`web/src/pages/AgentWorkbench.tsx`（案件详情区条件渲染）

**接口：**
- 上游依赖：任务 3 产出的三个 JSON 键。
- 对外产出：类型 `HandoffBundle` 的三个可选字段 `history_summary?: string` /
  `history_summary_source?: string` / `history_summary_max_chars?: number`。

- [x] 步骤 1：改类型（`web/src/types.ts`）

三个字段全部**可选**（`?`），因为包里没有摘要时它们**不出现**。

- [x] 步骤 2：改渲染（`web/src/pages/AgentWorkbench.tsx`）

在案件详情区（`bundle.suggested_next_step` 那一段之后、`case-columns` 之前）条件渲染：
`bundle.history_summary` 为空 ⇒ **不渲染、不显示占位符**；非空 ⇒ 渲染正文，来源标注与长度上限
**只在对应键存在时**显示。前端**不得**自带任何「这个客户之前说过什么」的事实副本，
也不得把摘要拼进任何输入框。

- [x] 步骤 3：构建验证

Run: `cd web && npm run build > ../.tmp/f8_web_build.txt 2>&1`
Expected: PASS —— `tsc` 与 `vite build` 无类型错误、无未使用变量告警。

### 任务 5：全量门禁与读数

**文件：** 无新增（只跑命令 + 报告读数）

- [x] 步骤 1：定向测试

```bash
./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_handoff_profile_summary.py \
  tests/test_handoff.py tests/test_personalization.py tests/test_memory.py > .tmp/f8_pytest.txt 2>&1
```

- [x] 步骤 2：静态检查

```bash
./.venv/Scripts/python.exe -m ruff check app tests scripts evals alembic > .tmp/f8_ruff.txt 2>&1
./.venv/Scripts/python.exe -m mypy app > .tmp/f8_mypy.txt 2>&1
```

- [x] 步骤 3：架构与文档守卫

```bash
./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_architecture_guard.py \
  tests/test_doc_code_anchors.py > .tmp/f8_guards.txt 2>&1
```

- [x] 步骤 4：控制台构建

```bash
cd web && npm run build > ../.tmp/f8_web_build.txt 2>&1
```

- [x] 步骤 5：文档一致性守卫（**预期失败，只允许是收集用例数漂移**）

```bash
./.venv/Scripts/python.exe -m pytest -o addopts= tests/test_docs_consistency.py > .tmp/f8_docs.txt 2>&1
./.venv/Scripts/python.exe -m pytest -o addopts= --collect-only -q | tail -3
```

Expected: 只有 `test_collected` 一项失败（本轮新增测试函数改变了收集数，文档里的数字同步
属收口轮）；迁移条数 / head / 表数 / 文件数等其它事实项**必须全绿**，否则是真问题。

实际（本轮读数）：`test_documented_numbers_match_the_code` 失败，失败项**不止**收集数漂移，
另有迁移条数（14 → 15）、迁移 head（`a1c7e9d4b820` → `d9e4c7a15b82`）、`app/` 文件数
（146 → 147）、`tests/` 文件数（65 → 68）、收集用例数（885 → 936）五项。迁移与 `app/` 的
增量来自并发轮次的新迁移与新模块；`tests/` 与收集数的增量里，本轮贡献 1 个测试文件 / 15 条
用例。数字同步留待收口轮，本轮**不改** `docs/**`。

- [x] 步骤 6：把本轮真实读数贴进汇报

汇报只报**产出**（改了哪些文件、每处的关键代码与行号、每条命令的真实输出、本轮真实收集
用例数、文档一致性守卫的失败项判定），**不下质量结论**——是否通过由 reviewer 读代码判定。
