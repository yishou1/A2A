import { describe, expect, it } from 'vitest'
import { createInitialInputs } from './SchemaInputForm'

describe('schema-driven invocation inputs', () => {
  const schema = {
    type: 'object',
    properties: {
      text: { type: 'string', default: 'hello' },
      count: { type: 'integer' },
      enabled: { type: 'boolean' },
      points: { type: 'array' },
      metadata: { type: 'object' },
    },
  }

  it('uses an algorithm card example when one is available', () => {
    expect(createInitialInputs(schema, { text: 'example', count: 3 })).toEqual({
      text: 'example',
      count: 3,
    })
  })

  it('creates type-safe editable defaults from JSON Schema', () => {
    expect(createInitialInputs(schema)).toEqual({
      text: 'hello',
      count: 0,
      enabled: false,
      points: [],
      metadata: {},
    })
  })
})
