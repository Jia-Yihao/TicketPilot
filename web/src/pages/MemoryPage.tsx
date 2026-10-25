import { useEffect, useMemo, useState } from 'react'
import {
  cancelNotification,
  confirmNotification,
  deleteMemory,
  errorText,
  exportMemory,
  getMemory,
  listNotifications,
  updateMemory,
} from '../api'
import { CopyValue } from '../components/CopyValue'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { formatDateTime } from '../format'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type {
  MemoryExport,
  MemoryProfile,
  MemoryUpdatePayload,
  NotificationView,
} from '../types'

type Draft = {
  preferred_name: string
  locale: string
  tone: string
  contact_window: string
  contact_channel: string
  product_interests: string
  history_summary: string
  proactive_opt_in: boolean
  max_notifications_per_day: number
}

const CHANNEL_OPTIONS: Record<string, string> = {
  EMAIL: '邮件',
  WECHAT: '微信',
  WECOM: '企业微信',
}

const EMPTY_PROFILE: MemoryProfile = {
  preferred_name: null,
  locale: 'zh-CN',
  preferences: {},
  history_summary: '',
  proactive_opt_in: false,
  muted_event_types: [],
  max_notifications_per_day: 3,
  updated_at: null,
}

const NOTIFICATION_LABELS: Record<string, { text: string; tone: string }> = {
  PENDING_CONFIRMATION: { text: '等你确认', tone: 'pending' },
  CONFIRMED: { text: '已确认', tone: 'active' },
  SENT: { text: '已推送', tone: 'success' },
  CANCELLED: { text: '已取消', tone: 'muted' },
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function interestsOf(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join('、') : ''
}

function draftOf(profile: MemoryProfile): Draft {
  const preferences = profile.preferences ?? {}
  return {
    preferred_name: profile.preferred_name ?? '',
    locale: profile.locale,
    tone: textOf(preferences.tone),
    contact_window: textOf(preferences.contact_window),
    contact_channel: textOf(preferences.contact_channel),
    product_interests: interestsOf(preferences.product_interests),
    history_summary: profile.history_summary ?? '',
    proactive_opt_in: profile.proactive_opt_in,
    max_notifications_per_day: profile.max_notifications_per_day,
  }
}

function buildPayload(profile: MemoryProfile, draft: Draft): MemoryUpdatePayload {
  const payload: MemoryUpdatePayload = {}
  const name = draft.preferred_name.trim()
  if (name !== (profile.preferred_name ?? '')) payload.preferred_name = name || null

  const locale = draft.locale.trim()
  if (locale && locale !== profile.locale) payload.locale = locale

  if (draft.history_summary !== (profile.history_summary ?? '')) {
    payload.history_summary = draft.history_summary
  }
  if (draft.proactive_opt_in !== profile.proactive_opt_in) {
    payload.proactive_opt_in = draft.proactive_opt_in
  }
  if (draft.max_notifications_per_day !== profile.max_notifications_per_day) {
    payload.max_notifications_per_day = draft.max_notifications_per_day
  }

  const current = profile.preferences ?? {}
  const preferences: Record<string, unknown> = {}
  if (draft.tone && draft.tone !== textOf(current.tone)) {
    preferences.tone = draft.tone
  }
  const window = draft.contact_window.trim()
  if (window && window !== textOf(current.contact_window)) {
    preferences.contact_window = window
  }
  const channel = draft.contact_channel
  if (channel !== textOf(current.contact_channel)) {
    // An empty choice is a real change: it clears the preference on the server.
    preferences.contact_channel = channel || null
  }
  const interests = draft.product_interests
    .split(/[、,，]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
  const currentInterests = Array.isArray(current.product_interests)
    ? current.product_interests.map((item) => String(item))
    : []
  if (
    interests.length > 0 &&
    interests.join('|').toLowerCase() !== currentInterests.join('|').toLowerCase()
  ) {
    preferences.product_interests = interests
  }
  if (Object.keys(preferences).length > 0) payload.preferences = preferences

  return payload
}

export function MemoryPage() {
  usePageTitle('我的记忆')
  const { identity } = useIdentity()
  const [profile, setProfile] = useState<MemoryProfile | null>(null)
  const [draft, setDraft] = useState<Draft>(draftOf(EMPTY_PROFILE))
  const [notifications, setNotifications] = useState<NotificationView[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [exported, setExported] = useState<MemoryExport | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getMemory(identity)
      .then(async (data) => {
        if (cancelled) return
        setProfile(data)
        setDraft(draftOf(data))
        setExported(null)
        setConfirmDelete(false)
        const rows = data.proactive_opt_in
          ? (await listNotifications(identity)).notifications
          : []
        if (!cancelled) setNotifications(rows)
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
  }, [identity, reloadTick])

  const payload = useMemo(
    () => (profile ? buildPayload(profile, draft) : {}),
    [profile, draft],
  )
  const dirtyCount = Object.keys(payload).length

  function patch(next: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...next }))
    setNotice(null)
  }

  async function save() {
    if (!profile || dirtyCount === 0) return
    setSaving(true)
    setFormError(null)
    try {
      const updated = await updateMemory(identity, payload)
      setProfile(updated)
      setDraft(draftOf(updated))
      if (updated.proactive_opt_in && !profile.proactive_opt_in) {
        setNotifications((await listNotifications(identity)).notifications)
      }
      setNotice('已保存。只有你提交的字段被写入，其余保持原样。')
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setSaving(false)
    }
  }

  async function runExport() {
    setFormError(null)
    try {
      setExported(await exportMemory(identity))
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    }
  }

  async function erase() {
    setDeleting(true)
    setFormError(null)
    try {
      await deleteMemory(identity)
      const fresh = await getMemory(identity)
      setProfile(fresh)
      setDraft(draftOf(fresh))
      setNotifications([])
      setExported(null)
      setConfirmDelete(false)
      setNotice('已删除档案：推送记录一并清除，还在排队的推送也已取消。')
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setDeleting(false)
    }
  }

  async function decide(notificationId: string, action: 'confirm' | 'cancel') {
    setBusyId(notificationId)
    setFormError(null)
    try {
      const call =
        action === 'confirm' ? confirmNotification : cancelNotification
      const updated = await call(identity, notificationId)
      setNotifications((current) =>
        current.map((item) =>
          item.notification_id === notificationId ? updated : item,
        ),
      )
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setBusyId(null)
    }
  }

  const muted = profile?.muted_event_types ?? []

  return (
    <div className="page page--memory">
      <PageHeader
        eyebrow="MEMORY / 我的记忆"
        title="我的档案与推送"
        description="这份档案由你自己持有：可以查看、修改、导出、删除。删除会连带取消还在排队的推送。"
        meta={
          <div className="matrix-summary">
            <span className="chip">
              {identity.tenant} / {identity.user}
            </span>
            <span className="chip">
              更新于 {formatDateTime(profile?.updated_at) || '尚无记录'}
            </span>
          </div>
        }
        actions={
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => setReloadTick((value) => value + 1)}
            disabled={loading}
          >
            <Icon name="refresh" size={15} />
            重新读取
          </button>
        }
      />

      <div className="role-note">
        <Icon name="shield" size={16} />
        <p>
          档案不参与答案生成：改这里不会改变客服给出的退款资格或政策结论。历史摘要在写入前已脱敏，
          页面与导出看到的都是脱敏后的文本。
        </p>
      </div>

      {loading && !profile ? (
        <div className="memory-grid" aria-hidden="true">
          {[0, 1, 2].map((key) => (
            <div className="panel" key={key}>
              <div className="panel__body">
                <span
                  className="skeleton skeleton--line"
                  style={{ width: '38%' }}
                />
                <span
                  className="skeleton skeleton--line"
                  style={{ width: '72%', marginTop: 10 }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <ErrorPanel
          title="无法读取你的档案"
          error={error}
          onRetry={() => setReloadTick((value) => value + 1)}
        />
      ) : (
        <>
          <section className="panel">
            <div className="panel__head">
              <div>
                <span className="panel__tag">PROFILE / 档案</span>
                <h2>你允许我们记住的内容</h2>
              </div>
              <span className="chip">
                {dirtyCount > 0 ? `${dirtyCount} 项待保存` : '没有改动'}
              </span>
            </div>
            <div className="panel__body">
              <div className="memory-grid">
                <label className="field field--inline">
                  <span className="field__label">
                    称呼
                    <small>40 字以内，不接受证件号一类的敏感内容</small>
                  </span>
                  <input
                    className="input"
                    type="text"
                    maxLength={40}
                    value={draft.preferred_name}
                    onChange={(event) =>
                      patch({ preferred_name: event.target.value })
                    }
                    placeholder="例如：王女士"
                  />
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    语言
                    <small>形如 zh-CN</small>
                  </span>
                  <input
                    className="input"
                    type="text"
                    maxLength={16}
                    value={draft.locale}
                    onChange={(event) => patch({ locale: event.target.value })}
                  />
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    回复风格
                    <small>不选表示不修改</small>
                  </span>
                  <select
                    className="select"
                    value={draft.tone}
                    onChange={(event) => patch({ tone: event.target.value })}
                  >
                    <option value="">未设置</option>
                    <option value="concise">简洁</option>
                    <option value="detailed">详细</option>
                  </select>
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    方便联系的时间
                    <small>40 字以内</small>
                  </span>
                  <input
                    className="input"
                    type="text"
                    maxLength={40}
                    value={draft.contact_window}
                    onChange={(event) =>
                      patch({ contact_window: event.target.value })
                    }
                    placeholder="例如：工作日 19:00 之后"
                  />
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    推送渠道
                    <small>只列能真正送到的渠道；地址不存在这里</small>
                  </span>
                  <select
                    className="select"
                    value={draft.contact_channel}
                    onChange={(event) =>
                      patch({ contact_channel: event.target.value })
                    }
                  >
                    <option value="">未设置</option>
                    {Object.entries(CHANNEL_OPTIONS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                    {draft.contact_channel && !(draft.contact_channel in CHANNEL_OPTIONS) ? (
                      // A channel saved outside this page still has to show what
                      // it is, instead of looking unset and being silently rewritten.
                      <option value={draft.contact_channel}>
                        {draft.contact_channel}（当前送不到）
                      </option>
                    ) : null}
                  </select>
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    关注的商品
                    <small>用顿号或逗号分隔</small>
                  </span>
                  <input
                    className="input"
                    type="text"
                    value={draft.product_interests}
                    onChange={(event) =>
                      patch({ product_interests: event.target.value })
                    }
                    placeholder="例如：扫地机、净水器"
                  />
                </label>

                <label className="field field--inline">
                  <span className="field__label">
                    每日推送上限
                    <small>1 到 10 条</small>
                  </span>
                  <input
                    className="input"
                    type="number"
                    min={1}
                    max={10}
                    value={draft.max_notifications_per_day}
                    onChange={(event) =>
                      patch({
                        max_notifications_per_day: Number(event.target.value),
                      })
                    }
                  />
                </label>
              </div>

              <label className="field">
                <span className="field__label">
                  历史摘要
                  <small>最多 500 字，写入前自动脱敏</small>
                </span>
                <textarea
                  className="textarea"
                  rows={3}
                  maxLength={500}
                  value={draft.history_summary}
                  onChange={(event) =>
                    patch({ history_summary: event.target.value })
                  }
                  placeholder="例如：多次咨询净水器滤芯，偏好上门安装。"
                />
                <span className="field__hint">
                  <span>页面显示的已经是脱敏结果。</span>
                  <span className="mono">{draft.history_summary.length} / 500</span>
                </span>
              </label>

              <label className="field field--inline">
                <span className="field__label">
                  接受主动推送
                  <small>关掉之后不再生成新的待确认推送</small>
                </span>
                <input
                  type="checkbox"
                  checked={draft.proactive_opt_in}
                  onChange={(event) =>
                    patch({ proactive_opt_in: event.target.checked })
                  }
                />
              </label>

              {muted.length > 0 ? (
                <p className="memory-muted mono">
                  已静音的事件类型：{muted.join('、')}
                  （按事件类型静音目前只能在接口里改）
                </p>
              ) : null}

              {formError ? (
                <div className="inline-error" role="alert">
                  <Icon name="alert" size={15} />
                  {formError}
                </div>
              ) : null}
              {notice ? (
                <p className="memory-notice" role="status">
                  <Icon name="check" size={14} />
                  {notice}
                </p>
              ) : null}

              <div className="memory-actions">
                <button
                  type="button"
                  className="button button--primary button--sm"
                  onClick={save}
                  disabled={dirtyCount === 0 || saving}
                >
                  <Icon name="check" size={15} />
                  {saving ? '保存中' : '保存改动'}
                </button>
                <button
                  type="button"
                  className="button button--ghost button--sm"
                  onClick={() => {
                    if (profile) setDraft(draftOf(profile))
                    setNotice('已还原成档案里现有的内容。')
                  }}
                  disabled={dirtyCount === 0 || saving}
                >
                  放弃改动
                </button>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <span className="panel__tag">NOTIFICATIONS / 待确认推送</span>
                <h2>推送都要你先点头</h2>
              </div>
              <span className="chip">{profile?.proactive_opt_in ? '已开启' : '未开启'}</span>
            </div>
            <div className="panel__body">
              {!profile?.proactive_opt_in ? (
                <p className="memory-empty">
                  当前没有开启主动推送，所以这里没有待确认的推送。上面的开关保存之后才会生成。
                </p>
              ) : notifications.length === 0 ? (
                <p className="memory-empty">
                  暂时没有需要你确认的推送。开启后如果发生退款到账、工单更新或物流异常，
                  我们会先写成一条待确认的推送，等你同意才可能送出。
                </p>
              ) : (
                <ul className="memory-list">
                  {notifications.map((item) => {
                    const meta = NOTIFICATION_LABELS[item.status] ?? {
                      text: item.status,
                      tone: 'neutral',
                    }
                    const pending = item.status === 'PENDING_CONFIRMATION'
                    return (
                      <li className="memory-item" key={item.notification_id}>
                        <div className="memory-item__head">
                          <strong>{item.title}</strong>
                          <span className={`badge badge--${meta.tone}`}>
                            <span className="badge__dot" aria-hidden="true" />
                            {meta.text}
                          </span>
                        </div>
                        <p className="memory-item__body">{item.body}</p>
                        <div className="memory-item__foot">
                          <span className="mono">
                            {item.event_type} · {formatDateTime(item.created_at)}
                          </span>
                          {pending ? (
                            <span className="memory-item__actions">
                              <button
                                type="button"
                                className="button button--primary button--sm"
                                onClick={() =>
                                  decide(item.notification_id, 'confirm')
                                }
                                disabled={busyId === item.notification_id}
                              >
                                同意推送
                              </button>
                              <button
                                type="button"
                                className="button button--ghost button--sm"
                                onClick={() =>
                                  decide(item.notification_id, 'cancel')
                                }
                                disabled={busyId === item.notification_id}
                              >
                                不用了
                              </button>
                            </span>
                          ) : null}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
              {notifications.some((item) => item.status === 'SENT') ? (
                <p className="memory-muted">
                  「已推送」只表示内容已交给本站配置的出站通道并写成功，
                  不是运营商或邮件服务商的送达回执；实际是否到达以那些系统为准。
                </p>
              ) : null}
            </div>
          </section>

          <section className="panel">
            <div className="panel__head">
              <div>
                <span className="panel__tag">DATA / 导出与删除</span>
                <h2>随时拿走，也随时清空</h2>
              </div>
            </div>
            <div className="panel__body">
              <div className="memory-actions">
                <button
                  type="button"
                  className="button button--ghost button--sm"
                  onClick={runExport}
                >
                  <Icon name="external" size={15} />
                  导出我的数据
                </button>
                {!confirmDelete ? (
                  <button
                    type="button"
                    className="button button--danger button--sm"
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Icon name="x" size={15} />
                    删除我的记忆
                  </button>
                ) : (
                  <span className="memory-actions">
                    <button
                      type="button"
                      className="button button--danger button--sm"
                      onClick={erase}
                      disabled={deleting}
                    >
                      {deleting ? '删除中' : '确认删除，不可撤销'}
                    </button>
                    <button
                      type="button"
                      className="button button--ghost button--sm"
                      onClick={() => setConfirmDelete(false)}
                      disabled={deleting}
                    >
                      取消
                    </button>
                  </span>
                )}
              </div>

              {exported ? (
                <div className="memory-export">
                  <p className="memory-export__note">
                    导出的是脱敏后的档案与通知计数，原始对话内容不在其中。
                  </p>
                  <pre className="memory-export__json mono">
                    {JSON.stringify(exported, null, 2)}
                  </pre>
                  <CopyValue
                    value={JSON.stringify(exported, null, 2)}
                    display="复制导出 JSON"
                  />
                  <span className="chip">
                    {exported.redacted ? 'redacted · 已脱敏' : 'redacted 标记缺失'}
                  </span>
                </div>
              ) : null}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
