import { useEffect, useMemo, useState } from 'react'
import {
  approveProactiveRuleChange,
  errorText,
  listProactiveRuleChanges,
  listProactiveRules,
  listProactiveRuleVersions,
  rejectProactiveRuleChange,
  rollbackProactiveRule,
  updateProactiveRule,
} from '../api'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { formatDateTime } from '../format'
import { usePageTitle } from '../hooks'
import { useIdentity } from '../identity'
import type {
  ProactiveRule,
  ProactiveRuleChange,
  ProactiveRuleUpdatePayload,
  ProactiveRuleVersion,
} from '../types'

const EVENT_LABELS: Record<string, string> = {
  DELIVERY_EXCEPTION: '物流异常提醒',
  REFUND_SETTLED: '退款到账提醒',
  TICKET_UPDATED: '工单进展提醒',
}

// The emitter recognises these three and only these three. A fourth event type
// needs code, not a config row, so this page cannot offer to add one.
const EVENT_ORDER: string[] = Object.keys(EVENT_LABELS).sort()

const CONTEXT_KEYS = [
  'order_id',
  'ticket_no',
  'amount',
  'summary',
  'status',
  'product',
]

const PLACEHOLDER_HINTS: Record<string, string> = {
  DELIVERY_EXCEPTION: '订单 {order_id} 物流异常：{summary}。需要我帮您跟进吗？',
  REFUND_SETTLED: '您的订单 {order_id} 退款 {amount} 元已到账，请注意查收。',
  TICKET_UPDATED: '工单 {ticket_no} 有新进展：{summary}。',
}

const TEMPLATE_LIMIT = 400
const COOLDOWN_LIMIT = 1440
const DEFAULT_COOLDOWN = 60

type Draft = {
  template: string
  cooldown_minutes: number
  enabled: boolean
}

function draftOf(rule: ProactiveRule): Draft {
  return {
    template: rule.template,
    cooldown_minutes: rule.cooldown_minutes,
    enabled: rule.enabled,
  }
}

function blankDraft(): Draft {
  return { template: '', cooldown_minutes: DEFAULT_COOLDOWN, enabled: true }
}

function placeholdersOf(template: string): string[] {
  const found = new Set<string>()
  const pattern = /\{([^{}]*)\}/g
  let match = pattern.exec(template)
  while (match !== null) {
    found.add(match[1].split('.')[0].split('[')[0])
    match = pattern.exec(template)
  }
  return Array.from(found).sort()
}

function buildPayload(
  current: ProactiveRule,
  draft: Draft,
): ProactiveRuleUpdatePayload {
  const payload: ProactiveRuleUpdatePayload = {}
  const template = draft.template.trim()
  // An empty box means "leave the stored words alone", never "send nothing".
  if (template.length > 0 && template !== current.template) payload.template = template
  if (draft.cooldown_minutes !== current.cooldown_minutes) {
    payload.cooldown_minutes = draft.cooldown_minutes
  }
  if (draft.enabled !== current.enabled) payload.enabled = draft.enabled
  return payload
}

