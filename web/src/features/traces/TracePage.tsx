import { ClockCircleOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Empty, Input, Space, Tag, Timeline, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTrace } from '../../api/queries'
import { QueryState } from '../../components/QueryState'
import { backendLabel } from '../../lib/presentation'

export function TracePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState(searchParams.get('trace_id') ?? '')
  const [traceId, setTraceId] = useState(searchParams.get('trace_id') ?? '')
  const query = useTrace(traceId)

  useEffect(() => {
    const value = searchParams.get('trace_id') ?? ''
    setInput(value)
    setTraceId(value)
  }, [searchParams])

  const search = () => {
    const normalized = input.trim()
    if (!normalized) return
    if (normalized === traceId) void query.refetch()
    else {
      setTraceId(normalized)
      setSearchParams({ trace_id: normalized }, { replace: true })
    }
  }

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>执行链路</Typography.Title>
          <Typography.Text type="secondary">根据已知 trace ID 查看功能点、算法和步骤执行结果。</Typography.Text>
        </div>
      </div>
      <Card className="surface-card trace-search-card">
        <Space.Compact block>
          <Input size="large" prefix={<SearchOutlined />} placeholder="输入 trace ID" value={input} onChange={(event) => setInput(event.target.value)} onPressEnter={search} />
          <Button size="large" type="primary" onClick={search} disabled={!input.trim()}>查询链路</Button>
        </Space.Compact>
      </Card>
      {!traceId && <div className="surface trace-empty"><Empty description="输入在线调用或业务任务返回的 trace ID" /></div>}
      {traceId && (
        <div className="surface trace-results">
          <QueryState loading={query.isLoading} error={query.error} onRetry={() => void query.refetch()}>
            {query.data?.count === 0 ? (
              <Empty description="未找到功能点执行记录；该 trace 可能不存在，或调用没有功能点上下文。" />
            ) : (
              <>
                <Alert type="info" showIcon message={`Trace ${traceId}`} description={`共 ${query.data?.count ?? 0} 个功能执行步骤`} className="page-alert" />
                <Timeline
                  items={(query.data?.function_executions ?? []).map((event) => ({
                    color: event.status === 'success' ? 'green' : 'red',
                    dot: <ClockCircleOutlined />,
                    children: (
                      <Card size="small" className="trace-event-card">
                        <div className="trace-event-heading">
                          <Space wrap>
                            <Tag color="blue">#{event.sequence}</Tag>
                            <strong>{event.function_execution.function_id || '未匹配功能点'}</strong>
                            <span>{event.function_execution.function_name || event.function_execution.function_code}</span>
                            <Tag color={event.status === 'success' ? 'success' : 'error'}>{event.status}</Tag>
                          </Space>
                          <Typography.Text strong>{event.latency_ms} ms</Typography.Text>
                        </div>
                        <Descriptions size="small" column={2} className="trace-event-details">
                          <Descriptions.Item label="算法">{event.algorithm_id} / {event.version}</Descriptions.Item>
                          <Descriptions.Item label="后端">{backendLabel(event.backend_type)}</Descriptions.Item>
                          <Descriptions.Item label="Request ID">{event.request_id}</Descriptions.Item>
                          <Descriptions.Item label="记录时间">{event.recorded_at || '—'}</Descriptions.Item>
                          <Descriptions.Item label="角色/覆盖">{event.function_execution.role || '—'} / {event.function_execution.coverage_level || '—'}</Descriptions.Item>
                          <Descriptions.Item label="错误码">{event.error_code ?? '—'}</Descriptions.Item>
                          <Descriptions.Item label="Workflow/Step" span={2}>{event.function_execution.workflow_instance_id || '—'} / {event.function_execution.step_instance_id || '—'}</Descriptions.Item>
                        </Descriptions>
                      </Card>
                    ),
                  }))}
                />
              </>
            )}
          </QueryState>
        </div>
      )}
    </section>
  )
}
