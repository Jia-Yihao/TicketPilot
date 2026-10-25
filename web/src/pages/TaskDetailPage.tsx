import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  cancelTask,
  errorText,
  getTask,
  getTaskEvents,
} from '../api'
import { CopyValue } from '../components/CopyValue'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/StatusBadge'
import { TaskResultView } from '../components/TaskResult'
import {
  formatCountdown,
  formatDateTime,
  formatFullDate,
  formatMoney,
  intentLabel,
  outcomeMeta,
} from '../format'
import { usePageTitle } from '../hooks'
import { IDENTITY_PRESETS, useIdentity } from '../identity'
import type { TaskEvent, TaskSnapshot } from '../types'

const TERMINAL = new Set([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'TIMED_OUT',
  'REJECTED',
  'EXPIRED',
])

const EVENT_META: Record<string, { label: string; tone: string }> = {
  'task.created': { label: '任务创建', tone: 'neutral' },
  'task.running': { label: '工作流启动', tone: 'active' },
  'task.routed': { label: '意图路由', tone: 'neutral' },
  'tool.call_started': { label: '工具调用开始', tone: 'active' },
  'tool.call_completed': { label: '工具返回', tone: 'neutral' },
  'approval.requested': { label: '发起审批', tone: 'pending' },
  'approval.approved': { label: '审批通过', tone: 'success' },
  'approval.rejected': { label: '审批驳回', tone: 'danger' },
  'task.succeeded': { label: '任务完成', tone: 'success' },
  'task.failed': { label: '任务失败', tone: 'danger' },
  'task.rejected': { label: '任务驳回', tone: 'danger' },
  'task.cancelled': { label: '任务取消', tone: 'muted' },
  'task.expired': { label: '任务过期', tone: 'muted' },
  'refund.reconciled': { label: '对账完成', tone: 'active' },
}

const REVIEWER = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'reviewer' && preset.tenant === 'demo',
)

function stringField(
  source: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = source?.[key]
  return typeof value === 'string' ? value : null
}

function numberField(
  source: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  const value = source?.[key]
  return typeof value === 'number' ? value : null
}

function eventChips(event: TaskEvent): { label: string; value: string }[] {
  const data = event.data ?? {}
  const chips: { label: string; value: string }[] = []

  if (event.event === 'task.routed') {
    const intent = stringField(data, 'intent')
    const orderId = stringField(data, 'order_id')
    const source = stringField(data, 'planning_source')
    if (intent) chips.push({ label: '意图', value: intentLabel(intent) })
    if (orderId) chips.push({ label: '订单', value: orderId })
    if (source) chips.push({ label: '规划来源', value: source })
  } else if (event.event.startsWith('tool.')) {
    const tool = stringField(data, 'tool')
    if (tool) chips.push({ label: '工具', value: tool })
    if (typeof data.ok === 'boolean') {
      chips.push({ label: '结果', value: data.ok ? '成功' : '失败' })
    }
    const message = stringField(data, 'message')
    if (message) chips.push({ label: '返回', value: message })
  } else if (event.event === 'approval.requested') {
    const orderId = stringField(data, 'order_id')
    const amount = numberField(data, 'amount_cents')
    const approvalId = stringField(data, 'approval_id')
    if (orderId) chips.push({ label: '订单', value: orderId })
    if (typeof amount === 'number') {
      chips.push({ label: '金额', value: formatMoney(amount) })
    }
    if (approvalId) chips.push({ label: '审批号', value: approvalId })
  } else if (
    event.event === 'approval.approved' ||
    event.event === 'approval.rejected'
  ) {
    const approvalId = stringField(data, 'approval_id')
    if (approvalId) chips.push({ label: '审批号', value: approvalId })
  } else if (event.event === 'refund.reconciled') {
    const status = stringField(data, 'status')
    const reason = stringField(data, 'reason_code')
    if (status) chips.push({ label: '结果', value: outcomeMeta(status).label })
    if (reason) chips.push({ label: '原因码', value: reason })
  } else if (event.event.startsWith('task.')) {
    const outcome = stringField(data, 'outcome')
    const status = stringField(data, 'status')
    if (outcome) chips.push({ label: '结果', value: outcomeMeta(outcome).label })
    if (status) chips.push({ label: '状态', value: status })
  }

  if (chips.length === 0) {
    return Object.entries(data)
      .filter(
        (entry): entry is [string, string | number] =>
          entry[0] !== 'created_at' &&
          (typeof entry[1] === 'string' || typeof entry[1] === 'number'),
      )
      .slice(0, 4)
      .map(([key, value]) => ({ label: key, value: String(value) }))
  }
  return chips
}

