import { createInitialInputs } from './SchemaInputForm'

export interface RequestTemplate {
  key: string
  label: string
  source: 'algorithm_card' | 'schema'
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
    const schemaInputs = createInitialInputs(schema)
    const fingerprint = JSON.stringify(schemaInputs)
    if (!templates.some((template) => JSON.stringify(template.inputs) === fingerprint)) {
      templates.push({
        key: 'schema-defaults',
        label: 'Schema 缺省输入',
        source: 'schema',
        inputs: schemaInputs,
        params: {},
      })
    }
  }

  return templates
}
