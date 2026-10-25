import {
  useEffect,
  useRef,
  useState,
} from 'react'
import type { CSSProperties } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ApiError,
  approveTask,
  errorText,
  getApproval,
  listApprovals,
} from '../api'
import { CopyValue } from '../components/CopyValue'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import {
  formatCountdown,
  formatFullDate,
  formatMoney,
  formatRelative,
  orderStatusLabel,
  outcomeMeta,
  parseUtc,
  shortDigest,
} from '../format'
import { useNow, usePageTitle } from '../hooks'
import { IDENTITY_PRESETS, useIdentity } from '../identity'
import type {
  ApprovalDetail,
  ApprovalListItem,
  TaskSnapshot,
} from '../types'

type FilterKey = 'pending' | 'approved' | 'done' | 'rejected' | 'all'

const FILTERS: {
  key: FilterKey
  label: string
  countKey: string | null
  statuses: string[]
}[] = [
  { key: 'pending', label: '待审批', countKey: 'PENDING', statuses: ['PENDING'] },
  { key: 'approved', label: '执行中', countKey: 'APPROVED', statuses: ['APPROVED'] },
  { key: 'done', label: '已完成', countKey: 'CONSUMED', statuses: ['CONSUMED'] },
  { key: 'rejected', label: '已驳回', countKey: 'REJECTED', statuses: ['REJECTED'] },
  { key: 'all', label: '全部', countKey: null, statuses: [] },
]

const STATS: { key: FilterKey; label: string; countKey: string; hint: string }[] = [
  { key: 'pending', label: '待审批', countKey: 'PENDING', hint: '等待四眼复核' },
  { key: 'approved', label: '执行中', countKey: 'APPROVED', hint: '已批准待执行' },
  { key: 'done', label: '已完成', countKey: 'CONSUMED', hint: '退款已验证' },
  { key: 'rejected', label: '已驳回', countKey: 'REJECTED', hint: '未执行退款' },
]

const REVIEWER = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'reviewer' && preset.tenant === 'demo',
)

type SafetyState = 'pass' | 'block' | 'idle'

interface SafetyCheck {
  name: string
  caption: string
  detail: string
  state: SafetyState
}

function filterStatuses(key: FilterKey): string[] {
  return FILTERS.find((item) => item.key === key)?.statuses ?? []
}

function payloadString(detail: ApprovalDetail, key: string): string | null {
  const value = detail.action_payload[key]
  return typeof value === 'string' ? value : null
}

function payloadNumber(detail: ApprovalDetail, key: string): number | null {
  const value = detail.action_payload[key]
  return typeof value === 'number' ? value : null
}

