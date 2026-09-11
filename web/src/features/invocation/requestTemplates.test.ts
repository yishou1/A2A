import { describe, expect, it } from 'vitest'
import { createRequestTemplates } from './requestTemplates'

const schema = {
  type: 'object',
  properties: {
    value: { type: 'number', default: 5 },
    enabled: { type: 'boolean' },
  },
}

describe('invocation request templates', () => {
  it('exposes every object example from the algorithm card', () => {
    const templates = createRequestTemplates(schema, [
      { input: { value: 1 } },
      { input: { value: 2 } },
    ])

    expect(templates.slice(0, 2).map((template) => template.label)).toEqual([
      '算法卡示例 1',
      '算法卡示例 2',
    ])
    expect(templates[1].inputs).toEqual({ value: 2 })
  })

  it('adds type-safe Schema defaults when they differ from card examples', () => {
    const templates = createRequestTemplates(schema, [{ input: { value: 1 } }])

    expect(templates.at(-1)).toMatchObject({
      key: 'schema-defaults',
      source: 'schema',
      inputs: { value: 5, enabled: false },
    })
  })

  it('ignores non-object examples and avoids duplicate defaults', () => {
    const templates = createRequestTemplates(schema, [
      { input: 'invalid' },
      { input: { value: 5, enabled: false } },
    ])

    expect(templates).toHaveLength(1)
    expect(templates[0].label).toBe('算法卡示例 1')
  })
})
