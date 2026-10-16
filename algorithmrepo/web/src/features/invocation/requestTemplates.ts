import { createInitialInputs } from './SchemaInputForm'

export interface RequestTemplate {
  key: string
  label: string
  source: 'algorithm_card' | 'schema_example' | 'schema_generated'
  inputs: Record<string, unknown>
  params: Record<string, unknown>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function createRequestTemplates(
  schema: Record<string, unknown> | undefined,
  examples: Array<{ input: unknown }> = [],
): RequestTemplate[] {
  const templates: RequestTemplate[] = examples
    .filter((example) => isRecord(example.input))
    .map((example, index) => ({
      key: `algorithm-card-${index + 1}`,
      label: `算法卡示例 ${index + 1}`,
      source: 'algorithm_card',
      inputs: structuredClone(example.input as Record<string, unknown>),
      params: {},
    }))

  if (schema) {
    const schemaExamples = Array.isArray(schema.examples) ? schema.examples : []
    schemaExamples.filter(isRecord).forEach((example, index) => {
      if (!templates.some((template) => JSON.stringify(template.inputs) === JSON.stringify(example))) {
        templates.push({
          key: `schema-example-${index + 1}`,
          label: `Schema 示例 ${index + 1}`,
          source: 'schema_example',
          inputs: structuredClone(example),
          params: {},
        })
      }
    })
    const schemaInputs = createInitialInputs(schema)
    const fingerprint = JSON.stringify(schemaInputs)
    if (!templates.some((template) => JSON.stringify(template.inputs) === fingerprint)) {
      templates.push({
        key: 'schema-generated',
        label: 'Schema 自动生成输入',
        source: 'schema_generated',
        inputs: schemaInputs,
        params: {},
      })
    }
  }

  return templates
}
