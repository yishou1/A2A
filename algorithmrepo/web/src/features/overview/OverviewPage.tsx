import { Alert, Card, Col, Row, Space, Statistic, Typography } from 'antd'
import { useAlgorithms, useHealth } from '../../api/queries'
import type { AlgorithmStatus, DeploymentStatus } from '../../api/contracts'
import { QueryState } from '../../components/QueryState'

export function OverviewPage() {
  const algorithmsQuery = useAlgorithms()
  const healthQuery = useHealth()
  const algorithms = algorithmsQuery.data?.algorithms ?? []

  const statusCount = (status: AlgorithmStatus) =>
    algorithms.filter((item) => item.registry_status === status).length
  const deploymentCount = (status?: DeploymentStatus) =>
    algorithms.flatMap((item) => item.deployments ?? [])
      .filter((item) => !status || item.deploy_status === status).length
  const attentionCount = algorithms.filter(
    (item) =>
      item.registry_status === 'draft' ||
      item.deployments.some((deployment) => deployment.deploy_status === 'error') ||
      (item.registry_status === 'active' &&
        !item.deployments.some((deployment) => deployment.deploy_status === 'ready')),
  ).length

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>运行总览</Typography.Title>
          <Typography.Text type="secondary">快速了解算法资产、部署就绪情况和服务健康。</Typography.Text>
        </div>
      </div>
      {healthQuery.error && (
        <Alert className="page-alert" type="error" showIcon message="算法库服务不可达，当前数据可能已过期。" />
      )}
      <QueryState
        loading={algorithmsQuery.isLoading}
        error={algorithmsQuery.error}
        onRetry={() => void algorithmsQuery.refetch()}
      >
        <Row gutter={[16, 16]}>
          {[
            ['算法总数', algorithms.length],
            ['已激活', statusCount('active')],
            ['已验证', statusCount('validated')],
            ['已禁用', statusCount('disabled')],
            ['草稿', statusCount('draft')],
            ['需处理项', attentionCount],
          ].map(([label, value]) => (
            <Col xs={12} lg={8} xl={4} key={String(label)}>
              <Card className="metric-card">
                <Statistic title={label} value={value} />
              </Card>
            </Col>
          ))}
        </Row>
        <Row gutter={[16, 16]} className="overview-row">
          <Col xs={24} xl={12}>
            <Card title="部署摘要" className="surface-card">
              <Space size="large" wrap>
                <Statistic title="部署总数" value={deploymentCount()} />
                <Statistic title="已就绪" value={deploymentCount('ready')} />
                <Statistic title="未加载" value={deploymentCount('unloaded')} />
                <Statistic title="异常" value={deploymentCount('error')} />
              </Space>
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card title="服务信息" className="surface-card">
              <dl className="service-info">
                <dt>服务状态</dt><dd>{healthQuery.data?.status ?? '不可达'}</dd>
                <dt>Runner Cache</dt><dd>{healthQuery.data?.runner_cache_size ?? '—'}</dd>
                <dt>注册表</dt><dd>{healthQuery.data?.registry_path ?? '—'}</dd>
                <dt>执行日志</dt><dd>{healthQuery.data?.execution_log_path ?? '—'}</dd>
              </dl>
            </Card>
          </Col>
        </Row>
      </QueryState>
    </section>
  )
}
