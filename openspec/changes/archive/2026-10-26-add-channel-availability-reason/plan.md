# 渠道入站可用性原因 — 实施计划

目标：给 `GET /api/v1/channels` 的每个渠道条目加一个**封闭四值**的 `availability`
字段，说明「这个渠道为什么点不动」；把「哪些渠道实现了入站」收成一份单一事实来源，
并用守卫钉住它与 `build_adapters()` / webhook 白名单 / `validate_production_safety()`
的关系；前端删掉自己那份硬编码入站表，改为显示后端给的原因。

架构：`app/schemas/channels.py`（声明四值契约 `ChannelAvailability`）
→ `app/channels/registry.py`（声明 `INBOUND_IMPLEMENTED` / `WEBHOOK_INBOUND`，与
`build_adapters` 同文件紧邻）→ `app/channels/availability.py`（纯判定函数，
按固定顺序四条）→ `app/channels/gateway.py`（`matrix()` 每个渠道多写一个键；
`_WEBHOOK_CHANNELS` 改为从 registry 导入，成员与 404 行为逐字不变）
→ `web/src/types.ts` + `web/src/pages/ChannelMatrix.tsx`（渲染后端原因，删除
`INBOUND_LABELS`）→ `docs/guide/接口说明.md` / `docs/下一步规划.md`（口径同步）。

技术栈：FastAPI + Pydantic v2（`Literal` 值域，OpenAPI 自动出枚举）；pytest；
mypy strict；ruff（line-length 100）；React 19 + TypeScript（`tsc --noEmit`）。
规格增量：`openspec/changes/add-channel-availability-reason/specs/channel-inbound/spec.md`。
口径来源：`AGENTS.md`（根）、`openspec/AGENTS.md`、`openspec/project.md`、
`docs/下一步规划.md` F-6（第 1973-1989 行）；本轮**不新增** `.qoder/rules/` 文件
（字段与取值即全部口径，落在规格增量与本计划的谓词映射表内）。

## 谓词映射表（本变更的唯一口径表，reviewer 照此判定）

| 取值 | 判据（按此顺序，第一个成立即为结果） | 判定所读的源码事实 | 该渠道入站请求当下的可观察结果 |
|---|---|---|---|
| `not_implemented` | `channel not in INBOUND_IMPLEMENTED` | `app/channels/registry.py` 新增集合；`app/channels/gateway.py:125-126` 既有 404 分支 | 404「This channel has no inbound webhook.」 |
| `not_enabled` | `channel in INBOUND_IMPLEMENTED and adapter is None` | `app/channels/gateway.py:180`（与 `enabled` 同源）、`app/channels/gateway.py:49-51` | 404「Channel X is not enabled.」 |
| `webhook_secret_missing` | `adapter is not None and channel in WEBHOOK_INBOUND and not secret_is_configured(adapter.secret)` | `app/channels/base.py:218-221`（与 `verify_hmac` 同一判据）；`app/core/config.py:201-222`（prod 侧同一条件） | 503「This channel has no webhook secret configured.」 |
| `available` | 以上皆不成立 | `app/channels/gateway.py:41`（`build_adapters` 产物） | 200（材料齐备时） |

`INBOUND_IMPLEMENTED = frozenset({Channel.WEB, Channel.EMAIL, Channel.WECHAT, Channel.WECOM})`；
`WEBHOOK_INBOUND = INBOUND_IMPLEMENTED - {Channel.WEB}`。

默认开发配置（`channel_web_enabled=True`、`channel_email_enabled=False`、
`channel_im_enabled=False`，`app/core/config.py:144-146`）下的期望取值：
`WEB=available`、`EMAIL/WECHAT/WECOM=not_enabled`、`APP/WHATSAPP/VOICE/SMS=not_implemented`。

## 全局约束

- **行号会漂**：本仓库正被另一个进程并发修改，上表与下文行号只是本轮核对时的锚点。
  执行时**按符号名定位**（`build_adapters`、`ChannelGateway.matrix`、`_WEBHOOK_CHANNELS`、
  `verify_hmac`、`validate_production_safety`、`INBOUND_LABELS`），不要按行号盲改。
