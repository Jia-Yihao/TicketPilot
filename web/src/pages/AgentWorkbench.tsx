import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  claimHandoff,
  closeHandoff,
  errorText,
  getHandoff,
  listHandoffs,
  releaseHandoff,
} from '../api'
import { CopyValue } from '../components/CopyValue'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import {
  businessStatusLabel,
  formatFullDate,
  formatRelative,
  handoffReasonLabel,
  handoffStatusMeta,
  intentLabel,
  priorityMeta,
  queueLabel,
  sentimentLabel,
} from '../format'
import { usePageTitle } from '../hooks'
import { IDENTITY_PRESETS, useIdentity } from '../identity'
import type { HandoffDetail, HandoffSummary } from '../types'

type CaseFilter = 'waiting' | 'active' | 'noted' | 'ended' | 'all'

const FILTERS: { key: CaseFilter; label: string; statuses: string[] }[] = [
  {
    key: 'waiting',
    label: '待接入',
    statuses: ['HANDOFF_PENDING', 'QUEUED'],
  },
  { key: 'active', label: '处理中', statuses: ['HUMAN_ACTIVE'] },
  { key: 'noted', label: '抄送', statuses: ['NOTED'] },
  { key: 'ended', label: '已结束', statuses: ['CLOSED', 'RELEASED'] },
  {
    key: 'all',
    label: '全部',
    statuses: [
      'HANDOFF_PENDING',
      'QUEUED',
      'HUMAN_ACTIVE',
      'NOTED',
      'CLOSED',
      'RELEASED',
    ],
  },
]

const TAKEOVER_STATES = new Set(['HANDOFF_PENDING', 'QUEUED', 'HUMAN_ACTIVE'])

const SLOT_LABELS: Record<string, string> = {
  order_id: '订单号',
  amount_cents: '退款金额',
  product_id: '商品',
  pending_intent: '待补意图',
  last_intent: '上一意图',
}

const RESOLUTIONS = [
  { value: 'RESOLVED', label: '已解决' },
  { value: 'FOLLOW_UP', label: '转跟进' },
  { value: 'DUPLICATE', label: '重复工单' },
]

const SEAT = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'seat' && preset.tenant === 'demo',
)

function HandoffBadge({ status }: { status: string | null | undefined }) {
  const meta = handoffStatusMeta(status)
  return (
    <span className={`badge badge--${meta.tone}`}>
      <span className="badge__dot" aria-hidden="true" />
      {meta.label}
    </span>
  )
}

function slotValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value)
}

function waitingTime(createdAt: string): string {
  return formatRelative(createdAt)
}

