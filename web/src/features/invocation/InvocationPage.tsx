import { CopyOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, App, Button, Card, Col, Descriptions, Input, Row, Select, Space, Tabs, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, api, type AlgorithmKey } from '../../api/client'
import type { AlgorithmSummary, RunRequest, RunResponse } from '../../api/contracts'
import { useAlgorithms, useAlgorithmSchema } from '../../api/queries'
import { QueryState } from '../../components/QueryState'
import { backendLabel } from '../../lib/presentation'
import { createInitialInputs, SchemaInputForm } from './SchemaInputForm'
import { validateSchemaInputs } from '../../lib/schemaValidation'

function keyValue(item: AlgorithmSummary) {
  return `${item.algorithm_id}\u0000${item.version}\u0000${item.backend_type}`
}

function newEnvelopeId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
}

function JsonPanel({ value }: { value: unknown }) {
  return <pre className="json-panel invocation-result-json">{JSON.stringify(value, null, 2)}</pre>
}

export function InvocationPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const algorithmsQuery = useAlgorithms()
  const activeAlgorithms = useMemo(
    () => (algorithmsQuery.data?.algorithms ?? []).filter((item) => item.registry_status === 'active'),
    [algorithmsQuery.data?.algorithms],
  )
  const [selectedValue, setSelectedValue] = useState('')
  const selected = activeAlgorithms.find((item) => keyValue(item) === selectedValue)
  const algorithmKey: AlgorithmKey = {
    algorithmId: selected?.algorithm_id ?? '',
    version: selected?.version ?? '',
    backendType: selected?.backend_type ?? 'onnx',
  }
  const schemaQuery = useAlgorithmSchema(algorithmKey, 'input')
  const [mode, setMode] = useState<'form' | 'json'>('form')
  const [inputs, setInputs] = useState<Record<string, unknown>>({})
  const [rawInputs, setRawInputs] = useState('{}')
  const [rawParams, setRawParams] = useState('{}')
  const [requestId, setRequestId] = useState(() => newEnvelopeId('req'))
  const [traceId, setTraceId] = useState(() => newEnvelopeId('trace'))
  const [deployId, setDeployId] = useState<string>()
  const [functionId, setFunctionId] = useState<string>()
  const [workflowId, setWorkflowId] = useState('')
  const [stepId, setStepId] = useState('')
  const [result, setResult] = useState<RunResponse>()
  const [requestPreview, setRequestPreview] = useState<RunRequest>()
  const [editorError, setEditorError] = useState<string>()

  useEffect(() => {
    if (!activeAlgorithms.length) return
    const requested = activeAlgorithms.find((item) =>
      item.algorithm_id === searchParams.get('algorithm_id') &&
      item.version === searchParams.get('version') &&
      item.backend_type === searchParams.get('backend_type'))
    const next = requested ?? activeAlgorithms[0]
    setSelectedValue((current) => current || keyValue(next))
  }, [activeAlgorithms, searchParams])

  useEffect(() => {
    if (!selected || !schemaQuery.data) return
    const initial = createInitialInputs(schemaQuery.data, selected.agent_card.examples[0]?.input)
    setInputs(initial)
    setRawInputs(JSON.stringify(initial, null, 2))
    setRawParams('{}')
    setDeployId(undefined)
    setFunctionId(undefined)
    setResult(undefined)
    setEditorError(undefined)
  }, [schemaQuery.data, selected])

  const mutation = useMutation({
    mutationFn: api.run,
    onSuccess: (response) => {
      setResult(response)
      message.success('算法调用成功')
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.payload && typeof error.payload === 'object') {
        setResult(error.payload as RunResponse)
      }
      message.error(error.message)
    },
  })

  const selectAlgorithm = (value: string) => {
    setSelectedValue(value)
    const item = activeAlgorithms.find((candidate) => keyValue(candidate) === value)
    if (item) {
      setSearchParams({
        algorithm_id: item.algorithm_id,
        version: item.version,
        backend_type: item.backend_type,
      }, { replace: true })
    }
  }

  const run = () => {
    if (!selected) return
    try {
      const effectiveInputs = mode === 'json' ? JSON.parse(rawInputs) : inputs
      const params = JSON.parse(rawParams)
      if (!effectiveInputs || typeof effectiveInputs !== 'object' || Array.isArray(effectiveInputs)) {
        throw new Error('inputs 必须是 JSON 对象')
      }
      if (!params || typeof params !== 'object' || Array.isArray(params)) {
        throw new Error('params 必须是 JSON 对象')
      }
      if (schemaQuery.data) {
        const validationErrors = validateSchemaInputs(schemaQuery.data, effectiveInputs)
        if (validationErrors.length) {
          throw new Error(`输入不符合 Schema：${validationErrors.join('；')}`)
        }
      }
      const mapping = selected.operational_functions.find((item) => item.function_id === functionId)
      const request: RunRequest = {
        request_id: requestId.trim(),
        trace_id: traceId.trim(),
        algorithm_id: selected.algorithm_id,
        version: selected.version,
        backend_type: selected.backend_type,
        inputs: effectiveInputs,
        params,
      }
      if (deployId) request.deploy_id = deployId
      if (mapping || workflowId || stepId) {
        request.function_context = {
          function_id: mapping?.function_id,
          function_code: mapping?.function_code,
          workflow_instance_id: workflowId || undefined,
          step_instance_id: stepId || undefined,
        }
      }
      setEditorError(undefined)
      setRequestPreview(request)
      setResult(undefined)
      mutation.mutate(request)
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : 'JSON 格式错误')
    }
  }

  const copyJson = async (value: unknown, label: string) => {
    await navigator.clipboard.writeText(JSON.stringify(value, null, 2))
    message.success(`${label}已复制`)
  }

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>在线调用</Typography.Title>
          <Typography.Text type="secondary">使用算法 Schema 构造请求，验证统一调用结果与功能点执行信息。</Typography.Text>
        </div>
      </div>
      <QueryState loading={algorithmsQuery.isLoading} error={algorithmsQuery.error} empty={!algorithmsQuery.isLoading && activeAlgorithms.length === 0} onRetry={() => void algorithmsQuery.refetch()} emptyContent={<Alert type="warning" showIcon message="当前没有已激活算法" description="请先在算法详情页完成激活。" />}>
        <Card className="surface-card invocation-toolbar-card">
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={10}>
              <label className="compact-field"><span>算法</span><Select showSearch optionFilterProp="label" value={selectedValue || undefined} onChange={selectAlgorithm} options={activeAlgorithms.map((item) => ({ value: keyValue(item), label: `${item.display_name || item.algorithm_id} · ${item.version} · ${backendLabel(item.backend_type)}` }))} /></label>
            </Col>
            <Col xs={12} xl={7}>
              <label className="compact-field"><span>Request ID</span><Input value={requestId} onChange={(event) => setRequestId(event.target.value)} suffix={<ReloadOutlined onClick={() => setRequestId(newEnvelopeId('req'))} />} /></label>
            </Col>
            <Col xs={12} xl={7}>
              <label className="compact-field"><span>Trace ID</span><Input value={traceId} onChange={(event) => setTraceId(event.target.value)} suffix={<ReloadOutlined onClick={() => setTraceId(newEnvelopeId('trace'))} />} /></label>
            </Col>
          </Row>
        </Card>
        {selected && (
          <Row gutter={16} className="invocation-workspace">
            <Col xs={24} xl={12}>
              <Card title="请求" className="surface-card" extra={<Tag>{backendLabel(selected.backend_type)}</Tag>}>
                <Tabs activeKey={mode} onChange={(key) => {
                  const next = key as 'form' | 'json'
                  if (next === 'json') setRawInputs(JSON.stringify(inputs, null, 2))
                  else {
                    try { setInputs(JSON.parse(rawInputs)); setEditorError(undefined) }
                    catch (error) { setEditorError(error instanceof Error ? error.message : 'JSON 格式错误'); return }
                  }
                  setMode(next)
                }} items={[
                  { key: 'form', label: 'Schema 表单', children: schemaQuery.data ? <SchemaInputForm schema={schemaQuery.data} values={inputs} onChange={setInputs} /> : <Typography.Text type="secondary">Schema 加载中…</Typography.Text> },
                  { key: 'json', label: 'JSON 模式', children: <Input.TextArea className="code-textarea" rows={14} value={rawInputs} onChange={(event) => setRawInputs(event.target.value)} /> },
                ]} />
                <div className="invocation-options">
                  <label className="compact-field"><span>指定部署（可选）</span><Select allowClear value={deployId} onChange={setDeployId} options={selected.deployments.filter((item) => item.deploy_status === 'ready').map((item) => ({ value: item.deploy_id, label: `${item.deploy_id} · ${item.node_id}` }))} placeholder="由服务端选择 / 按需加载" /></label>
                  <label className="compact-field"><span>业务功能点（可选）</span><Select allowClear value={functionId} onChange={setFunctionId} options={selected.operational_functions.map((item) => ({ value: item.function_id, label: `${item.function_id} · ${item.function_name}` }))} placeholder="使用算法卡默认映射" /></label>
                  <Row gutter={12}><Col span={12}><label className="compact-field"><span>Workflow ID</span><Input value={workflowId} onChange={(event) => setWorkflowId(event.target.value)} /></label></Col><Col span={12}><label className="compact-field"><span>Step ID</span><Input value={stepId} onChange={(event) => setStepId(event.target.value)} /></label></Col></Row>
                  <label className="compact-field"><span>Params JSON</span><Input.TextArea className="code-textarea" rows={4} value={rawParams} onChange={(event) => setRawParams(event.target.value)} /></label>
                </div>
                {editorError && <Alert type="error" showIcon message="请求编辑错误" description={editorError} />}
                <Button block type="primary" size="large" icon={<PlayCircleOutlined />} loading={mutation.isPending} onClick={run}>执行调用</Button>
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card title="结果" className="surface-card" extra={result && <Button size="small" icon={<CopyOutlined />} onClick={() => void copyJson(result, '响应')}>复制响应</Button>}>
                {!result && !mutation.isPending && <div className="invocation-empty"><Typography.Text type="secondary">执行后将在此显示 outputs、usage 和错误信息。</Typography.Text></div>}
                {mutation.isPending && <Alert type="info" showIcon message="正在执行算法，请勿重复提交。" />}
                {result && (
                  <Space direction="vertical" size="middle" className="full-width">
                    <Alert type={result.ok ? 'success' : 'error'} showIcon message={result.ok ? '调用成功' : `调用失败：${result.error?.code ?? 'UNKNOWN_ERROR'}`} description={result.error?.message} />
                    <Descriptions bordered size="small" column={2}>
                      <Descriptions.Item label="Request ID">{result.request_id}</Descriptions.Item>
                      <Descriptions.Item label="Trace ID">
                        <Space size={4}>{result.trace_id}<Button type="link" size="small" onClick={() => navigate(`/traces?trace_id=${encodeURIComponent(result.trace_id)}`)}>查看链路</Button></Space>
                      </Descriptions.Item>
                      <Descriptions.Item label="算法">{result.algorithm_id} / {result.version}</Descriptions.Item>
                      <Descriptions.Item label="后端">{backendLabel(result.backend_type)}</Descriptions.Item>
                    </Descriptions>
                    <div><h3>Outputs</h3><JsonPanel value={result.outputs} /></div>
                    <div><h3>Usage</h3><JsonPanel value={result.usage} /></div>
                    {result.function_execution && <div><h3>Function Execution</h3><JsonPanel value={result.function_execution} /></div>}
                  </Space>
                )}
              </Card>
            </Col>
          </Row>
        )}
        {requestPreview && <Card className="surface-card request-preview" title="最近一次请求" extra={<Button size="small" icon={<CopyOutlined />} onClick={() => void copyJson(requestPreview, '请求')}>复制请求</Button>}><JsonPanel value={requestPreview} /></Card>}
      </QueryState>
    </section>
  )
}