- **禁止整文件覆盖**：只做定点替换与新增；改完用 `git diff` 自查改动面是否越界。
  以下文件本轮改动必须为零：`tests/test_channels.py`、`docs/spec/项目规格.md`、
  `app/api/v1/channels.py`、`alembic/**`、`docs/architecture-guard.yaml`、
  `web/src/pages/` 下除 `ChannelMatrix.tsx` 之外的全部文件。
- 不动 webhook 的 404 行为与两处 404 的理由文本；不动 `enabled` 语义；
  不动既有能力字段的名字、类型与顺序；不新增配置项、依赖、迁移。
- 不新增第五个取值：身份映射缺失或写错仍由请求时的 403/503 表达
  （`app/channels/base.py:189-199`：未登记 403、映射串写错 503）。
- 四值字符串**只声明一次**：`ChannelAvailability` 定义在 `app/schemas/channels.py`，
  `app/channels/availability.py` 导入它。这是一条合法的新向下依赖
  （`docs/architecture-guard.yaml` tiers：channels 3 → schemas 1，规则 2 允许），
  **不得**为它往 `exceptions` 或 `tiers` 里加任何东西，也不得反过来让 schemas 依赖 channels。
- `webhook_secret_missing` 的判据读适配器实例上的 `secret`——即 `verify()` 实际用来
  验签的那同一个值；**不得**另写一份「渠道 → `Settings` 字段名」的映射表，
  那才是第二份真相。`getattr` 取值后必须用 `isinstance` 收窄，
  保证 `python -m mypy app`（strict）零新增告警。
- 每个实现任务先写会失败的测试并**亲眼看到它失败**（红 → 绿 → 重构），
  红因原文写进自检证据；本轮没跑出的红不许声称跑过。`tasks.md` 的勾选由
  organizer 在 reviewer 通过后执行。
- 命令一律用 `./.venv/Scripts/python.exe`；`pyproject.toml` 里 `addopts = "-q"`，
  要看清逐条结果必须加 `-o addopts=`；Windows 控制台中文会乱码，
  读数重定向到 `.tmp/*.txt` 并设 `PYTHONIOENCODING=utf-8`。
- 任何 `git add` / `git commit` / `git push` 都不在本轮范围内。
- **文档锚点守卫已存在**：`tests/test_doc_code_anchors.py` 会扫描 `docs/**/*.md` 里形如
  `` `path.py:NN` `` 的锚点（文件必须存在、行号区间不得越界），并按
  `docs/code-anchors.yaml` 用 AST 核对「文档点名的符号在代码里真的存在」。
  因此本轮在 `docs/` 里**不得新增任何 `path.py:NN` 行号锚点**（行号会漂，写进去就是
  一份会过期的事实）；要指向代码就写**符号名**。若确实点名的新符号值得登记，
  必须同轮在 `docs/code-anchors.yaml` 里加一条（`anchor` 不含数字），不得空指点名。
  本计划与本目录内的 `:NN` 锚点不受该守卫扫描（守卫只走 `docs/**`），
  所以下文行号同样只作定位锚点，不作为事实引用。

## 审查重点（reviewer 读代码与 diff 判定，不运行测试）

1. 四值判定顺序是否与谓词映射表逐字一致；8 个 `Channel` 枚举值
   （`app/models/enums.py:104-118`）是否每个都能且只能落进一个取值。
2. `INBOUND_IMPLEMENTED` 是否全仓只有一份；`_WEBHOOK_CHANNELS` 是否真的改为**派生**
   （不是复制四个成员）；两处 404 理由文本是否原样保留。
3. `webhook_secret_missing` 与 `verify_hmac` 读的是不是同一个 `secret` 对象；
   `validate_production_safety()` 那侧有没有被顺手复制成第二份判断（它的条件不许改）。
4. `enabled` 与 `availability` 的关系式是否成立且由测试钉住；
   `tests/test_channels.py` 的 diff 是否为空。
5. `ChannelCapabilityView` 里新字段是否必填（无默认、不接受 `None`），
   以免出现「没有原因的渠道」。
