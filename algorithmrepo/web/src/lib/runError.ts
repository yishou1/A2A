import { ApiError } from '../api/client'
import type { RunRequest, RunResponse } from '../api/contracts'

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === 'string' && value ? value : fallback
}

function fallbackCode(error: Error): string {
  if (!(error instanceof ApiError)) return 'NETWORK_ERROR'
  if (error.status === 502 || error.status === 503) return 'SERVICE_UNAVAILABLE'
  if (error.status === 504) return 'SERVICE_TIMEOUT'
  return `HTTP_${error.status}`
}

export function normalizeRunFailure(error: Error, request: RunRequest): RunResponse {
  const payload = asRecord(error instanceof ApiError ? error.payload : undefined)
  const nestedError = asRecord(payload.error)
  const code = stringValue(
    nestedError.code,
    stringValue(payload.error_code, error instanceof ApiError ? error.errorCode ?? fallbackCode(error) : fallbackCode(error)),
  )
  const message = stringValue(nestedError.message, stringValue(payload.message, error.message))

  const result: RunResponse = {
    ok: false,
    request_id: stringValue(payload.request_id, request.request_id),
    trace_id: stringValue(payload.trace_id, request.trace_id),
    algorithm_id: stringValue(payload.algorithm_id, request.algorithm_id),
    version: stringValue(payload.version, request.version),
    backend_type: request.backend_type,
    outputs: asRecord(payload.outputs),
    usage: asRecord(payload.usage),
    error: { code, message },
  }
  if (payload.function_execution && typeof payload.function_execution === 'object') {
    result.function_execution = payload.function_execution as RunResponse['function_execution']
  }
  return result
}

const retryableCodes = new Set(['NETWORK_ERROR', 'SERVICE_UNAVAILABLE', 'SERVICE_TIMEOUT'])

export function isRetryableRunFailure(result: RunResponse): boolean {
  return !result.ok && retryableCodes.has(result.error?.code ?? '')
}
