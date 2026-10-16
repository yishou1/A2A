import { Input, InputNumber, Select, Switch, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'

interface PropertySchema {
  type?: string | string[]
  title?: string
  description?: string
  enum?: unknown[]
  const?: unknown
  default?: unknown
  examples?: unknown[]
  properties?: Record<string, PropertySchema>
  required?: string[]
  items?: PropertySchema
  minItems?: number
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  minLength?: number
  format?: string
  oneOf?: PropertySchema[]
  anyOf?: PropertySchema[]
}

function primaryType(schema: PropertySchema): string {
  if (Array.isArray(schema.type)) return schema.type.find((value) => value !== 'null') ?? 'string'
  return schema.type ?? 'string'
}

export function createInitialInputs(
  schema: Record<string, unknown> | undefined,
  example?: unknown,
): Record<string, unknown> {
  if (example && typeof example === 'object' && !Array.isArray(example)) {
    return structuredClone(example as Record<string, unknown>)
  }
  const root = schema as PropertySchema | undefined
  const generated = createSchemaValue(root, true)
  return generated && typeof generated === 'object' && !Array.isArray(generated)
    ? generated as Record<string, unknown>
    : {}
}

function createSchemaValue(
  schema: PropertySchema | undefined,
  required: boolean,
  sampleIndex = 0,
  depth = 0,
): unknown {
  if (!schema || depth > 10) return null
  if (schema.default !== undefined) return structuredClone(schema.default)
  if (schema.examples?.length) return structuredClone(schema.examples[0])
  if (schema.const !== undefined) return structuredClone(schema.const)
  if (schema.enum?.length) return structuredClone(schema.enum[0])

  const variant = schema.oneOf?.[0] ?? schema.anyOf?.[0]
  if (variant) return createSchemaValue(variant, required, sampleIndex, depth + 1)

  const type = primaryType(schema)
  if (type === 'object') {
    const properties = schema.properties ?? {}
    const requiredNames = new Set(schema.required ?? [])
    return Object.fromEntries(Object.entries(properties).map(([name, definition]) => [
      name,
      createSchemaValue(definition, requiredNames.has(name), sampleIndex, depth + 1),
    ]))
  }
  if (type === 'array') {
    const minimumCount = Number.isFinite(schema.minItems) ? Math.max(0, Math.ceil(schema.minItems!)) : 0
    const count = Math.max(minimumCount, required ? 1 : 0)
    return Array.from({ length: count }, (_, index) =>
      createSchemaValue(schema.items, true, index, depth + 1))
  }
  if (type === 'boolean') return false
  if (type === 'number' || type === 'integer') {
    const lowerBound = Number.isFinite(schema.minimum)
      ? schema.minimum!
      : Number.isFinite(schema.exclusiveMinimum)
        ? schema.exclusiveMinimum! + (type === 'integer' ? 1 : 0.1)
        : 0
    const candidate = lowerBound + sampleIndex
    const bounded = Number.isFinite(schema.maximum) ? Math.min(candidate, schema.maximum!) : candidate
    return type === 'integer' ? Math.ceil(bounded) : bounded
  }
  if (type === 'null') return null
  if (schema.format === 'date-time') return '2026-01-01T00:00:00Z'
  if (schema.format === 'date') return '2026-01-01'
  if (schema.format === 'email') return 'example@example.com'
  if (schema.format === 'uri') return 'https://example.com/'
  const seed = sampleIndex ? `示例值-${sampleIndex + 1}` : '示例值'
  const minLength = Number.isFinite(schema.minLength) ? Math.max(0, Math.ceil(schema.minLength!)) : 0
  return seed.length >= minLength ? seed : seed.padEnd(minLength, '0')
}

function JsonValueField({ value, onChange }: { value: unknown; onChange: (value: unknown) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [error, setError] = useState<string>()
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value])
  const commit = () => {
    try {
      onChange(JSON.parse(text))
      setError(undefined)
    } catch (parseError) {
      setError(parseError instanceof Error ? parseError.message : 'JSON 格式错误')
    }
  }
  return (
    <>
      <Input.TextArea className="schema-json-input" rows={5} value={text} onChange={(event) => setText(event.target.value)} onBlur={commit} />
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
    </>
  )
}

export function SchemaInputForm({
  schema,
  values,
  onChange,
}: {
  schema: Record<string, unknown>
  values: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
}) {
  const properties = useMemo(
    () => (schema.properties ?? {}) as Record<string, PropertySchema>,
    [schema],
  )
  const required = new Set(Array.isArray(schema.required) ? schema.required as string[] : [])
  const setField = (name: string, value: unknown) => onChange({ ...values, [name]: value })

  if (!Object.keys(properties).length) {
    return <Typography.Text type="secondary">Schema 未定义顶层字段，请使用 JSON 模式。</Typography.Text>
  }

  return (
    <div className="schema-form">
      {Object.entries(properties).map(([name, definition]) => {
        const type = primaryType(definition)
        const value = values[name]
        let control
        if (definition.enum?.length) {
          control = <Select value={value} onChange={(next) => setField(name, next)} options={definition.enum.map((option) => ({ value: option, label: String(option) }))} />
        } else if (type === 'boolean') {
          control = <Switch checked={Boolean(value)} onChange={(next) => setField(name, next)} />
        } else if (type === 'number' || type === 'integer') {
          control = <InputNumber value={typeof value === 'number' ? value : undefined} precision={type === 'integer' ? 0 : undefined} onChange={(next) => setField(name, next)} />
        } else if (type === 'object' || type === 'array') {
          control = <JsonValueField value={value} onChange={(next) => setField(name, next)} />
        } else {
          control = <Input value={typeof value === 'string' ? value : ''} onChange={(event) => setField(name, event.target.value)} />
        }
        return (
          <label className="schema-field" key={name}>
            <span className="schema-field-label">
              {required.has(name) && <b>*</b>}{definition.title || name}
              <small>{name !== definition.title ? name : ''} · {type}</small>
            </span>
            {definition.description && <Typography.Text type="secondary">{definition.description}</Typography.Text>}
            {control}
          </label>
        )
      })}
    </div>
  )
}