6. 前端是否删净 `INBOUND_LABELS`、四值文案是否齐全、未知取值是否原样兜底
   （不得显示成「已接入」「可入站」）。
7. `docs/guide/接口说明.md` 示例与取值表是否与新字段一致；`docs/spec/项目规格.md` 是否确实未改
   （未改则说明书不必重生成，`test_manual_build_record_matches_the_specification` 应保持绿）。

### 任务 1 — 入站实现集合与可用性纯函数

文件（实现）：

- `app/channels/registry.py`：在 `build_adapters` 上方新增两个集合
  `INBOUND_IMPLEMENTED: frozenset[Channel] = frozenset({Channel.WEB, Channel.EMAIL,
  Channel.WECHAT, Channel.WECOM})` 与
  `WEBHOOK_INBOUND: frozenset[Channel] = INBOUND_IMPLEMENTED - {Channel.WEB}`，
  并把两者加进 `__all__`（现为 `__all__ = ["build_adapters"]`，
  `app/channels/registry.py:10`）。紧邻注释必须补一句：这份集合回答的是
  「代码里有没有入站路径」，与配置开关无关；`build_adapters` 的三处开关分支不动。
- `app/channels/base.py`：在 `verify_hmac`（`app/channels/base.py:206-221`）旁新增
  `def secret_is_configured(secret: SecretStr | None) -> bool`，实现就是那句既有判空的反面
  （`secret is not None and bool(secret.get_secret_value())`）；把 `verify_hmac` 里的
  `if secret is None or not secret.get_secret_value():` 改为 `if not secret_is_configured(secret):`；
  `__all__` 补该符号。行为必须逐字等价，503 的抛点与消息不动。
- `app/channels/gateway.py`：本任务只允许把 `_WEBHOOK_CHANNELS`
  （`app/channels/gateway.py:22`）改为从 `registry` 派生
  （`from app.channels.registry import INBOUND_IMPLEMENTED, WEBHOOK_INBOUND` +
  `_WEBHOOK_CHANNELS = WEBHOOK_INBOUND`，或直接把 `accept_webhook` 里的引用换成
  `WEBHOOK_INBOUND`）；`app/channels/gateway.py:125-126` 的 404 分支一行不动。
- `app/channels/availability.py`（新建）：

```python
"""Why a channel can or cannot take inbound traffic. Observation only."""

from __future__ import annotations

from typing import get_args

from pydantic import SecretStr

from app.channels.base import ChannelAdapter, secret_is_configured
from app.channels.registry import INBOUND_IMPLEMENTED, WEBHOOK_INBOUND
from app.models.enums import Channel
from app.schemas.channels import ChannelAvailability

AVAILABILITY_VALUES: tuple[ChannelAvailability, ...] = get_args(ChannelAvailability)


def _webhook_secret(adapter: ChannelAdapter) -> SecretStr | None:
    secret = getattr(adapter, "secret", None)
    return secret if isinstance(secret, SecretStr) else None


def inbound_availability(
    channel: Channel, adapter: ChannelAdapter | None
) -> ChannelAvailability:
    if channel not in INBOUND_IMPLEMENTED:
        return "not_implemented"
    if adapter is None:
        return "not_enabled"
    if channel in WEBHOOK_INBOUND and not secret_is_configured(_webhook_secret(adapter)):
        return "webhook_secret_missing"
    return "available"
```

  模块与函数 docstring 必须写明：判定顺序不可调换；不读 `Settings`、不复制凭据判断；
  该值是观测，不改变任何请求结果。

文件（测试）：`tests/test_channel_availability.py`（新建）

