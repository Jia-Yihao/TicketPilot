import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { errorText, getTrace } from '../api'
import { CopyValue } from '../components/CopyValue'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import {
  formatFullDate,
  formatLatency,
  parseUtc,
  spanKindLabel,
} from '../format'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type { TraceDetail, TraceSpan } from '../types'

interface SpanNode {
  span: TraceSpan
  depth: number
}

function buildTree(spans: TraceSpan[]): SpanNode[] {
  const byId = new Map(spans.map((span) => [span.span_id, span]))
  const children = new Map<string | null, TraceSpan[]>()
  for (const span of spans) {
    const parent =
      span.parent_span_id && byId.has(span.parent_span_id)
        ? span.parent_span_id
        : null
    const list = children.get(parent) ?? []
    list.push(span)
    children.set(parent, list)
  }
  for (const list of children.values()) {
    list.sort((left, right) => left.started_at.localeCompare(right.started_at))
  }

  const ordered: SpanNode[] = []
  const seen = new Set<string>()
  function walk(parent: string | null, depth: number) {
    for (const span of children.get(parent) ?? []) {
      if (seen.has(span.span_id)) continue
      seen.add(span.span_id)
      ordered.push({ span, depth })
      walk(span.span_id, depth + 1)
    }
  }
  walk(null, 0)
  // A span whose parent was not persisted still deserves a row.
  for (const span of spans) {
    if (!seen.has(span.span_id)) ordered.push({ span, depth: 0 })
  }
  return ordered
}

function durationMs(span: TraceSpan): number {
  if (span.latency_ms > 0) return span.latency_ms
  const start = parseUtc(span.started_at)
  const end = parseUtc(span.ended_at)
  if (!start || !end) return 0
  return Math.max(0, end.getTime() - start.getTime())
}

