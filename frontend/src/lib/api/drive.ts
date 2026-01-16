import apiClient from './client'
import { DriveResolveResponse } from '@/lib/types/drive'

export const driveApi = {
  resolve: async (url: string, recursive = true) => {
    const response = await apiClient.post<DriveResolveResponse>('/drive/resolve', {
      url,
      recursive,
    })
    return response.data
  },
}
