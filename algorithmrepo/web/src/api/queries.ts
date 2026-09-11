import { useQuery } from '@tanstack/react-query'
import { api, type AlgorithmKey } from './client'

export const queryKeys = {
  health: ['health'] as const,
  algorithms: ['algorithms'] as const,
  algorithm: (key: AlgorithmKey) => ['algorithms', key] as const,
  schema: (key: AlgorithmKey, kind: 'input' | 'output') =>
    ['algorithms', key, 'schema', kind] as const,
  operationalFunctions: ['operational-functions'] as const,
  trace: (traceId: string) => ['traces', traceId] as const,
}

export function useOperationalFunctions() {
  return useQuery({
    queryKey: queryKeys.operationalFunctions,
    queryFn: api.operationalFunctions,
    staleTime: 60_000,
  })
}

export function useTrace(traceId: string) {
  return useQuery({
    queryKey: queryKeys.trace(traceId),
    queryFn: () => api.trace(traceId),
    enabled: Boolean(traceId),
    retry: false,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.health,
    refetchInterval: 15_000,
  })
}

export function useAlgorithms() {
  return useQuery({ queryKey: queryKeys.algorithms, queryFn: api.algorithms })
}

export function useAlgorithm(key: AlgorithmKey) {
  return useQuery({
    queryKey: queryKeys.algorithm(key),
    queryFn: () => api.algorithm(key),
  })
}

export function useAlgorithmSchema(key: AlgorithmKey, kind: 'input' | 'output') {
  return useQuery({
    queryKey: queryKeys.schema(key, kind),
    queryFn: () => api.schema(key, kind),
    enabled: Boolean(key.algorithmId && key.version && key.backendType),
  })
}
