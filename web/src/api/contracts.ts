export type BackendType = 'onnx' | 'python_http_service'
export type AlgorithmStatus = 'draft' | 'validated' | 'active' | 'disabled' | 'deleted'
export type DeploymentStatus = 'loading' | 'ready' | 'error' | 'unloaded'

export interface HealthResponse {
  ok: boolean
  status: string
  registry_path: string
  execution_log_path: string
  operational_function_catalog_path: string
  runner_cache_size: number
}

export interface DeploymentSummary {
  deploy_id: string
  node_id: string
  zone: string
  endpoint: string
  deploy_status: DeploymentStatus
  status_message: string
}

export interface OperationalFunctionMapping {
  function_id: string
  function_code: string
  function_name: string
  role: string
  coverage_level: string
}

export interface AlgorithmSummary {
  algorithm_id: string
  version: string
  display_name: string
  backend_type: BackendType
  registry_status: AlgorithmStatus
  card_status: AlgorithmStatus
  task_family: string
  capabilities: string[]
  modalities: { input: string[]; output: string[] }
  deployments: DeploymentSummary[]
  ready_endpoints: Array<{
    deploy_id: string
    node_id: string
    zone: string
    endpoint: string
  }>
  operational_functions: OperationalFunctionMapping[]
  agent_card: {
    summary: string
    when_to_use: string[]
    when_not_to_use: string[]
    input_description: string
    output_description: string
    examples: Array<{ input: unknown; output: unknown }>
  }
  constraints: Record<string, unknown>
  performance: Record<string, unknown>
  resource_requirements: Record<string, unknown>
  model_profile: Record<string, unknown>
  safety: Record<string, unknown>
  input_schema_summary: Record<string, unknown>
  output_schema_summary: Record<string, unknown>
}

export interface AlgorithmListResponse {
  ok: boolean
  count: number
  filter: Record<string, unknown>
  algorithms: AlgorithmSummary[]
}

export interface DeploymentDetail extends DeploymentSummary {
  health_endpoint: string
  local_model_path: string
  deployed_at: string
  updated_at: string
}

export interface AlgorithmDetailResponse {
  ok: boolean
  entry: {
    key: {
      algorithm_id: string
      version: string
      backend_type: BackendType
    }
    status: AlgorithmStatus
    card: Record<string, unknown> & {
      status: AlgorithmStatus
      display_name: string
    }
    input_schema_summary: Record<string, unknown>
    output_schema_summary: Record<string, unknown>
    deployments: DeploymentDetail[]
  }
  agent_view: AlgorithmSummary
}

export interface ApiErrorBody {
  ok?: false
  error_code?: string
  message?: string
}

export interface AlgorithmMutationResponse {
  ok: boolean
  algorithm_id: string
  version: string
  backend_type: BackendType
  status: AlgorithmStatus
  agent_view: AlgorithmSummary
}

export interface ModelLoadResponse {
  ok: boolean
  algorithm_id: string
  version: string
  backend_type: BackendType
  deploy_id?: string
  load_status: 'loaded' | 'unloaded' | 'already_loaded' | 'not_found' | 'error'
  message: string
  health?: { ok: boolean; status: string; message: string }
}

export interface OperationalFunction {
  function_id: string
  function_code: string
  function_name: string
  ooda_phase?: string
  f2t2ea_stage?: string
  execution_boundary?: string
}

export interface OperationalFunctionsResponse {
  ok: boolean
  schema_version: string
  count: number
  functions: OperationalFunction[]
}

export interface FunctionExecution {
  function_id: string
  function_code: string
  function_name: string
  role: string
  coverage_level: string
  mapping_source: string
  execution_status: string
  workflow_instance_id: string
  step_instance_id: string
  matched: boolean
}

export interface RunRequest {
  request_id: string
  trace_id: string
  algorithm_id: string
  version: string
  backend_type: BackendType
  deploy_id?: string
  inputs: Record<string, unknown>
  params: Record<string, unknown>
  function_context?: {
    function_id?: string
    function_code?: string
    workflow_instance_id?: string
    step_instance_id?: string
  }
}

export interface RunResponse {
  ok: boolean
  request_id: string
  trace_id: string
  algorithm_id: string
  version: string
  backend_type: BackendType
  outputs: Record<string, unknown>
  usage: Record<string, unknown>
  error: null | { code: string; message: string }
  function_execution?: FunctionExecution
}

export interface TraceEvent {
  sequence: number
  request_id: string
  trace_id: string
  algorithm_id: string
  version: string
  backend_type: BackendType
  status: string
  latency_ms: number
  error_code: string | null
  recorded_at: string
  function_execution: FunctionExecution
}

export interface TraceResponse {
  ok: boolean
  trace_id: string
  count: number
  function_executions: TraceEvent[]
}
