import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { errorText, listTasks, sendSupportMessage } from '../api'
import { ContextCardView } from '../components/ContextCard'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { TaskResultView } from '../components/TaskResult'
import {
  formatDateTime,
  formatFullDate,
  formatMoney,
  formatRelative,
  initials,
  intentLabel,
  roleLabel,
  statusMeta,
} from '../format'
import { usePageTitle } from '../hooks'
import { IDENTITY_PRESETS, useIdentity } from '../identity'
import type { Identity, SupportReply, TaskSummary } from '../types'

type ThreadMessage =
  | { id: string; role: 'user'; text: string; at: string }
  | { id: string; role: 'agent'; reply: SupportReply; at: string }

const REVIEWER = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'reviewer' && preset.tenant === 'demo',
)
const ALICE = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'alice' && preset.tenant === 'demo',
)
const BOB = IDENTITY_PRESETS.find(
  (preset) => preset.user === 'bob' && preset.tenant === 'demo',
)

function orderFor(identity: Identity): {
  orderId: string
  amount: string
} {
  if (identity.tenant === 'acme') {
    return { orderId: 'ORD-OTHER-3001', amount: '30' }
  }
  if (identity.user === 'bob') {
    return { orderId: 'ORD-DEMO-2001', amount: '50' }
  }
  return { orderId: 'ORD-DEMO-1001', amount: '129' }
}

function promptsFor(identity: Identity): { label: string; text: string }[] {
  const { orderId, amount } = orderFor(identity)
  return [
    { label: '商品信息', text: `订单 ${orderId} 买的是什么商品？` },
    { label: '查询订单', text: `帮我查询订单 ${orderId}` },
    { label: '物流进度', text: `订单 ${orderId} 的物流到哪了？` },
    { label: '售后条款', text: '退款条件是什么？' },
    { label: '申请退款', text: `申请退款 ${amount} 元，订单 ${orderId}` },
  ]
}

function threadsKey(identity: Identity): string {
  return `ticketpilot.thread.v2.${identity.tenant}.${identity.user}`
}

function conversationKey(identity: Identity): string {
  return `ticketpilot.conversation.v1.${identity.tenant}.${identity.user}`
}

const SLOT_LABELS: Record<string, string> = {
  order_id: '订单号',
  amount_cents: '退款金额',
  product_id: '商品',
}

// The four effect codes of app/agent/personalization.py, phrased as what the
// customer gets rather than as a log token.
const PERSONALIZATION_LABELS: Record<string, string> = {
  ADDRESSED: '用你的称呼开头',
  CARDS_REORDERED: '把你关注的商品排在前面',
  SUGGESTIONS_TRIMMED: '按简洁偏好少给追问',
  INTEREST_SUGGESTION: '补了一条你关注商品的追问',
}

function loadConversationId(key: string): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function saveConversationId(key: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(key, value)
    else sessionStorage.removeItem(key)
  } catch {
    // Storage is best-effort; the conversation still works in memory.
  }
}

function loadThread(key: string): ThreadMessage[] {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw) as ThreadMessage[]
    if (!Array.isArray(parsed)) return []
    return parsed.slice(-16)
  } catch {
    return []
  }
}

function messageId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
}

function SeedOrders({ identity }: { identity: Identity }) {
  const rows: { id: string; status: string; detail: string }[] =
    identity.tenant === 'acme'
      ? [
          {
            id: 'ORD-OTHER-3001',
            status: '已签收',
            detail: '实付 ¥30.00 · Acme 租户',
          },
        ]
      : identity.user === 'bob'
        ? [{ id: 'ORD-DEMO-2001', status: '已签收', detail: '实付 ¥50.00 · 可退 ¥50.00' }]
        : [
            { id: 'ORD-DEMO-1001', status: '已签收', detail: '实付 ¥129.00 · 可退 ¥129.00' },
            { id: 'ORD-DEMO-1002', status: '运输中', detail: '实付 ¥80.00 · 物流停留超 48 小时' },
          ]

  return (
    <section className="rail-card">
      <header>
        <h3>演示订单</h3>
        <span className="mono">SEED DATA</span>
      </header>
      <ul className="seed-orders">
        {rows.map((row) => (
          <li key={row.id}>
            <div>
              <span className="mono">{row.id}</span>
              <small>{row.detail}</small>
            </div>
            <span className="seed-orders__status">{row.status}</span>
          </li>
        ))}
      </ul>
      <p className="rail-note">来自种子数据，仅用于本地演示。</p>
    </section>
  )
}

