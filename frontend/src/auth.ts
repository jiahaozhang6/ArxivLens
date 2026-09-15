import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { AuthStatus } from './types'

export const authQueryKey = ['auth-status'] as const

export function useAuthStatus() {
  return useQuery({
    queryKey: authQueryKey,
    queryFn: () => api<AuthStatus>('/auth/status'),
    staleTime: 30_000,
    retry: false,
  })
}
