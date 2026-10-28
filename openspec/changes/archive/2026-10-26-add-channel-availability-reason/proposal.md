# 渠道入站可用性原因（F-6）

## 为什么

能力矩阵声明 8 个渠道，真正能把客户消息送进来的只有 4 个（`WEB` / `EMAIL` /
`WECHAT` / `WECOM`）。今天唯一的可用性信号是 `GET /api/v1/channels` 里那个
`enabled` 布尔：它在 `ChannelGateway.matrix()` 里就是「这个渠道在不在已构建的适配器
字典里」（`app/channels/gateway.py:180`），而适配器是否在字典里取决于配置开关
（`app/channels/registry.py:13-36`）。于是三种完全不同的情况在响应里长得一模一样：

- **没实现**（`APP` / `WHATSAPP` / `VOICE` / `SMS`：代码里没有入站路径，webhook 返回 404
  「This channel has no inbound webhook.」，`app/channels/gateway.py:125-126`）；
- **没启用**（`EMAIL` / `WECHAT` / `WECOM`：代码里有入站路径，但开关关着，适配器没建出来，
  webhook 走 `self.adapter(channel)` 返回 404 「Channel X is not enabled.」，`app/channels/gateway.py:49-51`）；
- **凭据没配齐**（开关开着但 webhook 密钥为空：适配器建出来了，请求时 `verify_hmac`
  抛 503 「This channel has no webhook secret configured.」，`app/channels/base.py:218-221`）。

控制台渠道页因此只能把这件事**硬编码在前端**：`web/src/pages/ChannelMatrix.tsx:21-26`
自己写了一张「哪四个渠道有入站」的表，第 236 行兜底成「无入站通道」。这份表与后端
的适配器字典是同一件事的两份真相，任何一边改动都不会让另一边变红。

补适配器在 `docs/下一步规划.md` §8 已列为不做，所以本轮要解决的是**可解释性**：
让矩阵自己说明「这个渠道为什么点不动」，并顺手把前端那份重复真相删掉。

## 改什么

按能力 `channel-inbound` 列变更点（一条一句，取值字符串是对外契约）：

1. `ChannelCapabilityView` 新增必填字段 `availability`，取值封闭为四个字符串：
   `available` / `webhook_secret_missing` / `not_enabled` / `not_implemented`。
2. 该字段只回答**入站**可用性（客户消息能否进到本服务），不覆盖出站渲染，
   也不覆盖服务商协议；计算发生在渠道层生成矩阵时，前端不得自行推断。
3. 四个值按固定顺序判定，每个渠道恰好命中一个，每条判据都能指到源码里已有的事实：
   - `not_implemented` ← 该渠道不在「已实现入站」集合里；
   - `not_enabled` ← 在集合里，但适配器没建出来（等价于今天的 `enabled == false`）；
   - `webhook_secret_missing` ← 适配器建出来了，但该 webhook 渠道的验签密钥为空；
   - `available` ← 以上皆否。
4. 「已实现入站」集合成为一份**新增的单一事实来源**，与既有两处事实绑定防漂移：
   `build_adapters()` 在全开关下产出的键集合必须等于它；webhook 白名单集合必须是它减去 `WEB`。
5. `webhook_secret_missing` 的判据读的就是 `verify()` 实际用的那同一个密钥值
   （从适配器实例上取，不再从 `Settings` 复制一份判断），因此它与
   `validate_production_safety()` 对 `CHANNEL_EMAIL_SECRET` / `CHANNEL_IM_SECRET`
   的非空要求是同一条条件（`app/core/config.py:201-222`）。
6. `enabled` 字段语义与现有 8 渠道能力断言逐字不变；两者的一致性成为一条可测关系：
   `enabled == (availability ∈ {available, webhook_secret_missing})`。
7. 控制台渠道页显示原因：入站列改为渲染后端 `availability` 的中文标签，
   并**删除** `web/src/pages/ChannelMatrix.tsx` 里硬编码的 `INBOUND_LABELS` 表。

### 口径取舍（与 `docs/下一步规划.md` F-6 原文的差异，必读）

原文建议的三值是 `implemented` / `declared_only` / `disabled_in_prod`。本轮按代码事实
调整为上面四值，差异与理由逐条登记如下（这属于对外响应契约的口径变化，故显式声明）：

- **D-1 去掉 `disabled_in_prod`，换成 `webhook_secret_missing`。**
  `validate_production_safety()` 是 `Settings` 的启动期校验器
  （`app/core/config.py:170-229`，`mode="after"`），在 `environment == "prod"` 下对不合规配置
  **直接抛 `ValueError`**——进程起不来，永远走不到 `GET /api/v1/channels`。代码里不存在
  「生产环境下把某个已实现渠道判为禁用」的谓词，因此 `disabled_in_prod` 今天没有任何
  可判定来源；按「找不到就不许发明、也不许写一个永远为假的分支凑枚举」的要求删掉。
  取而代之的 `webhook_secret_missing` 正是**同一条条件**（webhook 密钥非空）在非
  生产环境下的可观察形态（503），既保留了原文想表达的「这套配置在生产会被拒绝」，
  又满足「与 `validate_production_safety()` 判断同源、不新增第二份真相」。