- 先失败的测试：`test_availability_decision_order_matches_facts`。
  用最小桩件适配器（`types.SimpleNamespace` 或本地类，带 `secret` 属性）构组合表，
  断言至少覆盖：
  1. `APP` + `adapter=None` → `not_implemented`；
  2. `APP` + 一个非 `None` 的桩件 → 仍 `not_implemented`（证明顺序不可调换）；
  3. `EMAIL` + `None` → `not_enabled`；
  4. `EMAIL` + `secret=None` → `webhook_secret_missing`；
  5. `EMAIL` + `secret=SecretStr("")` → `webhook_secret_missing`；
  6. `EMAIL` + `secret=SecretStr("k")` → `available`；
  7. `WEB` + 真的 `WebChannelAdapter`（它没有 `secret` 属性，`app/channels/web.py:34-35`）
     → `available`，**绝不**返回 `webhook_secret_missing`；
  8. `WECHAT` / `WECOM` 与 `EMAIL` 同形；
  9. 所有返回值都属于 `AVAILABILITY_VALUES`，且 `len(AVAILABILITY_VALUES) == 4`。
- 同任务追加 `test_webhook_channels_are_derived_from_inbound_set`：
  `gateway._WEBHOOK_CHANNELS == WEBHOOK_INBOUND`，且
  `WEBHOOK_INBOUND == INBOUND_IMPLEMENTED - {Channel.WEB}`。
- 期望红因：`ModuleNotFoundError: No module named 'app.channels.availability'`
  （或 `AttributeError: module 'app.channels.registry' has no attribute 'INBOUND_IMPLEMENTED'`，
  取决于导入顺序）——必须把真实红因贴进证据。

验证：`tests/test_channel_availability.py` 全绿；`ruff check app tests`；
`python -m mypy app` 无新增错误；`tests/test_channels.py` 与
`tests/test_architecture_guard.py` 保持全绿。

### 任务 2 — 响应字段与矩阵输出

文件（实现）：

- `app/schemas/channels.py`：在 `ChannelCapabilityView`
  （`app/schemas/channels.py:19-31`）上方新增
  `ChannelAvailability = Literal["available", "webhook_secret_missing",
  "not_enabled", "not_implemented"]`（`from typing import Literal`），
  成员顺序一旦定下不得再改；视图内 `enabled: bool`（`app/schemas/channels.py:21`）
  **之后**插入 `availability: ChannelAvailability`——必填、无默认、不接受 `None`；
  其余十个字段的名字、类型与顺序不动；`ChannelMatrixResponse`
  （`app/schemas/channels.py:33-34`）不动。
- `app/channels/gateway.py::ChannelGateway.matrix`（`app/channels/gateway.py:172-192`）：
  字典字面量里 `"enabled"` 那项之后加
  `"availability": inbound_availability(channel, self.adapters.get(channel))`，
  顶部从 `app.channels.availability` 导入该函数。循环结构、其余键、
  `capabilities_for` 与 `counts` 用法不动；`app/api/v1/channels.py` 一行不动
  （端点仍是 `return {"channels": gateway.matrix()}`，`app/api/v1/channels.py:16-22`）。

文件（测试）：`tests/test_channel_availability.py` 追加

- 先失败的测试：`test_matrix_gives_every_channel_an_availability_value`
  （沿用 `tests/test_channels.py` 的 `client` fixture 与 `ALICE` 身份头）。
  断言：`GET /api/v1/channels` 为 200；8 条条目都带 `availability`；逐渠道期望值等于
  「默认开发配置下的期望取值」；出现的值集合恰为四值，无第五值、无缺项。
- `test_availability_agrees_with_enabled`：逐条断言
  `item["enabled"] == (item["availability"] in {"available", "webhook_secret_missing"})`。
- `test_enabled_channel_with_secret_reports_available`：用 `app_factory`
  造 `channel_email_enabled=True` + `channel_email_secret` 非空 +
  `channel_email_identity_map` 非空的配置，断言 `EMAIL` 为 `available`。
- 期望红因（两种都算有效红，必须写清实际发生哪一种）：字段缺失导致
  `KeyError: 'availability'`；或先改 schema 未改 `matrix()` 时 FastAPI 响应模型
  校验直接 500。

验证：`tests/test_channel_availability.py` 与 `tests/test_channels.py` 全绿；
`python -m mypy app`。

### 任务 3 — 三条防漂移守卫 + 503 行为钉

文件：只改 `tests/test_channel_availability.py`（追加一段）。
若守卫把实现问红，回到任务 1 收口；本任务**不得**改 `app/core/config.py`。

