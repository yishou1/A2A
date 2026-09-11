import { useMutation } from '@tanstack/react-query'
import { Alert, App, Form, Input, Modal, Switch } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { useManagementRefresh } from '../../api/useManagementRefresh'

interface RegisterFormValues {
  packagePath: string
  openAfterRegister: boolean
}

export function RegisterAlgorithmModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<RegisterFormValues>()
  const [serverError, setServerError] = useState<string>()
  const navigate = useNavigate()
  const refresh = useManagementRefresh()
  const { message } = App.useApp()
  const mutation = useMutation({
    mutationFn: (values: RegisterFormValues) => api.registerAlgorithm(values.packagePath.trim()),
    onSuccess: async (result, values) => {
      await refresh()
      message.success(`算法 ${result.algorithm_id} 已纳管，当前状态为已验证`)
      form.resetFields()
      setServerError(undefined)
      onClose()
      if (values.openAfterRegister) {
        navigate(
          `/algorithms/${encodeURIComponent(result.algorithm_id)}/${encodeURIComponent(result.version)}/${encodeURIComponent(result.backend_type)}`,
        )
      }
    },
    onError: (error: Error) => setServerError(error.message),
  })

  const submit = async () => {
    setServerError(undefined)
    const values = await form.validateFields()
    mutation.mutate(values)
  }

  return (
    <Modal
      title="注册算法"
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      okText="开始纳管"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        message="请输入 AlgoLib Server 可访问的算法包目录或 algorithm_card.yaml 路径。这不是浏览器本地文件上传。"
        className="modal-alert"
      />
      {serverError && <Alert type="error" showIcon message="纳管失败" description={serverError} className="modal-alert" />}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ openAfterRegister: true }}
        preserve={false}
      >
        <Form.Item
          name="packagePath"
          label="服务器算法包路径"
          rules={[{ required: true, whitespace: true, message: '请输入算法包路径' }]}
        >
          <Input placeholder="D:\\model_repo\\algorithm_id\\1.0.0" autoFocus />
        </Form.Item>
        <Form.Item name="openAfterRegister" label="注册成功后打开详情" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  )
}
