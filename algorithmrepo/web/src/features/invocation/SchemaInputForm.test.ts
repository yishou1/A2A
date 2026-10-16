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

  it('recursively creates populated values that satisfy nested array structure', () => {
    expect(createInitialInputs({
      type: 'object',
      required: ['track', 'features'],
      properties: {
        track: {
          type: 'object',
          required: ['history'],
          properties: {
            phase: { type: 'string', enum: ['strike', 'assault'] },
            history: {
              type: 'array',
              minItems: 2,
              items: {
                type: 'object',
                required: ['t', 'x'],
                properties: {
                  t: { type: 'number', minimum: 0 },
                  x: { type: 'number' },
                },
              },
            },
          },
        },
        features: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'array',
            minItems: 3,
            items: { type: 'number', minimum: 0, maximum: 1 },
          },
        },
      },
    })).toEqual({
      track: {
        phase: 'strike',
        history: [
          { t: 0, x: 0 },
          { t: 1, x: 1 },
        ],
      },
      features: [[0, 1, 1]],
    })
  })
})
