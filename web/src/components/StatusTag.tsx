import { Tag } from 'antd'
import type { AlgorithmStatus, DeploymentStatus } from '../api/contracts'
import { algorithmStatusMeta, deploymentStatusMeta } from '../lib/presentation'

export function AlgorithmStatusTag({ status }: { status: AlgorithmStatus }) {
  const meta = algorithmStatusMeta[status]
  return <Tag color={meta?.color}>{meta?.label ?? status}</Tag>
}

export function DeploymentStatusTag({ status }: { status: DeploymentStatus }) {
  const meta = deploymentStatusMeta[status]
  return <Tag color={meta?.color}>{meta?.label ?? status}</Tag>
}
