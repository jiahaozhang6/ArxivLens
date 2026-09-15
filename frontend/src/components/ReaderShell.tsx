import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogOut, Settings2 } from 'lucide-react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api, formatDateTime } from '../api'
import { authQueryKey } from '../auth'
import type { SystemStatus } from '../types'

export function ReaderShell() {
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const statusQuery = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api<SystemStatus>('/system/status'),
    refetchInterval: (query) => (query.state.data?.last_run?.status === 'running' ? 5000 : 30_000),
  })
  const system = statusQuery.data
  const logoutMutation = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      queryClient.setQueryData(authQueryKey, {
        setup_required: false,
        authenticated: false,
        user: null,
        session_expires_at: null,
      })
      navigate('/login', { replace: true })
    },
    onError: (error: Error) => setNotice(error.message),
  })

  return (
    <div className="reader-site">
      <header className="reader-site-header">
        <NavLink to="/" className="reader-brand" aria-label="ArxivLens 科研阅读首页">
          <span className="brand-mark" aria-hidden="true">aX</span>
          <span><strong>ArxivLens</strong><small>科研阅读</small></span>
        </NavLink>
        <div className="reader-header-actions">
          <span className="reader-last-sync">{system?.last_run ? `上次同步 ${formatDateTime(system.last_run.started_at)}` : '尚未同步'}</span>
          <NavLink to="/admin/daily" className="icon-text-button">
            <Settings2 size={16} />
            <span className="reader-action-label">后台管理</span>
          </NavLink>
          <button className="icon-button reader-logout-button" onClick={() => logoutMutation.mutate()} disabled={logoutMutation.isPending} title="退出登录"><LogOut size={17} /></button>
        </div>
      </header>

      {system && !system.ready && (
        <div className="reader-setup-banner">
          <span>阅读服务尚未配置完成。</span>
          <NavLink to="/admin/topics">进入后台配置</NavLink>
        </div>
      )}

      <main className="reader-site-main"><Outlet /></main>
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