function DetailSkeleton() {
  return (
    <div className="detail-skeleton" aria-hidden="true">
      <span className="skeleton skeleton--line" style={{ width: '24%' }} />
      <span className="skeleton skeleton--line" style={{ width: '68%', height: 28 }} />
      <span className="skeleton skeleton--line" style={{ width: '42%' }} />
      <span className="skeleton skeleton--block" />
      <span className="skeleton skeleton--block" />
    </div>
  )
}

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const { identity, isApprover, setIdentity } = useIdentity()
  const navigate = useNavigate()
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)

  usePageTitle(
    task ? `任务 ${task.task_id.slice(-8)}` : '任务详情',
  )

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    if (!task) setLoading(true)
    Promise.all([getTask(identity, taskId), getTaskEvents(identity, taskId)])
      .then(([snapshot, eventList]) => {
        if (cancelled) return
        setTask(snapshot)
        setEvents(eventList)
        setError(null)
      })
      .catch((errorValue: unknown) => {
        if (!cancelled) setError(errorValue)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [identity, taskId, refreshTick])

  const isTerminal = !task || TERMINAL.has(task.status)

  useEffect(() => {
    if (isTerminal || !taskId) return
    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1)
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [isTerminal, taskId])

  async function handleCancel() {
    if (!taskId) return
    setCancelling(true)
    setActionError(null)
    try {
      const snapshot = await cancelTask(identity, taskId)
      setTask(snapshot)
      setCancelOpen(false)
      setActionNotice('任务已取消；关联的待审批申请同步关闭。')
      setRefreshTick((value) => value + 1)
      window.dispatchEvent(new Event('ticketpilot:approvals-changed'))
    } catch (errorValue) {
      setActionError(errorText(errorValue))
    } finally {
      setCancelling(false)
    }
  }

  function openApproval(approvalId: string) {
    if (!isApprover && REVIEWER) setIdentity(REVIEWER)
    navigate(`/approvals?focus=${encodeURIComponent(approvalId)}`)
  }

  if (!taskId) {
    return (
      <div className="page">
        <ErrorPanel
          title="缺少任务编号"
          error={new Error('任务编号为空，请从任务档案进入。')}
        />
        <div className="detail-back">
          <Link className="text-link" to="/tasks">
            <Icon name="back" size={14} />
            返回任务档案
          </Link>
        </div>
      </div>
    )
  }

  if (loading && !task) {
    return (
      <div className="page">
        <div className="detail-back">
          <Link className="text-link" to="/tasks">
            <Icon name="back" size={14} />
            返回任务档案
          </Link>
        </div>
        <DetailSkeleton />
      </div>
    )
  }

  if (error && !task) {
    return (
      <div className="page">
        <div className="detail-back">
          <Link className="text-link" to="/tasks">
            <Icon name="back" size={14} />
            返回任务档案
          </Link>
        </div>
        <ErrorPanel
          title="无法读取任务记录"
          error={error}
          onRetry={() => setRefreshTick((value) => value + 1)}
        />
      </div>
    )
  }

  if (!task) return null

  const result = task.result
  const tool = task.tool_execution
  const latestApproval = task.latest_approval
  const latestStatus = stringField(latestApproval, 'status')
  const latestReviewer = stringField(latestApproval, 'reviewer_id')
  const pending = task.pending_approval
  const pendingCountdown = pending
    ? formatCountdown(pending.expires_at)
    : null

  return (
    <div className="page page--detail">
      <div className="detail-back">
        <Link className="text-link" to="/tasks">
          <Icon name="back" size={14} />
          返回任务档案
        </Link>
      </div>

      <header className="detail-head">
        <div className="detail-head__main">
          <p className="eyebrow mono">TASK RECORD / {task.task_id}</p>
          <h1>{task.request}</h1>
          <div className="detail-head__meta">
            <span>
              创建 {formatFullDate(task.created_at)}
            </span>
            <span aria-hidden="true">·</span>
            <span>更新 {formatFullDate(task.updated_at)}</span>
            <span aria-hidden="true">·</span>
            <span className="mono">v{task.version}</span>
            <CopyValue
              value={task.task_id}
              display={`${task.task_id.slice(0, 8)}…`}
              label="复制任务编号"
            />
          </div>
        </div>
        <div className="detail-head__side">
          <StatusBadge status={task.status} />
          {result?.outcome ? (
            <StatusBadge kind="outcome" status={result.outcome} />
          ) : null}
          <div className="detail-head__actions">
            {task.trace_id ? (
              <Link
                className="button button--ghost button--sm"
                to={`/traces/${encodeURIComponent(task.trace_id)}`}
              >
                <Icon name="activity" size={15} />
                查看 Trace
              </Link>
            ) : null}
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={() => setRefreshTick((value) => value + 1)}
              disabled={loading}
            >
              <Icon
                name="refresh"
                size={15}
                className={loading ? 'is-spinning' : undefined}
              />
              刷新
            </button>
            {!isTerminal ? (
              <button
                type="button"
                className="button button--quiet button--sm button--danger"
                onClick={() => {
                  setCancelOpen(true)
                  setActionError(null)
                }}
              >
                取消任务
              </button>
            ) : null}
          </div>
        </div>
      </header>

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

      {cancelOpen && !isTerminal ? (
        <div className="confirm-strip confirm-strip--reject">
          <div>
            <strong>取消这个任务？</strong>
            <p>
              任务会终止，关联的待审批申请同步关闭。已经执行的外部动作不会回滚。
            </p>
          </div>
          <div className="confirm-strip__actions">
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={() => setCancelOpen(false)}
              disabled={cancelling}
            >
              返回
            </button>
            <button
              type="button"
              className="button button--danger-solid button--sm"
              onClick={() => void handleCancel()}
              disabled={cancelling}
            >
              {cancelling ? '取消中…' : '确认取消'}
            </button>
          </div>
        </div>
      ) : null}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="panel">
            <header className="panel__head">
              <div>
                <p className="eyebrow">OUTCOME</p>
                <h2>执行结果</h2>
              </div>
              {result?.outcome ? (
                <StatusBadge kind="outcome" status={result.outcome} />
              ) : null}
            </header>
            <div className="panel__body">
              <TaskResultView
                result={result}
                onReview={openApproval}
                reviewLabel={
                  isApprover ? '进入审批工作台' : '以审批人身份复核'
                }
              />
            </div>
          </section>

          <section className="panel">
            <header className="panel__head">
              <div>
                <p className="eyebrow">EVENT STREAM</p>
                <h2>事件时间线</h2>
              </div>
              <span className="mono panel__tag">
                SSE REPLAY · {events.length}
              </span>
            </header>
            <div className="panel__body">
              {events.length === 0 ? (
                <p className="block-muted">该任务还没有事件记录。</p>
              ) : (
                <ol className="timeline">
                  {events.map((event) => {
                    const meta = EVENT_META[event.event] ?? {
                      label: event.event,
                      tone: 'neutral',
                    }
                    const chips = eventChips(event)
                    return (
                      <li
                        key={event.id}
                        className={`timeline__item tone-${meta.tone}`}
                      >
                        <span className="timeline__dot" aria-hidden="true" />
                        <div className="timeline__body">
                          <header>
                            <strong>{meta.label}</strong>
                            <span className="mono timeline__event">
                              {event.event}
                            </span>
                            <time className="mono">
                              {formatDateTime(event.created_at)}
                            </time>
                          </header>
                          {chips.length > 0 ? (
                            <div className="event-chips">
                              {chips.map((chip) => (
                                <span
                                  key={`${chip.label}-${chip.value}`}
                                  className="event-chip"
                                >
                                  <b>{chip.label}</b>
                                  <span>{chip.value}</span>
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </li>
                    )
                  })}
                </ol>
              )}
            </div>
          </section>
        </div>

        <aside className="detail-rail">
          <section className="rail-card">
            <header>
              <h3>任务元数据</h3>
              <span className="mono">META</span>
            </header>
            <dl className="data-list data-list--compact">
              <div>
                <dt>任务状态</dt>
                <dd>
                  <StatusBadge status={task.status} />
                </dd>
              </div>
              <div>
                <dt>任务版本</dt>
                <dd className="mono">v{task.version}</dd>
              </div>
              <div>
                <dt>创建时间</dt>
                <dd>{formatFullDate(task.created_at)}</dd>
              </div>
              <div>
                <dt>最后更新</dt>
                <dd>{formatFullDate(task.updated_at)}</dd>
              </div>
              <div>
                <dt>事件数量</dt>
                <dd className="mono">{events.length}</dd>
              </div>
            </dl>
          </section>

          <section className="rail-card">
            <header>
              <h3>工具执行</h3>
              <span className="mono">SIDE EFFECT</span>
            </header>
            {tool ? (
              <dl className="data-list data-list--compact">
                <div>
                  <dt>工具</dt>
                  <dd className="mono">{stringField(tool, 'tool_name') ?? '—'}</dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd className="mono">{stringField(tool, 'status') ?? '—'}</dd>
                </div>
                <div>
                  <dt>尝试次数</dt>
                  <dd className="mono">{numberField(tool, 'attempt') ?? '—'}</dd>
                </div>
                <div>
                  <dt>执行记录</dt>
                  <dd className="mono">{stringField(tool, 'id') ?? '—'}</dd>
                </div>
              </dl>
            ) : (
              <p className="rail-empty">
                本任务尚未调用有副作用的工具。查询类工具不记录执行台账。
              </p>
            )}
          </section>

          <section className="rail-card">
            <header>
              <h3>审批</h3>
              <span className="mono">APPROVAL</span>
            </header>
            {pending ? (
              <div className="approval-mini">
                <span className="eyebrow">PENDING</span>
                <strong>
                  {typeof pending.amount_cents === 'number'
                    ? formatMoney(pending.amount_cents)
                    : '退款'}{' '}
                  等待复核
                </strong>
                <span className="mono approval-mini__id">
                  {pending.approval_id}
                </span>
                <p>
                  <Icon name="clock" size={13} />
                  {pendingCountdown?.label} 后失效
                </p>
                <button
                  type="button"
                  className="button button--primary button--sm"
                  onClick={() => openApproval(pending.approval_id)}
                >
                  {isApprover ? '打开审批工单' : '以审批人身份复核'}
                  <Icon name="arrow" size={14} />
                </button>
              </div>
            ) : latestApproval ? (
              <dl className="data-list data-list--compact">
                <div>
                  <dt>状态</dt>
                  <dd>
                    {latestStatus ? (
                      <StatusBadge status={latestStatus} />
                    ) : (
                      '—'
                    )}
                  </dd>
                </div>
                <div>
                  <dt>复核人</dt>
                  <dd className="mono">{latestReviewer ?? '—'}</dd>
                </div>
                <div>
                  <dt>审批号</dt>
                  <dd className="mono">
                    {stringField(latestApproval, 'approval_id') ?? '—'}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="rail-empty">本次任务没有审批记录。</p>
            )}
          </section>

          <section className="rail-card rail-card--boundary">
            <header>
              <h3>证据边界</h3>
              <Icon name="shield" size={15} />
            </header>
            <p>
              任务结果由数据库快照与事件流组成。退款成功必须由权威退款记录验证，不以模型输出为准。
            </p>
            {Object.keys(task.context).length > 0 ? (
              <details className="raw-block">
                <summary>查看任务上下文</summary>
                <pre>{JSON.stringify(task.context, null, 2)}</pre>
              </details>
            ) : null}
          </section>
        </aside>
      </div>
    </div>
  )
}