export function ProactiveRules() {
  usePageTitle('通知规则')
  const { identity, canOperate, isApprover } = useIdentity()
  const [rules, setRules] = useState<ProactiveRule[]>([])
  const [changes, setChanges] = useState<ProactiveRuleChange[]>([])
  const [versions, setVersions] = useState<Record<string, ProactiveRuleVersion[]>>({})
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyType, setBusyType] = useState<string | null>(null)
  const [decisionBusy, setDecisionBusy] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    if (!canOperate) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      listProactiveRules(identity),
      listProactiveRuleChanges(identity),
      Promise.all(
        EVENT_ORDER.map((eventType) =>
          listProactiveRuleVersions(identity, eventType),
        ),
      ),
    ])
      .then(([ruleData, changeData, versionLists]) => {
        if (cancelled) return
        setRules(ruleData.rules)
        setChanges(changeData.changes)
        setVersions(
          Object.fromEntries(
            EVENT_ORDER.map((eventType, index) => [
              eventType,
              versionLists[index].versions,
            ]),
          ),
        )
        setDrafts(
          Object.fromEntries(
            ruleData.rules.map((rule) => [rule.event_type, draftOf(rule)]),
          ),
        )
        setNotice(null)
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
  }, [identity, canOperate, reloadTick])

  const byType = useMemo(() => {
    const map: Record<string, ProactiveRule> = {}
    for (const rule of rules) map[rule.event_type] = rule
    return map
  }, [rules])

  const missingTypes = useMemo(
    () => EVENT_ORDER.filter((eventType) => !(eventType in byType)),
    [byType],
  )

  function patch(eventType: string, next: Partial<Draft>) {
    setDrafts((current) => ({
      ...current,
      [eventType]: { ...(current[eventType] ?? blankDraft()), ...next },
    }))
    setNotice(null)
  }

  function payloadOf(eventType: string): ProactiveRuleUpdatePayload {
    const draft = drafts[eventType]
    const current = byType[eventType]
    if (!draft || !current) return {}
    return buildPayload(current, draft)
  }

  async function save(eventType: string) {
    const changes = payloadOf(eventType)
    if (Object.keys(changes).length === 0 || busyType) return
    setBusyType(eventType)
    setFormError(null)
    try {
      const submitted = await updateProactiveRule(identity, eventType, changes)
      setChanges((current) => [submitted, ...current])
      setDrafts((current) => ({
        ...current,
        [eventType]: draftOf(byType[eventType]),
      }))
      setNotice(
        `「${EVENT_LABELS[submitted.event_type] ?? submitted.event_type}」改动已提交，` +
          '等另一位审批人批准后才生效；现行文案与已生成的通知都不变。',
      )
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setBusyType(null)
    }
  }

  async function decide(change: ProactiveRuleChange, approve: boolean) {
    if (decisionBusy) return
    setDecisionBusy(change.change_id)
    setFormError(null)
    try {
      const payload = approve ? {} : { note: '控制台拒绝' }
      const resolved = approve
        ? await approveProactiveRuleChange(identity, change.change_id, payload)
        : await rejectProactiveRuleChange(identity, change.change_id, payload)
      setChanges((current) =>
        current.map((item) =>
          item.change_id === resolved.change_id ? resolved : item,
        ),
      )
      setNotice(
        `「${EVENT_LABELS[resolved.event_type] ?? resolved.event_type}」` +
          (approve
            ? `已批准，从版本 v${resolved.applied_version ?? '?'} 起生效。`
            : '已拒绝，现行规则没有变化。'),
      )
      setReloadTick((value) => value + 1)
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setDecisionBusy(null)
    }
  }

  async function rollback(eventType: string) {
    const rule = byType[eventType]
    if (!rule || rule.version <= 1 || busyType) return
    setBusyType(eventType)
    setFormError(null)
    try {
      const submitted = await rollbackProactiveRule(identity, eventType, {
        version: rule.version - 1,
        reason: `回滚到版本 ${rule.version - 1}`,
      })
      setChanges((current) => [submitted, ...current])
      setNotice(
        `已提交回滚到 v${rule.version - 1}，仍要另一位审批人批准；` +
          '历史版本不会被删除。',
      )
    } catch (errorValue: unknown) {
      setFormError(errorText(errorValue))
    } finally {
      setBusyType(null)
    }
  }

  if (!canOperate) {
    return (
      <div className="page">
        <PageHeader
          eyebrow="RULES / 通知规则"
          title="主动通知的文案与阈值"
          description="发生退款到账、工单进展或物流异常时，规则决定我们说什么、多久说一次。"
        />
        <section className="gate">
          <span className="gate__stamp">
            <Icon name="lock" size={26} />
          </span>
          <p className="eyebrow">ROLE GATE / STAFF REQUIRED</p>
          <h2>当前身份无权编辑通知规则</h2>
          <p className="gate__copy">
            当前身份是 {identity.tenant} / {identity.user}，没有 support
            权限。规则文案只对坐席与管理员开放；客户一侧只在自己的档案页决定收不收。
          </p>
        </section>
      </div>
    )
  }

  const liveCount = rules.filter((rule) => rule.enabled).length
  const pendingChanges = changes.filter((item) => item.status === 'PENDING')

  return (
    <div className="page page--memory">
      <PageHeader
        eyebrow="RULES / 通知规则"
        title="主动通知的文案与阈值"
        description="改这里是改「我们怎么说」，不是改「要不要说」：每条推送仍然要客户先确认。"
        meta={
          <div className="matrix-summary">
            <span className="chip">
              {identity.tenant} · 生效 {liveCount} / {rules.length} 条
            </span>
            <span className="chip">事件类型由代码固定</span>
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
          可改的只有文案、冷却时长与启停。提交后先进入待批队列，由另一位具备 approver
          角色的同事批准才生效；提交者不能自批，未批准的改动对客户不可见。
          改动只对下一条通知生效，已经生成的通知不会被回溯改写；每次生效都会留下
          一个不可变版本，可按版本回滚。
        </p>
      </div>

      {pendingChanges.length > 0 ? (
        <section className="panel">
          <div className="panel__head">
            <div>
              <span className="panel__tag">PENDING / 待批改动</span>
              <h2>等待另一位审批人</h2>
            </div>
            <span className="chip">{pendingChanges.length} 条待批</span>
          </div>
          <div className="panel__body">
            {pendingChanges.map((change) => {
              const canDecide =
                isApprover && change.submitted_by !== identity.user
              // "Current" must come from the rule row, not the proposal's
              // snapshot: if the rule moved on, the snapshot is history.
              const currentTemplate = byType[change.event_type]?.template
              return (
                <div key={change.change_id} className="memory-muted">
                  <p>
                    <strong>
                      {EVENT_LABELS[change.event_type] ?? change.event_type}
                    </strong>
                    {' · '}
                    {change.kind === 'ROLLBACK'
                      ? `回滚到 v${change.target_version}`
                      : '改文案 / 阈值'}
                    {' · '}
                    基于 v{change.base_version} · 提交人 {change.submitted_by}
                  </p>
                  {currentTemplate !== undefined &&
                  currentTemplate !== change.template ? (
                    <p className="mono">
                      现行：{currentTemplate}
                      <br />
                      待批：{change.template}
                    </p>
                  ) : (
                    <p className="mono">待批：{change.template}</p>
                  )}
                  {change.reason ? <p>理由：{change.reason}</p> : null}
                  <p className="mono">
                    审批人：{change.decided_by ?? '尚未审批'}
                    {' · '}
                    提交时间：{formatDateTime(change.submitted_at) || '未知'}
                  </p>
                  <div className="memory-actions">
                    {canDecide ? (
                      <>
                        <button
                          type="button"
                          className="button button--primary button--sm"
                          onClick={() => decide(change, true)}
                          disabled={decisionBusy !== null}
                        >
                          <Icon name="check" size={15} />
                          {decisionBusy === change.change_id ? '处理中' : '批准'}
                        </button>
                        <button
                          type="button"
                          className="button button--ghost button--sm"
                          onClick={() => decide(change, false)}
                          disabled={decisionBusy !== null}
                        >
                          拒绝
                        </button>
                      </>
                    ) : (
                      <span className="chip">
                        {change.submitted_by === identity.user
                          ? '提交人不能自批，请切换另一位审批人'
                          : '需要 approver 角色才能批准'}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ) : null}

      {loading && rules.length === 0 ? (
        <div className="memory-grid" aria-hidden="true">
          {[0, 1, 2].map((key) => (
            <div className="panel" key={key}>
              <div className="panel__body">
                <span className="skeleton skeleton--line" style={{ width: '38%' }} />
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
          title="无法读取通知规则"
          error={error}
          onRetry={() => setReloadTick((value) => value + 1)}
        />
      ) : (
        <>
          {missingTypes.length > 0 ? (
            <p className="memory-muted">
              本租户还没有{missingTypes.map((item) => EVENT_LABELS[item]).join('、')}
              的规则，这些事件发生时会直接抑制为 NO_RULE。可以在下面的卡片里补一条。
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

          {EVENT_ORDER.map((eventType) => {
            const rule = byType[eventType]
            const draft = drafts[eventType] ?? blankDraft()
            const used = placeholdersOf(draft.template)
            const unknown = used.filter((item) => !CONTEXT_KEYS.includes(item))
            const dirty = rule ? Object.keys(payloadOf(eventType)).length : 0
            const isNew = !rule
            const history = versions[eventType] ?? []
            const thisEventPending = pendingChanges.some(
              (item) => item.event_type === eventType,
            )

            return (
              <section className="panel" key={eventType}>
                <div className="panel__head">
                  <div>
                    <span className="panel__tag">
                      {isNew ? 'NEW RULE / 待新建' : 'RULE / 现行规则'}
                    </span>
                    <h2>{EVENT_LABELS[eventType]}</h2>
                  </div>
                  <span className="chip">
                    {isNew
                      ? '本租户还没有这条规则'
                      : dirty > 0
                        ? `${dirty} 项待提交`
                        : rule.enabled
                          ? `v${rule.version} 生效中`
                          : `v${rule.version} 已停用`}
                  </span>
                </div>
                <div className="panel__body">
                  <label className="field">
                    <span className="field__label">
                      通知文案
                      <small>
                        用 {'{字段}'} 引用事件里的内容，最多 {TEMPLATE_LIMIT} 字；
                        正文渲染到 500 字截断，写入时留出余量免得句子被切尾
                      </small>
                    </span>
                    <textarea
                      className="textarea"
                      rows={3}
                      maxLength={TEMPLATE_LIMIT}
                      value={draft.template}
                      onChange={(event) =>
                        patch(eventType, { template: event.target.value })
                      }
                      placeholder={PLACEHOLDER_HINTS[eventType]}
                    />
                    <span className="field__hint">
                      <span>
                        可用占位符：{CONTEXT_KEYS.map((key) => `{${key}}`).join(' ')}
                      </span>
                      <span className="mono">
                        {draft.template.length} / {TEMPLATE_LIMIT}
                      </span>
                    </span>
                  </label>

                  {used.length > 0 ? (
                    <div className="matrix-summary">
                      {used.map((item) => (
                        <span
                          className={`chip${
                            item && !CONTEXT_KEYS.includes(item) ? ' chip--warn' : ''
                          }`}
                          key={item || 'positional'}
                        >
                          {item ? `引用 {${item}}` : '占位符不能留空 {}'}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {unknown.length > 0 ? (
                    <p className="memory-muted">
                      上面标出的字段不在允许清单里，保存会被拒绝（422 且带允许的字段）。
                      允许清单是：{CONTEXT_KEYS.join('、')}。
                    </p>
                  ) : null}

                  <div className="memory-grid">
                    <label className="field field--inline">
                      <span className="field__label">
                        冷却时长
                        <small>
                          分钟，0 到 {COOLDOWN_LIMIT}；0 表示不冷却
                        </small>
                      </span>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        max={COOLDOWN_LIMIT}
                        value={draft.cooldown_minutes}
                        onChange={(event) =>
                          patch(eventType, {
                            cooldown_minutes: Number(event.target.value),
                          })
                        }
                      />
                    </label>

                    <label className="field field--inline">
                      <span className="field__label">
                        启用这条规则
                        <small>关掉之后该事件一律抑制为 NO_RULE</small>
                      </span>
                      <input
                        type="checkbox"
                        checked={draft.enabled}
                        onChange={(event) =>
                          patch(eventType, { enabled: event.target.checked })
                        }
                      />
                    </label>

                    <label className="field field--inline">
                      <span className="field__label">
                        需要客户先确认
                        <small>不在可改范围内：关掉它等于拆掉同意闸</small>
                      </span>
                      <span
                        className={`badge badge--${
                          rule && !rule.requires_confirmation ? 'muted' : 'success'
                        }`}
                      >
                        <span className="badge__dot" aria-hidden="true" />
                        {rule
                          ? rule.requires_confirmation
                            ? '是'
                            : '否（历史规则）'
                          : '新建时固定为是'}
                      </span>
                    </label>
                  </div>

                  {rule ? (
                    <p className="memory-muted mono">
                      {rule.rule_id} · 版本 v{rule.version} · 需要字段{' '}
                      {rule.required_context.join('、') || '无'}
                      {' · 上次修改 '}
                      {formatDateTime(rule.updated_at) || '未知'}
                    </p>
                  ) : (
                    <p className="memory-muted">
                      新建的规则固定为「启用 + 需要客户确认 + 冷却 60 分钟」，
                      提交并经另一位审批人批准后，本租户的这个事件才会开始生成通知。
                    </p>
                  )}

                  {rule && history.length > 1 ? (
                    <p className="memory-muted">
                      历史版本：
                      {history
                        .map((item) => `v${item.version}（${item.approved_by}）`)
                        .join('、')}
                    </p>
                  ) : null}

                  <div className="memory-actions">
                    <button
                      type="button"
                      className="button button--primary button--sm"
                      onClick={() => save(eventType)}
                      disabled={
                        busyType !== null ||
                        (isNew ? draft.template.trim().length === 0 : dirty === 0)
                      }
                    >
                      <Icon name="check" size={15} />
                      {busyType === eventType
                        ? '提交中'
                        : isNew
                          ? '提交新建申请'
                          : '提交改动'}
                    </button>
                    {rule ? (
                      <button
                        type="button"
                        className="button button--ghost button--sm"
                        onClick={() => patch(eventType, draftOf(rule))}
                        disabled={dirty === 0 || busyType !== null}
                      >
                        放弃改动
                      </button>
                    ) : null}
                    {rule && rule.version > 1 ? (
                      <button
                        type="button"
                        className="button button--ghost button--sm"
                        onClick={() => rollback(eventType)}
                        disabled={busyType !== null || thisEventPending}
                      >
                        回滚到上一版（v{rule.version - 1}）
                      </button>
                    ) : null}
                  </div>
                </div>
              </section>
            )
          })}

          <p className="matrix-note mono">
            规则治理审计：proactive.rule.submit / approve / reject 记录前后版本、
            提交人、批准人与模板 SHA-256，不记文案本身；版本快照另存，可按版本回滚。
          </p>
        </>
      )}
    </div>
  )
}
