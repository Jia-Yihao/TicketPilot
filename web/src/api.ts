import type {
  AbandonListResponse,
  AnalyticsQuerySummary,
  ApiErrorDetails,
  ApprovalDetail,
  ApprovalListResponse,
  ChannelMatrixResponse,
  ContextSectionDetail,
  HandoffDetail,
  HandoffMetrics,
  HandoffQueueResponse,
  Identity,
  MemoryExport,
  MemoryProfile,
  MemoryUpdatePayload,
  NotificationListResponse,
  NotificationView,
  OutboxQueueResponse,
  ProactiveRule,
  ProactiveRuleListResponse,
  ProactiveRuleUpdatePayload,
  SecurityMetrics,
  SupportReply,
  TaskEvent,
  TaskListResponse,
  TaskSnapshot,
  TraceDetail,
  TraceMetrics,
} from './types'

export class ApiError extends Error {
  status: number
  code: string
  requestId: string
  details?: Record<string, unknown>

  constructor(
    status: number,
    code: string,
    message: string,
    requestId = '',
    details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? `（${error.code}）` : ''
    const request = error.requestId ? ` · 请求 ${error.requestId}` : ''
    return `${error.message}${code}${request}`
  }
  if (error instanceof Error) return error.message
  return '请求失败，请稍后重试。'
}

export function identityHeaders(identity: Identity): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Tenant-Id': identity.tenant,
    'X-User-Id': identity.user,
  }
  if (identity.roles.length > 0) headers['X-Roles'] = identity.roles.join(',')
  return headers
}

async function request<T>(
  path: string,
  identity: Identity,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  for (const [key, value] of Object.entries(identityHeaders(identity))) {
    headers.set(key, value)
  }
  if (init.body) headers.set('Content-Type', 'application/json')

  const response = await fetch(`/api/v1${path}`, { ...init, headers })
  if (!response.ok) {
    let payload: { error?: ApiErrorDetails } | null = null
    try {
      payload = (await response.json()) as { error?: ApiErrorDetails }
    } catch {
      payload = null
    }
    const error = payload?.error
    throw new ApiError(
      response.status,
      error?.code ?? `HTTP_${response.status}`,
      error?.message ?? `Request failed with ${response.status}.`,
      error?.request_id,
      error?.details,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function createTask(
  identity: Identity,
  text: string,
): Promise<TaskSnapshot> {
  return request('/tasks', identity, {
    method: 'POST',
    body: JSON.stringify({ request: text }),
  })
}

export function sendSupportMessage(
  identity: Identity,
  message: string,
  context: { conversation_id?: string; order_id?: string } = {},
  deliveryId?: string,
): Promise<SupportReply> {
  const headers: Record<string, string> = {}
  if (deliveryId) headers['X-Channel-Message-Id'] = deliveryId
  return request('/channels/web/messages', identity, {
    method: 'POST',
    body: JSON.stringify({ message, ...context }),
    headers,
  })
}

export function getContextSection(
  identity: Identity,
  kind: string,
  cardId: string,
  sectionId: string,
): Promise<ContextSectionDetail> {
  return request(
    `/support/cards/${encodeURIComponent(kind)}/${encodeURIComponent(cardId)}` +
      `/sections/${encodeURIComponent(sectionId)}`,
    identity,
  )
}

export function listTasks(
  identity: Identity,
  limit = 50,
  offset = 0,
): Promise<TaskListResponse> {
  return request(`/tasks?limit=${limit}&offset=${offset}`, identity)
}

export function getTask(
  identity: Identity,
  taskId: string,
): Promise<TaskSnapshot> {
  return request(`/tasks/${encodeURIComponent(taskId)}`, identity)
}

export function cancelTask(
  identity: Identity,
  taskId: string,
): Promise<TaskSnapshot> {
  return request(`/tasks/${encodeURIComponent(taskId)}/cancel`, identity, {
    method: 'POST',
  })
}

export function listApprovals(
  identity: Identity,
  statuses: string[] = [],
  limit = 50,
  offset = 0,
): Promise<ApprovalListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  for (const status of statuses) params.append('status', status)
  return request(`/approvals?${params.toString()}`, identity)
}

export function getApproval(
  identity: Identity,
  approvalId: string,
): Promise<ApprovalDetail> {
  return request(`/approvals/${encodeURIComponent(approvalId)}`, identity)
}

export function approveTask(
  identity: Identity,
  taskId: string,
  approvalId: string,
  decision: 'APPROVE' | 'REJECT',
  reason?: string,
): Promise<TaskSnapshot> {
  return request(`/tasks/${encodeURIComponent(taskId)}/approve`, identity, {
    method: 'POST',
    body: JSON.stringify({
      approval_id: approvalId,
      decision,
      reason: reason?.trim() || null,
    }),
  })
}

function parseSse(text: string): TaskEvent[] {
  const events: TaskEvent[] = []
  for (const block of text.split(/\n\n+/)) {
    if (!block.trim()) continue
    let id = ''
    let event = ''
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('id: ')) id = line.slice(4)
      else if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    }
    if (!event) continue
    let data: Record<string, unknown> = {}
    try {
      data = dataLines.length ? (JSON.parse(dataLines.join('\n')) as Record<string, unknown>) : {}
    } catch {
      data = { raw: dataLines.join('\n') }
    }
    const createdAt =
      typeof data.created_at === 'string' ? data.created_at : new Date().toISOString()
    events.push({ id, event, data, created_at: createdAt })
  }
  return events
}