function deriveSafety(
  detail: ApprovalDetail,
  isApprover: boolean,
  currentUser: string,
  now: number,
): SafetyCheck[] {
  const isPending = detail.status === 'PENDING'
  const order = detail.order
  const amount = formatMoney(detail.amount_cents, detail.currency)
  const expiresAt = parseUtc(detail.expires_at)
  const resolvedAt = parseUtc(detail.resolved_at)
  const expired = !expiresAt || expiresAt.getTime() <= now

  let eligible: SafetyCheck
  if (isPending) {
    if (!order) {
      eligible = {
        name: '资格',
        state: 'block',
        caption: '缺少订单快照',
        detail: '无法核对可退余额',
      }
    } else if (order.refundable_cents >= detail.amount_cents) {
      eligible = {
        name: '资格',
        state: 'pass',
        caption: '可退余额充足',
        detail: `${formatMoney(order.refundable_cents, order.currency)} ≥ ${amount}`,
      }
    } else {
      eligible = {
        name: '资格',
        state: 'block',
        caption: '可退余额不足',
        detail: `${formatMoney(order.refundable_cents, order.currency)} < ${amount}`,
      }
    }
  } else if (['APPROVED', 'CONSUMED', 'REJECTED'].includes(detail.status)) {
    eligible = {
      name: '资格',
      state: 'pass',
      caption: '申请时已完成校验',
      detail: order
        ? `审批快照 v${detail.resource_version}`
        : '订单快照已归档',
    }
  } else {
    eligible = {
      name: '资格',
      state: 'idle',
      caption: '未执行退款',
      detail: '申请未进入有效审批',
    }
  }

  const boundFields =
    detail.action_payload.action === 'REFUND' &&
    payloadString(detail, 'order_id') === detail.order_id &&
    payloadNumber(detail, 'amount_cents') === detail.amount_cents &&
    payloadNumber(detail, 'resource_version') === detail.resource_version
  const versionAligned = !order || order.version === detail.resource_version
  const bindingPass =
    Boolean(detail.action_digest) && boundFields && (!isPending || versionAligned)
  const policyVersion = payloadString(detail, 'policy_version') ?? '—'

  const binding: SafetyCheck = {
    name: '绑定',
    state: bindingPass ? 'pass' : 'block',
    caption: bindingPass ? '摘要与快照一致' : '摘要绑定未通过',
    detail: bindingPass
      ? `政策 ${policyVersion} · 订单 v${detail.resource_version}`
      : isPending && !versionAligned
        ? `订单已从 v${detail.resource_version} 推进到 v${order?.version}`
        : '摘要缺失或载荷字段被修改',
  }

  let fourEyes: SafetyCheck
  if (detail.reviewer_id) {
    const separated = detail.reviewer_id !== detail.requested_by
    fourEyes = {
      name: '四眼',
      state: separated ? 'pass' : 'block',
      caption: separated ? '双人复核完成' : '违反四眼规则',
      detail: `复核人 ${detail.reviewer_id} · 发起人 ${detail.requested_by}`,
    }
  } else if (isPending) {
    if (!isApprover) {
      fourEyes = {
        name: '四眼',
        state: 'block',
        caption: '需要审批角色',
        detail: '当前身份没有 approver 权限',
      }
    } else if (detail.requested_by === currentUser) {
      fourEyes = {
        name: '四眼',
        state: 'block',
        caption: '不可自审',
        detail: '请求人与复核人必须分离',
      }
    } else {
      fourEyes = {
        name: '四眼',
        state: 'pass',
        caption: '请求人与复核人分离',
        detail: `发起人 ${detail.requested_by} · 当前复核 ${currentUser}`,
      }
    }
  } else {
    fourEyes = {
      name: '四眼',
      state: 'idle',
      caption: '尚未完成复核',
      detail: '无复核人记录',
    }
  }

  let timing: SafetyCheck
  if (isPending) {
    const remaining = formatCountdown(detail.expires_at, now)
    timing = expired
      ? {
          name: '时效',
          state: 'block',
          caption: '审批已过期',
          detail: `到期 ${formatFullDate(detail.expires_at)}`,
        }
      : {
          name: '时效',
          state: 'pass',
          caption: '仍在有效期内',
          detail: `剩余 ${remaining.label}`,
        }
  } else if (detail.status === 'EXPIRED') {
    timing = {
      name: '时效',
      state: 'block',
      caption: '超过审批时效',
      detail: `到期 ${formatFullDate(detail.expires_at)}`,
    }
  } else if (detail.status === 'INVALID') {
    timing = {
      name: '时效',
      state: 'block',
      caption: '审批已失效',
      detail: '摘要或资源版本不再匹配',
    }
  } else if (detail.status === 'CANCELLED') {
    timing = {
      name: '时效',
      state: 'idle',
      caption: '申请已取消',
      detail: '任务或审批被主动关闭',
    }
  } else {
    const inWindow =
      !resolvedAt || !expiresAt || resolvedAt.getTime() <= expiresAt.getTime()
    timing = {
      name: '时效',
      state: inWindow ? 'pass' : 'block',
      caption: inWindow ? '在有效期内处理' : '超时处理',
      detail: resolvedAt
        ? `处理于 ${formatFullDate(detail.resolved_at)}`
        : `到期 ${formatFullDate(detail.expires_at)}`,
    }
  }

  return [eligible, binding, fourEyes, timing]
}

