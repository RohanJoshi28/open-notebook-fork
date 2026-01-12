import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { infraApi, DbVmStatusResponse } from '@/lib/api/infra'
import { QUERY_KEYS } from '@/lib/api/query-client'

const STATUS_STORAGE_KEY = 'db-vm-last-status'
const MAX_CACHE_AGE_MS = 60_000 // 1 minute; after that we force a live check

export function useDbVmStatus(): UseQueryResult<DbVmStatusResponse> {
  const queryClient = useQueryClient()

  const getCachedStatus = (): DbVmStatusResponse | undefined => {
    // Prefer react-query cache
    const cached = queryClient.getQueryData<DbVmStatusResponse>(QUERY_KEYS.dbVmStatus)
    if (cached) return cached
    // Fallback to localStorage so we avoid a flash on first paint
    if (typeof window !== 'undefined') {
      const raw = window.localStorage.getItem(STATUS_STORAGE_KEY)
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as DbVmStatusResponse
          // If the cached value says "running" but is old, ignore it so we don't hide the gate
          const ageMs = Date.now() - new Date(parsed.checkedAt).getTime()
          if (parsed.status === 'running' && ageMs > MAX_CACHE_AGE_MS) {
            return undefined
          }
          return parsed
        } catch {
          // ignore parse errors
        }
      }
    }
    return undefined
  }

  const initial = getCachedStatus()
  const [hasValidated, setHasValidated] = useState(false)

  const query = useQuery<DbVmStatusResponse>({
    queryKey: QUERY_KEYS.dbVmStatus,
    queryFn: () => infraApi.status(),
    ...(initial ? { initialData: initial } : {}),
    placeholderData: (prev) => prev ?? initial,
    refetchInterval: (query) => {
      // Poll every 10s when not running to detect transitions quickly
      const status = (query.state.data as DbVmStatusResponse | undefined)?.status
      if (!status || status === 'running') return false
      return 10_000
    },
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // Always verify on mount so stale "running" cache can't bypass the gate
    refetchOnMount: 'always',
    staleTime: 5_000,
    gcTime: 5 * 60 * 1000,
  })

  // Track when we've completed a live check (used by the gate to avoid flashing login)
  useEffect(() => {
    if (query.isFetched && !query.isFetching && !hasValidated) {
      setHasValidated(true)
    }
  }, [query.isFetched, query.isFetching, hasValidated])

  // Persist last known status to avoid flashes on next mount
  useEffect(() => {
    if (query.data && typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(STATUS_STORAGE_KEY, JSON.stringify(query.data))
      } catch {
        // ignore storage failures
      }
    }
  }, [query.data])

  return query
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
