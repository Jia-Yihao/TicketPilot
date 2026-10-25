import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  errorText,
  getHandoffMetrics,
  getSecurityMetrics,
  getTraceMetrics,
  listAbandons,
  listOutbox,
} from '../api'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import {
  abandonReasonLabel,
  formatLatency,
  formatRelative,
  handoffReasonLabel,
  intentLabel,
  outboxStatusLabel,
  percent,
  queueLabel,
  securityCategoryLabel,
  spanKindLabel,
} from '../format'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type {
  AbandonListResponse,
  HandoffMetrics,
  OutboxMessageSummary,
  SecurityMetrics,
  TraceMetrics,
} from '../types'

type RangeKey = '24h' | '7d' | '30d'

const RANGES: { key: RangeKey; label: string; minutes: number }[] = [
  { key: '24h', label: '近 24 小时', minutes: 60 * 24 },
  { key: '7d', label: '近 7 天', minutes: 60 * 24 * 7 },
  { key: '30d', label: '近 30 天', minutes: 60 * 24 * 30 },
]

interface OutboxSnapshot {
  pending: number | null
  processing: number | null
  failed: number | null
  recent: OutboxMessageSummary[]
}

function rateValue(
  numerator: number,
  denominator: number,
  digits = 1,
): string {
  if (denominator <= 0) return '—'
  return percent(numerator / denominator, digits)
}

function BarList({
  data,
  labelOf,
  emptyHint,
}: {
  data: Record<string, number>
  labelOf: (key: string) => string
  emptyHint: string
}) {
  const rows = Object.entries(data)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 6)
  const max = Math.max(1, ...rows.map((row) => row[1]))
  if (rows.length === 0) {
    return <p className="block-muted">{emptyHint}</p>
  }
  return (
    <ul className="bar-list">
      {rows.map(([key, value]) => (
        <li key={key}>
          <span className="bar-list__label">{labelOf(key)}</span>
          <span className="bar-list__track" aria-hidden="true">
            <span
              className="bar-list__fill"
              style={{ width: `${(value / max) * 100}%` }}
            />
          </span>
          <span className="bar-list__value mono">{value}</span>
        </li>
      ))}
    </ul>
  )
}

function HourBars({ data }: { data: Record<string, number> }) {
  const hours = Array.from({ length: 24 }, (_, index) =>
    String(index).padStart(2, '0'),
  )
  const max = Math.max(1, ...hours.map((hour) => data[hour] ?? 0))
  const total = hours.reduce((sum, hour) => sum + (data[hour] ?? 0), 0)
  if (total === 0) {
    return <p className="block-muted">该时段没有事件。</p>
  }
  return (
    <div className="hour-bars" role="img" aria-label="按小时分布">
      {hours.map((hour) => {
        const value = data[hour] ?? 0
        return (
          <span className="hour-bars__col" key={hour}>
            <span
              className="hour-bars__bar"
              style={{ height: `${Math.max(2, (value / max) * 100)}%` }}
              title={`${hour}:00 · ${value}`}
            />
            {Number(hour) % 6 === 0 ? (
              <small className="mono">{hour}</small>
            ) : null}
          </span>
        )
      })}
    </div>
  )
}

