import type { ReactNode } from 'react'
import { Alert, Button, Empty, Skeleton } from 'antd'
import { ApiError } from '../api/client'

export function QueryState({
  loading,
  error,
  empty,
  emptyContent,
  onRetry,
  children,
}: {
  loading: boolean
  error: unknown
  empty?: boolean
  emptyContent?: ReactNode
  onRetry?: () => void
  children: ReactNode
}) {
  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (error) {
    const apiError = error instanceof ApiError ? error : undefined
    return (
      <Alert
        type="error"
        showIcon
        message="数据加载失败"
        description={
          <span>
            {error instanceof Error ? error.message : '无法连接算法库服务'}
            {apiError?.errorCode ? `（${apiError.errorCode}）` : ''}
          </span>
        }
        action={onRetry ? <Button onClick={onRetry}>重试</Button> : undefined}
      />
    )
  }
  if (empty) return <>{emptyContent ?? <Empty description="暂无数据" />}</>
  return children
}