- `test_inbound_declared_set_equals_adapters_with_all_flags_on`（守卫 a）
  以全开关配置（三个 `channel_*_enabled=True` + 两个密钥非空 + 两个 identity map 非空）
  调 `build_adapters(settings)`，断言 `set(adapters) == set(INBOUND_IMPLEMENTED)`，
  并断言 `adapters[Channel.WECHAT] is not adapters[Channel.WECOM]`
  （同一 IM 开关造两个实例，集合仍记两个渠道）。
- `test_webhook_inbound_set_is_inbound_minus_web`（守卫 b）
  断言 `WEBHOOK_INBOUND == INBOUND_IMPLEMENTED - {Channel.WEB}` 且
  `Channel.WEB not in WEBHOOK_INBOUND`。
- `test_prod_safety_rejects_exactly_when_availability_is_webhook_secret_missing`
  （守卫 c，双向等价）对 `EMAIL` / `WECHAT` / `WECOM` 各两轮：
  轮 1（密钥空）：`pytest.raises(ValueError)` 包住
  `Settings(environment="prod", channel_email_enabled=True, channel_email_secret=None,
  channel_email_identity_map={"a@b.c": "t:u"})`（IM 侧改用 `channel_im_*`，
  并按 `app/core/config.py:172-200` 把数据库、身份、退款、docs 等其它 prod 前置项补齐到
  不抛错，以便错误消息唯一指向密钥项）；同一份渠道开关在非 prod 环境下断言
  `inbound_availability(channel, adapter)` 得 `webhook_secret_missing`；
  并断言抛出的消息含 `CHANNEL_EMAIL_SECRET` / `CHANNEL_IM_SECRET`。
  轮 2（密钥非空）：同一构造不再抛错，非 prod 下得 `available`。
- `test_webhook_returns_503_exactly_when_availability_is_webhook_secret_missing`
  （守卫 d，行为钉）`app_factory(channel_email_enabled=True, channel_email_secret=None,
  channel_email_identity_map={"a@b.c": "t:u"})`：矩阵里 `EMAIL` 为
  `webhook_secret_missing`；同一实例 POST `/api/v1/channels/email/webhook`
  （带签名头，走 `tests/test_channels.py` 里的 `_signed` 同类辅助）返回 **503** 且
  错误信封为 `DEPENDENCY_UNAVAILABLE`（`app/core/errors.py:79-82`）并说明缺密钥；对照组密钥非空 → `available` 且 200。
  这是该 503 语义在本仓库的**第一条**测试（现有 `tests/` 无 503 断言），
  必须写在 `tests/` 内，不得用临时脚本代替。
- 红-绿纪律：本任务的测试是「钉住既有事实」的守卫，很可能一次就绿。
  这种情况下**不得**伪造红因，必须在证据里如实写「未先红，因为它钉的是任务 1 已建立的
  等价关系」，并另附一次**破坏性自检**：临时把 `INBOUND_IMPLEMENTED` 少写一个成员
  （或临时把 `_WEBHOOK_CHANNELS` 改回硬编码四元组），跑守卫并贴出它变红的输出，
  然后**还原**该临时改动（`git diff` 必须显示还原后无残留）。守卫 c/d 各做一次同样的
  破坏性自检（把凭据判据换成从 `Settings` 另读一份，观察 c 或 d 是否变红）。

验证：`tests/test_channel_availability.py` 全绿；
`tests/test_architecture_guard.py` 全绿（确认新边 `channels -> schemas` 合法通过；
若被拦下，说明依赖方向写反了，必须改成 schemas 声明、channels 导入，
**不许**动 `docs/architecture-guard.yaml`）。

### 任务 4 — 不退化确认（零实现改动）

文件：不改任何 `app/` 或 `web/` 代码；只允许往
`tests/test_channel_availability.py` 追加
`test_existing_capability_assertions_unchanged`。

动作与断言：

1. `git diff --stat tests/test_channels.py` 必须为空；`git diff --name-only` 里
   不得出现任务边界之外的文件（对照「全局约束」的零改动清单逐条核对）。
