import { Input, InputNumber, Select, Switch, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'

interface PropertySchema {
  type?: string | string[]
  title?: string
  description?: string
  enum?: unknown[]
  default?: unknown
  examples?: unknown[]
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
  const properties = (schema?.properties ?? {}) as Record<string, PropertySchema>
  return Object.fromEntries(Object.entries(properties).map(([name, definition]) => {
    if (definition.default !== undefined) return [name, definition.default]
    if (definition.examples?.length) return [name, definition.examples[0]]
    const type = primaryType(definition)
    if (type === 'boolean') return [name, false]
    if (type === 'number' || type === 'integer') return [name, 0]
    if (type === 'array') return [name, []]
    if (type === 'object') return [name, {}]
    return [name, '']
  }))
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
