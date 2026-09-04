import { CloudDownloadOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, App, Button, Card, Descriptions, Empty, Form, Input, InputNumber, Modal, Select, Space, Tooltip } from 'antd'
import { useState } from 'react'
import { api, type AlgorithmKey } from '../../api/client'
import type { DeploymentDetail, DeploymentStatus } from '../../api/contracts'
import { useManagementRefresh } from '../../api/useManagementRefresh'
import { DeploymentStatusTag } from '../../components/StatusTag'
import { displayValue } from '../../lib/presentation'
import { canDeleteDeployment } from '../../lib/managementRules'

interface AddDeploymentValues {
  deploy_id: string
  node_id: string
  zone?: string
  endpoint?: string
  health_endpoint?: string
  local_model_path?: string
}

interface LoadValues {
  transfer_mode?: 'none' | 'http_pull'
  source_base_url?: string
  target_local_dir?: string
  files_to_pull?: string
  pool_size?: number
  pool_checkout_timeout_ms?: number
}

interface StatusValues {
  deploy_status: DeploymentStatus
  status_message?: string
}

export function DeploymentPanel({
  algorithmKey,
  deployments,
  mutationsDisabled,
}: {
  algorithmKey: AlgorithmKey
  deployments: DeploymentDetail[]
  mutationsDisabled: boolean
}) {
  const { message, modal } = App.useApp()
  const refresh = useManagementRefresh()
  const [addForm] = Form.useForm<AddDeploymentValues>()
  const [loadForm] = Form.useForm<LoadValues>()
  const [statusForm] = Form.useForm<StatusValues>()
  const [addOpen, setAddOpen] = useState(false)
  const [loadTarget, setLoadTarget] = useState<DeploymentDetail>()
  const [statusTarget, setStatusTarget] = useState<DeploymentDetail>()

  const afterChange = async (successMessage: string) => {
    await refresh()
    message.success(successMessage)
  }

  const add = useMutation({
    mutationFn: (values: AddDeploymentValues) => api.addDeployment(algorithmKey, {
      ...values,
      deploy_status: 'unloaded',
    }),
    onSuccess: async () => {
      await afterChange('部署实例已登记，当前为未加载状态')
      setAddOpen(false)
      addForm.resetFields()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const load = useMutation({
    mutationFn: ({ target, values }: { target: DeploymentDetail; values: LoadValues }) => {
      const payload: Record<string, unknown> = { deploy_id: target.deploy_id }
      if (algorithmKey.backendType === 'onnx') {
        payload.transfer_mode = values.transfer_mode ?? 'none'
        if (values.source_base_url) payload.source_base_url = values.source_base_url.trim()
        if (values.target_local_dir) payload.target_local_dir = values.target_local_dir.trim()
        if (values.files_to_pull?.trim()) {
          payload.files_to_pull = values.files_to_pull.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
        }
      } else {
        if (values.pool_size) payload.pool_size = values.pool_size
        if (values.pool_checkout_timeout_ms) payload.pool_checkout_timeout_ms = values.pool_checkout_timeout_ms
      }
      return api.load(algorithmKey, payload)
    },
    onSuccess: async (result) => {
      await afterChange(result.load_status === 'already_loaded' ? '该实例已加载，未重复创建 Runner' : '预加载完成')
      setLoadTarget(undefined)
      loadForm.resetFields()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const unload = useMutation({
    mutationFn: (deployment: DeploymentDetail) => api.unload(algorithmKey, deployment.deploy_id),
    onSuccess: async () => afterChange('运行资源已卸载，部署记录仍保留'),
    onError: (error: Error) => message.error(error.message),
  })

  const updateStatus = useMutation({
    mutationFn: ({ target, values }: { target: DeploymentDetail; values: StatusValues }) =>
      api.updateDeploymentStatus(algorithmKey, target.deploy_id, values),
    onSuccess: async () => {
      await afterChange('部署登记状态已更新')
      setStatusTarget(undefined)
      statusForm.resetFields()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const remove = useMutation({
    mutationFn: (deployment: DeploymentDetail) => api.deleteDeployment(algorithmKey, deployment.deploy_id),
    onSuccess: async () => afterChange('部署登记已删除，模型文件和外部服务未删除'),
    onError: (error: Error) => message.error(error.message),
  })

  const confirmUnload = (deployment: DeploymentDetail) => modal.confirm({
    title: `卸载部署 ${deployment.deploy_id}？`,
    content: '卸载会释放该部署实例的 Runner 资源，不会禁用算法或删除部署记录。',
    okText: '确认卸载',
    okButtonProps: { danger: true },
    onOk: () => unload.mutateAsync(deployment),
  })

  const confirmRemove = (deployment: DeploymentDetail) => modal.confirm({
    title: `删除部署记录 ${deployment.deploy_id}？`,
    content: '此操作只移除 AlgoLib 中的登记信息，不会停止外部服务或删除模型文件。',
    okText: '确认删除',
    okButtonProps: { danger: true },
    onOk: () => remove.mutateAsync(deployment),
  })

  const submitAdd = async () => add.mutate(await addForm.validateFields())
  const submitLoad = async () => {
    if (!loadTarget) return
    load.mutate({ target: loadTarget, values: await loadForm.validateFields() })
  }
  const submitStatus = async () => {
    if (!statusTarget) return
    updateStatus.mutate({ target: statusTarget, values: await statusForm.validateFields() })
  }

  return (
    <>
      <div className="panel-toolbar">
        <span>同一算法可登记多个部署节点，加载状态与算法激活状态相互独立。</span>
        <Button type="primary" icon={<PlusOutlined />} disabled={mutationsDisabled} onClick={() => setAddOpen(true)}>
          添加部署
        </Button>
      </div>
      {deployments.length ? (
        <div className="deployment-list">
          {deployments.map((deployment) => {
            const mustUnload = !canDeleteDeployment(deployment.deploy_status)
            return (
              <Card size="small" key={deployment.deploy_id}>
                <div className="deployment-card-heading">
                  <Space wrap>
                    <strong>{deployment.deploy_id}</strong>
                    <DeploymentStatusTag status={deployment.deploy_status} />
                  </Space>
                  <Space wrap>
                    {deployment.deploy_status !== 'ready' && deployment.deploy_status !== 'loading' && (
                      <Button
                        size="small"
                        icon={<CloudDownloadOutlined />}
                        disabled={mutationsDisabled}
                        onClick={() => { setLoadTarget(deployment); loadForm.resetFields() }}
                      >预加载</Button>
                    )}
                    {deployment.deploy_status === 'ready' && (
                      <Button size="small" danger disabled={mutationsDisabled} onClick={() => confirmUnload(deployment)}>卸载</Button>
                    )}
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      disabled={mutationsDisabled}
                      onClick={() => {
                        setStatusTarget(deployment)
                        statusForm.setFieldsValue({
                          deploy_status: deployment.deploy_status,
                          status_message: deployment.status_message,
                        })
                      }}
                    >状态</Button>
                    <Tooltip title={mustUnload ? '请先卸载该部署实例' : undefined}>
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        disabled={mutationsDisabled || mustUnload}
                        onClick={() => confirmRemove(deployment)}
                      >删除</Button>
                    </Tooltip>
                  </Space>
                </div>
                <Descriptions size="small" column={2} className="deployment-description">
                  <Descriptions.Item label="节点">{displayValue(deployment.node_id)}</Descriptions.Item>
                  <Descriptions.Item label="区域">{displayValue(deployment.zone)}</Descriptions.Item>
                  <Descriptions.Item label="Endpoint">{displayValue(deployment.endpoint)}</Descriptions.Item>
                  <Descriptions.Item label="本地模型">{displayValue(deployment.local_model_path)}</Descriptions.Item>
                  <Descriptions.Item label="最近更新">{displayValue(deployment.updated_at)}</Descriptions.Item>
                  <Descriptions.Item label="状态信息">{displayValue(deployment.status_message)}</Descriptions.Item>
                </Descriptions>
              </Card>
            )
          })}
        </div>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记部署实例" />}

      <Modal
        title="添加部署实例"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={() => void submitAdd()}
        confirmLoading={add.isPending}
        okText="添加"
        destroyOnHidden
      >
        <Form form={addForm} layout="vertical" preserve={false}>
          <Form.Item name="deploy_id" label="部署 ID" rules={[{ required: true, whitespace: true }]}>
            <Input placeholder="node-01/deploy-01" />
          </Form.Item>
          <Form.Item name="node_id" label="节点 ID" rules={[{ required: true, whitespace: true }]}>
            <Input placeholder="node-01" />
          </Form.Item>
          <Form.Item name="zone" label="区域/集群"><Input placeholder="zone-a" /></Form.Item>
          <Form.Item name="endpoint" label="推理 Endpoint"><Input placeholder="http://node-01:8080/predict" /></Form.Item>
          <Form.Item name="health_endpoint" label="健康检查 Endpoint"><Input placeholder="http://node-01:8080/health" /></Form.Item>
          {algorithmKey.backendType === 'onnx' && (
            <Form.Item name="local_model_path" label="节点本地模型路径"><Input /></Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={`预加载 ${loadTarget?.deploy_id ?? ''}`}
        open={Boolean(loadTarget)}
        onCancel={() => setLoadTarget(undefined)}
        onOk={() => void submitLoad()}
        confirmLoading={load.isPending}
        okText="开始加载"
        destroyOnHidden
      >
        <Alert type="info" showIcon message="加载接口为同步操作，请在请求完成前保持页面打开。" className="modal-alert" />
        <Form form={loadForm} layout="vertical" initialValues={{ transfer_mode: 'none', pool_size: 4, pool_checkout_timeout_ms: 5000 }} preserve={false}>
          {algorithmKey.backendType === 'onnx' ? (
            <>
              <Form.Item name="transfer_mode" label="传输模式">
                <Select options={[{ value: 'none', label: '文件已在本地' }, { value: 'http_pull', label: '从主库 HTTP 拉取' }]} />
              </Form.Item>
              <Form.Item noStyle shouldUpdate={(previous, current) => previous.transfer_mode !== current.transfer_mode}>
                {({ getFieldValue }) => getFieldValue('transfer_mode') === 'http_pull' ? (
                  <>
                    <Form.Item name="source_base_url" label="主库 Base URL" rules={[{ required: true }]}><Input placeholder="http://main-node:8088" /></Form.Item>
                    <Form.Item name="target_local_dir" label="目标保存目录"><Input /></Form.Item>
                    <Form.Item name="files_to_pull" label="指定拉取文件（每行一个，留空则自动推导）"><Input.TextArea rows={4} /></Form.Item>
                  </>
                ) : null}
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="pool_size" label="连接池大小"><InputNumber min={1} max={128} /></Form.Item>
              <Form.Item name="pool_checkout_timeout_ms" label="连接池等待超时（ms）"><InputNumber min={100} max={120000} /></Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title={`更新部署状态 ${statusTarget?.deploy_id ?? ''}`}
        open={Boolean(statusTarget)}
        onCancel={() => setStatusTarget(undefined)}
        onOk={() => void submitStatus()}
        confirmLoading={updateStatus.isPending}
        okText="保存状态"
        destroyOnHidden
      >
        <Alert type="warning" showIcon message="这里修改的是部署登记状态，不会实际加载或卸载 Runner。" className="modal-alert" />
        <Form form={statusForm} layout="vertical" preserve={false}>
          <Form.Item name="deploy_status" label="部署状态" rules={[{ required: true }]}>
            <Select options={[
              { value: 'unloaded', label: '未加载' },
              { value: 'loading', label: '加载中' },
              { value: 'ready', label: '已就绪' },
              { value: 'error', label: '异常' },
            ]} />
          </Form.Item>
          <Form.Item name="status_message" label="状态说明"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}
