import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpenText, Eye, EyeOff, LogIn, ShieldCheck, UserRound } from 'lucide-react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { authQueryKey, useAuthStatus } from '../auth'
import type { AuthStatus } from '../types'

export function AuthPage() {
  const authQuery = useAuthStatus()
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const setupRequired = Boolean(authQuery.data?.setup_required)
  const requestedPath = (location.state as { from?: string } | null)?.from
  const destination = requestedPath?.startsWith('/') && requestedPath !== '/login'
    ? requestedPath
    : '/'

  const authMutation = useMutation({
    mutationFn: () => api<AuthStatus>(setupRequired ? '/auth/setup' : '/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.trim(), password }),
    }),
    onSuccess: (data) => {
      queryClient.setQueryData(authQueryKey, data)
      navigate(destination, { replace: true })
    },
    onError: (error: Error) => setFormError(error.message),
  })
  const guestMutation = useMutation({
    mutationFn: () => api<AuthStatus>('/auth/guest', { method: 'POST' }),
    onSuccess: (data) => {
      queryClient.setQueryData(authQueryKey, data)
      navigate('/', { replace: true })
    },
    onError: (error: Error) => setFormError(error.message),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setFormError(null)
    if (setupRequired && password !== confirmation) {
      setFormError('两次输入的密码不一致')
      return
    }
    authMutation.mutate()
  }

  if (authQuery.isLoading) return <div className="auth-loading-page"><span className="auth-loading-mark">aX</span><span>正在读取安全状态</span></div>
  if (authQuery.data?.authenticated) return <Navigate to={destination} replace />

  return (
    <main className="auth-page">
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-brand-row">
          <span className="brand-mark" aria-hidden="true">aX</span>
          <span><strong>ArxivLens</strong><small>张家豪的科研工作空间</small></span>
        </div>
        <div className="auth-heading">
          <span className="auth-heading-icon">{setupRequired ? <ShieldCheck size={22} /> : <LogIn size={22} />}</span>
          <div>
            <h1 id="auth-title">{setupRequired ? '创建管理员账号' : '登录张家豪的科研工作空间'}</h1>
            <p>{setupRequired ? '首次使用需要建立唯一管理员。账号用于保护论文、笔记和云模型配置。' : '请输入管理员账号，继续访问阅读端或后台管理。'}</p>
          </div>
        </div>

        {authQuery.isError && <div className="auth-error">认证状态读取失败：{(authQuery.error as Error).message}</div>}
        {formError && <div className="auth-error" role="alert">{formError}</div>}

        <form className="auth-form" onSubmit={submit}>
          <label className="field-stack">
            <span>管理员用户名</span>
            <span className="auth-input-wrap"><UserRound size={17} /><input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} required /></span>
          </label>
          <label className="field-stack">
            <span>{setupRequired ? '设置密码' : '密码'}</span>
            <span className="auth-input-wrap"><input type={showPassword ? 'text' : 'password'} autoComplete={setupRequired ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} minLength={setupRequired ? 14 : 1} maxLength={128} required /><button type="button" className="icon-button" onClick={() => setShowPassword((value) => !value)} title={showPassword ? '隐藏密码' : '显示密码'}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></span>
            {setupRequired && <small className="field-help">至少 14 个字符；推荐使用 20 个字符以上且不重复的密码短语。</small>}
          </label>
          {setupRequired && (
            <label className="field-stack">
              <span>确认密码</span>
              <span className="auth-input-wrap"><input type={showPassword ? 'text' : 'password'} autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} minLength={14} maxLength={128} required /></span>
            </label>
          )}
          <button className="primary-button auth-submit" type="submit" disabled={authMutation.isPending || authQuery.isError}>
            {setupRequired ? <ShieldCheck size={17} /> : <LogIn size={17} />}
            {authMutation.isPending ? '正在验证' : setupRequired ? '创建账号并进入' : '登录'}
          </button>
          {!setupRequired && (
            <>
              <div className="auth-divider"><span>或</span></div>
              <button
                className="secondary-button auth-guest-submit"
                type="button"
                onClick={() => guestMutation.mutate()}
                disabled={guestMutation.isPending || authQuery.isError}
              >
                <BookOpenText size={17} />
                {guestMutation.isPending ? '正在进入' : '访客只读进入'}
              </button>
              <small className="auth-guest-help">无需账号，可查看论文与已有解读，不能修改数据或使用云模型。</small>
            </>
          )}
        </form>

        <div className="auth-footnote">
          <ShieldCheck size={15} />
          <span>密码仅保存为加盐哈希，登录会话使用 HttpOnly Cookie。</span>
        </div>
      </section>
    </main>
  )
}