- **D-2 `implemented` 拆成 `available` 与 `not_enabled`。** 验收要求「每个渠道都有原因值」
  并且要能区分「没实现」与「没配置」；只留一个 `implemented` 会与 `enabled` 布尔重叠，
  「实现了但没启用」这个真实状态仍然没有名字。
- **D-3 `declared_only` 改名 `not_implemented`。** `WHATSAPP` / `VOICE` / `SMS` 其实有
  出站渲染形状（`app/channels/render.py:305-312`、`375-385` 都处理它们），
  写成 `declared_only` 容易被读成「这个渠道什么都做不了」，写成 `implemented`
  又会被读成「入站可用」。`not_implemented` 把否定范围明确收在入站，正是原文
  「不承诺声明即支持」的落点。
- **D-4 不引入自由文本 `reason` 字段**（原文允许「枚举 或 reason 字段」二选一）。
  自由文本会把面向用户的中文文案塞进后端契约，既无法逐条测试，也会与前端标签表
  形成第二份真相；文案留在控制台的枚举→标签映射里。
- **D-5 不承诺穷举所有配置缺陷。** 身份映射缺失或写错仍然是请求时的既有错误
  （未登记发件人 403、映射串写错 503，`app/channels/base.py:189-199`，
  与 `docs/guide/接口说明.md` 现有语义一致），本轮不加第五个值去描述它们；
  矩阵只回答「入站通不通、为什么不通」。

## 不改什么

- 不新增任何渠道适配器，不补齐 `APP` / `WHATSAPP` / `VOICE` / `SMS` 的入站
  （`docs/下一步规划.md` §8 已列为不做）。
- 不动 `POST /api/v1/channels/{channel}/webhook` 的 404 行为与 404 语义分层
  （「没有入站 webhook」与「渠道未启用」两处 404 的理由文本与命中条件逐字不变）。
- 不改 `enabled` 字段的现有语义，也不改现有 8 渠道能力断言
  （`tests/test_channels.py:437-449` 一行都不动，本轮对 `tests/test_channels.py` 的改动为零）。
- 不改 `ChannelCapabilityView` 已有字段的名字、类型与顺序；不改
  `ChannelMatrixResponse` 的形状。
- 不改出站投递面（`app/channels/outbound.py` 的 `_ADDRESS_MAPS`、地址反查、11 个拒绝原因码）。
- 不改控制台渠道页以外的前端；不改 `docs/spec/项目规格.md`（因此不需要重生成说明书）。
- 不引入新依赖，不加迁移，不加配置项。

## 验收

逐条可判定，`reviewer` 读代码与 diff 判定：

1. `GET /api/v1/channels` 返回的 8 个渠道**每个都带** `availability`，值属于四个登记
   字符串之一；默认开发配置下的期望值可逐渠道指名：`WEB=available`、
   `EMAIL/WECHAT/WECOM=not_enabled`、`APP/WHATSAPP/VOICE/SMS=not_implemented`。
2. 每个值都由源码里可指名的谓词算出：`not_implemented` 与 `not_enabled` 落在渠道层的
   「已实现入站」集合与适配器字典；`webhook_secret_missing` 读的就是 `verify()` 用的
   那同一个密钥值，且该状态下 webhook 实际返回 503。
3. 不存在第二份真相，且有三条守卫防漂移：
   （a）全开关下 `build_adapters()` 的键集合 == 「已实现入站」集合；
   （b）webhook 白名单集合 == 该集合减去 `WEB`；
   （c）对 `EMAIL` / `WECHAT` / `WECOM`，「密钥为空 ⇒ prod 下 `Settings()` 抛 `ValueError`」
   与「同一配置在非 prod 下 ⇒ `webhook_secret_missing`」双向等价。
4. 现有 8 渠道能力断言不退化：`tests/test_channels.py` 零改动且全绿；
   `enabled` 与 `availability` 的关系有独立断言。
5. 控制台渠道页显示原因：入站列按 `availability` 出中文标签，四个值都有文案，
   未登记的返回值有可见兜底；`INBOUND_LABELS` 已删除，前端不再自带「哪四个渠道能入站」
   这类事实。`npm run typecheck` 与 `npm run build` 通过。
6. 文档同步已登记并落实：`docs/guide/接口说明.md` 的 `GET /api/v1/channels` 一节补该字段与
   取值表，`docs/下一步规划.md` F-6 段标注收口状态并登记取值差异。文档里**不得新增**
   形如 `path.py:NN` 的行号锚点（`tests/test_doc_code_anchors.py` 会扫 `docs/**`，
   行号本身会漂），要指向代码就写符号名。本轮不改 `docs/spec/项目规格.md`，
   故 `docs/spec/manual-build.json` 与说明书不重生成。

## 边界（写进文档，不许含糊）

- 「声明即支持」不成立：`availability` 只说明入站可达性，**不代表**服务商协议存在；
  签名格式仍是本项目自定义 HMAC。
- 出站能力不在本字段的表达范围内（`WHATSAPP` / `VOICE` / `SMS` 有渲染形状这一事实
  不因本字段改变）。
- 本字段是**观测**，不是**决策**：它不改变任何路由、鉴权、幂等或渲染行为。