export async function getTaskEvents(
  identity: Identity,
  taskId: string,
): Promise<TaskEvent[]> {
  const headers = new Headers(identityHeaders(identity))
  const response = await fetch(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/events`,
    { headers },
  )
  if (!response.ok) {
    throw new ApiError(response.status, `HTTP_${response.status}`, '读取任务事件失败。')
  }
  return parseSse(await response.text())
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch('/health/ready', { cache: 'no-store' })
    return response.ok
  } catch {
    return false
  }
}

function queryString(
  params: Record<string, string | number | string[] | null | undefined>,
): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, item)
    } else {
      search.set(key, String(value))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function listHandoffs(
  identity: Identity,
  options: {
    statuses?: string[]
    queue?: string
    limit?: number
    offset?: number
  } = {},
): Promise<HandoffQueueResponse> {
  return request(
    `/handoffs${queryString({
      status: options.statuses ?? [],
      queue: options.queue,
      limit: options.limit ?? 100,
      offset: options.offset ?? 0,
    })}`,
    identity,
  )
}

export function getHandoff(
  identity: Identity,
  eventId: string,
): Promise<HandoffDetail> {
  return request(`/handoffs/${encodeURIComponent(eventId)}`, identity)
}

export function claimHandoff(
  identity: Identity,
  eventId: string,
): Promise<HandoffDetail> {
  return request(`/handoffs/${encodeURIComponent(eventId)}/claim`, identity, {
    method: 'POST',
  })
}

export function releaseHandoff(
  identity: Identity,
  eventId: string,
  note?: string,
): Promise<HandoffDetail> {
  return request(`/handoffs/${encodeURIComponent(eventId)}/release`, identity, {
    method: 'POST',
    body: JSON.stringify({ note: note?.trim() || null }),
  })
}

export function closeHandoff(
  identity: Identity,
  eventId: string,
  resolution = 'RESOLVED',
): Promise<HandoffDetail> {
  return request(`/handoffs/${encodeURIComponent(eventId)}/close`, identity, {
    method: 'POST',
    body: JSON.stringify({ resolution }),
  })
}

export function getHandoffMetrics(
  identity: Identity,
  windowMinutes?: number,
): Promise<HandoffMetrics> {
  return request(
    `/handoffs/metrics${queryString({ window_minutes: windowMinutes })}`,
    identity,
  )
}

export function listAbandons(
  identity: Identity,
  limit = 50,
  offset = 0,
): Promise<AbandonListResponse> {
  return request(
    `/handoffs/abandons${queryString({ limit, offset })}`,
    identity,
  )
}

export function listOutbox(
  identity: Identity,
  status?: string,
  limit = 50,
  offset = 0,
): Promise<OutboxQueueResponse> {
  return request(
    `/outbox${queryString({ status, limit, offset })}`,
    identity,
  )
}

export function getTrace(
  identity: Identity,
  traceId: string,
): Promise<TraceDetail> {
  return request(`/traces/${encodeURIComponent(traceId)}`, identity)
}

export function getTraceMetrics(
  identity: Identity,
  windowMinutes?: number,
): Promise<TraceMetrics> {
  return request(
    `/traces/metrics${queryString({ window_minutes: windowMinutes })}`,
    identity,
  )
}

export function getSecurityMetrics(
  identity: Identity,
  windowMinutes?: number,
): Promise<SecurityMetrics> {
  return request(
    `/security/metrics${queryString({ window_minutes: windowMinutes })}`,
    identity,
  )
}

export function getAnalyticsQuery(
  identity: Identity,
  queryId: string,
): Promise<AnalyticsQuerySummary> {
  return request(`/analytics/queries/${encodeURIComponent(queryId)}`, identity)
}

export function getChannelMatrix(
  identity: Identity,
): Promise<ChannelMatrixResponse> {
  return request('/channels', identity)
}

export function getMemory(identity: Identity): Promise<MemoryProfile> {
  return request('/memory', identity)
}

export function updateMemory(
  identity: Identity,
  changes: MemoryUpdatePayload,
): Promise<MemoryProfile> {
  return request('/memory', identity, {
    method: 'PUT',
    body: JSON.stringify(changes),
  })
}

export function deleteMemory(identity: Identity): Promise<void> {
  return request('/memory', identity, { method: 'DELETE' })
}

export function exportMemory(identity: Identity): Promise<MemoryExport> {
  return request('/memory/export', identity)
}

export function listNotifications(
  identity: Identity,
  limit = 50,
): Promise<NotificationListResponse> {
  return request(`/memory/notifications${queryString({ limit })}`, identity)
}

export function confirmNotification(
  identity: Identity,
  notificationId: string,
): Promise<NotificationView> {
  return request(
    `/memory/notifications/${encodeURIComponent(notificationId)}/confirm`,
    identity,
    { method: 'POST' },
  )
}

export function cancelNotification(
  identity: Identity,
  notificationId: string,
): Promise<NotificationView> {
  return request(
    `/memory/notifications/${encodeURIComponent(notificationId)}/cancel`,
    identity,
    { method: 'POST' },
  )
}

export function listProactiveRules(
  identity: Identity,
): Promise<ProactiveRuleListResponse> {
  return request('/memory/rules', identity)
}

export function updateProactiveRule(
  identity: Identity,
  eventType: string,
  changes: ProactiveRuleUpdatePayload,
): Promise<ProactiveRule> {
  return request(`/memory/rules/${encodeURIComponent(eventType)}`, identity, {
    method: 'PUT',
    body: JSON.stringify(changes),
  })
}
