import { outcomeMeta, statusMeta } from '../format'

export function StatusBadge({
  status,
  kind = 'status',
}: {
  status: string | null | undefined
  kind?: 'status' | 'outcome'
}) {
  const meta = kind === 'outcome' ? outcomeMeta(status) : statusMeta(status)
  return (
    <span className={`badge badge--${meta.tone}`}>
      <span className="badge__dot" aria-hidden="true" />
      {meta.label}
    </span>
  )
}