export function AgentWorkbench() {
  usePageTitle('坐席工作台')
  const { identity, canOperate, setIdentity } = useIdentity()
  const [filter, setFilter] = useState<CaseFilter>('waiting')
  const [items, setItems] = useState<HandoffSummary[]>([])
  const [total, setTotal] = useState(0)
  const [listLoading, setListLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [listError, setListError] = useState<unknown>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<HandoffDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<unknown>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [resolution, setResolution] = useState('RESOLVED')
  const [closeOpen, setCloseOpen] = useState(false)
  const detailRef = useRef<HTMLDivElement | null>(null)

  const statuses =
    FILTERS.find((item) => item.key === filter)?.statuses ?? []

  useEffect(() => {
    if (!canOperate) return
    let cancelled = false
    if (items.length === 0) setListLoading(true)
    else setRefreshing(true)
    listHandoffs(identity, { statuses, limit: 100 })
      .then((data) => {
        if (cancelled) return
        // The seat works the oldest case first, not the newest.
        const sorted = [...data.items].sort((left, right) =>
          left.created_at.localeCompare(right.created_at),
        )
        setItems(sorted)
        setTotal(data.total)
        setListError(null)
        setSelectedId((current) => {
          if (current && sorted.some((item) => item.event_id === current)) {
            return current
          }
          return sorted[0]?.event_id ?? null
        })
      })
      .catch((errorValue: unknown) => {
        if (!cancelled) setListError(errorValue)
      })
      .finally(() => {
        if (!cancelled) {
          setListLoading(false)
          setRefreshing(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [identity, filter, refreshTick, canOperate])

  useEffect(() => {
    if (!canOperate) return
    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1)
    }, 20_000)
    return () => window.clearInterval(timer)
  }, [canOperate])

  useEffect(() => {
    if (!selectedId || !canOperate) {
      setDetail(null)
      setDetailError(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    getHandoff(identity, selectedId)
      .then((data) => {
        if (cancelled) return
        setDetail(data)
        setDetailError(null)
      })
      .catch((errorValue: unknown) => {
        if (!cancelled) setDetailError(errorValue)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [identity, selectedId, refreshTick, canOperate])

  function refresh() {
    setRefreshTick((value) => value + 1)
  }

  function selectCase(eventId: string) {
    setSelectedId(eventId)
    setActionError(null)
    setActionNotice(null)
    setCloseOpen(false)
    setNote('')
    if (window.matchMedia('(max-width: 980px)').matches) {
      window.setTimeout(() => {
        detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 80)
    }
  }

  function changeFilter(next: CaseFilter) {
    if (next === filter) return
    setFilter(next)
    setSelectedId(null)
    setDetail(null)
    setActionError(null)
    setActionNotice(null)
    setCloseOpen(false)
  }

  async function runAction(
    action: 'claim' | 'release' | 'close',
    request: () => Promise<HandoffDetail>,
    message: string,
  ) {
    setActionBusy(action)
    setActionError(null)
    try {
      const updated = await request()
      setDetail(updated)
      setActionNotice(message)
      setCloseOpen(false)
      setNote('')
      refresh()
    } catch (errorValue) {
      setActionError(errorText(errorValue))
    } finally {
      setActionBusy(null)
    }
  }

  if (!canOperate) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="AGENT WORKBENCH / 转人工接管"
          title="坐席工作台"
          description="租户内的转人工队列、上下文移交包与接管状态。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="lock" size={26} />
          </span>
          <p className="eyebrow">ROLE GATE / SUPPORT REQUIRED</p>
          <h2>当前身份无权进入坐席队列</h2>
          <p className="gate__copy">
            当前身份是 {identity.tenant} / {identity.user}
            ，没有 support 权限。切换为演示坐席后，可以查看 demo
            租户内等待人工接管的会话。
          </p>
          <div className="gate__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                if (SEAT) setIdentity(SEAT)
              }}
            >
              切换为 Seat
              <Icon name="arrow" size={16} />
            </button>
            <Link className="button button--ghost" to="/conversation">
              返回售后对话
            </Link>
          </div>
          <ul className="gate__facts">
            <li>
              <Icon name="shield" size={15} />
              队列与案件按租户隔离
            </li>
            <li>
              <Icon name="eye" size={15} />
              移交包是冻结快照，坐席与审计看到同一份
            </li>
            <li>
              <Icon name="clock" size={15} />
              未及时接入的案件会自动转入排队
            </li>
          </ul>
        </section>
      </div>
    )
  }

  const activeFilter = FILTERS.find((item) => item.key === filter) ?? FILTERS[0]
  const bundle = detail?.bundle ?? {}
  const intents = bundle.intent_history ?? []
  const actions = bundle.attempted_actions ?? []
  const candidates = bundle.attempted_candidates ?? []
  const citations = bundle.policy_refs ?? []
  const slots = Object.entries(bundle.slots ?? {})
  const sentiment = bundle.sentiment
  const status = detail?.status ?? ''
  const isTakeover = TAKEOVER_STATES.has(status)
  const canClaim = status === 'HANDOFF_PENDING' || status === 'QUEUED'
  const canRelease = isTakeover
  const canClose =
    isTakeover || status === 'NOTED'

  return (
    <div className="page page--workbench">
      <PageHeader
        eyebrow="AGENT WORKBENCH / 转人工接管"
        title="坐席工作台"
        description="按等待时间从早到晚处理转人工会话。接管期间 Agent 保持静默；交还后由 Agent 继续服务。"
        actions={
          <div className="header-tools">
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={refresh}
              disabled={refreshing}
            >
              <Icon
                name="refresh"
                size={15}
                className={refreshing ? 'is-spinning' : undefined}
              />
              刷新
            </button>
          </div>
        }
      />

      <div className="tabs" role="tablist" aria-label="案件状态筛选">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={filter === item.key}
            className={`tab${filter === item.key ? ' is-active' : ''}`}
            onClick={() => changeFilter(item.key)}
          >
            {item.label}
            <span className="tab__count">
              {filter === item.key ? items.length : '·'}
            </span>
          </button>
        ))}
        <span className="task-toolbar__total mono">
          {activeFilter.label.toUpperCase()} · {items.length}/{total}
        </span>
      </div>

      <div className="workbench-grid">
        <section className="queue-panel" aria-label="转人工队列">
          <header className="queue-panel__head">
            <div>
              <span className="mono queue-panel__eyebrow">HANDOFF QUEUE</span>
              <h2>{activeFilter.label}</h2>
            </div>
            <span className="queue-panel__count mono">
              {items.length} / {total}
            </span>
          </header>

          {listError && items.length > 0 ? (
            <div className="inline-error" role="alert">
              <Icon name="alert" size={15} />
              队列刷新失败：{errorText(listError)}
            </div>
          ) : null}

          <div className="queue-list">
            {listLoading && items.length === 0 ? (
              <div className="queue-skeleton" aria-hidden="true">
                {[0, 1, 2].map((row) => (
                  <div key={row} className="queue-skeleton__item">
                    <span
                      className="skeleton skeleton--line"
                      style={{ width: '38%' }}
                    />
                    <span
                      className="skeleton skeleton--line"
                      style={{ width: '62%' }}
                    />
                    <span
                      className="skeleton skeleton--line"
                      style={{ width: '84%' }}
                    />
                  </div>
                ))}
              </div>
            ) : listError && items.length === 0 ? (
              <ErrorPanel
                title="无法读取坐席队列"
                error={listError}
                onRetry={refresh}
              />
            ) : items.length === 0 ? (
              <div className="queue-empty">
                <span className="queue-empty__icon">
                  <Icon name="inbox" size={24} />
                </span>
                <p>
                  {filter === 'waiting'
                    ? '当前没有等待接入的会话'
                    : '该状态下暂无案件'}
                </p>
                <small>
                  {filter === 'waiting'
                    ? '客户要求转人工或触发升级信号后，案件会出现在这里。'
                    : '切换其他状态查看历史接管记录。'}
                </small>
              </div>
            ) : (
              items.map((item) => {
                const priority = priorityMeta(item.priority)
                const meta = handoffStatusMeta(item.status)
                return (
                  <button
                    key={item.event_id}
                    type="button"
                    className={`queue-item${
                      selectedId === item.event_id ? ' is-active' : ''
                    }${item.priority === 'P0' ? ' is-urgent' : ''}`}
                    onClick={() => selectCase(item.event_id)}
                    aria-pressed={selectedId === item.event_id}
                  >
                    <span className="queue-item__top">
                      <span className="queue-item__order mono">
                        {queueLabel(item.queue)} · {item.event_id}
                      </span>
                      <HandoffBadge status={item.status} />
                    </span>
                    <span className="queue-item__amount">
                      {handoffReasonLabel(item.reason)}
                      <small>{priority.label}</small>
                    </span>
                    <span className="queue-item__meta">
                      <span>{intentLabel(item.intent)}</span>
                      <span className="queue-item__dot" aria-hidden="true">
                        ·
                      </span>
                      <span>{item.claimed_by ?? '未认领'}</span>
                      <span className="queue-item__countdown">
                        <Icon name="clock" size={12} />
                        {waitingTime(item.created_at)}
                      </span>
                    </span>
                    <span className="queue-item__meta">
                      <span className="bundle-hint">{meta.hint}</span>
                    </span>
                  </button>
                )
              })
            )}
          </div>
          <footer className="queue-panel__foot">
            <span className="mono">SORT / WAITING ASC</span>
            <span>20 秒自动刷新</span>
          </footer>
        </section>

        <section className="case-panel" aria-label="移交案件" ref={detailRef}>
          {detailLoading && !detail ? (
            <div className="case-skeleton" aria-hidden="true">
              <span
                className="skeleton skeleton--line"
                style={{ width: '26%' }}
              />
              <span className="skeleton skeleton--block" />
              <span
                className="skeleton skeleton--line"
                style={{ width: '64%' }}
              />
              <div className="case-skeleton__row">
                <span className="skeleton skeleton--block" />
                <span className="skeleton skeleton--block" />
              </div>
            </div>
          ) : detailError && !detail ? (
            <div className="case-error">
              <ErrorPanel
                title="无法读取移交案件"
                error={detailError}
                onRetry={refresh}
              />
            </div>
          ) : !detail ? (
            <div className="case-empty">
              <span className="case-empty__stamp mono">TP</span>
              <p className="eyebrow">HANDOFF CASE / EMPTY</p>
              <h2>从队列中选择一条案件</h2>
              <p>
                查看移交包里的意图轨迹、槽位、已尝试动作与政策引用，再决定是否接管。
              </p>
            </div>
          ) : (
            <>
              <header className="case-head">
                <div>
                  <p className="eyebrow mono">
                    HANDOFF / {detail.event_id}
                  </p>
                  <h2>{handoffReasonLabel(detail.reason)}</h2>
                  <p className="case-head__meta">
                    <span>{queueLabel(detail.queue)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{priorityMeta(detail.priority).label}</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatFullDate(detail.created_at)}</span>
                    <span aria-hidden="true">·</span>
                    <span className="mono">{detail.signal_id}</span>
                  </p>
                </div>
                <div className="case-head__status">
                  <HandoffBadge status={detail.status} />
                  {detail.trace_id ? (
                    <Link
                      className="text-link"
                      to={`/traces/${detail.trace_id}`}
                    >
                      查看 Trace
                      <Icon name="external" size={12} />
                    </Link>
                  ) : null}
                </div>
              </header>

              <div
                className={`silence-strip${
                  isTakeover ? ' is-silent' : ''
                }`}
                role="status"
              >
                <Icon
                  name={isTakeover ? 'lock' : status === 'NOTED' ? 'eye' : 'chat'}
                  size={16}
                />
                <div>
                  <strong>
                    {isTakeover
                      ? 'Agent 静默中'
                      : status === 'NOTED'
                        ? 'Agent 仍在服务'
                        : 'Agent 已恢复服务'}
                  </strong>
                  <span>{handoffStatusMeta(detail.status).hint}</span>
                </div>
              </div>

              {actionNotice ? (
                <div className="notice notice--info" role="status">
                  <span className="notice__icon">
                    <Icon name="check" size={17} />
                  </span>
                  <div className="notice__body">
                    <strong>操作完成</strong>
                    <p>{actionNotice}</p>
                  </div>
                </div>
              ) : null}

              {actionError ? (
                <div className="inline-error" role="alert">
                  <Icon name="alert" size={15} />
                  {actionError}
                </div>
              ) : null}

              <div className="case-overview">
                <div className="case-amount">
                  <span className="eyebrow">TAKEOVER</span>
                  <strong className="case-amount__value">
                    {detail.claimed_by ?? '未认领'}
                  </strong>
                  <span className="case-amount__meta">
                    {detail.claimed_at
                      ? `接入于 ${formatFullDate(detail.claimed_at)}`
                      : '等待坐席接入'}
                  </span>
                </div>
                <dl className="case-facts">
                  <div>
                    <dt>客户</dt>
                    <dd className="mono">{detail.user_id}</dd>
                  </div>
                  <div>
                    <dt>会话</dt>
                    <dd>
                      <CopyValue
                        value={detail.conversation_id}
                        display={detail.conversation_id}
                        label="复制会话编号"
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>移交摘要</dt>
                    <dd className="mono">
                      {detail.transcript_digest
                        ? `${detail.transcript_digest.slice(0, 10)}…`
                        : '—'}
                    </dd>
                  </div>
                </dl>
              </div>

              {bundle.history_summary ? (
                <div className="notice notice--info">
                  <span className="notice__icon">
                    <Icon name="user" size={17} />
                  </span>
                  <div className="notice__body">
                    <strong>
                      客户档案摘要
                      {bundle.history_summary_source ? (
                        <span className="mono">
                          {bundle.history_summary_source}
                        </span>
                      ) : null}
                      {typeof bundle.history_summary_max_chars === 'number' ? (
                        <span className="mono">
                          上限 {bundle.history_summary_max_chars} 字
                        </span>
                      ) : null}
                    </strong>
                    <p>{bundle.history_summary}</p>
                  </div>
                </div>
              ) : null}

              {bundle.suggested_next_step ? (
                <section className="case-block case-block--next">
                  <header>
                    <h3>建议下一步</h3>
                    <span className="mono">NEXT STEP</span>
                  </header>
                  <p>{bundle.suggested_next_step}</p>
                </section>
              ) : null}

              <div className="case-columns">
                <section className="case-block">
                  <header>
                    <h3>会话轨迹</h3>
                    <span className="mono">TURNS / {intents.length}</span>
                  </header>
                  {intents.length === 0 ? (
                    <p className="block-muted">移交包里没有意图轨迹。</p>
                  ) : (
                    <ol className="bundle-timeline">
                      {intents.map((record) => (
                        <li key={`${record.turn}-${record.at}`}>
                          <span className="mono bundle-timeline__turn">
                            {String(record.turn).padStart(2, '0')}
                          </span>
                          <div>
                            <strong>{intentLabel(record.intent)}</strong>
                            <p>{record.user_message}</p>
                            <small className="mono">
                              {record.outcome} · {formatFullDate(record.at)}
                            </small>
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>

                <section className="case-block">
                  <header>
                    <h3>情绪与会话槽位</h3>
                    <span className="mono">CONTEXT</span>
                  </header>
                  <dl className="data-list data-list--compact">
                    <div>
                      <dt>情绪</dt>
                      <dd>
                        {sentiment
                          ? `${sentimentLabel(sentiment.label)} · ${sentiment.score.toFixed(2)}`
                          : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>命中线索</dt>
                      <dd>
                        {sentiment && sentiment.signals.length > 0
                          ? sentiment.signals.join('、')
                          : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>渠道</dt>
                      <dd className="mono">{bundle.channel ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>移交引用</dt>
                      <dd className="mono bundle-ref">
                        {bundle.transcript_ref ?? '—'}
                      </dd>
                    </div>
                  </dl>
                  {slots.length > 0 ? (
                    <dl className="data-list data-list--compact">
                      {slots.map(([key, value]) => (
                        <div key={key}>
                          <dt>{SLOT_LABELS[key] ?? key}</dt>
                          <dd className="mono">{slotValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="block-muted">会话没有已确认的业务槽位。</p>
                  )}
                </section>
              </div>

              <div className="case-columns">
                <section className="case-block">
                  <header>
                    <h3>已尝试动作</h3>
                    <span className="mono">ATTEMPTED / {actions.length}</span>
                  </header>
                  {actions.length === 0 ? (
                    <p className="block-muted">Agent 还没有执行过动作。</p>
                  ) : (
                    <ul className="bundle-actions">
                      {actions.map((action, index) => (
                        <li key={`${action.action}-${index}`}>
                          <span
                            className={`bundle-actions__mark${
                              action.ok ? ' is-ok' : ' is-failed'
                            }`}
                          >
                            <Icon
                              name={action.ok ? 'check' : 'x'}
                              size={14}
                            />
                          </span>
                          <div>
                            <strong className="mono">{action.action}</strong>
                            <p>{action.result}</p>
                            <small className="mono">
                              {intentLabel(action.intent)} ·{' '}
                              {formatFullDate(action.at)}
                            </small>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="case-block">
                  <header>
                    <h3>已展示候选</h3>
                    <span className="mono">CANDIDATES / {candidates.length}</span>
                  </header>
                  {candidates.length === 0 ? (
                    <p className="block-muted">Agent 没有给出过订单候选。</p>
                  ) : (
                    <ul className="bundle-actions">
                      {candidates.map((candidate) => (
                        <li key={candidate.order_id}>
                          <span className="bundle-actions__mark is-offered">
                            <Icon name="eye" size={14} />
                          </span>
                          <div>
                            <strong className="mono">{candidate.order_id}</strong>
                            <p>
                              {candidate.product_name} ·{' '}
                              {businessStatusLabel(candidate.status)}
                            </p>
                            <small className="mono">
                              {candidate.delivered_at
                                ? `签收 ${formatFullDate(candidate.delivered_at)}`
                                : '未签收'}
                            </small>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="case-block">
                  <header>
                    <h3>政策引用</h3>
                    <span className="mono">CITATIONS / {citations.length}</span>
                  </header>
                  {citations.length === 0 ? (
                    <p className="block-muted">Agent 没有引用政策章节。</p>
                  ) : (
                    <ul className="bundle-citations">
                      {citations.map((citation) => (
                        <li key={`${citation.document_id}-${citation.section}`}>
                          <strong>{citation.title}</strong>
                          <span className="mono">
                            {citation.version}
                            {citation.section ? ` · ${citation.section}` : ''}
                          </span>
                          {citation.source_uri ? (
                            <small className="mono">
                              {citation.source_uri}
                            </small>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                  {detail.pending_task_id ? (
                    <p className="bundle-task-link">
                      关联任务
                      <Link
                        className="text-link"
                        to={`/tasks/${detail.pending_task_id}`}
                      >
                        {detail.pending_task_id}
                        <Icon name="arrow" size={12} />
                      </Link>
                    </p>
                  ) : null}
                </section>
              </div>

              <section className="decision" aria-label="接管动作">
                <header className="decision__head">
                  <div>
                    <p className="eyebrow">TAKEOVER ACTIONS</p>
                    <h3>{isTakeover ? '接管处理' : '案件已结束'}</h3>
                  </div>
                  <span className="mono decision__identity">
                    SEAT / {identity.user}
                  </span>
                </header>

                {canClaim || canRelease ? (
                  <>
                    <label className="field">
                      <span className="field__label">
                        交还备注
                        <small>交还 Agent 时写入审计</small>
                      </span>
                      <textarea
                        className="textarea"
                        rows={2}
                        maxLength={500}
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                        placeholder="例如：已核对订单，客户接受补发方案。"
                        disabled={actionBusy !== null}
                      />
                    </label>

                    {closeOpen ? (
                      <div className="confirm-strip confirm-strip--reject">
                        <div>
                          <strong>确认结案？</strong>
                          <p>结案后会话不再回到 Agent 服务，处理记录写入审计。</p>
                          <label className="field field--inline">
                            <span className="field__label">结案结论</span>
                            <select
                              className="select"
                              value={resolution}
                              onChange={(event) =>
                                setResolution(event.target.value)
                              }
                              disabled={actionBusy !== null}
                            >
                              {RESOLUTIONS.map((item) => (
                                <option key={item.value} value={item.value}>
                                  {item.label}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <div className="confirm-strip__actions">
                          <button
                            type="button"
                            className="button button--ghost button--sm"
                            onClick={() => setCloseOpen(false)}
                            disabled={actionBusy !== null}
                          >
                            返回
                          </button>
                          <button
                            type="button"
                            className="button button--danger-solid button--sm"
                            onClick={() =>
                              void runAction(
                                'close',
                                () =>
                                  closeHandoff(
                                    identity,
                                    detail.event_id,
                                    resolution,
                                  ),
                                '案件已结案。',
                              )
                            }
                            disabled={actionBusy !== null}
                          >
                            {actionBusy === 'close' ? '结案中…' : '确认结案'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="decision__actions">
                        {canClaim ? (
                          <button
                            type="button"
                            className="button button--primary"
                            onClick={() =>
                              void runAction(
                                'claim',
                                () => claimHandoff(identity, detail.event_id),
                                '已接管会话，Agent 现在保持静默。',
                              )
                            }
                            disabled={actionBusy !== null}
                          >
                            <Icon name="user" size={15} />
                            {actionBusy === 'claim' ? '接管中…' : '接管会话'}
                          </button>
                        ) : null}
                        {canRelease ? (
                          <button
                            type="button"
                            className="button button--ghost"
                            onClick={() =>
                              void runAction(
                                'release',
                                () =>
                                  releaseHandoff(identity, detail.event_id, note),
                                '已交还 Agent，后续由 Agent 继续服务。',
                              )
                            }
                            disabled={actionBusy !== null}
                          >
                            <Icon name="chat" size={15} />
                            {actionBusy === 'release' ? '交还中…' : '交还 Agent'}
                          </button>
                        ) : null}
                        {canClose ? (
                          <button
                            type="button"
                            className="button button--ghost button--danger"
                            onClick={() => setCloseOpen(true)}
                            disabled={actionBusy !== null}
                          >
                            <Icon name="x" size={15} />
                            结案
                          </button>
                        ) : null}
                      </div>
                    )}
                    <p className="decision__foot">
                      接管期间 Agent 不会回复该会话；交还或结案后状态会同步到客户侧。
                    </p>
                  </>
                ) : (
                  <dl className="data-list data-list--inline">
                    <div>
                      <dt>状态</dt>
                      <dd>
                        <HandoffBadge status={detail.status} />
                      </dd>
                    </div>
                    <div>
                      <dt>{detail.released_by ? '交还人' : '结案人'}</dt>
                      <dd className="mono">
                        {detail.released_by ?? detail.closed_by ?? '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>结论</dt>
                      <dd>{detail.resolution ?? '—'}</dd>
                    </div>
                  </dl>
                )}
              </section>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
