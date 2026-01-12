import apiClient from '@/lib/api/client'

export interface DbVmStatusResponse {
  status: 'running' | 'stopped' | 'suspended' | 'starting' | 'unknown' | string
  rawStatus: string
  checkedAt: string
  config: {
    project: string
    zone: string
    name: string
    estimatedStartSeconds?: number
  }
}

export interface DbVmActionResponse {
  requestedAt: string
  previousStatus: string
  operation: unknown
  action?: 'suspend' | 'stop'
  config: {
    project: string
    zone: string
    name: string
    estimatedStartSeconds?: number
  }
}

export const infraApi = {
  async status() {
    const { data } = await apiClient.get<DbVmStatusResponse>('/infra/db-vm/status')
    return data
  },
  async start() {
    const { data } = await apiClient.post<DbVmActionResponse>('/infra/db-vm/start')
    return data
  },
  async stop() {
    const { data } = await apiClient.post<DbVmActionResponse>('/infra/db-vm/stop')
    return data
  },
}