export function OpsDashboard() {
  usePageTitle('运营看板')
  const { identity, canOperate } = useIdentity()
  const [range, setRange] = useState<RangeKey>('24h')
  const [handoff, setHandoff] = useState<HandoffMetrics | null>(null)
  const [abandons, setAbandons] = useState<AbandonListResponse | null>(null)
  const [outbox, setOutbox] = useState<OutboxSnapshot>({
    pending: null,
    processing: null,
    failed: null,
    recent: [],
  })
  const [traces, setTraces] = useState<TraceMetrics | null>(null)
  const [security, setSecurity] = useState<SecurityMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [refreshTick, setRefreshTick] = useState(0)

  const minutes = RANGES.find((item) => item.key === range)?.minutes ?? 1440

  useEffect(() => {
    if (!canOperate) return
    let cancelled = false
    setRefreshing(true)

    Promise.allSettled([
      getHandoffMetrics(identity, minutes),
      listAbandons(identity, 6, 0),
      listOutbox(identity, 'PENDING', 5, 0),
      listOutbox(identity, 'PROCESSING', 1, 0),
      listOutbox(identity, 'FAILED', 5, 0),
      getTraceMetrics(identity, minutes),
      getSecurityMetrics(identity, minutes),
    ] as const)
      .then((results) => {
        if (cancelled) return
        const failures: string[] = []
        const [
          handoffResult,
          abandonsResult,
          pendingResult,
          processingResult,
          failedResult,
          tracesResult,
          securityResult,
        ] = results

        const pick = <T,>(
          result: PromiseSettledResult<T>,
          label: string,
        ): T | null => {
          if (result.status === 'fulfilled') return result.value
          failures.push(`${label}：${errorText(result.reason)}`)
          return null
        }

        setHandoff(pick(handoffResult, '转人工指标'))
        setAbandons(pick(abandonsResult, '放弃事件'))
        const pending = pick(pendingResult, '待投递')
        const processing = pick(processingResult, '投递中')
        const failed = pick(failedResult, '死信')
        const recent = [
          ...(failed?.items ?? []),
          ...(pending?.items ?? []),
        ].slice(0, 6)
        setOutbox({
          pending: pending?.total ?? null,
          processing: processing?.total ?? null,
          failed: failed?.total ?? null,
          recent,
        })
        setTraces(pick(tracesResult, '轨迹指标'))
        setSecurity(pick(securityResult, '安全事件'))
        setErrors(failures)
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
          setRefreshing(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [identity, canOperate, minutes, refreshTick])

  useEffect(() => {
    if (!canOperate) return
    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [canOperate])

  if (!canOperate) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="OPERATIONS / 运营看板"
          title="运营看板"
          description="转人工率、自结率、放弃、Outbox 积压与 trace 健康度。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="lock" size={26} />
          </span>
          <p className="eyebrow">ROLE GATE / STAFF REQUIRED</p>
          <h2>当前身份无权查看运营指标</h2>
          <p className="gate__copy">
            当前身份是 {identity.tenant} / {identity.user}
            ，没有 support 权限。指标按租户隔离，只对坐席与管理员开放。
          </p>
          <div className="gate__actions">
            <Link className="button button--ghost" to="/conversation">
              返回售后对话
            </Link>
          </div>
        </section>
      </div>
    )
  }

  const backlog = (outbox.pending ?? 0) + (outbox.processing ?? 0)
  const deadLetters = outbox.failed

  return (
    <div className="page page--ops">
      <PageHeader
        eyebrow="OPERATIONS / 运营看板"
        title="运营看板"
        description="指标按租户隔离。Outbox 积压是当前快照，其余指标随所选时段变化。"
        actions={
          <div className="header-tools">
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
            <Link className="button button--primary button--sm" to="/agent">
              打开坐席工作台
              <Icon name="arrow" size={15} />
            </Link>
          </div>
        }
      />

      <div className="ops-toolbar">
        <div className="tabs" role="tablist" aria-label="统计时段">
          {RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={range === item.key}
              className={`tab${range === item.key ? ' is-active' : ''}`}
              onClick={() => setRange(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <span className="mono ops-toolbar__note">
          30 秒自动刷新 · Outbox 为实时积压
        </span>
      </div>

      {errors.length > 0 ? (
        <div className="inline-error" role="alert">
          <Icon name="alert" size={15} />
          <span>{errors.join('；')}</span>
        </div>
      ) : null}

      {loading && !handoff && !traces ? (
        <div className="ops-grid" aria-hidden="true">
          {[0, 1, 2, 3, 4, 5].map((key) => (
            <div className="ops-card" key={key}>
              <span
                className="skeleton skeleton--line"
                style={{ width: '42%' }}
              />
              <span
                className="skeleton skeleton--line"
                style={{ width: '62%', height: 34 }}
              />
              <span
                className="skeleton skeleton--line"
                style={{ width: '78%' }}
              />
            </div>
          ))}
        </div>
      ) : !handoff && !traces && errors.length > 0 ? (
        <ErrorPanel
          title="无法读取运营指标"
          error={new Error(errors[0])}
          onRetry={() => setRefreshTick((value) => value + 1)}
        />
      ) : (
        <>
          <section className="ops-grid" aria-label="核心指标">
            <article className="ops-card">
              <span className="ops-card__label">转人工率</span>
              <strong className="ops-card__value">
                {handoff
                  ? rateValue(
                      handoff.handoff_conversations,
                      handoff.conversations,
                    )
                  : '—'}
              </strong>
              <span className="ops-card__hint">
                {handoff
                  ? `${handoff.handoff_conversations} / ${handoff.conversations} 个会话`
                  : '指标不可用'}
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">自结率</span>
              <strong className="ops-card__value">
                {handoff && handoff.conversations > 0
                  ? percent(handoff.auto_resolution_rate)
                  : '—'}
              </strong>
              <span className="ops-card__hint">
                Agent 未转人工且最后一轮已解决
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">会话放弃率</span>
              <strong className="ops-card__value">
                {handoff
                  ? rateValue(handoff.abandon_events, handoff.conversations)
                  : '—'}
              </strong>
              <span className="ops-card__hint">
                {handoff
                  ? `${handoff.abandon_events} 次放弃事件`
                  : '指标不可用'}
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">Outbox 积压</span>
              <strong className="ops-card__value">
                {outbox.pending === null && outbox.processing === null
                  ? '—'
                  : backlog}
              </strong>
              <span className="ops-card__hint">
                待投递 {outbox.pending ?? '—'} · 投递中{' '}
                {outbox.processing ?? '—'}
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">死信</span>
              <strong
                className={`ops-card__value${
                  deadLetters ? ' is-alert' : ''
                }`}
              >
                {deadLetters === null ? '—' : deadLetters}
              </strong>
              <span className="ops-card__hint">
                超过重试上限，需要人工核对
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">Span 错误率</span>
              <strong className="ops-card__value">
                {traces && traces.spans > 0
                  ? percent(traces.error_rate, 2)
                  : '—'}
              </strong>
              <span className="ops-card__hint">
                {traces && traces.spans > 0
                  ? `${traces.error_spans} / ${traces.spans} 个 span · P95 ${formatLatency(
                      traces.p95_latency_ms,
                    )}`
                  : '该时段没有 span'}
              </span>
            </article>
            <article className="ops-card">
              <span className="ops-card__label">安全事件</span>
              <strong
                className={`ops-card__value${
                  security && security.blocked > 0 ? ' is-alert' : ''
                }`}
              >
                {security ? security.total : '—'}
              </strong>
              <span className="ops-card__hint">
                {security
                  ? `拦截 ${security.blocked} · 改写 ${security.redacted} · 留痕 ${security.logged}`
                  : '指标不可用'}
              </span>
            </article>
          </section>

          <div className="ops-columns">
            <section className="ops-panel">
              <header className="ops-panel__head">
                <div>
                  <p className="eyebrow">HANDOFF</p>
                  <h2>转人工原因</h2>
                </div>
                <span className="mono">
                  OPEN {handoff ? handoff.open_cases : '—'}
                </span>
              </header>
              <div className="ops-panel__body">
                <BarList
                  data={handoff?.by_reason ?? {}}
                  labelOf={handoffReasonLabel}
                  emptyHint="该时段没有转人工事件。"
                />
                <div className="ops-split">
                  <div>
                    <span className="ops-split__label">按队列</span>
                    <ul className="ops-kv">
                      {Object.entries(handoff?.by_queue ?? {}).map(
                        ([key, value]) => (
                          <li key={key}>
                            <span>{queueLabel(key)}</span>
                            <b className="mono">{value}</b>
                          </li>
                        ),
                      )}
                      {Object.keys(handoff?.by_queue ?? {}).length === 0 ? (
                        <li>
                          <span>—</span>
                        </li>
                      ) : null}
                    </ul>
                  </div>
                  <div>
                    <span className="ops-split__label">按信号</span>
                    <ul className="ops-kv">
                      {Object.entries(handoff?.by_signal ?? {})
                        .sort((left, right) => right[1] - left[1])
                        .slice(0, 4)
                        .map(([key, value]) => (
                          <li key={key}>
                            <span className="mono">{key}</span>
                            <b className="mono">{value}</b>
                          </li>
                        ))}
                      {Object.keys(handoff?.by_signal ?? {}).length === 0 ? (
                        <li>
                          <span>—</span>
                        </li>
                      ) : null}
                    </ul>
                  </div>
                </div>
                <span className="ops-split__label">转人工时段分布</span>
                <HourBars data={handoff?.by_hour ?? {}} />
              </div>
            </section>

            <section className="ops-panel">
              <header className="ops-panel__head">
                <div>
                  <p className="eyebrow">TRACE</p>
                  <h2>轨迹健康度</h2>
                </div>
                <span className="mono">
                  TRACES {traces ? traces.trace_count : '—'}
                </span>
              </header>
              <div className="ops-panel__body">
                <BarList
                  data={traces?.by_kind ?? {}}
                  labelOf={spanKindLabel}
                  emptyHint="该时段没有 span。"
                />
                <div className="ops-split">
                  <div>
                    <span className="ops-split__label">状态</span>
                    <ul className="ops-kv">
                      {Object.entries(traces?.by_status ?? {}).map(
                        ([key, value]) => (
                          <li key={key}>
                            <span className="mono">{key}</span>
                            <b className="mono">{value}</b>
                          </li>
                        ),
                      )}
                      {Object.keys(traces?.by_status ?? {}).length === 0 ? (
                        <li>
                          <span>—</span>
                        </li>
                      ) : null}
                    </ul>
                  </div>
                  <div>
                    <span className="ops-split__label">P95 延迟</span>
                    <ul className="ops-kv">
                      <li>
                        <span>span</span>
                        <b className="mono">
                          {traces && traces.spans > 0
                            ? formatLatency(traces.p95_latency_ms)
                            : '—'}
                        </b>
                      </li>
                      <li>
                        <span>span 总数</span>
                        <b className="mono">{traces?.spans ?? '—'}</b>
                      </li>
                    </ul>
                  </div>
                </div>
                <span className="ops-split__label">span 时段分布</span>
                <HourBars data={traces?.by_hour ?? {}} />
              </div>
            </section>
          </div>

          <div className="ops-columns">
            <section className="ops-panel">
              <header className="ops-panel__head">
                <div>
                  <p className="eyebrow">OUTBOX</p>
                  <h2>待投递与死信</h2>
                </div>
                <span className="mono">SNAPSHOT</span>
              </header>
              <div className="ops-panel__body">
                {outbox.recent.length === 0 ? (
                  <p className="block-muted">
                    Outbox 为空，没有等待投递或失败的消息。
                  </p>
                ) : (
                  <ul className="ops-feed">
                    {outbox.recent.map((message) => (
                      <li key={message.id}>
                        <span
                          className={`ops-feed__dot${
                            message.status === 'FAILED' ? ' is-alert' : ''
                          }`}
                          aria-hidden="true"
                        />
                        <div>
                          <strong className="mono">{message.event_type}</strong>
                          <p>
                            {outboxStatusLabel(message.status)} · 重试{' '}
                            {message.retry_count} 次 ·{' '}
                            {formatRelative(message.updated_at)}
                          </p>
                          {message.last_error ? (
                            <small className="mono">{message.last_error}</small>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>

            <section className="ops-panel">
              <header className="ops-panel__head">
                <div>
                  <p className="eyebrow">ABANDON</p>
                  <h2>最近放弃事件</h2>
                </div>
                <span className="mono">
                  TOTAL {abandons ? abandons.total : '—'}
                </span>
              </header>
              <div className="ops-panel__body">
                {!abandons ? (
                  <p className="block-muted">放弃事件不可用。</p>
                ) : abandons.items.length === 0 ? (
                  <p className="block-muted">该租户还没有放弃事件。</p>
                ) : (
                  <ul className="ops-feed">
                    {abandons.items.map((event) => (
                      <li key={event.event_id}>
                        <span
                          className="ops-feed__dot"
                          aria-hidden="true"
                        />
                        <div>
                          <strong>{intentLabel(event.intent)}</strong>
                          <p>
                            {abandonReasonLabel(event.reason)} · {event.channel} ·{' '}
                            {formatRelative(event.created_at)}
                          </p>
                          <small className="mono">
                            {event.idle_minutes > 0
                              ? `空闲 ${event.idle_minutes} 分钟`
                              : '重复提问检出'}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          </div>

          <section className="ops-panel ops-panel--full">
            <header className="ops-panel__head">
              <div>
                <p className="eyebrow">SECURITY</p>
                <h2>安全防线</h2>
              </div>
              <span className="mono">
                EVENTS {security ? security.total : '—'}
              </span>
            </header>
            <div className="ops-panel__body">
              <div className="ops-split">
                <div>
                  <span className="ops-split__label">按类型</span>
                  <BarList
                    data={security?.by_category ?? {}}
                    labelOf={securityCategoryLabel}
                    emptyHint="该时段没有安全事件。"
                  />
                </div>
                <div>
                  <span className="ops-split__label">处置结果</span>
                  <ul className="ops-kv">
                    <li>
                      <span>已拦截</span>
                      <b className="mono">{security?.blocked ?? '—'}</b>
                    </li>
                    <li>
                      <span>已改写</span>
                      <b className="mono">{security?.redacted ?? '—'}</b>
                    </li>
                    <li>
                      <span>仅留痕</span>
                      <b className="mono">{security?.logged ?? '—'}</b>
                    </li>
                    <li>
                      <span>高危</span>
                      <b className="mono">
                        {security?.by_severity.HIGH ?? '—'}
                      </b>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
