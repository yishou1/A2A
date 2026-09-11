import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from './queries'

export function useManagementRefresh() {
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.algorithms }),
      queryClient.invalidateQueries({ queryKey: queryKeys.health }),
    ])
  }
}
