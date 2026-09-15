import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpenText,
  BrainCircuit,
  CalendarClock,
  History,
  LibraryBig,
  ListChecks,
  LogOut,
  Menu,
  Play,
  Send,
  Settings,
  Tags,
  UserRound,
  X,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, formatDateTime } from '../api'
import { authQueryKey, useAuthStatus } from '../auth'
import type { DailyEmailResult, SystemStatus } from '../types'

const navigationSections = [
  {
    label: '后台管理',
    items: [
      { to: '/admin/daily', label: '每日论文管理', icon: ListChecks },
      { to: '/admin/library', label: '论文数据库', icon: LibraryBig },
      { to: '/admin/topics', label: '主题订阅', icon: Tags },
      { to: '/admin/models', label: '云模型', icon: BrainCircuit },
      { to: '/admin/settings', label: '计划与邮件', icon: Settings },
      { to: '/admin/runs', label: '运行记录', icon: History },
    ],
  },
]

const pageTitles: Record<string, string> = {
  '/admin/daily': '每日论文管理',
  '/admin/library': '论文数据库与历史回溯',
  '/admin/topics': '主题订阅',
  '/admin/models': '云模型配置',
  '/admin/settings': '计划与邮件',
  '/admin/runs': '运行记录',
}

function scheduleStatusText(system: SystemStatus | undefined) {
  if (!system?.schedule_enabled || system.schedule_state === 'disabled') return '\u6bcf\u65e5\u8ba1\u5212\u672a\u542f\u7528'
  if (system.schedule_state === 'pending') return '\u4eca\u65e5 ' + system.schedule_time + ' \u6267\u884c'
  if (system.schedule_state === 'starting') return '\u4eca\u65e5\u8ba1\u5212\u6b63\u5728\u542f\u52a8'
  if (system.schedule_state === 'running') return '\u4eca\u65e5\u8ba1\u5212\u6b63\u5728\u8fd0\u884c'
  if (system.schedule_state === 'overdue') return '\u4eca\u65e5 ' + system.schedule_time + ' \u5c1a\u672a\u6267\u884c'
  if (system.schedule_state === 'failed') return '\u4eca\u65e5\u8ba1\u5212\u6267\u884c\u5931\u8d25'
  if (system.schedule_state === 'partial') return '\u4eca\u65e5\u8ba1\u5212\u90e8\u5206\u5b8c\u6210'
  return '\u4eca\u65e5\u8ba1\u5212\u5df2\u6267\u884c'
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const authQuery = useAuthStatus()
  const statusQuery = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api<SystemStatus>('/system/status'),
    refetchInterval: (query) => (query.state.data?.last_run?.status === 'running' ? 5000 : 30_000),
  })
  const runMutation = useMutation({
    mutationFn: () =>
      api<{ run_id: number }>('/jobs/runs', {
        method: 'POST',
        body: JSON.stringify({ topic_ids: null, send_email: false }),
      }),
    onSuccess: (data) => {
      setNotice('任务 #' + data.run_id + ' 已启动')
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const sendTodayEmailMutation = useMutation({
    mutationFn: () => api<DailyEmailResult>('/settings/email/actions/send-today', {
      method: 'POST',
    }),
    onSuccess: (data) => {
      setNotice(`${data.day} 邮件已发送：${data.papers} 篇论文，${data.recipients} 个收件人`)
    },
    onError: (error: Error) => setNotice('发送失败：' + error.message),
  })
  const system = statusQuery.data
  const running = system?.last_run?.status === 'running' || runMutation.isPending
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
    <div className="app-shell">
      <aside className={'sidebar ' + (mobileOpen ? 'sidebar-open' : '')}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">aX</div>
          <div>
            <strong>ArxivLens</strong>
            <span>管理后台</span>
          </div>
          <button className="icon-button mobile-only" onClick={() => setMobileOpen(false)} title="关闭导航">
            <X size={20} />
          </button>
        </div>
        <NavLink to="/" end className="sidebar-reader-link" onClick={() => setMobileOpen(false)}>
          <BookOpenText size={17} />
          <span>返回科研阅读端</span>
        </NavLink>
        <nav className="primary-nav" aria-label="主导航">
          {navigationSections.map((section) => (
            <div className="nav-section" key={section.label}>
              <span className="nav-section-label">{section.label}</span>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={() => setMobileOpen(false)}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  <item.icon size={18} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-status">
          <div className="sidebar-account">
            <UserRound size={16} />
            <span><small>管理员</small><strong>{authQuery.data?.user?.username ?? '-'}</strong></span>
            <button className="icon-button" onClick={() => logoutMutation.mutate()} disabled={logoutMutation.isPending} title="退出登录"><LogOut size={16} /></button>
          </div>
          <div className="status-line">
            <span className={'status-dot ' + (system?.schedule_enabled && system.schedule_state !== 'overdue' && system.schedule_state !== 'failed' ? 'online' : '')} />
            <span>{scheduleStatusText(system)}</span>
          </div>
          <div className="status-line muted">
            <CalendarClock size={14} />
            <span>
              {system?.last_run ? '上次 ' + formatDateTime(system.last_run.started_at) : '尚未运行'}
            </span>
          </div>
        </div>
      </aside>

      {mobileOpen && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileOpen(false)} />}

      <div className="workspace">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button mobile-only" onClick={() => setMobileOpen(true)} title="打开导航">
              <Menu size={20} />
            </button>
            <h1>{pageTitles[location.pathname] ?? 'ArxivLens'}</h1>
          </div>
          <div className="topbar-actions">
            {location.pathname === '/admin/daily' && (
              <button
                className="secondary-button topbar-email-button"
                onClick={() => sendTodayEmailMutation.mutate()}
                disabled={running || sendTodayEmailMutation.isPending || !system?.email_enabled}
                title={!system?.email_enabled ? '请先在计划与邮件中启用邮件摘要' : '发送今日已完成的论文解读'}
              >
                <Send size={16} />
                <span className="topbar-email-label">{sendTodayEmailMutation.isPending ? '发送中' : '发送今日邮件'}</span>
              </button>
            )}
            <button
              className="primary-button"
              onClick={() => runMutation.mutate()}
              disabled={running || !system?.ready}
              title={!system?.ready ? '请先添加主题和云模型' : '立即抓取并解读'}
            >
              <Play size={16} fill="currentColor" />
              <span className="topbar-run-label">{running ? '运行中' : '立即更新'}</span>
            </button>
          </div>
        </header>

        {system && !system.ready && (
          <div className="setup-banner">
            <strong>完成初始配置后即可开始。</strong>
            <span>
              {system.enabled_profiles === 0 ? '先添加一个云模型；' : ''}
              {system.enabled_topics === 0 ? '再创建至少一个检索主题。' : ''}
              {system.topics_without_model > 0
                ? system.topics_without_model + ' 个主题没有可用模型，请设置默认模型或为主题单独绑定。'
                : ''}
            </span>
          </div>
        )}

        <main className="main-content">
          <Outlet />
        </main>
      </div>

      {notice && (
        <button className="toast" onClick={() => setNotice(null)} aria-label="关闭提示">
          {notice}
        </button>
      )}
    </div>
  )
}
