const STATUS_META: Record<
  string,
  { label: string; tone: 'neutral' | 'pending' | 'active' | 'success' | 'danger' | 'muted' }
> = {
  PENDING: { label: '待审批', tone: 'pending' },
  APPROVED: { label: '执行中', tone: 'active' },
  CONSUMED: { label: '已完成', tone: 'success' },
  REJECTED: { label: '已驳回', tone: 'danger' },
  EXPIRED: { label: '已过期', tone: 'muted' },
  INVALID: { label: '已失效', tone: 'muted' },
  CANCELLED: { label: '已取消', tone: 'muted' },
  CREATED: { label: '已创建', tone: 'neutral' },
  RUNNING: { label: '执行中', tone: 'active' },
  WAITING_APPROVAL: { label: '等待审批', tone: 'pending' },
  SUCCEEDED: { label: '已完成', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  TIMED_OUT: { label: '已超时', tone: 'muted' },
}

export const VERIFIED = STATUS_META

const OUTCOME_META: Record<string, { label: string; tone: string }> = {
  VERIFIED_SUCCESS: { label: '权威记录已验证', tone: 'success' },
  PARTIAL_SUCCESS: { label: '部分成功', tone: 'active' },
  PENDING_CONFIRMATION: { label: '等待对账确认', tone: 'pending' },
  FAILED: { label: '未成功', tone: 'danger' },
  NEEDS_HUMAN_REVIEW: { label: '需要人工复核', tone: 'pending' },
  WAITING_APPROVAL: { label: '等待审批', tone: 'pending' },
}

const ORDER_STATUS_META: Record<string, string> = {
  PLACED: '已下单',
  SHIPPED: '运输中',
  DELIVERED: '已签收',
  CANCELLED: '已取消',
}

export function statusMeta(status: string | null | undefined) {
  if (!status) return { label: '未知', tone: 'muted' as const }
  return STATUS_META[status] ?? { label: status, tone: 'neutral' as const }
}

export function outcomeMeta(outcome: string | null | undefined) {
  if (!outcome) return { label: '—', tone: 'muted' }
  return OUTCOME_META[outcome] ?? { label: outcome, tone: 'neutral' }
}

export function orderStatusLabel(status: string | null | undefined) {
  if (!status) return '未知'
  return ORDER_STATUS_META[status] ?? status
}

const INTENT_META: Record<string, string> = {
  PRODUCT_QUERY: '商品咨询',
  POLICY_QA: '政策问答',
  ORDER_QUERY: '订单查询',
  LOGISTICS_QUERY: '物流查询',
  REFUND_ELIGIBILITY: '退款资格',
  REFUND_STATUS: '退款进度',
  DELIVERY_ISSUE: '配送异常',
  TICKET_CREATE: '创建工单',
  REFUND_REQUEST: '退款申请',
  HUMAN_HANDOFF: '转人工',
  SELF_ANALYTICS: '自助统计',
  UNKNOWN: '未识别意图',
}

const BUSINESS_STATUS_META: Record<string, string> = {
  PLACED: '已下单',
  SHIPPED: '运输中',
  DELIVERED: '已签收',
  CANCELLED: '已取消',
  PENDING: '待处理',
  IN_TRANSIT: '运输中',
  EXCEPTION: '物流异常',
  REQUESTED: '已申请',
  SUCCEEDED: '成功',
  FAILED: '失败',
  UNKNOWN: '未知',
  ACTIVE: '在售',
}

export function businessStatusLabel(status: string | null | undefined) {
  if (!status) return '—'
  return BUSINESS_STATUS_META[status] ?? status
}

export function intentLabel(intent: string | null | undefined) {
  if (!intent) return '—'
  return INTENT_META[intent] ?? intent
}

export function roleLabel(roles: string[]): string {
  if (roles.includes('admin')) return '管理员'
  if (roles.includes('approver')) return '审批人'
  if (roles.includes('support')) return '客服'
  return '客户'
}

export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  return new Date(hasZone ? value : `${value}Z`)
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatFullDate(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatRelative(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return '—'
  const minutes = Math.round((Date.now() - date.getTime()) / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

export function formatMoney(cents: number, currency = 'CNY'): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: currency || 'CNY',
    minimumFractionDigits: 2,
  }).format(cents / 100)
}

export function formatCountdown(value: string | null | undefined, now = Date.now()) {
  const date = parseUtc(value)
  if (!date) return { label: '—', expired: true, ms: 0, urgent: false }
  const ms = date.getTime() - now
  if (ms <= 0) return { label: '已过期', expired: true, ms: 0, urgent: true }
  const seconds = Math.floor(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const label =
    hours > 0
      ? `${hours} 小时 ${minutes % 60} 分`
      : minutes > 0
        ? `${minutes} 分 ${seconds % 60} 秒`
        : `${seconds} 秒`
  return { label, expired: false, ms, urgent: ms < 5 * 60 * 1000 }
}

export function shortDigest(digest: string | null | undefined): string {
  if (!digest) return '—'
  return `${digest.slice(0, 8)}…${digest.slice(-6)}`
}

export function initials(value: string): string {
  return (value || '?').slice(0, 1).toUpperCase()
}

const HANDOFF_STATUS_META: Record<
  string,
  { label: string; tone: string; hint: string }
> = {
  HANDOFF_PENDING: {
    label: '待接入',
    tone: 'pending',
    hint: 'Agent 已停止接话，等待坐席接入',
  },
  QUEUED: {
    label: '排队中',
    tone: 'pending',
    hint: '超过接入时限，已转入排队',
  },
  HUMAN_ACTIVE: {
    label: '人工接管',
    tone: 'active',
    hint: '坐席已接管，Agent 保持静默',
  },
  NOTED: {
    label: '已抄送坐席',
    tone: 'neutral',
    hint: 'Agent 仍在服务，坐席同步可见',
  },
  CLOSED: {
    label: '已结案',
    tone: 'muted',
    hint: '会话已由坐席结束',
  },
  RELEASED: {
    label: '已交还',
    tone: 'success',
    hint: '会话已交还 Agent',
  },
  AGENT_ACTIVE: {
    label: 'Agent 服务中',
    tone: 'neutral',
    hint: '没有人工接管',
  },
}

export function handoffStatusMeta(status: string | null | undefined) {
  if (!status) return { label: '—', tone: 'muted', hint: '' }
  return (
    HANDOFF_STATUS_META[status] ?? {
      label: status,
      tone: 'neutral',
      hint: '',
    }
  )
}

const HANDOFF_REASON_META: Record<string, string> = {
  USER_REQUEST: '用户要求转人工',
  FRUSTRATION: '情绪激动',
  NO_EVIDENCE: '检索无可用证据',
  REPEAT_FAILURE: '重复失败',
  NO_UNDERSTANDING: '反复澄清未果',
  RISK: '高风险操作',
  COMPLAINT: '投诉',
  IDLE_TIMEOUT: '会话空闲',
  OTHER: '其他原因',
}

export function handoffReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—'
  return HANDOFF_REASON_META[reason] ?? reason
}

const QUEUE_META: Record<string, string> = {
  GENERAL: '综合队列',
  COMPLAINT: '投诉专席',
  RISK: '风险复核',
}

export function queueLabel(queue: string | null | undefined): string {
  if (!queue) return '—'
  return QUEUE_META[queue] ?? queue
}

const PRIORITY_META: Record<string, { label: string; tone: string }> = {
  P0: { label: 'P0 立即', tone: 'danger' },
  P1: { label: 'P1 阈值', tone: 'pending' },
  P2: { label: 'P2 预防', tone: 'neutral' },
}

export function priorityMeta(priority: string | null | undefined) {
  if (!priority) return { label: '—', tone: 'muted' }
  return PRIORITY_META[priority] ?? { label: priority, tone: 'neutral' }
}

const OUTBOX_STATUS_META: Record<string, string> = {
  PENDING: '待投递',
  PROCESSING: '投递中',
  SENT: '已投递',
  FAILED: '死信',
  CANCELLED: '已取消',
}

export function outboxStatusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return OUTBOX_STATUS_META[status] ?? status
}

