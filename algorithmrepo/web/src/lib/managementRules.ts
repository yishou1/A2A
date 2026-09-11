import type { AlgorithmStatus, DeploymentStatus } from '../api/contracts'

export type LifecycleAction = 'validate' | 'activate' | 'disable' | 'delete'

export function availableLifecycleActions(status: AlgorithmStatus): LifecycleAction[] {
  switch (status) {
    case 'draft': return ['validate', 'delete']
    case 'validated': return ['validate', 'activate', 'disable', 'delete']
    case 'active': return ['disable']
    case 'disabled': return ['validate', 'activate', 'delete']
    case 'deleted': return []
  }
}

export function canDeleteDeployment(status: DeploymentStatus): boolean {
  return status === 'unloaded' || status === 'error'
}
