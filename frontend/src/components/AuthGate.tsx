import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { authQueryKey, useAuthStatus } from '../auth'

export function AuthGate({ adminOnly = false }: { adminOnly?: boolean }) {
  const authQuery = useAuthStatus()
  const queryClient = useQueryClient()
  const location = useLocation()

  useEffect(() => {
    const handleUnauthorized = () => {
      void queryClient.invalidateQueries({ queryKey: authQueryKey })
    }
    window.addEventListener('arxivlens:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('arxivlens:unauthorized', handleUnauthorized)
  }, [queryClient])

  if (authQuery.isLoading) {
    return <div className="auth-loading-page"><span className="auth-loading-mark">aX</span><span>正在验证安全会话</span></div>
  }
  if (authQuery.isError) {
    return (
      <div className="auth-loading-page error-state">
        <strong>认证服务暂时不可用</strong>
        <span>{(authQuery.error as Error).message}</span>
        <button className="secondary-button" onClick={() => void authQuery.refetch()}>重新连接</button>
      </div>
    )
  }
  if (!authQuery.data?.authenticated) {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />
  }
  if (adminOnly && authQuery.data.role !== 'admin') {
    return <Navigate to="/" replace />
  }
  return <Outlet />
}
