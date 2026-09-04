import { describe, expect, it } from 'vitest'
import { algorithmPath } from '../api/client'
import { algorithmStatusMeta, backendLabel, displayValue } from './presentation'

describe('management console presentation helpers', () => {
  it('keeps registry lifecycle labels distinct', () => {
    expect(algorithmStatusMeta.active.label).toBe('已激活')
    expect(algorithmStatusMeta.validated.label).toBe('已验证')
    expect(algorithmStatusMeta.disabled.label).toBe('已禁用')
  })

  it('encodes every algorithm key path segment', () => {
    expect(algorithmPath({
      algorithmId: 'target/classifier',
      version: '1.0 beta',
      backendType: 'onnx',
    })).toBe('/algorithms/target%2Fclassifier/1.0%20beta/onnx')
  })

  it('formats backend and empty values for management views', () => {
    expect(backendLabel('python_http_service')).toBe('Python HTTP')
    expect(displayValue('')).toBe('—')
    expect(displayValue(false)).toBe('否')
  })
})
