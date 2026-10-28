import { useEffect, useRef, useState } from 'react'
import { getChannelMatrix } from '../api'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type { ChannelCapability } from '../types'

const CHANNEL_LABELS: Record<string, string> = {
  WEB: '网页控制台',
  APP: '移动 App',
  EMAIL: '邮件',
  WECHAT: '微信',
  WECOM: '企业微信',
  WHATSAPP: 'WhatsApp',
  VOICE: '语音',
  SMS: '短信',
}

// The inbound availability reason comes from the backend (`availability`).
// The console only maps that value to Chinese text; it keeps no list of its
// own about which channels can take inbound traffic, so the two sides cannot
// drift apart. An unregistered value falls through to the raw value instead of
// being shown as "available".
const AVAILABILITY_LABELS: Record<string, string | undefined> = {
  available: '可入站',
  webhook_secret_missing: '缺验签密钥（请求会 503）',
  not_enabled: '未启用（开关关闭）',
  not_implemented: '未实现入站',
}

const CELL_LABELS = {
  status: '状态',
  inbound: '入站可用性',
  stream: '流式',
  card: '富卡片',
  voice: '语音',
  length: '长度上限',
  formal: '语气',
  handoff: '转人工',
  deliveries: '入站记录',
} as const

function deliveryTotal(deliveries: Record<string, number>): number {
  return Object.values(deliveries).reduce((sum, value) => sum + value, 0)
}

function deliveryBreakdown(deliveries: Record<string, number>): string {
  const parts = Object.entries(deliveries)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([status, count]) => `${status} ${count}`)
  return parts.length > 0 ? parts.join(' · ') : '没有入站记录'
}

function Flag({ on, yes, no }: { on: boolean; yes: string; no: string }) {
  return (
    <span className={`matrix-flag${on ? ' is-on' : ''}`}>
      <Icon name={on ? 'check' : 'x'} size={13} />
      {on ? yes : no}
    </span>
  )
}

