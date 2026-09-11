import { describe, expect, it } from 'vitest'
import { validateSchemaInputs } from './schemaValidation'

describe('invocation JSON Schema validation', () => {
  const schema = {
    type: 'object',
    required: ['features'],
    properties: {
      features: {
        type: 'array',
        minItems: 1,
        maxItems: 1,
        items: {
          type: 'array',
          minItems: 6,
          maxItems: 6,
          items: { type: 'number' },
        },
      },
    },
    additionalProperties: false,
  }

  it('accepts a correctly shaped tensor-like input', () => {
    expect(validateSchemaInputs(schema, { features: [[0, 1, 0.2, 0, 2, 0]] })).toEqual([])
  })

  it('reports a flattened array before a network request is sent', () => {
    const errors = validateSchemaInputs(schema, { features: [0, 1, 0.2, 0, 2, 0] })
    expect(errors.length).toBeGreaterThan(0)
    expect(errors.join(' ')).toContain('items')
  })
})
