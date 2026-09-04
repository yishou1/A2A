import { SearchOutlined } from '@ant-design/icons'
import { Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AlgorithmSummary, DeploymentStatus, DeploymentSummary } from '../../api/contracts'
import { useAlgorithms } from '../../api/queries'
import { DeploymentStatusTag } from '../../components/StatusTag'
import { QueryState } from '../../components/QueryState'
import { backendLabel } from '../../lib/presentation'

interface DeploymentRow extends DeploymentSummary {
  algorithm: AlgorithmSummary
}

export function DeploymentListPage() {
  const query = useAlgorithms()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<DeploymentStatus>()

  const data = useMemo(() => {
    const normalized = keyword.trim().toLowerCase()
    return (query.data?.algorithms ?? [])
      .flatMap((algorithm) => algorithm.deployments.map((deployment) => ({ ...deployment, algorithm })))
      .filter((row) =>
        (!normalized || [row.deploy_id, row.node_id, row.algorithm.algorithm_id, row.algorithm.display_name]
          .some((value) => value.toLowerCase().includes(normalized))) &&
        (!status || row.deploy_status === status),
      )
  }, [keyword, query.data?.algorithms, status])

  const openAlgorithm = (row: DeploymentRow) => navigate(
    `/algorithms/${encodeURIComponent(row.algorithm.algorithm_id)}/${encodeURIComponent(row.algorithm.version)}/${encodeURIComponent(row.algorithm.backend_type)}?tab=deployments`,
  )

  const columns: ColumnsType<DeploymentRow> = [
    { title: '部署 ID', dataIndex: 'deploy_id', width: 190 },
    {
      title: '算法', key: 'algorithm', width: 250,
      render: (_, row) => (
        <button className="link-button" onClick={() => openAlgorithm(row)}>
          <span>{row.algorithm.display_name || row.algorithm.algorithm_id}</span>
          <small>{row.algorithm.algorithm_id} / {row.algorithm.version}</small>
        </button>
      ),
    },
    { title: '节点', dataIndex: 'node_id', width: 160 },
    { title: '区域', dataIndex: 'zone', width: 140, render: (value: string) => value || '—' },
    {
      title: '后端', key: 'backend', width: 130,
      render: (_, row) => <Tag>{backendLabel(row.algorithm.backend_type)}</Tag>,
    },
    {
      title: '部署状态', dataIndex: 'deploy_status', width: 110,
      render: (value: DeploymentStatus) => <DeploymentStatusTag status={value} />,
    },
    { title: '状态信息', dataIndex: 'status_message', ellipsis: true, render: (value: string) => value || '—' },
    { title: '操作', key: 'action', width: 90, fixed: 'right', render: (_, row) => <Button type="link" onClick={() => openAlgorithm(row)}>管理</Button> },
  ]

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>部署管理</Typography.Title>
          <Typography.Text type="secondary">查看全部算法的节点部署、就绪和异常状态。</Typography.Text>
        </div>
      </div>
      <div className="surface">
        <div className="filter-bar">
          <Input className="search-input" allowClear prefix={<SearchOutlined />} placeholder="搜索部署、节点或算法" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
          <Select
            allowClear
            placeholder="部署状态"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'ready', label: '已就绪' },
              { value: 'unloaded', label: '未加载' },
              { value: 'loading', label: '加载中' },
              { value: 'error', label: '异常' },
            ]}
          />
          <Typography.Text type="secondary" className="result-count">共 {data.length} 个部署实例</Typography.Text>
        </div>
        <QueryState loading={query.isLoading} error={query.error} empty={!query.isLoading && !query.error && data.length === 0} onRetry={() => void query.refetch()}>
          <Table
            rowKey={(row) => `${row.algorithm.algorithm_id}:${row.algorithm.version}:${row.algorithm.backend_type}:${row.deploy_id}`}
            columns={columns}
            dataSource={data}
            size="small"
            scroll={{ x: 1080 }}
            pagination={{ pageSize: 15, showSizeChanger: true }}
          />
        </QueryState>
      </div>
    </section>
  )
}