function SafetyStrip({
  checks,
  resolved,
}: {
  checks: SafetyCheck[]
  resolved: boolean
}) {
  return (
    <section className="safety" aria-label="执行前安全校验">
      <header className="safety__head">
        <div>
          <p className="eyebrow">CONTROL STRIP</p>
          <h3>{resolved ? '审批控制项' : '执行前安全校验'}</h3>
        </div>
        <span className="mono safety__mode">SERVER-AUTHORITATIVE</span>
      </header>
      <ol className="safety__grid">
        {checks.map((check, index) => (
          <li key={check.name} className={`safety-check is-${check.state}`}>
            <span className="safety-check__stamp" aria-hidden="true">
              <Icon
                name={
                  check.state === 'pass'
                    ? 'check'
                    : check.state === 'block'
                      ? 'x'
                      : 'clock'
                }
                size={17}
              />
            </span>
            <div className="safety-check__body">
              <span className="safety-check__top">
                <strong>
                  <span className="safety-check__index mono">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  {check.name}
                </strong>
                <span className="safety-check__state mono">
                  {check.state === 'pass'
                    ? 'PASS'
                    : check.state === 'block'
                      ? 'BLOCK'
                      : 'HOLD'}
                </span>
              </span>
              <p>{check.caption}</p>
              <small>{check.detail}</small>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function QueueSkeleton() {
  return (
    <div className="queue-skeleton" aria-hidden="true">
      {[0, 1, 2].map((item) => (
        <div key={item} className="queue-skeleton__item">
          <span className="skeleton skeleton--line" style={{ width: '34%' }} />
          <span className="skeleton skeleton--line" style={{ width: '58%' }} />
          <span className="skeleton skeleton--line" style={{ width: '78%' }} />
        </div>
      ))}
    </div>
  )
}

function CaseSkeleton() {
  return (
    <div className="case-skeleton" aria-hidden="true">
      <span className="skeleton skeleton--line" style={{ width: '26%' }} />
      <span className="skeleton skeleton--block" />
      <span className="skeleton skeleton--line" style={{ width: '64%' }} />
      <div className="case-skeleton__row">
        <span className="skeleton skeleton--block" />
        <span className="skeleton skeleton--block" />
      </div>
    </div>
  )
}

export function ApprovalWorkbench() {
  usePageTitle('审批工作台')
  const { identity, isApprover, setIdentity } = useIdentity()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const now = useNow(1000)

  const [filter, setFilter] = useState<FilterKey>('pending')
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    searchParams.get('focus'),
  )
  const [items, setItems] = useState<ApprovalListItem[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [listLoading, setListLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [listError, setListError] = useState<unknown>(null)
  const [detail, setDetail] = useState<ApprovalDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<unknown>(null)
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)

  const [decision, setDecision] = useState<'APPROVE' | 'REJECT' | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionResult, setActionResult] = useState<TaskSnapshot | null>(null)

  const hasLoadedRef = useRef(false)
  const detailRef = useRef<HTMLDivElement | null>(null)
  const resultRef = useRef<HTMLDivElement | null>(null)
  const reasonRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (!isApprover) return
    let cancelled = false
    if (!hasLoadedRef.current) setListLoading(true)
    else setRefreshing(true)

    listApprovals(identity, filterStatuses(filter), 50, 0)
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
        setCounts(data.counts)
        setListError(null)
        setLastLoadedAt(new Date())
        hasLoadedRef.current = true
        setSelectedId((current) => current ?? data.items[0]?.approval_id ?? null)
      })
      .catch((error: unknown) => {
        if (!cancelled) setListError(error)
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
  }, [identity, filter, refreshTick, isApprover])

  useEffect(() => {
    if (!isApprover) return
    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [isApprover])

  useEffect(() => {
    if (!selectedId || !isApprover) {
      setDetail(null)
      setDetailError(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    getApproval(identity, selectedId)
      .then((data) => {
        if (cancelled) return
        setDetail(data)
        setDetailError(null)
      })
      .catch((error: unknown) => {
        if (!cancelled) setDetailError(error)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [identity, selectedId, refreshTick, isApprover])

  useEffect(() => {
    if (!actionResult) return
    resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [actionResult])

  function changeFilter(next: FilterKey) {
    if (next === filter) return
    setFilter(next)
    setSelectedId(null)
    setDetail(null)
    setActionResult(null)
    setDecision(null)
    setReason('')
    setActionError(null)
  }

  function selectApproval(approvalId: string) {
    setSelectedId(approvalId)
    setActionResult(null)
    setDecision(null)
    setReason('')
    setActionError(null)
    setDetail(null)
    if (searchParams.get('focus')) {
      const next = new URLSearchParams(searchParams)
      next.delete('focus')
      setSearchParams(next, { replace: true })
    }
    if (window.matchMedia('(max-width: 980px)').matches) {
      window.setTimeout(() => {
        detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 80)
    }
  }

  function refresh() {
    setRefreshTick((value) => value + 1)
  }

  function prepareDecision(next: 'APPROVE' | 'REJECT') {
    setActionError(null)
    if (next === 'REJECT' && reason.trim().length < 2) {
      setActionError('驳回必须填写原因（至少 2 个字符），用于审计留痕。')
      reasonRef.current?.focus()
      return
    }
    setDecision(next)
  }

  async function submitDecision() {
    if (!detail || !decision) return
    if (decision === 'REJECT' && reason.trim().length < 2) {
      setActionError('驳回必须填写原因（至少 2 个字符）。')
      return
    }
    setSubmitting(true)
    setActionError(null)
    try {
      const snapshot = await approveTask(
        identity,
        detail.task_id,
        detail.approval_id,
        decision,
        decision === 'REJECT' ? reason : reason.trim() || undefined,
      )
      setActionResult(snapshot)
      setDecision(null)
      setReason('')
      setRefreshTick((value) => value + 1)
      window.dispatchEvent(new Event('ticketpilot:approvals-changed'))
    } catch (error) {
      setActionError(errorText(error))
      if (error instanceof ApiError && error.code === 'APPROVAL_EXPIRED') {
        setRefreshTick((value) => value + 1)
      }
    } finally {
      setSubmitting(false)
    }
  }

  const totalAll = Object.values(counts).reduce((sum, value) => sum + value, 0)
  const queueFilter = FILTERS.find((item) => item.key === filter)
  const isPending = detail?.status === 'PENDING'
  const expired =
    !parseUtc(detail?.expires_at) || parseUtc(detail?.expires_at)!.getTime() <= now
  const selfReview = Boolean(detail && detail.requested_by === identity.user)
  const safety = detail
    ? deriveSafety(detail, isApprover, identity.user, now)
    : []
  const eligibilityBlocked = safety[0]?.state === 'block'
  const bindingBlocked = safety[1]?.state === 'block'
  const canApprove =
    Boolean(detail) &&
    isPending &&
    isApprover &&
    !expired &&
    !selfReview &&
    !eligibilityBlocked &&
    !bindingBlocked
  const canReject = Boolean(detail) && isPending && isApprover && !expired && !selfReview

  if (!isApprover) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="APPROVAL WORKBENCH / 四眼复核"
          title="审批工作台"
          description="租户内退款申请的复核队列。审批通过后，退款工具才会被调用。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="lock" size={26} />
          </span>
          <p className="eyebrow">ROLE GATE / APPROVER REQUIRED</p>
          <h2>当前身份无权进入审批队列</h2>
          <p className="gate__copy">
            当前身份是 {identity.tenant} / {identity.user}
            ，没有 approver 权限。切换为演示审批人后，可以查看 demo
            租户内待复核的退款申请。
          </p>
          <div className="gate__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                if (REVIEWER) setIdentity(REVIEWER)
              }}
            >
              切换为 Reviewer
              <Icon name="arrow" size={16} />
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => navigate('/conversation')}
            >
              返回售后对话
            </button>
          </div>
          <ul className="gate__facts">
            <li>
              <Icon name="shield" size={15} />
              四眼规则对管理员同样生效
            </li>
            <li>
              <Icon name="lock" size={15} />
              队列按租户隔离，跨租户不可见
            </li>
            <li>
              <Icon name="clock" size={15} />
              过期申请由服务端自动失效
            </li>
          </ul>
        </section>
      </div>
    )
  }

  return (
    <div className="page page--workbench">
      <PageHeader
        eyebrow="APPROVAL WORKBENCH / 四眼复核"
        title="审批工作台"
        description="复核租户内的退款申请。界面校验只做提示，服务端会在执行前重新核对摘要与订单版本。"
        actions={
          <div className="header-tools">
            {lastLoadedAt ? (
              <span className="refresh-meta mono">
                LAST{' '}
                {lastLoadedAt.toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                  hour12: false,
                })}
              </span>
            ) : null}
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

      <section className="stat-band" aria-label="审批计数">
        {STATS.map((stat) => {
          const active = filter === stat.key
          const count = counts[stat.countKey]
          return (
            <button
              key={stat.key}
              type="button"
              className={`stat${active ? ' is-active' : ''}`}
              onClick={() => changeFilter(stat.key)}
              aria-pressed={active}
            >
              <span className="stat__label">{stat.label}</span>
              <span className="stat__value">
                {typeof count === 'number' ? count : '—'}
              </span>
              <span className="stat__hint">{stat.hint}</span>
            </button>
          )
        })}
        <div className="stat stat--note" aria-hidden="true">
          <span className="stat__label">全量记录</span>
          <span className="stat__value">{listLoading ? '—' : totalAll}</span>
          <span className="stat__hint">包含过期、失效与取消</span>
        </div>
      </section>

      <div className="tabs" role="tablist" aria-label="审批状态筛选">
        {FILTERS.map((item) => {
          const count =
            item.countKey === null ? totalAll : counts[item.countKey] ?? 0
          const active = filter === item.key
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={active}
              className={`tab${active ? ' is-active' : ''}`}
              onClick={() => changeFilter(item.key)}
            >
              {item.label}
              <span className="tab__count">{listLoading ? '·' : count}</span>
            </button>
          )
        })}
      </div>

      <div className="workbench-grid">
        <section className="queue-panel" aria-label="审批队列">
          <header className="queue-panel__head">
            <div>
              <span className="mono queue-panel__eyebrow">QUEUE</span>
              <h2>{queueFilter?.label}</h2>
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
              <QueueSkeleton />
            ) : listError && items.length === 0 ? (
              <ErrorPanel
                title="无法读取审批队列"
                error={listError}
                onRetry={refresh}
              />
            ) : items.length === 0 ? (
              <div className="queue-empty">
                <span className="queue-empty__icon">
                  <Icon name="inbox" size={24} />
                </span>
                <p>
                  {filter === 'pending'
                    ? '待办队列已清空'
                    : '该状态下暂无审批记录'}
                </p>
                <small>
                  {filter === 'pending'
                    ? '客户发起退款后，申请会出现在这里。'
                    : '切换其他状态查看历史记录。'}
                </small>
              </div>
            ) : (
              items.map((item) => {
                const expiresAt = parseUtc(item.expires_at)
                const createdAt = parseUtc(item.created_at)
                const remainingMs = expiresAt ? expiresAt.getTime() - now : 0
                const totalMs =
                  expiresAt && createdAt
                    ? expiresAt.getTime() - createdAt.getTime()
                    : 0
                const ratio =
                  totalMs > 0
                    ? Math.max(0, Math.min(1, remainingMs / totalMs))
                    : 0
                const countdown = formatCountdown(item.expires_at, now)
                const urgent = item.status === 'PENDING' && countdown.urgent
                return (
                  <button
                    key={item.approval_id}
                    type="button"
                    className={`queue-item${
                      selectedId === item.approval_id ? ' is-active' : ''
                    }${urgent ? ' is-urgent' : ''}`}
                    onClick={() => selectApproval(item.approval_id)}
                    aria-pressed={selectedId === item.approval_id}
                  >
                    <span className="queue-item__top">
                      <span className="queue-item__order mono">
                        {item.order_id}
                      </span>
                      <StatusBadge status={item.status} />
                    </span>
                    <span className="queue-item__amount">
                      {formatMoney(item.amount_cents, item.currency)}
                      <small>退款</small>
                    </span>
                    <span className="queue-item__meta">
                      <span>{item.requested_by}</span>
                      <span className="queue-item__dot" aria-hidden="true">
                        ·
                      </span>
                      <span>{formatRelative(item.created_at)}</span>
                      {item.status === 'PENDING' ? (
                        <span className="queue-item__countdown">
                          <Icon name="clock" size={12} />
                          {countdown.label}
                        </span>
                      ) : null}
                    </span>
                    {item.status === 'PENDING' ? (
                      <span
                        className="queue-item__meter"
                        aria-hidden="true"
                        style={
                          { '--remaining': `${ratio * 100}%` } as CSSProperties
                        }
                      />
                    ) : null}
                  </button>
                )
              })
            )}
          </div>
          <footer className="queue-panel__foot">
            <span className="mono">SORT / CREATED DESC</span>
            <span>30 秒自动刷新</span>
          </footer>
        </section>

        <section
          className="case-panel"
          aria-label="审批详情"
          ref={detailRef}
        >
          {detailLoading && !detail ? (
            <CaseSkeleton />
          ) : detailError && !detail ? (
            <div className="case-error">
              <ErrorPanel
                title="无法读取审批详情"
                error={detailError}
                onRetry={refresh}
              />
            </div>
          ) : !detail ? (
            <div className="case-empty">
              <span className="case-empty__stamp mono">TP</span>
              <p className="eyebrow">CASE FILE / EMPTY</p>
              <h2>从队列中选择一条审批</h2>
              <p>查看订单快照、摘要绑定与四眼校验，再做出复核决定。</p>
            </div>
          ) : (
            <>
              <header className="case-head">
                <div>
                  <p className="eyebrow mono">
                    CASE / {detail.approval_id}
                  </p>
                  <h2>退款执行审批</h2>
                  <p className="case-head__meta">
                    <span>{detail.requested_by} 发起</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatFullDate(detail.created_at)}</span>
                    <span aria-hidden="true">·</span>
                    <Link className="text-link" to={`/tasks/${detail.task_id}`}>
                      查看任务记录
                      <Icon name="external" size={12} />
                    </Link>
                  </p>
                </div>
                <div className="case-head__status">
                  <StatusBadge status={detail.status} />
                  {isPending ? (
                    <span className={`case-head__timer${expired ? ' is-expired' : ''}`}>
                      <Icon name="clock" size={13} />
                      {formatCountdown(detail.expires_at, now).label}
                    </span>
                  ) : null}
                </div>
              </header>

              {actionResult ? (
                <div
                  ref={resultRef}
                  className={`notice notice--${
                    actionResult.status === 'REJECTED'
                      ? 'rejected'
                      : actionResult.result?.outcome === 'PENDING_CONFIRMATION'
                        ? 'warn'
                        : outcomeMeta(actionResult.result?.outcome).tone ===
                            'danger'
                          ? 'error'
                          : 'success'
                  }`}
                  role="status"
                >
                  <span className="notice__icon">
                    <Icon
                      name={
                        actionResult.status === 'REJECTED' ? 'x' : 'check'
                      }
                      size={18}
                    />
                  </span>
                  <div className="notice__body">
                    <strong>
                      {actionResult.status === 'REJECTED'
                        ? '申请已驳回'
                        : '审批已通过'}
                      {actionResult.status === 'REJECTED' ? null : actionResult
                          .result?.outcome ? (
                        <span className="notice__outcome">
                          {outcomeMeta(actionResult.result.outcome).label}
                        </span>
                      ) : null}
                    </strong>
                    <p>
                      {actionResult.result?.summary ??
                        '处理完成，任务状态已更新。'}
                    </p>
                    <Link
                      className="text-link"
                      to={`/tasks/${actionResult.task_id}`}
                    >
                      查看任务时间线
                      <Icon name="arrow" size={13} />
                    </Link>
                  </div>
                </div>
              ) : null}

              <div className="case-overview">
                <div className="case-amount">
                  <span className="eyebrow">REFUND AMOUNT</span>
                  <strong className="case-amount__value">
                    {formatMoney(detail.amount_cents, detail.currency)}
                  </strong>
                  <span className="case-amount__meta">
                    {detail.currency} · 退款至原支付渠道
                  </span>
                </div>
                <dl className="case-facts">
                  <div>
                    <dt>订单</dt>
                    <dd>
                      <CopyValue
                        value={detail.order_id}
                        display={detail.order_id}
                        label="复制订单号"
                      />
                    </dd>
                  </div>
                  <div>
                    <dt>申请客户</dt>
                    <dd className="mono">
                      {detail.order?.user_id ?? detail.requested_by}
                    </dd>
                  </div>
                  <div>
                    <dt>审批号</dt>
                    <dd>
                      <CopyValue
                        value={detail.approval_id}
                        display={shortDigest(detail.approval_id)}
                        label="复制审批号"
                      />
                    </dd>
                  </div>
                </dl>
              </div>

              {!isPending ? (
                <section className="resolution" aria-label="处理记录">
                  <div className="resolution__head">
                    <div>
                      <p className="eyebrow">DECISION RECORD</p>
                      <h3>处理记录</h3>
                    </div>
                    <StatusBadge status={detail.status} />
                  </div>
                  <dl className="data-list data-list--inline">
                    <div>
                      <dt>处理人</dt>
                      <dd className="mono">{detail.reviewer_id ?? '系统'}</dd>
                    </div>
                    <div>
                      <dt>处理时间</dt>
                      <dd>{formatFullDate(detail.resolved_at)}</dd>
                    </div>
                    <div>
                      <dt>审批备注</dt>
                      <dd>{detail.reason || '—'}</dd>
                    </div>
                    <div>
                      <dt>任务结果</dt>
                      <dd>
                        <StatusBadge kind="outcome" status={detail.task_outcome} />
                      </dd>
                    </div>
                  </dl>
                </section>
              ) : null}

              <div className="case-columns">
                <section className="case-block">
                  <header>
                    <h3>订单快照</h3>
                    <span className="mono">
                      ORDER / v{detail.order?.version ?? detail.resource_version}
                    </span>
                  </header>
                  {detail.order ? (
                    <>
                      <dl className="data-list">
                        <div>
                          <dt>订单状态</dt>
                          <dd>{orderStatusLabel(detail.order.status)}</dd>
                        </div>
                        <div>
                          <dt>实付金额</dt>
                          <dd className="mono">
                            {formatMoney(
                              detail.order.total_cents,
                              detail.order.currency,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>可退余额</dt>
                          <dd className="mono data-list__accent">
                            {formatMoney(
                              detail.order.refundable_cents,
                              detail.order.currency,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>数据版本</dt>
                          <dd className="mono">v{detail.order.version}</dd>
                        </div>
                        <div>
                          <dt>支付时间</dt>
                          <dd>{formatFullDate(detail.order.paid_at)}</dd>
                        </div>
                        <div>
                          <dt>签收时间</dt>
                          <dd>{formatFullDate(detail.order.delivered_at)}</dd>
                        </div>
                      </dl>
                      {isPending &&
                      detail.order.version !== detail.resource_version ? (
                        <p className="inline-warning">
                          <Icon name="alert" size={14} />
                          订单版本已从 v{detail.resource_version} 推进到 v
                          {detail.order.version}。摘要绑定失效，无法批准执行。
                        </p>
                      ) : null}
                    </>
                  ) : (
                    <p className="block-muted">
                      订单快照缺失，无法在界面核对可退余额与归属。
                    </p>
                  )}
                </section>

                <section className="case-block">
                  <header>
                    <h3>客户请求</h3>
                    <span className="mono">TASK / {detail.task_id.slice(-10)}</span>
                  </header>
                  <blockquote className="case-quote">
                    {detail.request}
                  </blockquote>
                  <dl className="data-list data-list--compact">
                    <div>
                      <dt>发起人</dt>
                      <dd className="mono">{detail.requested_by}</dd>
                    </div>
                    <div>
                      <dt>发起时间</dt>
                      <dd>{formatFullDate(detail.created_at)}</dd>
                    </div>
                    <div>
                      <dt>任务状态</dt>
                      <dd>
                        <StatusBadge status={detail.task_status} />
                      </dd>
                    </div>
                    <div>
                      <dt>政策版本</dt>
                      <dd className="mono">
                        {payloadString(detail, 'policy_version') ?? '—'}
                      </dd>
                    </div>
                  </dl>
                </section>
              </div>

              <SafetyStrip checks={safety} resolved={!isPending} />

              <section className="digest" aria-label="摘要绑定">
                <header className="digest__head">
                  <div>
                    <p className="eyebrow">BINDING / SHA-256</p>
                    <h3>审批摘要</h3>
                  </div>
                  <CopyValue
                    value={detail.action_digest}
                    display={shortDigest(detail.action_digest)}
                    label="复制完整摘要"
                  />
                </header>
                <p className="digest__copy">
                  摘要覆盖动作、订单、金额、币种、资源版本与政策版本。
                  执行退款前，服务端会重算摘要并比对订单版本；任何漂移都会使审批失效。
                </p>
                <details className="raw-block">
                  <summary>查看签名载荷</summary>
                  <pre>{JSON.stringify(detail.action_payload, null, 2)}</pre>
                </details>
              </section>

              <section className="decision" aria-label="复核决定">
                <header className="decision__head">
                  <div>
                    <p className="eyebrow">DECISION</p>
                    <h3>{isPending ? '复核决定' : '审批已结束'}</h3>
                  </div>
                  <span className="mono decision__identity">
                    REVIEWER / {identity.user}
                  </span>
                </header>

                {!isPending ? (
                  <p className="decision__closed">
                    该申请已离开待办队列。处理结果与任务时间线保存在任务档案中。
                    <Link className="text-link" to={`/tasks/${detail.task_id}`}>
                      打开任务记录
                      <Icon name="arrow" size={13} />
                    </Link>
                  </p>
                ) : (
                  <>
                    <div className="decision__blockers">
                      {selfReview ? (
                        <p className="inline-warning">
                          <Icon name="lock" size={14} />
                          四眼规则：不能审批自己发起的申请。请切换其他审批人。
                        </p>
                      ) : null}
                      {expired ? (
                        <p className="inline-warning">
                          <Icon name="clock" size={14} />
                          审批已过期，无法继续处理；请让客户重新发起申请。
                        </p>
                      ) : null}
                      {bindingBlocked ? (
                        <p className="inline-warning">
                          <Icon name="alert" size={14} />
                          摘要绑定未通过，禁止批准执行；仍可驳回该申请。
                        </p>
                      ) : null}
                      {eligibilityBlocked ? (
                        <p className="inline-warning">
                          <Icon name="alert" size={14} />
                          可退余额不足或订单快照缺失，禁止批准执行；仍可驳回。
                        </p>
                      ) : null}
                    </div>

                    <label className="field">
                      <span className="field__label">
                        审批备注
                        <small>通过时选填，驳回时必填</small>
                      </span>
                      <textarea
                        ref={reasonRef}
                        className="textarea"
                        rows={3}
                        maxLength={500}
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        placeholder="写明复核依据，例如：已核对订单与可退余额，同意退款。"
                        disabled={!canReject || submitting}
                      />
                      <span className="field__hint">
                        <span>备注会写入审计日志。</span>
                        <span className="mono">{reason.length} / 500</span>
                      </span>
                    </label>

                    {actionError ? (
                      <div className="inline-error" role="alert">
                        <Icon name="alert" size={15} />
                        {actionError}
                      </div>
                    ) : null}

                    {decision ? (
                      <div
                        className={`confirm-strip confirm-strip--${decision.toLowerCase()}`}
                      >
                        <div>
                          <strong>
                            {decision === 'APPROVE'
                              ? `确认执行 ${formatMoney(
                                  detail.amount_cents,
                                  detail.currency,
                                )} 退款？`
                              : '确认驳回这条退款申请？'}
                          </strong>
                          <p>
                            {decision === 'APPROVE'
                              ? '确认后会立即调用退款工具并扣减可退余额，该操作不可撤销。'
                              : '驳回后不会执行退款，客户需要重新发起申请。'}
                          </p>
                        </div>
                        <div className="confirm-strip__actions">
                          <button
                            type="button"
                            className="button button--ghost button--sm"
                            onClick={() => setDecision(null)}
                            disabled={submitting}
                          >
                            返回
                          </button>
                          <button
                            type="button"
                            className={`button button--sm ${
                              decision === 'APPROVE'
                                ? 'button--primary'
                                : 'button--danger-solid'
                            }`}
                            onClick={() => void submitDecision()}
                            disabled={submitting}
                          >
                            {submitting
                              ? '提交中…'
                              : decision === 'APPROVE'
                                ? '确认执行'
                                : '确认驳回'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="decision__actions">
                        <button
                          type="button"
                          className="button button--ghost button--danger"
                          onClick={() => prepareDecision('REJECT')}
                          disabled={!canReject || submitting}
                        >
                          <Icon name="x" size={15} />
                          驳回申请
                        </button>
                        <button
                          type="button"
                          className="button button--primary"
                          onClick={() => prepareDecision('APPROVE')}
                          disabled={!canApprove || submitting}
                          title={
                            !canApprove
                              ? '当前申请不满足执行条件，请查看上方安全校验。'
                              : undefined
                          }
                        >
                          <Icon name="lock" size={15} />
                          通过并执行退款
                        </button>
                      </div>
                    )}

                    <p className="decision__foot">
                      决定会写入审计日志。通过后由服务端重新校验摘要，再调用退款工具。
                    </p>
                  </>
                )}
              </section>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
