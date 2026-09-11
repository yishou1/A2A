import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020'

const ajv = new Ajv2020({ allErrors: true, strict: false })
const validators = new WeakMap<object, ValidateFunction>()

export function validateSchemaInputs(
  schema: Record<string, unknown>,
  inputs: Record<string, unknown>,
): string[] {
  let validate = validators.get(schema)
  if (!validate) {
    validate = ajv.compile(schema)
    validators.set(schema, validate)
  }
  if (validate(inputs)) return []
  return (validate.errors ?? []).map((error) => {
    const location = error.instancePath || '/'
    return `${location} ${error.message ?? '不符合 Schema'}`
  })
}
