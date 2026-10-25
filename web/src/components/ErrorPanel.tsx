import { ApiError } from '../api'
import { Icon } from './Icon'

export function ErrorPanel({
  title = '无法加载数据',
  error,
  onRetry,
}: {
  title?: string
  error: unknown
  onRetry?: () => void
}) {
  const apiError = error instanceof ApiError ? error : null
  const message =
    error instanceof Error ? error.message : '请求失败，请检查服务状态后重试。'

  return (
    <div className="error-panel" role="alert">
      <span className="error-panel__icon">
        <Icon name="alert" size={20} />
      </span>
      <div className="error-panel__body">
        <h3>{title}</h3>
        <p>{message}</p>
        {apiError ? (
          <p className="error-panel__meta mono">
            {apiError.code}
            {apiError.requestId ? ` · REQ ${apiError.requestId}` : ''}
          </p>
        ) : null}
      </div>
      {onRetry ? (
        <button type="button" className="button button--ghost button--sm" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  )
}
