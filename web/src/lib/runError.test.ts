import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import type { RunRequest } from '../api/contracts'
import { isRetryableRunFailure, normalizeRunFailure } from './runError'

const request: RunRequest = {
  request_id: 'req-test',
  trace_id: 'trace-test',
  algorithm_id: 'service-test',
  version: '1.0.0',
  backend_type: 'python_http_service',
  inputs: {},
  params: {},
}

describe('run error normalization', () => {
  it('normalizes a management-style 503 response', () => {
    const result = normalizeRunFailure(new ApiError(
      'Python service is unavailable.',
      503,
      'SERVICE_UNAVAILABLE',
      { ok: false, error_code: 'SERVICE_UNAVAILABLE', message: 'Python service is unavailable.' },
    ), request)

    expect(result.ok).toBe(false)
    expect(result.error).toEqual({
      code: 'SERVICE_UNAVAILABLE',
      message: 'Python service is unavailable.',
    })
    expect(result.request_id).toBe('req-test')
    expect(result.trace_id).toBe('trace-test')
    expect(isRetryableRunFailure(result)).toBe(true)
  })

  it('preserves a run-style nested timeout error', () => {
    const result = normalizeRunFailure(new ApiError(
      'Timed out.',
      504,
      'SERVICE_TIMEOUT',
      {
        request_id: 'req-backend',
        trace_id: 'trace-backend',
        error: { code: 'SERVICE_TIMEOUT', message: 'Prediction timed out after 3000 ms.' },
        usage: { latency_ms: 3000 },
      },
    ), request)

    expect(result.error?.code).toBe('SERVICE_TIMEOUT')
    expect(result.error?.message).toContain('3000 ms')
    expect(result.request_id).toBe('req-backend')
    expect(result.usage).toEqual({ latency_ms: 3000 })
    expect(isRetryableRunFailure(result)).toBe(true)
  })

  it('classifies a transport failure as a retryable network error', () => {
    const result = normalizeRunFailure(new TypeError('Failed to fetch'), request)

    expect(result.error).toEqual({ code: 'NETWORK_ERROR', message: 'Failed to fetch' })
    expect(result.request_id).toBe('req-test')
    expect(isRetryableRunFailure(result)).toBe(true)
  })

  it('derives an HTTP error code and does not retry a validation failure', () => {
    const result = normalizeRunFailure(new ApiError('Invalid request.', 400), request)

    expect(result.error?.code).toBe('HTTP_400')
    expect(isRetryableRunFailure(result)).toBe(false)
  })
})
