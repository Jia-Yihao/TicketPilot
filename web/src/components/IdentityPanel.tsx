import { useEffect } from 'react'
import { IDENTITY_PRESETS } from '../identity'
import type { Identity } from '../types'
import { Icon } from './Icon'

export function IdentityPanel({
  open,
  current,
  onSelect,
  onClose,
}: {
  open: boolean
  current: Identity
  onSelect: (identity: Identity) => void
  onClose: () => void
}) {
  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="identity-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="identity-panel-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="identity-panel__head">
          <div>
            <p className="eyebrow">DEV IDENTITY ADAPTER</p>
            <h2 id="identity-panel-title">切换演示身份</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭身份面板"
          >
            <Icon name="x" />
          </button>
        </header>

        <p className="identity-panel__intro">
          当前版本使用开发 Header 身份。切换用户后，租户隔离、角色权限和四眼审批都会按新身份重新计算。
        </p>

        <div className="identity-list">
          {IDENTITY_PRESETS.map((preset) => {
            const active =
              preset.tenant === current.tenant && preset.user === current.user
            return (
              <button
                key={`${preset.tenant}/${preset.user}`}
                type="button"
                className={`identity-option${active ? ' is-active' : ''}`}
                onClick={() => {
                  onSelect(preset)
                  onClose()
                }}
              >
                <span className="identity-option__avatar">
                  {preset.user.slice(0, 1).toUpperCase()}
                </span>
                <span className="identity-option__body">
                  <strong>{preset.label}</strong>
                  <span>
                    {preset.tenant} / {preset.user}
                    {preset.roles.length ? ` · ${preset.roles.join(', ')}` : ' · 客户'}
                  </span>
                </span>
                {active ? <Icon name="check" size={17} /> : null}
              </button>
            )
          })}
        </div>

        <div className="identity-panel__foot">
          <div className="identity-headers">
            <span>X-Tenant-Id</span>
            <code>{current.tenant}</code>
            <span>X-User-Id</span>
            <code>{current.user}</code>
            <span>X-Roles</span>
            <code>{current.roles.join(',') || '—'}</code>
          </div>
          <p>
            <Icon name="lock" size={14} /> 生产配置会拒绝 Header 身份并要求 OIDC；当前适配器仅用于本地演示。
          </p>
        </div>
      </section>
    </div>
  )
}
