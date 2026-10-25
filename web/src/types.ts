export interface Identity {
  tenant: string
  user: string
  roles: string[]
  label: string
}

export interface TaskEvidence {
  type?: string
  reference?: string
  [key: string]: unknown
}

export interface TaskCheck {
  name?: string
  passed?: boolean
  reasons?: string[]
  [key: string]: unknown
}

export interface PendingAction {
  type: string
  approval_id?: string
  amount_cents?: number
  currency?: string
  requires_approval?: boolean
}

export interface TaskResult {
  outcome?: string
  summary?: string
  evidence?: TaskEvidence[]
  checks?: TaskCheck[]
  reason_code?: string
  error_code?: string
  data?: Record<string, unknown>
  pending_action?: PendingAction
}

export interface PendingApproval {
  approval_id: string
  action_type: string
  status: string
  expires_at: string
  amount_cents?: number | null
}

export interface TaskSnapshot {
  task_id: string
  status: string
  request: string
  context: Record<string, unknown>
  result: TaskResult | null
  version: number
  trace_id?: string | null
  created_at: string
  updated_at: string
  pending_approval: PendingApproval | null
  latest_approval: Record<string, unknown> | null
  tool_execution: Record<string, unknown> | null
}

export interface TaskSummary {
  task_id: string
  status: string
  request: string
  outcome: string | null
  summary: string | null
  created_at: string
  updated_at: string
}

export interface TaskListResponse {
  items: TaskSummary[]
  total: number
  limit: number
  offset: number
}

export interface ApprovalOrderSummary {
  order_id: string
  user_id: string
  status: string
  total_cents: number
  refundable_cents: number
  currency: string
  version: number
  paid_at: string
  delivered_at: string | null
}

export interface ApprovalListItem {
  approval_id: string
  task_id: string
  status: string
  action_type: string
  amount_cents: number
  currency: string
  order_id: string
  requested_by: string
  reviewer_id: string | null
  reason: string | null
  resource_version: number
  created_at: string
  expires_at: string
  resolved_at: string | null
  request: string
  task_status: string
  task_outcome: string | null
  order: ApprovalOrderSummary | null
}

export interface ApprovalDetail extends ApprovalListItem {
  action_digest: string
  action_payload: Record<string, unknown>
}

export interface ApprovalListResponse {
  items: ApprovalListItem[]
  total: number
  limit: number
  offset: number
  counts: Record<string, number>
}

export interface TaskEvent {
  id: string
  event: string
  data: Record<string, unknown>
  created_at: string
}

export type ContextCardKind = 'product' | 'policy' | 'order' | 'analytics'

export interface ContextSectionSummary {
  id: string
  title: string
  preview: string
  chars: number
}

export interface ContextCard {
  kind: ContextCardKind
  card_id: string
  title: string
  subtitle: string | null
  summary: string
  badges: string[]
  fields: Record<string, string | number | boolean | null>
  sections: ContextSectionSummary[]
  source: string | null
  total_chars: number
}

export interface ContextSectionDetail {
  kind: ContextCardKind
  card_id: string
  title: string
  section: {
    id: string
    title: string
    content: string
    chars: number
  }
  source: string | null
  citation: Record<string, unknown> | null
}

export interface SupportClarification {
  reason: string
  prompt: string
  expected_slot: string
  resumable_intent: string
}

export interface SupportReply {
  conversation_id: string
  reply: string
  intent: string
  agent_mode: string
  trace_id?: string | null
  context_cards: ContextCard[]
  task: TaskSnapshot | null
  suggestions: string[]
  slots: Record<string, string | number | boolean | null>
  inherited_slots: string[]
  clarification: SupportClarification | null
  personalization?: SupportPersonalization | null
}

export interface SupportPersonalization {
  effects: string[]
  fields: string[]
}

export interface ApiErrorDetails {
  code: string
  message: string
  retryable: boolean
  request_id: string
  details?: Record<string, unknown>
}

export interface SentimentSnapshot {
  label: string
  score: number
  signals: string[]
}

export interface HandoffIntentRecord {
  turn: number
  intent: string
  user_message: string
  outcome: string
  at: string
}

export interface HandoffActionRecord {
  action: string
  intent: string
  result: string
  ok: boolean
  at: string
}

export interface HandoffCitation {
  document_id: string
  title: string
  version: string
  section?: string | null
  source_uri?: string | null
  score?: number | null
}

export interface HandoffSummary {
  event_id: string
  conversation_id: string
  signal_id: string
  reason: string
  priority: string
  queue: string
  status: string
  intent: string | null
  detail: string
  claimed_by: string | null
  created_at: string
  updated_at: string
  transcript_digest: string
}

export interface HandoffQueueResponse {
  items: HandoffSummary[]
  total: number
  limit: number
  offset: number
}

