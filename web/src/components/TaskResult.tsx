import {
  businessStatusLabel,
  formatMoney,
} from '../format'
import type { TaskCheck, TaskEvidence, TaskResult } from '../types'
import { Icon } from './Icon'
import { StatusBadge } from './StatusBadge'

const DATA_LABELS: Record<string, string> = {
  order_id: '订单号',
  status: '状态',
  total_cents: '订单金额',
  refundable_cents: '可退余额',
  currency: '币种',
  version: '数据版本',
  paid_at: '支付时间',
  delivered_at: '签收时间',
  tracking_no: '运单号',
  carrier: '承运商',
  available: '物流记录',
  current_node: '最新节点',
  last_update: '更新时间',
  estimated_delivery: '预计送达',
  ticket_no: '工单号',
  ticket_id: '工单 ID',
  priority: '优先级',
  category: '分类',
  answer_mode: '回答模式',
}

const CHECK_LABELS: Record<string, string> = {
  required_slots_present: '必填槽位齐全',
  order_authorized_and_found: '订单归属与存在性',
  logistics_present: '物流记录存在',
  citations_present: '政策引用完整',
  documents_effective: '引用版本仍在有效期',
  ticket_created: '工单已创建',
  refund_eligible: '退款资格',
  approval_approved: '审批已通过',
  approval_valid: '审批时效与快照',
  refund_record_exists: '退款记录存在',
  refund_belongs_to_order: '退款归属订单',
  refund_amount_matches: '退款金额一致',
  refund_status: '退款状态',
  refundable_balance_non_negative: '可退余额非负',
  refundable_balance_applied: '本地余额已扣减',
}

const EVIDENCE_TYPES: Record<string, string> = {
  order: '订单',
  refund: '退款',
  ticket: '工单',
  policy: '政策',
  record: '记录',
}

function isScalar(value: unknown): value is string | number | boolean {
  return (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  )
}

function formatDataValue(key: string, value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (key.endsWith('_cents')) return formatMoney(value)
    if (key === 'version') return `v${value}`
    return String(value)
  }
  if (key === 'status') return businessStatusLabel(value)
  if (key === 'priority') {
    return value === 'HIGH' ? '高' : value === 'NORMAL' ? '普通' : value
  }
  return value
}

function checkLabel(check: TaskCheck): string {
  if (!check.name) return '校验项'
  return CHECK_LABELS[check.name] ?? check.name
}

function evidenceContent(item: TaskEvidence): {
  primary: string
  secondary: string | null
} {
  const type = String(
    item.type ?? (item.document_id ? 'policy' : 'record'),
  )
  const typeLabel = EVIDENCE_TYPES[type] ?? type
  if (item.reference) {
    return { primary: String(item.reference), secondary: typeLabel }
  }
  if (item.document_id) {
    const title =
      typeof item.title === 'string' ? item.title : String(item.document_id)
    const version = typeof item.version === 'string' ? item.version : ''
    const section =
      typeof item.section === 'string' && item.section ? item.section : ''
    const qualifier = [version, section].filter(Boolean).join(' · ')
    return {
      primary: title,
      secondary: [typeLabel, qualifier].filter(Boolean).join(' · '),
    }
  }
  return { primary: JSON.stringify(item), secondary: typeLabel }
}

export function TaskResultView({
  result,
  variant = 'full',
  onReview,
  reviewLabel = '进入审批',
}: {
  result: TaskResult | null
  variant?: 'full' | 'compact'
  onReview?: (approvalId: string) => void
  reviewLabel?: string
}) {
  if (!result) {
    return <p className="result result--empty">任务尚未产生结果。</p>
  }

  const compact = variant === 'compact'
  const pending = result.pending_action
  const checks = result.checks ?? []
  const dataEntries = Object.entries(result.data ?? {})
    .filter(
      (entry): entry is [string, string | number | boolean] =>
        isScalar(entry[1]),
    )
    .slice(0, compact ? 6 : 12)
  const evidence = result.evidence ?? []
  const allChecksPassed =
    checks.length > 0 && checks.every((check) => check.passed !== false)
  const visibleChecks = compact && allChecksPassed ? [] : checks

  return (
    <div className={`result${compact ? ' result--compact' : ''}`}>
      <div className="result__summary">
        <StatusBadge kind="outcome" status={result.outcome} />
        <p>{result.summary ?? '工作流未返回摘要。'}</p>
      </div>

      {pending?.requires_approval && pending.approval_id ? (
        <div className="pending-callout">
          <span className="pending-callout__icon">
            <Icon name="shield" size={20} />
          </span>
          <div className="pending-callout__body">
            <span className="eyebrow">APPROVAL REQUIRED</span>
            <strong>
              退款{' '}
              {typeof pending.amount_cents === 'number'
                ? formatMoney(pending.amount_cents, pending.currency)
                : ''}{' '}
              等待四眼复核
            </strong>
            <p>
              审批通过前不会调用退款工具。审批号{' '}
              <code>{pending.approval_id}</code>
            </p>
          </div>
          {onReview ? (
            <button
              type="button"
              className="button button--primary button--sm"
              onClick={() => onReview(pending.approval_id as string)}
            >
              {reviewLabel}
              <Icon name="arrow" size={15} />
            </button>
          ) : null}
        </div>
      ) : null}

      {dataEntries.length > 0 ? (
        <dl className="result-data">
          {dataEntries.map(([key, value]) => (
            <div key={key} className="result-data__item">
              <dt>{DATA_LABELS[key] ?? key}</dt>
              <dd className={/_id$|_no$/.test(key) ? 'mono' : undefined}>
                {formatDataValue(key, value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {evidence.length > 0 ? (
        <div className="result-evidence">
          <span className="result-label">证据</span>
          <div className="chip-row">
            {evidence.map((item, index) => {
              const content = evidenceContent(item)
              return (
                <span
                  key={`${content.primary}-${index}`}
                  className="evidence-chip"
                  title={content.secondary ?? undefined}
                >
                  {content.primary}
                  {content.secondary ? (
                    <small>{content.secondary}</small>
                  ) : null}
                </span>
              )
            })}
          </div>
        </div>
      ) : null}

      {checks.length > 0 ? (
        <div className="result-checks">
          <span className="result-label">校验</span>
          {compact && allChecksPassed ? (
            <p className="check-summary is-pass">
              <Icon name="check" size={14} />
              {checks.length} 项校验通过
            </p>
          ) : (
            <ul className="check-list">
              {visibleChecks.map((check, index) => {
                const passed = check.passed !== false
                return (
                  <li
                    key={`${check.name ?? 'check'}-${index}`}
                    className={`check-item ${passed ? 'is-pass' : 'is-fail'}`}
                  >
                    <Icon name={passed ? 'check' : 'x'} size={14} />
                    <span>{checkLabel(check)}</span>
                    {!passed && check.reasons?.length ? (
                      <small>{check.reasons.join('、')}</small>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      ) : null}

      {!compact && result.data && Object.keys(result.data).length > 0 ? (
        <details className="raw-block">
          <summary>查看原始结果</summary>
          <pre>{JSON.stringify(result.data, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  )
}
