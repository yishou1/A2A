import { describe, expect, it } from 'vitest'
import { availableLifecycleActions, canDeleteDeployment } from './managementRules'

describe('management operation guards', () => {
  it('only exposes transitions allowed by the registry lifecycle', () => {
    expect(availableLifecycleActions('draft')).toEqual(['validate', 'delete'])
    expect(availableLifecycleActions('validated')).toContain('activate')
    expect(availableLifecycleActions('active')).toEqual(['disable'])
    expect(availableLifecycleActions('disabled')).toContain('activate')
    expect(availableLifecycleActions('deleted')).toEqual([])
  })

  it('requires ready and loading deployments to be unloaded before deletion', () => {
    expect(canDeleteDeployment('ready')).toBe(false)
    expect(canDeleteDeployment('loading')).toBe(false)
    expect(canDeleteDeployment('unloaded')).toBe(true)
    expect(canDeleteDeployment('error')).toBe(true)
  })
})