2. 新断言：对 8 个渠道逐条比对矩阵里的能力字段
   （`supports_stream` / `supports_rich_card` / `supports_voice` / `max_length` /
   `max_cards` / `sentence_char_limit` / `formal` / `offers_human_handoff`）
   仍等于 `app/channels/render.py:79-88` 的 `CAPABILITIES` 读数——
   证明「加一个字段」没有挤压任何能力值。
3. 跑三条不退化基线：`tests/test_channels.py`（含
   `test_matrix_publishes_capabilities`，`tests/test_channels.py:437-449`，
   其中 `channels["APP"]["enabled"] is False` 那两行必须原样通过）、
   `tests/test_outbound.py`、`tests/test_retrieval_channels.py`。

验证：上述三个文件加 `tests/test_channel_availability.py` 全绿；
`git diff --stat` 输出贴进证据。

### 任务 5 — 控制台显示原因（与任务 6 并行，文件边界不重叠）

文件：

- `web/src/pages/ChannelMatrix.tsx`：
  1. **删除** `INBOUND_LABELS`（`web/src/pages/ChannelMatrix.tsx:21-26`）及其引用；
  2. 新增 `const AVAILABILITY_LABELS: Record<ChannelAvailability, string>`，四值齐全：
     `available` → 「可入站」；`webhook_secret_missing` → 「缺验签密钥（请求会 503）」；
     `not_enabled` → 「未启用（开关关闭）」；`not_implemented` → 「未实现入站」；
  3. `CELL_LABELS.inbound`（常量块在 `web/src/pages/ChannelMatrix.tsx:28-38`，
     现为 `inbound: '入站'`）改为 `inbound: '入站可用性'`；
  4. 入站单元格（`web/src/pages/ChannelMatrix.tsx:232-237`）改为
     `{AVAILABILITY_LABELS[item.availability] ?? item.availability}`——
     未登记取值原样显示，不得回落到「可入站」或「已接入」；
  5. `role-note` 段（`web/src/pages/ChannelMatrix.tsx:159-165`）补一句
     「入站有两条形态：控制台会话与签名回调；灰显渠道的原因看「入站可用性」列」，
     把删掉的形态信息收进静态说明，前端不再逐渠道自带入站事实；
  6. 顶部 `description`（`web/src/pages/ChannelMatrix.tsx:132`）里
     「入站只有配了适配器与验签的渠道能进来」若与新字段口径重复或含糊，按
     「原因由后端给出」微调措辞；行灰显（`item.enabled` 驱动，
     `web/src/pages/ChannelMatrix.tsx:218`）、状态徽章（同文件 224-231）
     与「已接入 x / 8」汇总（同文件 121、134-137）一律不动。
- `web/src/types.ts`：`ChannelCapability`（`web/src/types.ts:422-434`）的
  `enabled: boolean` 之后加 `availability: ChannelAvailability`；在其上方新增
  `export type ChannelAvailability = 'available' | 'webhook_secret_missing' |
  'not_enabled' | 'not_implemented'`。其余类型不动。

红-绿顺序（TS 侧的「先失败」照此做才是真红）：**只**改 `ChannelMatrix.tsx`
让它引用 `item.availability` 与 `AVAILABILITY_LABELS`，暂不改 `types.ts`
→ `npm run typecheck` 期望红于
`Property 'availability' does not exist on type 'ChannelCapability'`
（及未定义类型名报错）；再补 `types.ts` 转绿。红因输出必须贴进证据。

验证：`cd web && npm run typecheck` 与 `npm run build` 通过；
`git grep -n INBOUND_LABELS -- web` 无命中。
`web/` 没有测试运行器（`web/package.json` 只有 `dev` / `build` / `typecheck`），
本轮以 `tsc --noEmit` + 构建 + 后端契约测试代替前端单测——这是已知偏离，
在证据里写明，不得为此引入新依赖。

### 任务 6 — 文档同步（与任务 5 并行，文件边界不重叠）

文件（只做本轮最小必要修改）：

