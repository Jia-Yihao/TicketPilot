# Channel Inbound

## Purpose

能力矩阵必须说明每个渠道的入站可用性原因：每个渠道条目带一个必填的 `availability` 字段，
取值是封闭集合 `available` / `webhook_secret_missing` / `not_enabled` / `not_implemented`，
由渠道层在生成矩阵时算出，与入站请求的实际结果一一对应。消费方（含控制台）只展示后端给出的
原因，不自行维护「哪些渠道能入站」的事实副本。

## Requirements

### Requirement: 矩阵为每个渠道给出入站可用性原因

能力矩阵响应中的每个渠道条目 SHALL 携带一个必填字段 `availability`，其值 SHALL 属于
封闭集合 `available` / `webhook_secret_missing` / `not_enabled` / `not_implemented`，
且 SHALL 恰好取一个值。该字段 SHALL 只描述**入站**可用性——客户消息能否进入本服务，
SHALL NOT 被解读为出站能力、渲染能力或服务商协议的支持程度。

可用性 SHALL 由渠道层在生成矩阵时算出；消费方（含控制台）SHALL NOT 自行维护
「哪些渠道能入站」的事实副本。

矩阵既有的 `enabled` 字段语义 SHALL 不变，且与该字段保持可测关系：
`enabled` 为真当且仅当 `availability` 为 `available` 或 `webhook_secret_missing`。

#### Scenario: 默认开发配置下八个渠道各有原因值
- **WHEN** 以默认开发配置请求能力矩阵
- **THEN** 响应含 8 个渠道条目，每条都有 `availability`，值均属于上述封闭集合
- **AND** `WEB` 为 `available`；`EMAIL`、`WECHAT`、`WECOM` 为 `not_enabled`；
  `APP`、`WHATSAPP`、`VOICE`、`SMS` 为 `not_implemented`

#### Scenario: 未实现与未启用可被区分
- **WHEN** 比较 `APP` 与 `EMAIL` 两个条目的 `availability`
- **THEN** 两者不同值，且 `APP` 为 `not_implemented`、`EMAIL` 为 `not_enabled`
- **AND** 两者 `enabled` 均为假（该布尔不因本字段而改变）

#### Scenario: 集合外的值不得出现在响应里
- **WHEN** 渠道层对某渠道算出封闭集合之外的判定结果
- **THEN** 该结果 SHALL 被视为实现缺陷而不得写进响应；响应校验 SHALL 拒绝它

### Requirement: 四个取值各有固定判据与可观察后果

判定 SHALL 按以下顺序进行，第一个成立的分支即为该渠道的取值，不得跳序、不得并列：

1. 该渠道没有任何入站实现路径 → `not_implemented`；
2. 有入站实现路径但未被配置启用（没有构建出适配器） → `not_enabled`；
3. 已启用、且属于签名回调入站的渠道，其验签密钥为空 → `webhook_secret_missing`；
4. 以上皆不成立 → `available`。

每个取值 SHALL 与该渠道入站请求当下的实际可观察结果一致：
`not_implemented` 与 `not_enabled` 都返回 404 但理由 SHALL 可区分（沿用既有的两种
404 理由文本，不改写）；`webhook_secret_missing` 返回 503；`available` 在带齐请求
材料时返回 200。

#### Scenario: 未实现的渠道请求入站被拒且理由为「没有入站通道」
- **WHEN** 对 `availability` 为 `not_implemented` 的渠道发起签名回调请求
- **THEN** 返回 404，理由文本为既有的「该渠道没有入站 webhook」那条，SHALL NOT 改为其它文案

#### Scenario: 已实现但未启用的渠道请求入站被拒且理由为「未启用」
- **WHEN** 对 `availability` 为 `not_enabled` 的渠道发起签名回调请求
- **THEN** 返回 404，理由文本为既有的「该渠道未启用」那条

#### Scenario: 启用但缺密钥的渠道请求入站得到 503
- **WHEN** 打开某签名渠道的启用开关但不提供验签密钥，随后对其发起签名回调请求
- **THEN** 返回 503，说明缺少 webhook 密钥
- **AND** 矩阵中该渠道的 `availability` 为 `webhook_secret_missing`

#### Scenario: 配齐密钥后同一渠道变为可用
- **WHEN** 为上一场景的渠道补上非空验签密钥
- **THEN** 矩阵中该渠道的 `availability` 为 `available`
- **AND** 其 `enabled` 保持为真，不因取值变化而改变

#### Scenario: 控制台入口渠道不因缺密钥被误判
- **WHEN** 生成矩阵时判定控制台自身那条入站路径
- **THEN** 该渠道 SHALL NOT 被判为 `webhook_secret_missing`（它不使用签名回调密钥）

### Requirement: 入站实现集合是单一事实来源且不漂移