const SENTIMENT_META: Record<string, string> = {
  NEUTRAL: '平静',
  NEGATIVE: '负面',
}

export function sentimentLabel(label: string | null | undefined): string {
  if (!label) return '—'
  return SENTIMENT_META[label] ?? label
}

const ABANDON_REASON_META: Record<string, string> = {
  IDLE_NO_RESOLUTION: '空闲未解决',
  REPEAT_INTENT: '重复提问',
}

export function abandonReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—'
  return ABANDON_REASON_META[reason] ?? reason
}

export function percent(
  ratio: number | null | undefined,
  digits = 1,
): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return '—'
  return `${(ratio * 100).toFixed(digits)}%`
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

const SPAN_KIND_META: Record<string, string> = {
  guard: '护栏',
  understand: '意图理解',
  retrieve: '检索',
  generate: '生成',
  tool: '工具',
  approval: '审批',
  handoff: '转人工',
  reconcile: '对账',
}

export function spanKindLabel(kind: string | null | undefined): string {
  if (!kind) return '—'
  return SPAN_KIND_META[kind] ?? kind
}

const SECURITY_CATEGORY_META: Record<string, string> = {
  PROMPT_INJECTION: '提示注入',
  POLICY_BYPASS: '规则绕过',
  SENSITIVE_CONTENT: '敏感信息',
  OUTPUT_GROUNDING: '无来源输出',
  RATE_LIMIT: '请求限流',
  PAYLOAD_TOO_LARGE: '超体积',
}

export function securityCategoryLabel(
  category: string | null | undefined,
): string {
  if (!category) return '—'
  return SECURITY_CATEGORY_META[category] ?? category
}
