import { useState } from 'react'
import { errorText, getAnalyticsQuery, getContextSection } from '../api'
import {
  businessStatusLabel,
  formatFullDate,
  formatLatency,
  formatMoney,
} from '../format'
import type {
  AnalyticsQuerySummary,
  ContextCard,
  ContextCardKind,
  ContextSectionDetail,
  ContextSectionSummary,
  Identity,
} from '../types'
import { Icon } from './Icon'

const KIND_META: Record<
  ContextCardKind,
  { label: string; mono: string }
> = {
  product: { label: '商品', mono: 'PRODUCT' },
  policy: { label: '政策', mono: 'POLICY' },
  order: { label: '订单', mono: 'ORDER' },
  analytics: { label: '统计', mono: 'ANALYTICS' },
}

const FIELD_LABELS: Record<string, string> = {
  product_id: '商品 ID',
  sku: 'SKU',
  category: '分类',
  price_cents: '价格',
  stock: '库存',
  warehouse: '发货仓',
  warranty_months: '质保',
  status: '状态',
  document_id: '文档 ID',
  version: '版本',
  effective_from: '生效日',
  effective_to: '失效日',
  source_uri: '来源',
  matched_section: '命中章节',
  order_id: '订单号',
  total_cents: '订单金额',
  refundable_cents: '可退余额',
  currency: '币种',
  paid_at: '支付时间',
  delivered_at: '签收时间',
  range: '时间范围',
  records: '记录数',
  amount_cents: '金额合计',
}

type SectionState =
  | { status: 'loading' }
  | { status: 'loaded'; detail: ContextSectionDetail }
  | { status: 'error'; message: string }

type AnalyticsState =
  | { status: 'closed' }
  | { status: 'loading' }
  | { status: 'loaded'; query: AnalyticsQuerySummary }
  | { status: 'error'; message: string }

function fieldValue(
  key: string,
  value: string | number | boolean | null,
  card: ContextCard,
): string {
  if (value === null) {
    if (key === 'effective_to') return '长期有效'
    if (key === 'delivered_at') return '未签收'
    return '—'
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (key.endsWith('_cents')) {
      const currency =
        typeof card.fields.currency === 'string'
          ? card.fields.currency
          : 'CNY'
      return formatMoney(value, currency)
    }
    if (key === 'warranty_months') return `${value} 个月`
    if (key === 'stock') return `${value} 件`
    if (key === 'version') return `v${value}`
    return String(value)
  }
  if (key === 'status') return businessStatusLabel(value)
  if (key === 'paid_at' || key === 'delivered_at') {
    return formatFullDate(value)
  }
  return value
}

function fieldLabel(key: string): string {
  const known = FIELD_LABELS[key]
  if (known) return known
  if (key.endsWith('_amount_cents')) {
    return `${businessStatusLabel(key.slice(0, -'_amount_cents'.length))} 金额`
  }
  return businessStatusLabel(key)
}

function sectionDomId(card: ContextCard, sectionId: string): string {
  return `context-${card.kind}-${card.card_id}-${sectionId}`.replace(
    /[^a-zA-Z0-9_-]/g,
    '-',
  )
}

