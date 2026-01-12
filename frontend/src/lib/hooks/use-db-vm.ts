import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { infraApi, DbVmStatusResponse } from '@/lib/api/infra'
import { QUERY_KEYS } from '@/lib/api/query-client'

export function useDbVmStatus() {
  return useQuery({
    queryKey: QUERY_KEYS.dbVmStatus,
    queryFn: () => infraApi.status(),
    refetchInterval: (query) => {
      // Poll every 10s when not running to detect transitions quickly
      const status = (query.state.data as DbVmStatusResponse | undefined)?.status
      if (!status || status === 'running') return false
      return 10_000
    },
    staleTime: 5_000,
  })
}

export function useStartDbVm() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => infraApi.start(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.dbVmStatus })
    },
  })
}

export function useStopDbVm() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => infraApi.stop(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.dbVmStatus })
    },
  })
}

export type DbVmStatus = DbVmStatusResponse['status']
