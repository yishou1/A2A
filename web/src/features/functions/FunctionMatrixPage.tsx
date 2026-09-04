import { ApartmentOutlined } from '@ant-design/icons'
import { Button, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AlgorithmSummary, OperationalFunction, OperationalFunctionMapping } from '../../api/contracts'
import { useAlgorithms, useOperationalFunctions } from '../../api/queries'
import { AlgorithmStatusTag } from '../../components/StatusTag'
import { QueryState } from '../../components/QueryState'

interface FunctionAlgorithm {
  algorithm: AlgorithmSummary
  mapping: OperationalFunctionMapping
}

interface FunctionRow extends OperationalFunction {
  algorithms: FunctionAlgorithm[]
}

export function FunctionMatrixPage() {
  const functionsQuery = useOperationalFunctions()
  const algorithmsQuery = useAlgorithms()
  const navigate = useNavigate()
  const [ooda, setOoda] = useState<string>()
  const [stage, setStage] = useState<string>()

  const data = useMemo<FunctionRow[]>(() => {
    const algorithms = algorithmsQuery.data?.algorithms ?? []
    return (functionsQuery.data?.functions ?? []).map((fn) => ({
      ...fn,
      algorithms: algorithms.flatMap((algorithm) =>
        algorithm.operational_functions
          .filter((mapping) => mapping.function_id === fn.function_id || mapping.function_code === fn.function_code)
          .map((mapping) => ({ algorithm, mapping }))),
    })).filter((fn) => (!ooda || fn.ooda_phase === ooda) && (!stage || fn.f2t2ea_stage === stage))
  }, [algorithmsQuery.data?.algorithms, functionsQuery.data?.functions, ooda, stage])

  const openAlgorithm = (algorithm: AlgorithmSummary) => navigate(
    `/algorithms/${encodeURIComponent(algorithm.algorithm_id)}/${encodeURIComponent(algorithm.version)}/${encodeURIComponent(algorithm.backend_type)}?tab=functions`,
  )

  const columns: ColumnsType<FunctionRow> = [
    { title: 'KC', dataIndex: 'function_id', width: 90, render: (value: string) => <Tag color="blue">{value}</Tag> },
    {
      title: '业务功能', key: 'function', width: 310,
      render: (_, row) => <div className="function-name"><strong>{row.function_name}</strong><small>{row.function_code}</small></div>,
    },
    { title: 'OODA', dataIndex: 'ooda_phase', width: 110, render: (value: string) => value ? <Tag>{value}</Tag> : '—' },
    { title: 'F2T2EA', dataIndex: 'f2t2ea_stage', width: 110, render: (value: string) => value ? <Tag>{value}</Tag> : '—' },
    {
      title: '关联算法', key: 'algorithms',
      render: (_, row) => row.algorithms.length ? (
        <div className="function-algorithms">
          {row.algorithms.map(({ algorithm, mapping }) => {
            const ready = algorithm.deployments.filter((item) => item.deploy_status === 'ready').length
            return (
              <button className="function-algorithm" key={`${algorithm.algorithm_id}:${algorithm.version}:${algorithm.backend_type}:${mapping.role}`} onClick={() => openAlgorithm(algorithm)}>
                <span className="function-algorithm-main"><strong>{algorithm.display_name || algorithm.algorithm_id}</strong><AlgorithmStatusTag status={algorithm.registry_status} /></span>
                <small>{mapping.role} · {mapping.coverage_level} · ready {ready}/{algorithm.deployments.length}</small>
              </button>
            )
          })}
        </div>
      ) : <Typography.Text type="secondary">尚无算法映射</Typography.Text>,
    },
    { title: '覆盖', key: 'coverage', width: 90, render: (_, row) => <strong>{row.algorithms.length}</strong> },
  ]

  const loading = functionsQuery.isLoading || algorithmsQuery.isLoading
  const error = functionsQuery.error || algorithmsQuery.error
  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>功能点矩阵</Typography.Title>
          <Typography.Text type="secondary">从 KC-01～KC-28 业务视角查看算法角色、覆盖程度和运行准备情况。</Typography.Text>
        </div>
      </div>
      <div className="surface">
        <div className="filter-bar">
          <ApartmentOutlined />
          <Select allowClear placeholder="OODA 阶段" value={ooda} onChange={setOoda} options={['observe', 'orient', 'decide', 'act'].map((value) => ({ value, label: value }))} />
          <Select allowClear placeholder="F2T2EA 阶段" value={stage} onChange={setStage} options={['find', 'fix', 'track', 'target', 'engage', 'assess'].map((value) => ({ value, label: value }))} />
          <Button onClick={() => { setOoda(undefined); setStage(undefined) }}>重置</Button>
          <Typography.Text type="secondary" className="result-count">共 {data.length} 个功能点</Typography.Text>
        </div>
        <QueryState loading={loading} error={error} empty={!loading && data.length === 0} onRetry={() => { void functionsQuery.refetch(); void algorithmsQuery.refetch() }}>
          <Table rowKey="function_id" columns={columns} dataSource={data} size="small" pagination={false} scroll={{ x: 1050 }} />
        </QueryState>
      </div>
    </section>
  )
}
