import type { AlgorithmStatus, BackendType, DeploymentStatus } from '../api/contracts'

export const algorithmStatusMeta: Record<AlgorithmStatus, { label: string; color: string }> = {
  draft: { label: '草稿', color: 'default' },
  validated: { label: '已验证', color: 'blue' },
  active: { label: '已激活', color: 'green' },
  disabled: { label: '已禁用', color: 'orange' },
  deleted: { label: '已删除', color: 'red' },
}

export const deploymentStatusMeta: Record<DeploymentStatus, { label: string; color: string }> = {
  loading: { label: '加载中', color: 'processing' },
  ready: { label: '已就绪', color: 'success' },
  error: { label: '异常', color: 'error' },
  unloaded: { label: '未加载', color: 'default' },
}

export function backendLabel(backend: BackendType): string {
  return backend === 'onnx' ? 'ONNX' : 'Python HTTP'
}

export function displayValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.join(' × ')
  return String(value)
}
