import { DeleteOutlined, PauseCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, App, Button, Input, Modal, Space } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type AlgorithmKey } from '../../api/client'
import type { AlgorithmStatus } from '../../api/contracts'
import { useManagementRefresh } from '../../api/useManagementRefresh'
import { availableLifecycleActions } from '../../lib/managementRules'

function actionLabel(action: 'validate' | 'activate' | 'disable') {
  return { validate: '重新验证', activate: '激活', disable: '禁用' }[action]
}

export function LifecycleActions({
  algorithmKey,
  status,
  displayName,
  disabled = false,
}: {
  algorithmKey: AlgorithmKey
  status: AlgorithmStatus
  displayName: string
  disabled?: boolean
}) {
  const { message, modal } = App.useApp()
  const navigate = useNavigate()
  const refresh = useManagementRefresh()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [deleteError, setDeleteError] = useState<string>()
  const actions = availableLifecycleActions(status)

  const lifecycle = useMutation({
    mutationFn: (action: 'validate' | 'activate' | 'disable') => api.lifecycle(algorithmKey, action),
    onSuccess: async (result, action) => {
      await refresh()
      message.success(`${actionLabel(action)}成功，当前状态：${result.status}`)
    },
    onError: (error: Error) => message.error(error.message),
  })

  const remove = useMutation({
    mutationFn: () => api.deleteAlgorithm(algorithmKey),
    onSuccess: async () => {
      await refresh()
      message.success('算法注册记录已逻辑删除，原始算法包文件未删除')
      setDeleteOpen(false)
      navigate('/algorithms')
    },
    onError: (error: Error) => setDeleteError(error.message),
  })

  const confirmLifecycle = (action: 'validate' | 'activate' | 'disable') => {
    const descriptions = {
      validate: '将重新读取并校验算法卡和 Schema，已有部署记录会保留。',
      activate: '激活后算法将进入统一调用范围，请确认其运行依赖已准备。',
      disable: '禁用后新的统一调用将被拒绝，但不会自动卸载 Runner 或删除部署记录。',
    }
    modal.confirm({
      title: `确认${actionLabel(action)} ${displayName}？`,
      content: descriptions[action],
      okText: `确认${actionLabel(action)}`,
      okButtonProps: { danger: action === 'disable' },
      cancelText: '取消',
      onOk: () => lifecycle.mutateAsync(action),
    })
  }

  return (
    <>
      <Space wrap>
        {actions.includes('validate') && (
          <Button
            icon={<SafetyCertificateOutlined />}
            disabled={disabled}
            loading={lifecycle.isPending && lifecycle.variables === 'validate'}
            onClick={() => confirmLifecycle('validate')}
          >
            {status === 'draft' ? '验证' : '重新验证'}
          </Button>
        )}
        {actions.includes('activate') && (
          <Button
            type="primary"
            disabled={disabled}
            loading={lifecycle.isPending && lifecycle.variables === 'activate'}
            onClick={() => confirmLifecycle('activate')}
          >激活</Button>
        )}
        {actions.includes('disable') && (
          <Button
            danger
            icon={<PauseCircleOutlined />}
            disabled={disabled}
            loading={lifecycle.isPending && lifecycle.variables === 'disable'}
            onClick={() => confirmLifecycle('disable')}
          >禁用</Button>
        )}
        {actions.includes('delete') && (
          <Button danger type="text" icon={<DeleteOutlined />} disabled={disabled} onClick={() => setDeleteOpen(true)}>
            删除
          </Button>
        )}
      </Space>
      <Modal
        title="逻辑删除算法"
        open={deleteOpen}
        onCancel={() => { setDeleteOpen(false); setConfirmation(''); setDeleteError(undefined) }}
        onOk={() => remove.mutate()}
        okText="确认删除"
        okButtonProps={{ danger: true, disabled: confirmation !== algorithmKey.algorithmId }}
        confirmLoading={remove.isPending}
      >
        <Alert
          type="warning"
          showIcon
          message="此操作只会逻辑删除注册记录，不会删除服务器中的算法包文件。"
          className="modal-alert"
        />
        {deleteError && <Alert type="error" showIcon message={deleteError} className="modal-alert" />}
        <p>请输入算法 ID <strong>{algorithmKey.algorithmId}</strong> 确认：</p>
        <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      </Modal>
    </>
  )
}
