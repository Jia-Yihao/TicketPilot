import type { ReactNode } from 'react'

export function PageHeader({
  eyebrow,
  title,
  description,
  meta,
  actions,
}: {
  eyebrow: string
  title: string
  description?: string
  meta?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page-head">
      <div className="page-head__main">
        <p className="eyebrow">
          <span className="eyebrow__tick" aria-hidden="true" />
          {eyebrow}
        </p>
        <h1>{title}</h1>
        {description ? <p className="page-head__desc">{description}</p> : null}
        {meta ? <div className="page-head__meta">{meta}</div> : null}
      </div>
      {actions ? <div className="page-head__actions">{actions}</div> : null}
    </header>
  )
}
