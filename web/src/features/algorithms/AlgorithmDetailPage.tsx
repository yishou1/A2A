import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Space, Tabs, Tag, Typography } from 'antd'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { BackendType } from '../../api/contracts'
import { useAlgorithm, useAlgorithmSchema, useHealth } from '../../api/queries'
import { AlgorithmStatusTag, DeploymentStatusTag } from '../../components/StatusTag'
import { QueryState } from '../../components/QueryState'
import { backendLabel, displayValue } from '../../lib/presentation'
import { LifecycleActions } from './LifecycleActions'
import { DeploymentPanel } from '../deployments/DeploymentPanel'

function JsonPanel({ value }: { value: unknown }) {
  return <pre className="json-panel">{JSON.stringify(value, null, 2)}</pre>
}

export function AlgorithmDetailPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const params = useParams()
  const key = {
    algorithmId: params.algorithmId ?? '',
    version: params.version ?? '',
    backendType: (params.backendType ?? 'onnx') as BackendType,
  }
  const detail = useAlgorithm(key)
  const health = useHealth()
  const inputSchema = useAlgorithmSchema(key, 'input')
  const outputSchema = useAlgorithmSchema(key, 'output')
  const item = detail.data?.agent_view
  const entry = detail.data?.entry

  return (
    <section>
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/algorithms')}>
        返回算法目录
      </Button>
      <QueryState
        loading={detail.isLoading}
        error={detail.error}
        onRetry={() => void detail.refetch()}
      >
        {item && entry && (
          <>
            <div className="detail-heading">
              <div>
                <Space align="center" wrap>
                  <Typography.Title level={2}>{item.display_name || item.algorithm_id}</Typography.Title>
                  <AlgorithmStatusTag status={entry.status} />
                  <Tag>{backendLabel(item.backend_type)}</Tag>
                </Space>
                <Typography.Text type="secondary">
                  {item.algorithm_id} / {item.version} / {item.backend_type}
                </Typography.Text>
              </div>
              <Space wrap>
                {entry.status === 'active' && (
                  <Button
                    type="primary"
                    onClick={() => navigate(`/invoke?algorithm_id=${encodeURIComponent(item.algorithm_id)}&version=${encodeURIComponent(item.version)}&backend_type=${encodeURIComponent(item.backend_type)}`)}
                  >在线调用</Button>
                )}
                <LifecycleActions
                  algorithmKey={key}
                  status={entry.status}
                  displayName={item.display_name || item.algorithm_id}
                  disabled={Boolean(health.error)}
                />
              </Space>
            </div>
            {entry.status === 'active' && !entry.deployments.some((value) => value.deploy_status === 'ready') && (
              <Alert
                className="page-alert"
                type="warning"
                showIcon
                message="该算法已允许调用，但没有已就绪的部署记录；首次调用可能触发按需加载。"
              />
            )}
            <Card className="surface-card detail-card">
              <Tabs
                activeKey={searchParams.get('tab') ?? 'overview'}
                onChange={(tab) => setSearchParams(tab === 'overview' ? {} : { tab }, { replace: true })}
                items={[
                  {
                    key: 'overview', label: '概览',
                    children: (
                      <div className="detail-grid">
                        <Descriptions column={2} bordered size="small">
                          <Descriptions.Item label="任务族">{item.task_family}</Descriptions.Item>
                          <Descriptions.Item label="算法卡状态">
                            <AlgorithmStatusTag status={item.card_status} />
                          </Descriptions.Item>
                          <Descriptions.Item label="输入模态">{item.modalities.input.join(', ')}</Descriptions.Item>
                          <Descriptions.Item label="输出模态">{item.modalities.output.join(', ')}</Descriptions.Item>
                          <Descriptions.Item label="用途摘要" span={2}>{item.agent_card.summary || '—'}</Descriptions.Item>
                          <Descriptions.Item label="能力" span={2}>
                            <Space wrap>{item.capabilities.map((value) => <Tag key={value}>{value}</Tag>)}</Space>
                          </Descriptions.Item>
                        </Descriptions>
                        <div className="scenario-columns">
                          <div><h3>适用场景</h3><ul>{item.agent_card.when_to_use.map((value) => <li key={value}>{value}</li>)}</ul></div>
                          <div><h3>不适用场景</h3><ul>{item.agent_card.when_not_to_use.map((value) => <li key={value}>{value}</li>)}</ul></div>
                        </div>
                      </div>
                    ),
                  },
                  {
                    key: 'schemas', label: '输入输出',
                    children: (
                      <div className="schema-columns">
                        <div><h3>Input Schema</h3><JsonPanel value={inputSchema.data ?? entry.input_schema_summary} /></div>
                        <div><h3>Output Schema</h3><JsonPanel value={outputSchema.data ?? entry.output_schema_summary} /></div>
                      </div>
                    ),
                  },
                  {
                    key: 'performance', label: '性能资源',
                    children: (
                      <>
                        <Alert type="info" showIcon message="以下为算法卡登记指标，不是当前实时监控数据。" />
                        <div className="schema-columns padded-top">
                          <div><h3>性能</h3><JsonPanel value={item.performance} /></div>
                          <div><h3>资源要求</h3><JsonPanel value={item.resource_requirements} /></div>
                        </div>
                      </>
                    ),
                  },
                  {
                    key: 'functions', label: '功能点',
                    children: item.operational_functions.length ? (
                      <Descriptions bordered size="small" column={1}>
                        {item.operational_functions.map((value) => (
                          <Descriptions.Item key={`${value.function_id}:${value.role}`} label={value.function_id}>
                            {value.function_name} · {value.role} · {value.coverage_level}
                          </Descriptions.Item>
                        ))}
                      </Descriptions>
                    ) : <Typography.Text type="secondary">未登记业务功能点。</Typography.Text>,
                  },
                  {
                    key: 'deployments', label: `部署实例 (${entry.deployments.length})`,
                    children: (
                      <DeploymentPanel
                        algorithmKey={key}
                        deployments={entry.deployments}
                        mutationsDisabled={Boolean(health.error)}
                      />
                    ),
                  },
                  { key: 'card', label: '算法卡', children: <JsonPanel value={entry.card} /> },
                ]}
              />
            </Card>
          </>
        )}
      </QueryState>
    </section>
  )
}