export interface HandoffBundle {
  conversation_id: string
  tenant_id: string
  user_id: string
  channel: string
  reason: string
  signal_id: string
  priority: string
  queue: string
  transcript_digest: string
  transcript_ref: string
  intent_history: HandoffIntentRecord[]
  slots: Record<string, unknown>
  attempted_actions: HandoffActionRecord[]
  pending_task_id: string | null
  sentiment: SentimentSnapshot
  policy_refs: HandoffCitation[]
  suggested_next_step: string
  created_at: string
}

export interface HandoffDetail extends HandoffSummary {
  user_id: string
  trace_id: string | null
  pending_task_id: string | null
  claimed_at: string | null
  released_by: string | null
  closed_by: string | null
  resolution: string | null
  bundle: Partial<HandoffBundle>
}

export interface HandoffMetrics {
  window_minutes: number | null
  conversations: number
  handoff_conversations: number
  handoff_rate: number
  auto_resolution_rate: number
  open_cases: number
  by_queue: Record<string, number>
  by_reason: Record<string, number>
  by_signal: Record<string, number>
  by_hour: Record<string, number>
  abandon_events: number
  abandon_rate: number
  abandon_by_channel: Record<string, number>
  abandon_by_intent: Record<string, number>
  abandon_by_hour: Record<string, number>
}

export interface AbandonSummary {
  event_id: string
  conversation_id: string
  intent: string | null
  channel: string
  reason: string
  idle_minutes: number
  created_at: string
}

export interface AbandonListResponse {
  items: AbandonSummary[]
  total: number
}

export interface OutboxMessageSummary {
  id: string
  aggregate_type: string
  aggregate_id: string
  event_type: string
  status: string
  retry_count: number
  last_error: string | null
  created_at: string
  updated_at: string
  sent_at: string | null
}

export interface OutboxQueueResponse {
  items: OutboxMessageSummary[]
  total: number
  limit: number
  offset: number
}

export interface OutboxDeliveryReport {
  claimed: number
  sent: number
  failed: number
  dead_lettered: number
}

export interface TraceSpan {
  span_id: string
  parent_span_id: string | null
  kind: string
  name: string
  status: string
  latency_ms: number
  attributes: Record<string, unknown>
  started_at: string
  ended_at: string
}

export interface TraceDetail {
  trace_id: string
  conversation_id: string | null
  spans: TraceSpan[]
}

export interface TraceMetrics {
  window_minutes: number | null
  trace_count: number
  spans: number
  error_spans: number
  error_rate: number
  p95_latency_ms: number
  by_kind: Record<string, number>
  by_status: Record<string, number>
  by_hour: Record<string, number>
}

export interface SecurityMetrics {
  window_minutes: number | null
  total: number
  blocked: number
  redacted: number
  logged: number
  by_category: Record<string, number>
  by_action: Record<string, number>
  by_severity: Record<string, number>
  by_hour: Record<string, number>
}

export interface AnalyticsQuerySummary {
  query_id: string
  question: string
  dataset: string
  metric: string
  group_by: string | null
  row_status: string
  time_range: string | null
  sql_text: string
  row_count: number
  duration_ms: number
  status: string
  rejected_reason: string | null
  trace_id: string | null
  created_at: string
}

export interface ChannelCapability {
  channel: string
  enabled: boolean
  supports_stream: boolean
  supports_rich_card: boolean
  supports_voice: boolean
  max_length: number | null
  max_cards: number
  sentence_char_limit: number | null
  formal: boolean
  offers_human_handoff: boolean
  deliveries: Record<string, number>
}

export interface ChannelMatrixResponse {
  channels: ChannelCapability[]
}

export interface MemoryProfile {
  preferred_name: string | null
  locale: string
  preferences: Record<string, unknown>
  history_summary: string
  proactive_opt_in: boolean
  muted_event_types: string[]
  max_notifications_per_day: number
  updated_at: string | null
}

export interface MemoryUpdatePayload {
  preferred_name?: string | null
  locale?: string
  preferences?: Record<string, unknown>
  history_summary?: string
  proactive_opt_in?: boolean
  muted_event_types?: string[]
  max_notifications_per_day?: number
}

export interface MemoryExport {
  profile: MemoryProfile
  notification_counts: Record<string, number>
  redacted: boolean
}

export interface NotificationView {
  notification_id: string
  event_type: string
  status: string
  title: string
  body: string
  created_at: string | null
  confirmed_at: string | null
  cancelled_at: string | null
}

export interface NotificationListResponse {
  notifications: NotificationView[]
}

export interface ProactiveRule {
  rule_id: string
  event_type: string
  enabled: boolean
  requires_confirmation: boolean
  cooldown_minutes: number
  template: string
  required_context: string[]
  updated_at: string | null
}

export interface ProactiveRuleListResponse {
  rules: ProactiveRule[]
}

export interface ProactiveRuleUpdatePayload {
  template?: string
  enabled?: boolean
  cooldown_minutes?: number
}