export function ChannelMatrix() {
  usePageTitle('渠道矩阵')
  const { identity, canOperate } = useIdentity()
  const [channels, setChannels] = useState<ChannelCapability[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  const loadedRef = useRef(false)

  useEffect(() => {
    if (!canOperate) {
      setLoading(false)
      return
    }
    let cancelled = false
    if (loadedRef.current) setRefreshing(true)
    else setLoading(true)
    getChannelMatrix(identity)
      .then((data) => {
        if (cancelled) return
        setChannels(data.channels)
        setError(null)
      })
      .catch((errorValue: unknown) => {
        if (!cancelled) setError(errorValue)
      })
      .finally(() => {
        if (cancelled) return
        loadedRef.current = true
        setLoading(false)
        setRefreshing(false)
      })
    return () => {
      cancelled = true
    }
  }, [identity, canOperate, refreshTick])

  if (!canOperate) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="CHANNELS / 渠道矩阵"
          title="渠道矩阵"
          description="各渠道的渲染能力、接入状态与入站投递计数。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="lock" size={26} />
          </span>
          <p className="eyebrow">ROLE GATE / STAFF REQUIRED</p>
          <h2>当前身份无权查看渠道配置</h2>
          <p className="gate__copy">
            当前身份是 {identity.tenant} / {identity.user}，没有 support
            权限。渠道矩阵只对坐席与管理员开放。
          </p>
        </section>
      </div>
    )
  }

  const connected = channels.filter((item) => item.enabled)
  const deliveries = channels.reduce(
    (sum, item) => sum + deliveryTotal(item.deliveries),
    0,
  )

  return (
    <div className="page page--matrix">
      <PageHeader
        eyebrow="CHANNELS / 渠道矩阵"
        title="渠道矩阵"
        description="渲染矩阵决定同一答案在每个渠道里长成什么样；入站能不能进来，看「入站可用性」一列给出的原因。"
        meta={
          <div className="matrix-summary">
            <span className="chip">
              已接入 {connected.length} / {channels.length}
            </span>
            <span className="chip">入站记录 {deliveries}</span>
            <span className="chip">能力声明≠服务商协议</span>
          </div>
        }
        actions={
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => setRefreshTick((value) => value + 1)}
            disabled={refreshing}
          >
            <Icon
              name="refresh"
              size={15}
              className={refreshing ? 'is-spinning' : undefined}
            />
            刷新
          </button>
        }
      />

      <div className="role-note">
        <Icon name="shield" size={16} />
        <p>
          未接入的渠道会灰显但不隐藏：它们在枚举与渲染矩阵里存在，入站请求一律
          404（未实现与未启用都是 404，理由文本不同）。入站形态只有两种——控制台
          会话与签名回调（邮件、微信、企业微信）；某个渠道此刻通不通，看「入站
          可用性」一列给出的原因。签名格式是本项目自定义的 HMAC，还没有对接任何
          真实服务商协议。
        </p>
      </div>

      {loading && channels.length === 0 ? (
        <div className="table-panel">
          <div className="table-skeleton" aria-hidden="true">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((row) => (
              <div key={row} className="table-skeleton__row">
                <span
                  className="skeleton skeleton--line"
                  style={{ width: '22%' }}
                />
                <span
                  className="skeleton skeleton--line"
                  style={{ width: '12%' }}
                />
                <span
                  className="skeleton skeleton--line"
                  style={{ width: '40%' }}
                />
              </div>
            ))}
          </div>
        </div>
      ) : error ? (
        <ErrorPanel
          title="无法读取渠道矩阵"
          error={error}
          onRetry={() => setRefreshTick((value) => value + 1)}
        />
      ) : (
        <div className="table-panel">
          <table className="task-table matrix-table">
            <caption className="sr-only">
              八个渠道的接入状态、渲染能力与入站投递计数
            </caption>
            <thead>
              <tr>
                <th scope="col">渠道</th>
                <th scope="col">{CELL_LABELS.status}</th>
                <th scope="col">{CELL_LABELS.inbound}</th>
                <th scope="col">{CELL_LABELS.stream}</th>
                <th scope="col">{CELL_LABELS.card}</th>
                <th scope="col">{CELL_LABELS.voice}</th>
                <th scope="col">{CELL_LABELS.length}</th>
                <th scope="col">{CELL_LABELS.formal}</th>
                <th scope="col">{CELL_LABELS.handoff}</th>
                <th scope="col">{CELL_LABELS.deliveries}</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((item) => (
                <tr
                  key={item.channel}
                  className={item.enabled ? undefined : 'is-off'}
                >
                  <th scope="row" className="matrix-table__name">
                    <strong>{CHANNEL_LABELS[item.channel] ?? item.channel}</strong>
                    <span className="mono">{item.channel}</span>
                  </th>
                  <td data-label={CELL_LABELS.status}>
                    <span
                      className={`badge badge--${item.enabled ? 'success' : 'muted'}`}
                    >
                      <span className="badge__dot" aria-hidden="true" />
                      {item.enabled ? '已接入' : '未接入'}
                    </span>
                  </td>
                  <td
                    className="matrix-table__plain"
                    data-label={CELL_LABELS.inbound}
                  >
                    {AVAILABILITY_LABELS[item.availability] ?? item.availability}
                  </td>
                  <td data-label={CELL_LABELS.stream}>
                    <Flag
                      on={item.supports_stream}
                      yes="支持"
                      no="降级"
                    />
                  </td>
                  <td data-label={CELL_LABELS.card}>
                    <Flag
                      on={item.supports_rich_card}
                      yes={`最多 ${item.max_cards} 张`}
                      no="降级为索引"
                    />
                  </td>
                  <td data-label={CELL_LABELS.voice}>
                    <Flag on={item.supports_voice} yes="支持" no="不支持" />
                  </td>
                  <td
                    className="matrix-table__plain mono"
                    data-label={CELL_LABELS.length}
                  >
                    {item.max_length === null
                      ? '不限'
                      : `${item.max_length} 字`}
                    {item.sentence_char_limit !== null
                      ? ` · 句 ${item.sentence_char_limit}`
                      : ''}
                  </td>
                  <td
                    className="matrix-table__plain"
                    data-label={CELL_LABELS.formal}
                  >
                    {item.formal ? '正式' : '口语'}
                  </td>
                  <td data-label={CELL_LABELS.handoff}>
                    <Flag
                      on={item.offers_human_handoff}
                      yes="有入口"
                      no="无入口"
                    />
                  </td>
                  <td
                    className="matrix-table__count"
                    data-label={CELL_LABELS.deliveries}
                  >
                    <strong>{deliveryTotal(item.deliveries)}</strong>
                    <span className="mono">
                      {deliveryBreakdown(item.deliveries)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="matrix-note mono">
        入站记录来自幂等表 channel_messages，按状态拆分；重复投递只计一次。
      </p>
    </div>
  )
}
