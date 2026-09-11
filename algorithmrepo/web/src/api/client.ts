import type {
  AlgorithmDetailResponse,
  AlgorithmListResponse,
  AlgorithmMutationResponse,
  BackendType,
  HealthResponse,
  ModelLoadResponse,
  OperationalFunctionsResponse,
  RunRequest,
  RunResponse,
  TraceResponse,
} from './contracts'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly errorCode?: string,
    readonly payload?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const apiPrefix = (
  import.meta.env.VITE_ALGOLIB_API_PREFIX?.trim() ||
  (import.meta.env.DEV ? '/api' : '/algolib-api')
).replace(/\/+$/, '')

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiPrefix}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json, application/schema+json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  const contentType = response.headers.get('content-type') ?? ''
  const payload = contentType.includes('json') ? await response.json() : await response.text()
  if (!response.ok) {
    const body = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {}
    throw new ApiError(
      typeof body.message === 'string' ? body.message : `请求失败（HTTP ${response.status}）`,
      response.status,
      typeof body.error_code === 'string' ? body.error_code : undefined,
      payload,
    )
  }
  return payload as T
}

export interface AlgorithmKey {
  algorithmId: string
  version: string
  backendType: BackendType
}

export function algorithmPath(key: AlgorithmKey): string {
  return `/algorithms/${encodeURIComponent(key.algorithmId)}/${encodeURIComponent(key.version)}/${encodeURIComponent(key.backendType)}`
}

export const api = {
  health: () => requestJson<HealthResponse>('/health'),
  algorithms: () => requestJson<AlgorithmListResponse>('/algorithms?active_only=false'),
  algorithm: (key: AlgorithmKey) => requestJson<AlgorithmDetailResponse>(algorithmPath(key)),
  schema: (key: AlgorithmKey, kind: 'input' | 'output') =>
    requestJson<Record<string, unknown>>(`${algorithmPath(key)}/schemas/${kind}`),
  registerAlgorithm: (packageOrCardPath: string) =>
    requestJson<AlgorithmMutationResponse>('/algorithms/register', {
      method: 'POST',
      body: JSON.stringify({ package_or_card_path: packageOrCardPath }),
    }),
  lifecycle: (key: AlgorithmKey, action: 'validate' | 'activate' | 'disable') =>
    requestJson<AlgorithmMutationResponse>(`${algorithmPath(key)}/${action}`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  deleteAlgorithm: (key: AlgorithmKey) =>
    requestJson<AlgorithmMutationResponse>(algorithmPath(key), { method: 'DELETE' }),
  addDeployment: (key: AlgorithmKey, deployment: Record<string, unknown>) =>
    requestJson<AlgorithmDetailResponse>(`${algorithmPath(key)}/deployments`, {
      method: 'POST',
      body: JSON.stringify(deployment),
    }),
  updateDeploymentStatus: (
    key: AlgorithmKey,
    deployId: string,
    payload: { deploy_status: string; status_message?: string },
  ) => requestJson<AlgorithmDetailResponse>(
    `${algorithmPath(key)}/deployments/${encodeURIComponent(deployId)}/status`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  ),
  deleteDeployment: (key: AlgorithmKey, deployId: string) =>
    requestJson<AlgorithmDetailResponse>(
      `${algorithmPath(key)}/deployments/${encodeURIComponent(deployId)}`,
      { method: 'DELETE' },
    ),
  load: (key: AlgorithmKey, payload: Record<string, unknown>) =>
    requestJson<ModelLoadResponse>('/load', {
      method: 'POST',
      body: JSON.stringify({
        algorithm_id: key.algorithmId,
        version: key.version,
        backend_type: key.backendType,
        ...payload,
      }),
    }),
  unload: (key: AlgorithmKey, deployId: string) =>
    requestJson<ModelLoadResponse>('/unload', {
      method: 'POST',
      body: JSON.stringify({
        algorithm_id: key.algorithmId,
        version: key.version,
        backend_type: key.backendType,
        deploy_id: deployId,
      }),
    }),
  operationalFunctions: () =>
    requestJson<OperationalFunctionsResponse>('/operational-functions'),
  run: (request: RunRequest) =>
    requestJson<RunResponse>('/run', {
      method: 'POST',
      body: JSON.stringify(request),
    }),
  trace: (traceId: string) =>
    requestJson<TraceResponse>(
      `/traces/${encodeURIComponent(traceId)}/function-executions`,
    ),
}