- `docs/guide/接口说明.md` 的 `### GET /api/v1/channels`（`docs/guide/接口说明.md:241-271`）：
  1. JSON 示例的两个条目各补 `"availability": "available"` /
     `"availability": "not_implemented"`；
  2. 表后新增「`availability` 取值」四行小表：取值 / 含义 / 对应的入站请求结果
     （404 两种理由、503、200），并写明「该字段只回答入站，
     不代表出站能力，也不代表服务商协议已对接」；
  3. 第 243 行那句「未启用的渠道也在表里，只是 `enabled=false`」补一句：
     `enabled` 只说明适配器是否在册，原因看 `availability`；
  4. 第 269 行的「没有入站适配器」表述改为指向 `not_implemented`；
  5. `### POST /api/v1/channels/{channel}/webhook`（`docs/guide/接口说明.md:287-291`）补一段对应关系：
     `not_implemented` 与 `not_enabled` → 404（两种理由文本不同）、
     `webhook_secret_missing` → 503、身份映射缺失或写错 → 403 / 503（既有语义不变，
     矩阵不为此新增取值）。
- `docs/下一步规划.md` 的 F-6（`docs/下一步规划.md:1973-1989`）：
  标题补「（✅ 已由 T-2 收口）」；在「建议改动」下方加一行差异登记：
  原文的 `disabled_in_prod` 在代码里无可判定来源，落地为 `webhook_secret_missing`；
  `implemented` 拆为 `available` / `not_enabled`；`declared_only` 改名 `not_implemented`；
  理由见本目录 `proposal.md` 的 D-1..D-5。既有「现状（可核对）」段落保留并标注
  「立项时现状」，不删不改写。
- 明确**不改**并说明理由（reviewer 按此核对越界）：
  `docs/spec/项目规格.md`（`:333`「渠道侧只有三条入站适配器（WEB/EMAIL/IM）」、
  `:3477`、`:3686` 的陈述在加字段后仍为真）、`README.md:255`、`docs/guide/范围与边界.md:47`、
  `docs/records/智能体平台设想.md:931` —— 本轮不新增渠道、不改「四个渠道能入站」这一事实。
  因此**不需要**跑 `python scripts/build_manual.py`，`docs/spec/manual-build.json` 保持原样；
  `docs/fact-baseline.yaml` 也不动（本轮不引入新的、需要两处声明的数字事实）。
- 若执行时发现上述任一陈述已被并发修改推翻，只做最小修正并在证据里说明，
  不得顺手扩大文档改动面。

验证：`python -m pytest tests/test_docs_consistency.py tests/test_doc_code_anchors.py
-o addopts=` 全绿；`git diff --name-only docs/` 只列出 `接口说明.md` 与 `下一步规划.md`
（若登记了新符号锚点，才会多一个 `docs/code-anchors.yaml`）。

### 任务 7 — 全量门禁与提交审查

动作：

1. `./.venv/Scripts/python.exe -m pytest -o addopts= > .tmp/full.txt 2>&1`（全量）；
2. `ruff check app tests scripts evals alembic`；
3. `./.venv/Scripts/python.exe -m mypy app`；
4. `./.venv/Scripts/python.exe -m pytest tests/test_architecture_guard.py
   tests/test_docs_consistency.py tests/test_doc_code_anchors.py -o addopts=`
   （三个守卫：分层方向、数字事实、文档点名符号与行号锚点）；
5. `cd web && npm run build`；
6. `git diff --stat` + `git status --short` 自查改动面，逐条对照「全局约束」的零改动清单；
7. 提交 reviewer，附本轮真实命令输出（区分事实与判断）；不勾选 `tasks.md`；
   不做任何 `git add` / `git commit` / `git push`。

本轮不涉及迁移与检索评测改动，不要求 `alembic check` 与 `evals/run.py`；
若发现改动溢出到这两处，立即停下回报，不得自行扩大范围。

## 依赖与并行

`任务 1 → 任务 2 → 任务 3 → 任务 4`，串行（同一批文件）。
`任务 5` 与 `任务 6` 在任务 2 通过后可并行：5 只碰 `web/**`，6 只碰 `docs/**`，
文件集合不重叠。`任务 7` 等 1-6 全部提交审查后再跑。
