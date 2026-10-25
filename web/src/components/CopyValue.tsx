import { useState } from 'react'
import { Icon } from './Icon'

export function CopyValue({
  value,
  display,
  label = '复制',
}: {
  value: string
  display: string
  label?: string
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <span className="copy-value">
      <code>{display}</code>
      <button
        type="button"
        className="icon-button icon-button--tiny"
        onClick={copy}
        title={copied ? '已复制' : label}
        aria-label={copied ? '已复制' : label}
      >
        <Icon name={copied ? 'check' : 'copy'} size={14} />
      </button>
    </span>
  )
}