export function ContextCardView({
  card,
  identity,
}: {
  card: ContextCard
  identity: Identity
}) {
  const meta = KIND_META[card.kind] ?? {
    label: card.kind,
    mono: card.kind.toUpperCase(),
  }
  const [sections, setSections] = useState<Record<string, SectionState>>({})
  const [analytics, setAnalytics] = useState<AnalyticsState>({ status: 'closed' })

  async function toggleAnalytics() {
    if (analytics.status !== 'closed') {
      setAnalytics({ status: 'closed' })
      return
    }
    setAnalytics({ status: 'loading' })
    try {
      const query = await getAnalyticsQuery(identity, card.card_id)
      setAnalytics({ status: 'loaded', query })
    } catch (error) {
      setAnalytics({ status: 'error', message: errorText(error) })
    }
  }

  async function toggle(section: ContextSectionSummary) {
    const current = sections[section.id]
    if (current?.status === 'loaded') {
      setSections((previous) => {
        const next = { ...previous }
        delete next[section.id]
        return next
      })
      return
    }
    if (current?.status === 'loading') return
    setSections((previous) => ({
      ...previous,
      [section.id]: { status: 'loading' },
    }))
    try {
      const detail = await getContextSection(
        identity,
        card.kind,
        card.card_id,
        section.id,
      )
      setSections((previous) => ({
        ...previous,
        [section.id]: { status: 'loaded', detail },
      }))
    } catch (error) {
      setSections((previous) => ({
        ...previous,
        [section.id]: { status: 'error', message: errorText(error) },
      }))
    }
  }

  const fields = Object.entries(card.fields)

  return (
    <article className={`context-card context-card--${card.kind}`}>
      <header className="context-card__head">
        <span className={`context-card__kind context-card__kind--${card.kind}`}>
          {meta.label}
        </span>
        <div className="context-card__titles">
          <h3>{card.title}</h3>
          {card.subtitle ? <span>{card.subtitle}</span> : null}
        </div>
        <span className="mono context-card__code">{card.card_id}</span>
      </header>

      <div className="context-card__body">
        {card.badges.length > 0 ? (
          <div className="context-card__badges">
            {card.badges.map((badge) => (
              <span key={badge} className="context-badge">
                {businessStatusLabel(badge)}
              </span>
            ))}
          </div>
        ) : null}
        <p className="context-card__summary">{card.summary}</p>
        {fields.length > 0 ? (
          <dl className="context-fields">
            {fields.map(([key, value]) => (
              <div key={key}>
                <dt>{fieldLabel(key)}</dt>
                <dd className={key.endsWith('_id') ? 'mono' : undefined}>
                  {fieldValue(key, value, card)}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      <div className="context-sections">
        {card.kind === 'analytics' ? (
          <section
            className={`context-analytics${analytics.status === 'closed' ? '' : ' is-open'}`}
          >
            <button
              type="button"
              className="context-section__toggle"
              aria-expanded={analytics.status !== 'closed'}
              aria-controls={sectionDomId(card, 'sql')}
              onClick={() => void toggleAnalytics()}
            >
              <span className="context-section__heading">
                <span className="context-section__index mono">SQL</span>
                <span className="context-section__label">
                  <strong>查询计划与 SQL</strong>
                  <small>只读 · 默认收起</small>
                </span>
              </span>
              <span className="context-section__meta">
                <Icon
                  name="chevron"
                  size={15}
                  className="context-section__chevron"
                />
              </span>
            </button>
            {analytics.status !== 'closed' ? (
              <div
                className="context-analytics__body"
                id={sectionDomId(card, 'sql')}
              >
                {analytics.status === 'loading' ? (
                  <p className="context-section__loading">读取查询记录…</p>
                ) : null}
                {analytics.status === 'error' ? (
                  <p className="context-section__error" role="alert">
                    {analytics.message} 点击可重试。
                  </p>
                ) : null}
                {analytics.status === 'loaded' ? (
                  <>
                    <dl className="context-fields context-fields--plan">
                      <div>
                        <dt>数据集</dt>
                        <dd className="mono">{analytics.query.dataset}</dd>
                      </div>
                      <div>
                        <dt>指标</dt>
                        <dd className="mono">{analytics.query.metric}</dd>
                      </div>
                      <div>
                        <dt>分组</dt>
                        <dd className="mono">
                          {analytics.query.group_by ?? '—'}
                        </dd>
                      </div>
                      <div>
                        <dt>时间范围</dt>
                        <dd className="mono">
                          {analytics.query.time_range ?? '—'}
                        </dd>
                      </div>
                      <div>
                        <dt>行状态</dt>
                        <dd className="mono">{analytics.query.row_status}</dd>
                      </div>
                    </dl>
                    <pre className="sql-block" aria-label="只读 SQL">
                      <code>{analytics.query.sql_text}</code>
                    </pre>
                    <p className="context-analytics__rows">
                      <span className="mono">
                        {analytics.query.row_count} ROWS
                      </span>
                      <span className="mono">
                        {formatLatency(analytics.query.duration_ms)}
                      </span>
                      <span className="mono">{analytics.query.status}</span>
                    </p>
                    <p className="context-analytics__note">
                      账本只保留 SQL 文本与参数摘要，不保存结果行；卡片字段就是当时的聚合结果。
                    </p>
                  </>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}
        {card.sections.map((section, index) => {
          const state = sections[section.id]
          const open = state !== undefined
          return (
            <section
              key={section.id}
              className={`context-section${open ? ' is-open' : ''}`}
            >
              <button
                type="button"
                className="context-section__toggle"
                aria-expanded={open}
                aria-controls={sectionDomId(card, section.id)}
                onClick={() => void toggle(section)}
              >
                <span className="context-section__heading">
                  <span className="context-section__index mono">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="context-section__label">
                    <strong>{section.title}</strong>
                    <small>{section.preview}</small>
                  </span>
                </span>
                <span className="context-section__meta">
                  <span className="mono">{section.chars} 字</span>
                  <Icon
                    name="chevron"
                    size={15}
                    className="context-section__chevron"
                  />
                </span>
              </button>
              {open ? (
                <div
                  className="context-section__body"
                  id={sectionDomId(card, section.id)}
                >
                  {state.status === 'loading' ? (
                    <p className="context-section__loading">读取章节…</p>
                  ) : null}
                  {state.status === 'error' ? (
                    <p className="context-section__error" role="alert">
                      {state.message} 点击章节可重试。
                    </p>
                  ) : null}
                  {state.status === 'loaded' ? (
                    <>
                      <p className="context-section__content">
                        {state.detail.section.content}
                      </p>
                      {state.detail.citation ? (
                        <div className="context-citation">
                          <span className="mono">CITATION</span>
                          <span>
                            {String(state.detail.citation.title ?? '')} ·{' '}
                            {String(state.detail.citation.version ?? '')} ·{' '}
                            {String(state.detail.citation.section ?? '')}
                          </span>
                          <span className="mono context-citation__source">
                            {String(state.detail.citation.source_uri ?? '')}
                          </span>
                        </div>
                      ) : null}
                    </>
                  ) : null}
                </div>
              ) : null}
            </section>
          )
        })}
      </div>

      <footer className="context-card__foot">
        <span className="mono">
          {card.sections.length} SECTIONS · {card.total_chars} CHARS
        </span>
        <span className="mono">按需加载</span>
      </footer>
    </article>
  )
}