export function ConversationPage() {
  usePageTitle('售后对话')
  const { identity, isApprover, setIdentity } = useIdentity()
  const navigate = useNavigate()
  const storageKey = threadsKey(identity)
  const conversationStorageKey = conversationKey(identity)
  const [messages, setMessages] = useState<ThreadMessage[]>(() =>
    loadThread(storageKey),
  )
  const [conversationId, setConversationId] = useState<string | null>(() =>
    loadConversationId(conversationStorageKey),
  )
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [threadError, setThreadError] = useState<unknown>(null)
  const [recent, setRecent] = useState<TaskSummary[]>([])
  const [recentLoading, setRecentLoading] = useState(true)
  const [recentError, setRecentError] = useState(false)

  const endRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const storageKeyRef = useRef(storageKey)
  const sendSeq = useRef(0)
  const conversationRef = useRef<string | null>(null)
  const deliveryRef = useRef<{
    id: string
    text: string
    conversationId: string | null
  } | null>(null)

  const disclosure = useMemo(() => {
    const cards = messages.flatMap((message) =>
      message.role === 'agent' ? message.reply.context_cards : [],
    )
    return {
      cards: cards.length,
      sections: cards.reduce((sum, card) => sum + card.sections.length, 0),
      chars: cards.reduce((sum, card) => sum + card.total_chars, 0),
      tasks: messages.filter(
        (message) => message.role === 'agent' && message.reply.task,
      ).length,
    }
  }, [messages])

  const currentSlots = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.role === 'agent') return message.reply.slots
    }
    return {} as SupportReply['slots']
  }, [messages])

  useEffect(() => {
    storageKeyRef.current = storageKey
  }, [storageKey])

  useEffect(() => {
    saveConversationId(conversationStorageKey, conversationId)
  }, [conversationId, conversationStorageKey])

  useEffect(() => {
    try {
      const key = storageKeyRef.current
      if (messages.length === 0) sessionStorage.removeItem(key)
      else sessionStorage.setItem(key, JSON.stringify(messages.slice(-16)))
    } catch {
      // Storage is best-effort; the thread can continue in memory.
    }
  }, [messages])

  useEffect(() => {
    sendSeq.current += 1
    setMessages(loadThread(storageKey))
    const restored = loadConversationId(conversationStorageKey)
    conversationRef.current = restored
    deliveryRef.current = null
    setConversationId(restored)
    setThreadError(null)
    setInput('')
    setSending(false)
  }, [storageKey, conversationStorageKey])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [messages.length, sending, threadError])

  async function loadRecent() {
    try {
      const data = await listTasks(identity, 5, 0)
      setRecent(data.items)
      setRecentError(false)
    } catch {
      setRecentError(true)
    } finally {
      setRecentLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    setRecentLoading(true)
    listTasks(identity, 5, 0)
      .then((data) => {
        if (cancelled) return
        setRecent(data.items)
        setRecentError(false)
      })
      .catch(() => {
        if (!cancelled) setRecentError(true)
      })
      .finally(() => {
        if (!cancelled) setRecentLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [identity])

  async function send(raw: string) {
    const text = raw.trim()
    if (!text || sending) return
    const seq = ++sendSeq.current
    const localId = messageId('user')
    const conversationId = conversationRef.current
    const cached = deliveryRef.current
    const deliveryId =
      cached &&
      cached.text === text &&
      cached.conversationId === conversationId
        ? cached.id
        : messageId('dlv')
    deliveryRef.current = { id: deliveryId, text, conversationId }
    const userMessage: ThreadMessage = {
      id: localId,
      role: 'user',
      text,
      at: new Date().toISOString(),
    }
    setMessages((current) => [...current, userMessage])
    setThreadError(null)
    setSending(true)
    setInput('')

    try {
      const reply = await sendSupportMessage(
        identity,
        text,
        { conversation_id: conversationId ?? undefined },
        deliveryId,
      )
      if (seq !== sendSeq.current) return
      deliveryRef.current = null
      conversationRef.current = reply.conversation_id
      setConversationId(reply.conversation_id)
      setMessages((current) => [
        ...current,
        {
          id: messageId('agent'),
          role: 'agent',
          reply,
          at: new Date().toISOString(),
        },
      ])
      void loadRecent()
    } catch (error) {
      if (seq !== sendSeq.current) return
      setMessages((current) =>
        current.filter((message) => message.id !== localId),
      )
      setThreadError(error)
      setInput(text)
    } finally {
      if (seq === sendSeq.current) {
        setSending(false)
        window.setTimeout(() => inputRef.current?.focus(), 0)
      }
    }
  }

  function reviewApproval(approvalId: string) {
    if (REVIEWER) setIdentity(REVIEWER)
    navigate(`/approvals?focus=${encodeURIComponent(approvalId)}`)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send(input)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault()
      void send(input)
    }
  }

  function clearThread() {
    setMessages([])
    setThreadError(null)
    setInput('')
    conversationRef.current = null
    deliveryRef.current = null
    setConversationId(null)
    inputRef.current?.focus()
  }

  if (isApprover) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="SUPPORT CONVERSATION / 售后入口"
          title="售后对话"
          description="客户原话进入本地客服 Agent：查询直接给结论，动作进入确定性工作流。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="user" size={26} />
          </span>
          <p className="eyebrow">SURFACE / CUSTOMER IDENTITY</p>
          <h2>售后对话面向客户身份</h2>
          <p className="gate__copy">
            当前身份是 {identity.user}，没有演示订单。审批人可以进入审批工作台处理队列，
            或切换为客户身份走完申请流程。
          </p>
          <div className="gate__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={() => {
                if (ALICE) setIdentity(ALICE)
              }}
            >
              切换为 Alice
              <Icon name="arrow" size={16} />
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => {
                if (BOB) setIdentity(BOB)
              }}
            >
              切换为 Bob
            </button>
            <button
              type="button"
              className="button button--quiet"
              onClick={() => navigate('/approvals')}
            >
              打开审批工作台
            </button>
          </div>
          <ul className="gate__facts">
            <li>
              <Icon name="user" size={15} />
              客户身份只能看见自己的订单与任务
            </li>
            <li>
              <Icon name="shield" size={15} />
              退款申请会进入四眼审批，不在对话内直接执行
            </li>
          </ul>
        </section>
      </div>
    )
  }

  const prompts = promptsFor(identity)

  return (
    <div className="page page--conversation">
      <PageHeader
        eyebrow="SUPPORT CONVERSATION / 售后入口"
        title="售后对话"
        description="本地客服 Agent 先给结论与紧凑卡片，商品参数、政策章节与订单字段按需展开；退款与工单仍由确定性工作流执行。"
        actions={
          messages.length > 0 ? (
            <button
              type="button"
              className="button button--quiet button--sm"
              onClick={clearThread}
            >
              清空当前会话
            </button>
          ) : null
        }
      />

      <div className="conversation-grid">
        <section className="thread" aria-label="售后对话线程">
          <div className="thread__scroll">
            {messages.length === 0 ? (
              <div className="thread-empty">
                <span className="thread-empty__mark mono">TP</span>
                <p className="eyebrow">CONVERSATION / LOCAL SUPPORT AGENT</p>
                <h2>先给结论，细节按需展开</h2>
                <p className="thread-empty__copy">
                  本地 Agent 会把商品、政策、订单与物流压成摘要卡片；
                  只有点开章节时才读取全文。退款与工单不会在对话里直接执行。
                </p>
                <div className="thread-empty__steps">
                  <span>
                    <b>01</b>意图识别
                  </span>
                  <span>
                    <b>02</b>检索上下文
                  </span>
                  <span>
                    <b>03</b>按需展开
                  </span>
                  <span>
                    <b>04</b>审批执行
                  </span>
                </div>
                <p className="thread-empty__hint">
                  从下方选择一个示例，或直接描述售后问题。
                </p>
              </div>
            ) : null}

            <div className="thread__messages">
              {messages.map((message) =>
                message.role === 'user' ? (
                  <div key={message.id} className="msg msg--user">
                    <div className="msg__bubble">{message.text}</div>
                    <span className="msg__meta mono">
                      {identity.user} · {formatDateTime(message.at)}
                    </span>
                  </div>
                ) : (
                  <div key={message.id} className="msg msg--agent">
                    <span className="msg__meta mono">
                      SUPPORT AGENT · {formatDateTime(message.at)}
                    </span>
                    <article className="agent-card agent-card--support">
                      <header className="agent-card__head">
                        <span className="agent-card__mark mono">TP</span>
                        <div className="agent-card__title">
                          <strong>客服 Agent</strong>
                          <span className="mono">
                            {intentLabel(message.reply.intent)} ·{' '}
                            {message.reply.agent_mode.toUpperCase()}
                          </span>
                        </div>
                        {message.reply.task ? (
                          <StatusBadge status={message.reply.task.status} />
                        ) : (
                          <span className="agent-mode mono">READ ONLY</span>
                        )}
                      </header>

                      <div className="support-reply">
                        <p>{message.reply.reply}</p>
                        {message.reply.inherited_slots.length > 0 ? (
                          <p className="support-inherit">
                            沿用上一轮的
                            {message.reply.inherited_slots
                              .map(
                                (slot) =>
                                  `${SLOT_LABELS[slot] ?? slot}${
                                    slot === 'order_id' && message.reply.slots.order_id
                                      ? ` ${message.reply.slots.order_id}`
                                      : ''
                                  }`,
                              )
                              .join('、')}
                          </p>
                        ) : null}
                        {message.reply.personalization ? (
                          <p className="support-inherit">
                            本轮按你的档案调整过：
                            {message.reply.personalization.effects
                              .map(
                                (effect) => PERSONALIZATION_LABELS[effect] ?? effect,
                              )
                              .join('、')}
                          </p>
                        ) : null}
                      </div>

                      {message.reply.clarification ? (
                        <div className="support-clarify" role="status">
                          <span className="result-label">
                            MISSING SLOT ·{' '}
                            {message.reply.clarification.expected_slot}
                          </span>
                          <p>{message.reply.clarification.prompt}</p>
                        </div>
                      ) : null}

                      {message.reply.context_cards.length > 0 ? (
                        <div className="support-cards">
                          {message.reply.context_cards.map((card) => (
                            <ContextCardView
                              key={`${card.kind}:${card.card_id}`}
                              card={card}
                              identity={identity}
                            />
                          ))}
                        </div>
                      ) : null}

                      {message.reply.task ? (
                        <section className="support-task">
                          <header className="support-task__head">
                            <span className="result-label">
                              DETERMINISTIC WORKFLOW
                            </span>
                            <span className="mono">
                              {message.reply.task.task_id}
                            </span>
                          </header>
                          <TaskResultView
                            result={message.reply.task.result}
                            variant="compact"
                            onReview={reviewApproval}
                            reviewLabel="以审批人身份复核"
                          />
                          <footer className="support-task__foot">
                            <span className="mono">
                              CREATED{' '}
                              {formatFullDate(message.reply.task.created_at)}
                            </span>
                            <Link
                              className="text-link"
                              to={`/tasks/${message.reply.task.task_id}`}
                            >
                              完整时间线
                              <Icon name="arrow" size={13} />
                            </Link>
                          </footer>
                        </section>
                      ) : null}

                      {message.reply.suggestions.length > 0 ? (
                        <div className="support-suggestions">
                          <span className="result-label">继续追问</span>
                          <div className="chip-row">
                            {message.reply.suggestions.map((suggestion) => (
                              <button
                                key={suggestion}
                                type="button"
                                className="chip"
                                onClick={() => {
                                  setInput(suggestion)
                                  inputRef.current?.focus()
                                }}
                              >
                                {suggestion}
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <footer className="agent-card__foot">
                        <span className="mono">
                          {message.reply.task
                            ? 'ACTION ROUTED TO TASK'
                            : 'READ ONLY · NO TASK CREATED'}
                        </span>
                        <span className="mono">
                          {message.reply.slots.pending_intent
                            ? `PENDING · ${message.reply.slots.pending_intent}`
                            : message.reply.conversation_id}
                        </span>
                      </footer>
                    </article>
                  </div>
                ),
              )}

              {sending ? (
                <div className="msg msg--agent">
                  <span className="msg__meta mono">
                    SUPPORT AGENT / RETRIEVING CONTEXT
                  </span>
                  <div className="typing" role="status">
                    <span className="typing__dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    客服 Agent 正在检索上下文…
                  </div>
                </div>
              ) : null}

              {threadError ? (
                <div className="thread-error" role="alert">
                  <Icon name="alert" size={17} />
                  <div>
                    <strong>请求未完成</strong>
                    <p>{errorText(threadError)}</p>
                    <small>输入内容已保留，可直接修改后重新发送。</small>
                  </div>
                </div>
              ) : null}

              <div ref={endRef} />
            </div>
          </div>

          <div className="composer-area">
            <div className="prompt-chips" aria-label="示例请求">
              <span className="prompt-chips__label mono">TRY</span>
              {prompts.map((prompt) => (
                <button
                  key={prompt.label}
                  type="button"
                  className="chip"
                  title={prompt.text}
                  onClick={() => {
                    setInput(prompt.text)
                    inputRef.current?.focus()
                  }}
                >
                  {prompt.label}
                </button>
              ))}
            </div>
            <form className="composer" onSubmit={handleSubmit}>
              <label className="sr-only" htmlFor="conversation-input">
                输入售后请求
              </label>
              <textarea
                id="conversation-input"
                ref={inputRef}
                className="composer__input"
                rows={3}
                maxLength={500}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={`以 ${identity.user} 的身份描述售后问题，例如：订单 ORD-DEMO-1001 买的是什么商品？`}
              />
              <div className="composer__foot">
                <span className="composer__hint">
                  Enter 发送 · Shift + Enter 换行 · {input.length}/500
                </span>
                <button
                  type="submit"
                  className="button button--primary"
                  disabled={!input.trim() || sending}
                >
                  {sending ? '检索中…' : '发送'}
                  <Icon name="send" size={15} />
                </button>
              </div>
            </form>
            <p className="composer__boundary">
              <Icon name="lock" size={13} />
              查询由本地 Agent 直接回答；退款与工单进入确定性任务，R3 操作需审批。
            </p>
          </div>
        </section>

        <aside className="context-rail" aria-label="会话上下文">
          <section className="rail-card rail-card--identity">
            <header>
              <span className="mono">SESSION</span>
              <span className="rail-live">
                <span className="rail-live__dot" aria-hidden="true" />
                已连接
              </span>
            </header>
            <div className="rail-identity">
              <span className="rail-identity__avatar">
                {initials(identity.user)}
              </span>
              <div>
                <strong>{identity.user}</strong>
                <span>
                  {identity.tenant} · {roleLabel(identity.roles)}
                </span>
              </div>
            </div>
            <p className="rail-note">
              开发 Header 身份。切换身份后，租户范围与订单可见性会立即变化。
            </p>
          </section>

          <section className="rail-card">
            <header>
              <h3>最近任务</h3>
              <Link className="text-link" to="/tasks">
                全部
                <Icon name="arrow" size={13} />
              </Link>
            </header>
            {recentLoading ? (
              <p className="rail-empty">读取中…</p>
            ) : recentError ? (
              <p className="rail-empty">暂时无法读取最近任务。</p>
            ) : recent.length === 0 ? (
              <p className="rail-empty">还没有任务记录。发起退款或工单后会出现在这里。</p>
            ) : (
              <ul className="recent-list">
                {recent.map((task) => {
                  const meta = statusMeta(task.status)
                  return (
                    <li key={task.task_id}>
                      <Link to={`/tasks/${task.task_id}`}>
                        <span className="recent-list__top">
                          <span
                            className={`recent-list__dot tone-${meta.tone}`}
                            aria-hidden="true"
                          />
                          <span className="recent-list__status">
                            {meta.label}
                          </span>
                          <span className="recent-list__time mono">
                            {formatRelative(task.updated_at)}
                          </span>
                        </span>
                        <span className="recent-list__request">
                          {task.request}
                        </span>
                      </Link>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <section className="rail-card">
            <header>
              <h3>渐进式披露</h3>
              <span className="mono">ON DEMAND</span>
            </header>
            <ol className="disclosure-flow">
              <li>
                <b>01</b>
                <div>
                  <strong>回复结论</strong>
                  <span>先给一句可执行的答复，不倾倒全文。</span>
                </div>
              </li>
              <li>
                <b>02</b>
                <div>
                  <strong>紧凑卡片</strong>
                  <span>只带关键字段与章节标题。</span>
                </div>
              </li>
              <li>
                <b>03</b>
                <div>
                  <strong>按需展开</strong>
                  <span>点击章节时才请求全文与引用。</span>
                </div>
              </li>
            </ol>
            <div className="disclosure-stats">
              <span>
                <b>{disclosure.cards}</b>
                <small>卡片</small>
              </span>
              <span>
                <b>{disclosure.sections}</b>
                <small>章节</small>
              </span>
              <span>
                <b>{disclosure.chars}</b>
                <small>可展开字数</small>
              </span>
            </div>
            <p className="rail-note">
              当前会话已加载 {disclosure.tasks} 个确定性任务回执。
            </p>
          </section>

          <section className="rail-card">
            <header>
              <h3>会话槽位</h3>
              <span className="mono">SLOTS</span>
            </header>
            <ul className="slot-list">
              <li>
                <span>订单号</span>
                <b className="mono">{String(currentSlots.order_id ?? '—')}</b>
              </li>
              <li>
                <span>退款金额</span>
                <b className="mono">
                  {typeof currentSlots.amount_cents === 'number'
                    ? formatMoney(currentSlots.amount_cents)
                    : '—'}
                </b>
              </li>
              <li>
                <span>待补槽位</span>
                <b className="mono">
                  {String(currentSlots.pending_intent ?? '—')}
                </b>
              </li>
            </ul>
            <p className="rail-note">
              槽位按租户与用户保存，换一轮提问仍可续用同一订单。
            </p>
          </section>

          <SeedOrders identity={identity} />

          <section className="rail-card rail-card--boundary">
            <header>
              <h3>系统边界</h3>
              <Icon name="lock" size={15} />
            </header>
            <p>
              查询回答不写业务记录。退款必须进入任务、通过审批，再由退款工具执行并核对权威记录。
            </p>
          </section>
        </aside>
      </div>
    </div>
  )
}
