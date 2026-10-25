import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listTasks } from '../api'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { formatFullDate, formatRelative } from '../format'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type { TaskSummary } from '../types'

type TaskFilter = 'all' | 'open' | 'succeeded' | 'ended'

const TASK_FILTERS: {
  key: TaskFilter
  label: string
  test: (status: string) => boolean
}[] = [
  { key: 'all', label: '全部', test: () => true },
  {
    key: 'open',
    label: '进行中',
    test: (status) =>
      ['CREATED', 'RUNNING', 'WAITING_APPROVAL'].includes(status),
  },
  { key: 'succeeded', label: '已完成', test: (status) => status === 'SUCCEEDED' },
  {
    key: 'ended',
    label: '异常结束',
    test: (status) =>
      ['FAILED', 'REJECTED', 'TIMED_OUT', 'EXPIRED', 'CANCELLED'].includes(
        status,
      ),
  },
]

export function TaskHistoryPage() {
  usePageTitle('任务档案')
  const { identity, isApprover } = useIdentity()
  const navigate = useNavigate()
  const [items, setItems] = useState<TaskSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    if (items.length === 0) setLoading(true)
    else setRefreshing(true)
    listTasks(identity, 100, 0)
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
        setError(null)
      })
      .catch((errorValue: unknown) => {
        if (!cancelled) setError(errorValue)
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
  }, [identity, refreshTick])

  async function loadMore() {
    if (loadingMore) return
    setLoadingMore(true)
    try {
      const data = await listTasks(identity, 100, items.length)
      setItems((current) => [...current, ...data.items])
      setTotal(data.total)
    } catch {
      // The inline list stays usable; the retry button below covers the failure.
      setRefreshTick((value) => value + 1)
    } finally {
      setLoadingMore(false)
    }
  }

  const activeFilter =
    TASK_FILTERS.find((item) => item.key === filter) ?? TASK_FILTERS[0]
  const visible = items.filter((task) => activeFilter.test(task.status))
  const hasMore = items.length < total

  return (
    <div className="page">
      <PageHeader
        eyebrow="TASK ARCHIVE / 任务档案"
        title="任务档案"
        description="本人发起的售后任务、工作流结果与执行证据。审批记录按角色单独存放。"
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
            {!isApprover ? (
              <button
                type="button"
                className="button button--primary button--sm"
                onClick={() => navigate('/conversation')}
              >
                发起新请求
                <Icon name="arrow" size={15} />
              </button>
            ) : null}
          </div>
        }
      />

      {isApprover ? (
        <div className="role-note">
          <Icon name="shield" size={16} />
          <p>
            当前是审批人身份。这里只显示你本人发起的任务；租户内的退款复核队列在
            <Link className="text-link" to="/approvals">
              审批工作台
              <Icon name="arrow" size={12} />
            </Link>
            。
          </p>
        </div>
      ) : null}

      <div className="task-toolbar">
        <div className="tabs" role="tablist" aria-label="任务状态筛选">
          {TASK_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              className={`tab${filter === item.key ? ' is-active' : ''}`}
              onClick={() => setFilter(item.key)}
            >
              {item.label}
              <span className="tab__count">
                {items.filter((task) => item.test(task.status)).length}
              </span>
            </button>
          ))}
        </div>
        <span className="task-toolbar__total mono">TOTAL {total}</span>
      </div>

      {loading && items.length === 0 ? (
        <div className="table-panel">
          <div className="table-skeleton" aria-hidden="true">
            {[0, 1, 2, 3].map((row) => (
              <div key={row} className="table-skeleton__row">
                <span className="skeleton skeleton--line" style={{ width: '46%' }} />
                <span className="skeleton skeleton--line" style={{ width: '14%' }} />
                <span className="skeleton skeleton--line" style={{ width: '16%' }} />
              </div>
            ))}
          </div>
        </div>
      ) : error && items.length === 0 ? (
        <ErrorPanel
          title="无法读取任务档案"
          error={error}
          onRetry={() => setRefreshTick((value) => value + 1)}
        />
      ) : items.length === 0 ? (
        <section className="empty-state">
          <span className="empty-state__stamp mono">TASKS / 0</span>
          <p className="eyebrow">ARCHIVE / EMPTY</p>
          <h2>还没有任务记录</h2>
          <p>
            {isApprover
              ? '审批结果与任务时间线可以从审批工作台进入。'
              : '从售后对话发起第一条请求；审批与执行证据会归档在这里。'}
          </p>
          <div className="empty-state__actions">
            {!isApprover ? (
              <Link className="button button--primary" to="/conversation">
                去售后对话
                <Icon name="arrow" size={15} />
              </Link>
            ) : (
              <Link className="button button--primary" to="/approvals">
                打开审批工作台
                <Icon name="arrow" size={15} />
              </Link>
            )}
          </div>
        </section>
      ) : visible.length === 0 ? (
        <section className="empty-state empty-state--compact">
          <p className="eyebrow">FILTER / EMPTY</p>
          <h2>该状态下没有任务</h2>
          <p>切换筛选条件，或发起一条新的售后请求。</p>
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => setFilter('all')}
          >
            查看全部
          </button>
        </section>
      ) : (
        <>
          <div className="table-panel">
            <table className="task-table">
              <thead>
                <tr>
                  <th scope="col">请求</th>
                  <th scope="col">任务状态</th>
                  <th scope="col">结果</th>
                  <th scope="col">更新</th>
                  <th scope="col" className="task-table__action-head">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((task) => (
                  <tr key={task.task_id}>
                    <td data-label="请求">
                      <Link
                        className="task-row__request"
                        to={`/tasks/${task.task_id}`}
                      >
                        <span className="task-row__id mono">
                          {task.task_id}
                        </span>
                        <span className="task-row__text">{task.request}</span>
                      </Link>
                    </td>
                    <td data-label="任务状态">
                      <StatusBadge status={task.status} />
                    </td>
                    <td data-label="结果">
                      {task.outcome ? (
                        <StatusBadge kind="outcome" status={task.outcome} />
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td data-label="更新" className="task-table__time">
                      <span
                        className="mono"
                        title={formatFullDate(task.updated_at)}
                      >
                        {formatRelative(task.updated_at)}
                      </span>
                    </td>
                    <td className="task-table__action">
                      <Link
                        className="row-link"
                        to={`/tasks/${task.task_id}`}
                        aria-label={`查看任务 ${task.task_id}`}
                      >
                        查看
                        <Icon name="arrow" size={14} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore ? (
            <div className="load-more">
              <button
                type="button"
                className="button button--ghost button--sm"
                onClick={() => void loadMore()}
                disabled={loadingMore}
              >
                {loadingMore ? '加载中…' : `加载更多（剩余 ${total - items.length}）`}
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