渠道层 SHALL 声明一份「已实现入站的渠道」集合作为唯一事实来源，
矩阵判定、签名回调白名单与适配器构建三处 SHALL 都由这份集合派生或与之等价，
SHALL NOT 各自另写一份渠道清单。

该集合 SHALL 与适配器构建保持等价：在全部入站开关都打开的配置下，
构建出的适配器所覆盖的渠道集合 SHALL 等于该集合；签名回调入站的渠道集合
SHALL 等于该集合减去控制台自身那条入站路径。

#### Scenario: 全开关下适配器集合等于声明集合
- **WHEN** 以所有入站开关为真的配置构建适配器
- **THEN** 适配器覆盖的渠道集合与声明的入站实现集合完全相等

#### Scenario: 签名回调集合可由声明集合派生
- **WHEN** 取声明的入站实现集合并去掉控制台自身那条入站路径
- **THEN** 结果与受理签名回调的渠道集合完全相等
- **AND** 对结果之外的渠道发起签名回调仍然返回 404（既有行为不退化）

#### Scenario: 新增渠道开关时守卫变红
- **WHEN** 有人为某个渠道构建了适配器却没有把它登记进入站实现集合（或反之）
- **THEN** 全开关等价性检查失败，SHALL NOT 允许两份清单静默漂移

### Requirement: 缺密钥判定与生产安全校验同源

`webhook_secret_missing` 的判定 SHALL 读取该渠道在实际受理入站请求时用于验签的
那同一个密钥值，SHALL NOT 重新读取一份配置副本或复制一段判断逻辑。

该判定与生产安全校验的关系 SHALL 双向等价：对每个签名回调渠道，
「密钥为空」当且仅当「同一份配置在生产环境下会被启动期安全校验拒绝启动」。
非生产环境下该状态可被观察到（矩阵给出 `webhook_secret_missing`、请求得到 503）；
生产环境下它 SHALL NOT 出现在响应里，因为进程在校验阶段即拒绝启动，
这层关系 SHALL 有测试钉住，而不是靠注释承诺。

#### Scenario: 生产环境拒绝该配置而非报告该状态
- **WHEN** 以生产环境标识、启用某签名渠道但不给验签密钥来构造配置
- **THEN** 启动期安全校验抛出错误，服务不启动
- **AND** 该渠道的凭据缺陷在生产环境里永远不会以响应字段的形式出现

#### Scenario: 同一配置在非生产环境下报告原因
- **WHEN** 用与上一场景相同的渠道开关与空密钥、但非生产环境标识构造配置并生成矩阵
- **THEN** 该渠道的 `availability` 为 `webhook_secret_missing`

#### Scenario: 密钥齐备时两侧一致
- **WHEN** 为同一签名渠道提供非空验签密钥
- **THEN** 生产环境安全校验不再因该渠道抛错
- **AND** 非生产矩阵给出 `available`

### Requirement: 可用性字段不承诺穷举缺陷也不改变行为

`availability` SHALL 是观测值，SHALL NOT 改变路由、鉴权、幂等认领、渲染或投递行为。
它 SHALL NOT 被解读为「该渠道的所有配置都正确」：其它配置缺陷
（例如发件人身份映射缺失或格式写错）SHALL 仍由请求时的既有错误表达，
SHALL NOT 为该目的新增取值。

#### Scenario: 身份映射缺陷不新增取值
- **WHEN** 某签名渠道已启用且密钥齐备，但其发件人身份映射缺失或写错
- **THEN** 矩阵仍给出 `available`
- **AND** 请求时按既有语义返回相应错误，SHALL NOT 出现第五个取值

#### Scenario: 判定不改变任何请求结果
- **WHEN** 加入该字段前后分别以同一配置与同一请求材料发起入站请求
- **THEN** 状态码、理由文本与响应主体逐字一致（仅矩阵响应多出该字段）

### Requirement: 控制台展示后端给出的原因

控制台渠道页 SHALL 展示每个渠道的入站可用性原因，文案 SHALL 由该取值映射得到，
四个取值 SHALL 都有对应文案，未登记的返回值 SHALL 有可见兜底。
前端 SHALL NOT 保留自己的「哪些渠道能入站」硬编码清单。

#### Scenario: 四个取值都有中文文案
- **WHEN** 渠道页渲染矩阵
- **THEN** `available`、`webhook_secret_missing`、`not_enabled`、`not_implemented`
  四种取值各自显示一条不同的中文说明

#### Scenario: 前端不再自带入站事实
- **WHEN** 检查渠道页的入站说明来源
- **THEN** 说明来自后端 `availability` 字段，前端不存在按渠道硬编码入站有无的映射表

#### Scenario: 未知取值不静默显示为可用
- **WHEN** 后端返回一个不在映射表里的取值
- **THEN** 页面显示兜底文案（原样展示该取值），SHALL NOT 显示为「已接入」或「可入站」
