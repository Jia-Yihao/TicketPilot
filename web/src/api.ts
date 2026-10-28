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
  ProactiveRuleChange,
  ProactiveRuleChangeListResponse,
  ProactiveRuleDecisionPayload,
  ProactiveRuleListResponse,
  ProactiveRuleRollbackPayload,
  ProactiveRuleUpdatePayload,
  ProactiveRuleVersionListResponse,
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

const TERMINAL_EVENTS = new Set([
  'task.succeeded',
  'task.failed',
  'task.cancelled',
  'task.timed_out',
  'task.rejected',
  'task.expired',
])

export type TaskEventStreamState =
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'closed'

interface FollowTaskEventsOptions {
  signal?: AbortSignal
  /** Resume after this event; the server replays whatever comes next. */
  lastEventId?: string
  /** True while the page still expects new events. */
  shouldReconnect?: () => boolean
  onEvent: (event: TaskEvent) => void
  onState?: (state: TaskEventStreamState) => void
}

function parseSseBlock(block: string): TaskEvent | null {
  let id = ''
  let event = ''
  const dataLines: string[] = []
  for (const raw of block.split('\n')) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (!line) continue
    // A line starting with ":" is a comment; a heartbeat is one of these.
    if (line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'id') id = value
    else if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }
  if (!event) return null
  let data: Record<string, unknown> = {}
  try {
    data = dataLines.length
      ? (JSON.parse(dataLines.join('\n')) as Record<string, unknown>)
      : {}
  } catch {
    data = { raw: dataLines.join('\n') }
  }
  const createdAt =
    typeof data.created_at === 'string'
      ? data.created_at
      : new Date().toISOString()
  return { id, event, data, created_at: createdAt }
}

async function readEventStream(
  response: Response,
  onEvent: (event: TaskEvent) => boolean,
): Promise<boolean> {
  if (!response.body) {
    throw new Error('当前浏览器不支持流式事件读取。')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const event = parseSseBlock(block)
        if (event && onEvent(event)) terminal = true
        boundary = buffer.indexOf('\n\n')
      }
    }
    buffer += decoder.decode()
    const tail = parseSseBlock(buffer)
    if (tail && onEvent(tail)) terminal = true
  } finally {
    reader.releaseLock()
  }
  return terminal
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer)
        resolve()
      },
      { once: true },
    )
  })
}

/**
 * Follow a task's event stream until it reaches a terminal state.
 *
 * The server keeps the response open and pushes new events, so the console
 * must read the body incrementally — `response.text()` would wait for the
 * connection to close. A dropped connection resumes from the last id; the
 * page decides whether a clean close is worth reconnecting (an idle timeout
 * can end an open task, a terminal task cannot produce more events).
 */
export async function followTaskEvents(
  identity: Identity,
  taskId: string,
  options: FollowTaskEventsOptions,
): Promise<void> {
  let lastEventId = options.lastEventId
  let retryDelay = 1000
  let firstAttempt = true
  while (!options.signal?.aborted) {
    options.onState?.(firstAttempt ? 'connecting' : 'reconnecting')
    firstAttempt = false
    try {
      const headers = new Headers(identityHeaders(identity))
      headers.set('Accept', 'text/event-stream')
      if (lastEventId) headers.set('Last-Event-ID', lastEventId)
      const response = await fetch(
        `/api/v1/tasks/${encodeURIComponent(taskId)}/events`,
        { headers, signal: options.signal, cache: 'no-store' },
      )
      if (!response.ok) {
        throw new ApiError(
          response.status,
          `HTTP_${response.status}`,
          '读取任务事件失败。',
        )
      }
      options.onState?.('live')
      retryDelay = 1000
      const terminal = await readEventStream(response, (event) => {
        if (event.id) lastEventId = event.id
        options.onEvent(event)
        return TERMINAL_EVENTS.has(event.event)
      })
      if (terminal || !(options.shouldReconnect?.() ?? false)) {
        options.onState?.('closed')
        return
      }
      options.onState?.('reconnecting')
    } catch (error) {
      if (options.signal?.aborted) return
      if (error instanceof ApiError) {
        options.onState?.('closed')
        throw error
      }
      options.onState?.('reconnecting')
    }
    await wait(retryDelay, options.signal)
    retryDelay = Math.min(retryDelay * 2, 8000)
  }
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
): Promise<ProactiveRuleChange> {
  return request(`/memory/rules/${encodeURIComponent(eventType)}`, identity, {
    method: 'PUT',
    body: JSON.stringify(changes),
  })
}

export function listProactiveRuleChanges(
  identity: Identity,
  status?: string,
): Promise<ProactiveRuleChangeListResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/memory/rules/changes${query}`, identity)
}

export function approveProactiveRuleChange(
  identity: Identity,
  changeId: string,
  payload: ProactiveRuleDecisionPayload = {},
): Promise<ProactiveRuleChange> {
  return request(
    `/memory/rules/changes/${encodeURIComponent(changeId)}/approve`,
    identity,
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export function rejectProactiveRuleChange(
  identity: Identity,
  changeId: string,
  payload: ProactiveRuleDecisionPayload = {},
): Promise<ProactiveRuleChange> {
  return request(
    `/memory/rules/changes/${encodeURIComponent(changeId)}/reject`,
    identity,
    { method: 'POST', body: JSON.stringify(payload) },
  )
}

export function listProactiveRuleVersions(
  identity: Identity,
  eventType: string,
): Promise<ProactiveRuleVersionListResponse> {
  return request(
    `/memory/rules/${encodeURIComponent(eventType)}/versions`,
    identity,
  )
}

export function rollbackProactiveRule(
  identity: Identity,
  eventType: string,
  payload: ProactiveRuleRollbackPayload,
): Promise<ProactiveRuleChange> {
  return request(`/memory/rules/${encodeURIComponent(eventType)}/rollback`, identity, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