function AttributeList({ attributes }: { attributes: Record<string, unknown> }) {
  const entries = Object.entries(attributes)
  if (entries.length === 0) {
    return <p className="block-muted">该 span 没有附加属性。</p>
  }
  return (
    <dl className="trace-attributes">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt className="mono">{key}</dt>
          <dd className="mono">
            {typeof value === 'string'
              ? value
              : JSON.stringify(value, null, 2)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function TraceViewer() {
  usePageTitle('Trace 回放')
  const { traceId } = useParams<{ traceId: string }>()
  const { identity } = useIdentity()
  const [trace, setTrace] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  const [openSpan, setOpenSpan] = useState<string | null>(null)

  useEffect(() => {
    if (!traceId) return
    let cancelled = false
    setLoading(true)
    getTrace(identity, traceId)
      .then((data) => {
        if (cancelled) return
        setTrace(data)
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
  }, [identity, traceId, refreshTick])

  const nodes = useMemo(
    () => (trace ? buildTree(trace.spans) : []),
    [trace],
  )
  const maxLatency = useMemo(
    () => Math.max(1, ...nodes.map((node) => durationMs(node.span))),
    [nodes],
  )
  const errors = nodes.filter((node) => node.span.status !== 'OK').length
  const totalLatency = nodes.reduce(
    (sum, node) => sum + durationMs(node.span),
    0,
  )

  if (!traceId) {
    return (
      <div className="page">
        <ErrorPanel
          title="缺少 trace 编号"
          error={new Error('trace 编号为空，请从任务详情进入。')}
        />
      </div>
    )
  }

  return (
    <div className="page page--trace">
      <div className="detail-back">
        <Link className="text-link" to="/tasks">
          <Icon name="back" size={14} />
          返回任务档案
        </Link>
      </div>

      <PageHeader
        eyebrow="OBSERVABILITY / TRACE REPLAY"
        title="Trace 回放"
        description="一次请求的完整 span 树。属性在写入前已脱敏，这里只读。"
        meta={
          <span className="mono trace-id">
            {traceId}
            <CopyValue value={traceId} display="复制" label="复制 trace 编号" />
          </span>
        }
        actions={
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
        }
      />

      {loading && !trace ? (
        <div className="panel">
          <div className="panel__body">
            <div className="trace-skeleton" aria-hidden="true">
              {[0, 1, 2, 3].map((row) => (
                <span
                  key={row}
                  className="skeleton skeleton--line"
                  style={{ width: `${82 - row * 12}%` }}
                />
              ))}
            </div>
          </div>
        </div>
      ) : error && !trace ? (
        <ErrorPanel
          title="无法读取 Trace"
          error={error}
          onRetry={() => setRefreshTick((value) => value + 1)}
        />
      ) : trace ? (
        <>
          {error ? (
            <div className="inline-error" role="alert">
              <Icon name="alert" size={15} />
              刷新失败：{errorText(error)}
            </div>
          ) : null}

          <section className="trace-summary" aria-label="Trace 摘要">
            <div>
              <span className="mono">SPANS</span>
              <strong>{trace.spans.length}</strong>
            </div>
            <div>
              <span className="mono">ERRORS</span>
              <strong className={errors > 0 ? 'is-alert' : undefined}>
                {errors}
              </strong>
            </div>
            <div>
              <span className="mono">Σ LATENCY</span>
              <strong>{formatLatency(totalLatency)}</strong>
            </div>
            <div>
              <span className="mono">CONVERSATION</span>
              <strong className="mono">
                {trace.conversation_id ?? '—'}
              </strong>
            </div>
          </section>

          <section className="panel" aria-label="Span 树">
            <header className="panel__head">
              <div>
                <p className="eyebrow">SPAN TREE</p>
                <h2>请求轨迹</h2>
              </div>
              <span className="mono panel__tag">
                {errors > 0 ? `${errors} ERROR` : 'ALL OK'}
              </span>
            </header>
            <div className="panel__body panel__body--flush">
              {nodes.length === 0 ? (
                <p className="block-muted">这条 trace 没有 span。</p>
              ) : (
                <ul className="trace-tree">
                  {nodes.map(({ span, depth }) => {
                    const open = openSpan === span.span_id
                    const latency = durationMs(span)
                    const failed = span.status !== 'OK'
                    return (
                      <li
                        key={span.span_id}
                        className={`trace-row${failed ? ' is-failed' : ''}`}
                      >
                        <button
                          type="button"
                          className="trace-row__head"
                          aria-expanded={open}
                          onClick={() =>
                            setOpenSpan(open ? null : span.span_id)
                          }
                          style={{ paddingLeft: `${12 + depth * 18}px` }}
                        >
                          <span
                            className={`trace-row__status${
                              failed ? ' is-failed' : ''
                            }`}
                            aria-hidden="true"
                          />
                          <span className="trace-row__name">
                            <strong>{spanKindLabel(span.kind)}</strong>
                            <span className="mono">{span.name}</span>
                          </span>
                          <span className="trace-row__bar" aria-hidden="true">
                            <span
                              className={`trace-row__fill${
                                failed ? ' is-failed' : ''
                              }`}
                              style={{
                                width: `${Math.max(
                                  2,
                                  (latency / maxLatency) * 100,
                                )}%`,
                              }}
                            />
                          </span>
                          <span className="trace-row__latency mono">
                            {formatLatency(latency)}
                          </span>
                          <span className="trace-row__time mono">
                            {formatFullDate(span.started_at)}
                          </span>
                          <Icon
                            name="chevron"
                            size={14}
                            className={`trace-row__chevron${
                              open ? ' is-open' : ''
                            }`}
                          />
                        </button>
                        {open ? (
                          <div className="trace-row__detail">
                            <div className="trace-row__ids mono">
                              <span>SPAN {span.span_id}</span>
                              <span>
                                PARENT {span.parent_span_id ?? 'ROOT'}
                              </span>
                              <span>STATUS {span.status}</span>
                            </div>
                            <AttributeList attributes={span.attributes} />
                          </div>
                        ) : null}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </section>

          <p className="trace-note">
            <Icon name="lock" size={13} />
            手机号、邮箱等个人信息在写入 span 前已替换为掩码；本页不会重新展开原文。
          </p>
        </>
      ) : null}
    </div>
  )
}
