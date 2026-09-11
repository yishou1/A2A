import { SearchOutlined } from '@ant-design/icons'
import { Button, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AlgorithmStatus, AlgorithmSummary, BackendType } from '../../api/contracts'
import { useAlgorithms, useHealth } from '../../api/queries'
import { AlgorithmStatusTag, DeploymentStatusTag } from '../../components/StatusTag'
import { QueryState } from '../../components/QueryState'
import { algorithmStatusMeta, backendLabel } from '../../lib/presentation'
import { RegisterAlgorithmModal } from './RegisterAlgorithmModal'

function detailPath(item: AlgorithmSummary) {
  return `/algorithms/${encodeURIComponent(item.algorithm_id)}/${encodeURIComponent(item.version)}/${encodeURIComponent(item.backend_type)}`
}

export function AlgorithmListPage() {
  const query = useAlgorithms()
  const health = useHealth()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<AlgorithmStatus | undefined>()
  const [backend, setBackend] = useState<BackendType | undefined>()
  const [registerOpen, setRegisterOpen] = useState(false)

  const data = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    return (query.data?.algorithms ?? []).filter((item) => {
      const matchesKeyword = !normalized ||
        item.algorithm_id.toLowerCase().includes(normalized) ||
        item.display_name.toLowerCase().includes(normalized)
      return matchesKeyword && (!status || item.registry_status === status) &&
        (!backend || item.backend_type === backend)
    })
  }, [backend, keyword, query.data?.algorithms, status])

  const columns: ColumnsType<AlgorithmSummary> = [
    {
      title: '算法名称',
      key: 'name',
      width: 260,
      render: (_, item) => (
        <button className="link-button" onClick={() => navigate(detailPath(item))}>
          <span>{item.display_name || item.algorithm_id}</span>
          <small>{item.algorithm_id}</small>
        </button>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 100 },
    {
      title: '后端', dataIndex: 'backend_type', width: 130,
      render: (value: BackendType) => <Tag>{backendLabel(value)}</Tag>,
    },
    { title: '任务族', dataIndex: 'task_family', width: 170, ellipsis: true },
    {
      title: '注册状态', dataIndex: 'registry_status', width: 110,
      render: (value: AlgorithmStatus) => <AlgorithmStatusTag status={value} />,
    },
    {
      title: '部署', key: 'deployments', width: 150,
      render: (_, item) => {
        const ready = item.deployments.filter((value) => value.deploy_status === 'ready').length
        const error = item.deployments.find((value) => value.deploy_status === 'error')
        return error
          ? <DeploymentStatusTag status="error" />
          : <span>{ready} / {item.deployments.length} 就绪</span>
      },
    },
    {
      title: '能力', dataIndex: 'capabilities', ellipsis: true,
      render: (values: string[]) => (
        <Space size={[4, 4]} wrap>
          {(values ?? []).slice(0, 2).map((value) => <Tag key={value}>{value}</Tag>)}
          {(values?.length ?? 0) > 2 && <Tag>+{values.length - 2}</Tag>}
        </Space>
      ),
    },
    {
      title: '操作', key: 'actions', width: 90, fixed: 'right',
      render: (_, item) => <Button type="link" onClick={() => navigate(detailPath(item))}>查看</Button>,
    },
  ]

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>算法目录</Typography.Title>
          <Typography.Text type="secondary">统一查看算法能力、生命周期与部署就绪情况。</Typography.Text>
        </div>
        <Button type="primary" disabled={Boolean(health.error)} onClick={() => setRegisterOpen(true)}>注册算法</Button>
      </div>
      <div className="surface">
        <div className="filter-bar">
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索算法名称或 ID"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            className="search-input"
          />
          <Select
            allowClear
            placeholder="注册状态"
            value={status}
            onChange={setStatus}
            options={(Object.keys(algorithmStatusMeta) as AlgorithmStatus[])
              .filter((value) => value !== 'deleted')
              .map((value) => ({ value, label: algorithmStatusMeta[value].label }))}
          />
          <Select
            allowClear
            placeholder="后端类型"
            value={backend}
            onChange={setBackend}
            options={[
              { value: 'onnx', label: 'ONNX' },
              { value: 'python_http_service', label: 'Python HTTP' },
            ]}
          />
          <Button onClick={() => { setKeyword(''); setStatus(undefined); setBackend(undefined) }}>重置</Button>
          <Typography.Text type="secondary" className="result-count">共 {data.length} 项</Typography.Text>
        </div>
        <QueryState
          loading={query.isLoading}
          error={query.error}
          empty={!query.isLoading && !query.error && data.length === 0}
          emptyContent={
            !keyword && !status && !backend ? (
              <Empty
                description={
                  <Space direction="vertical" size={4}>
                    <span>当前 Registry 中没有已纳管的算法</span>
                    <Typography.Text type="secondary">
                      可注册服务器路径，例如 examples/onnx_text_classifier/1.0.0
                    </Typography.Text>
                  </Space>
                }
              >
                <Button type="primary" disabled={Boolean(health.error)} onClick={() => setRegisterOpen(true)}>
                  注册第一个算法
                </Button>
              </Empty>
            ) : <Empty description="没有符合当前筛选条件的算法" />
          }
          onRetry={() => void query.refetch()}
        >
          <Table
            rowKey={(item) => `${item.algorithm_id}:${item.version}:${item.backend_type}`}
            columns={columns}
            dataSource={data}
            size="small"
            scroll={{ x: 1160 }}
            pagination={{ pageSize: 15, showSizeChanger: true }}
          />
        </QueryState>
      </div>
      <RegisterAlgorithmModal open={registerOpen} onClose={() => setRegisterOpen(false)} />
    </section>
  )
}
