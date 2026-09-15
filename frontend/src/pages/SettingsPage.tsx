import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock3, KeyRound, MailCheck, RefreshCw, Save, Send, ShieldCheck } from 'lucide-react'
import { api } from '../api'
import { authQueryKey, useAuthStatus } from '../auth'
import type { AppSettings, AuthStatus, EmailSettings, NetworkTimeStatus, ScheduleSettings } from '../types'

const timezones = [
  'Asia/Shanghai',
  'Asia/Hong_Kong',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'UTC',
]

function formatTimeValue(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function humanScheduleTime(hour: number, minute: number) {
  const period = hour < 6 ? '凌晨' : hour < 9 ? '早上' : hour < 12 ? '上午' : hour < 14 ? '中午' : hour < 18 ? '下午' : '晚上'
  return `每天${period} ${formatTimeValue(hour, minute)}`
}

function formatZonedTime(value: string, timezone: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone,
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(value))
}

function formatRelativeTime(current: string, target: string) {
  const totalMinutes = Math.max(0, Math.ceil((new Date(target).getTime() - new Date(current).getTime()) / 60_000))
  const days = Math.floor(totalMinutes / 1440)
  const hours = Math.floor((totalMinutes % 1440) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) return `约 ${days} 天 ${hours} 小时后`
  if (hours > 0) return `约 ${hours} 小时 ${minutes} 分钟后`
  return totalMinutes > 0 ? `约 ${totalMinutes} 分钟后` : '即将执行'
}

function formatClockOffset(offset: number) {
  if (Math.abs(offset) < 0.05) return '小于 0.05 秒'
  return `${offset > 0 ? '+' : ''}${offset.toFixed(2)} 秒`
}

export function SettingsPage() {
  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api<AppSettings>('/settings'),
  })
  if (settingsQuery.isLoading || !settingsQuery.data) return <div className="table-loading page-loading" />
  if (settingsQuery.isError) return <div className="empty-state error-state">{(settingsQuery.error as Error).message}</div>
  const key = settingsQuery.data.schedule.updated_at + settingsQuery.data.email.updated_at
  return <SettingsForm key={key} initial={settingsQuery.data} />
}

function SettingsForm({ initial }: { initial: AppSettings }) {
  const [schedule, setSchedule] = useState<ScheduleSettings>(initial.schedule)
  const [email, setEmail] = useState<EmailSettings>(initial.email)
  const [password, setPassword] = useState('')
  const [recipients, setRecipients] = useState(initial.email.recipients.join('\n'))
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const authQuery = useAuthStatus()
  const networkTimeQuery = useQuery({
    queryKey: ['network-time'],
    queryFn: () => api<NetworkTimeStatus>('/system/time'),
    refetchInterval: 60_000,
  })

  const buildEmailPayload = () => {
    const payload: Record<string, unknown> = {
      ...email,
      recipients: recipients.split(/[\n,;]/).map((item) => item.trim()).filter(Boolean),
    }
    delete payload.has_password
    delete payload.updated_at
    if (password) payload.password = password
    return payload
  }

  const scheduleMutation = useMutation({
    mutationFn: () =>
      api('/settings/schedule', {
        method: 'PUT',
        body: JSON.stringify(schedule),
      }),
    onSuccess: () => {
      setNotice('每日计划已保存，调度进程会在一分钟内刷新')
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
      void queryClient.invalidateQueries({ queryKey: ['network-time'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const emailMutation = useMutation({
    mutationFn: () => api('/settings/email', {
      method: 'PUT',
      body: JSON.stringify(buildEmailPayload()),
    }),
    onSuccess: () => {
      setPassword('')
      setNotice('邮件配置已保存')
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const testEmailMutation = useMutation({
    mutationFn: () => api('/settings/email/actions/test', {
      method: 'POST',
      body: JSON.stringify(buildEmailPayload()),
    }),
    onSuccess: () => setNotice('测试邮件已发送；当前表单尚未自动保存'),
    onError: (error: Error) => setNotice('发送失败：' + error.message),
  })
  const timeSyncMutation = useMutation({
    mutationFn: () => api<NetworkTimeStatus>('/system/time/actions/sync', { method: 'POST' }),
    onSuccess: (data) => {
      queryClient.setQueryData(['network-time'], data)
      setNotice(data.synchronized ? '网络时间校准完成' : '网络校时不可用，当前使用系统时间')
    },
    onError: (error: Error) => setNotice('网络校时失败：' + error.message),
  })
  const passwordMutation = useMutation({
    mutationFn: () => {
      if (newPassword !== confirmPassword) throw new Error('两次输入的新密码不一致')
      return api<AuthStatus>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
    },
    onSuccess: (data) => {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      queryClient.setQueryData(authQueryKey, data)
      setNotice('密码已更新，其他设备上的登录会话已撤销')
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const scheduleTime = formatTimeValue(schedule.hour, schedule.minute)
  const scheduleChanged = schedule.timezone !== initial.schedule.timezone
    || schedule.hour !== initial.schedule.hour
    || schedule.minute !== initial.schedule.minute
  const networkTime = networkTimeQuery.data

  return (
    <div className="settings-page">
      <section className="settings-section">
        <header><div className="section-icon"><Clock3 size={19} /></div><div><h2>每日计划</h2><p>调度由独立 worker 执行，不会因 Web 请求中断。</p></div></header>
        <div className="form-grid three-columns">
          <label className="field-stack"><span>时区</span><select value={schedule.timezone} onChange={(event) => setSchedule({ ...schedule, timezone: event.target.value })}>{timezones.map((zone) => <option key={zone} value={zone}>{zone}</option>)}</select></label>
          <label className="field-stack"><span>每天执行时间</span><input type="time" step={60} value={scheduleTime} onChange={(event) => { const [hour, minute] = event.target.value.split(':').map(Number); if (Number.isInteger(hour) && Number.isInteger(minute)) setSchedule({ ...schedule, hour, minute }) }} /><small className="field-help">{humanScheduleTime(schedule.hour, schedule.minute)}</small></label>
          <div className={'network-time-panel ' + (networkTime?.synchronized ? 'synchronized' : 'fallback')}>
            <div className="network-time-heading"><span><i /> {networkTime?.synchronized ? '网络时间已校准' : networkTimeQuery.isLoading ? '正在读取网络时间' : '使用系统时间'}</span><button type="button" className="icon-button" onClick={() => timeSyncMutation.mutate()} disabled={timeSyncMutation.isPending || networkTimeQuery.isFetching} title="重新校准网络时间"><RefreshCw size={15} className={timeSyncMutation.isPending || networkTimeQuery.isFetching ? 'spin' : ''} /></button></div>
            {networkTime ? <><strong>{formatZonedTime(networkTime.current_time, schedule.timezone)}</strong><span>{scheduleChanged ? '保存计划后重新计算下次执行时间' : `下次 ${formatZonedTime(networkTime.next_run_at, schedule.timezone)}，${formatRelativeTime(networkTime.current_time, networkTime.next_run_at)}`}</span><small>{networkTime.source ?? '本机系统时钟'} · 偏差 {formatClockOffset(networkTime.offset_seconds)}</small></> : <span>{networkTimeQuery.isError ? '网络校时状态读取失败' : '正在计算当前时间和下次任务'}</span>}
          </div>
          <label className="field-stack"><span>解读语言</span><select value={schedule.digest_language} onChange={(event) => setSchedule({ ...schedule, digest_language: event.target.value })}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
          <label className="field-stack span-two"><span>站点公开地址</span><input value={schedule.public_base_url} onChange={(event) => setSchedule({ ...schedule, public_base_url: event.target.value })} /></label>
        </div>
        <div className="section-footer"><label className="toggle-label"><input type="checkbox" checked={schedule.enabled} onChange={(event) => setSchedule({ ...schedule, enabled: event.target.checked })} /><span>启用每日自动任务</span></label><button className="secondary-button" onClick={() => scheduleMutation.mutate()} disabled={scheduleMutation.isPending}><Save size={16} /> 保存计划</button></div>
      </section>

      <section className="settings-section">
        <header><div className="section-icon email"><MailCheck size={19} /></div><div><h2>邮件摘要</h2><p>每日任务结束后，将本次成功解读的论文合并发送。</p></div></header>
        <div className="form-grid three-columns">
          <label className="field-stack span-two"><span>SMTP 主机</span><input value={email.smtp_host} onChange={(event) => setEmail({ ...email, smtp_host: event.target.value })} placeholder="smtp.example.com" /></label>
          <label className="field-stack"><span>端口</span><input type="number" value={email.smtp_port} onChange={(event) => setEmail({ ...email, smtp_port: Number(event.target.value) })} /></label>
          <label className="field-stack"><span>安全方式</span><select value={email.security} onChange={(event) => setEmail({ ...email, security: event.target.value as EmailSettings['security'] })}><option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="plain">Plain</option></select></label>
          <label className="field-stack"><span>SMTP 用户名</span><input value={email.username ?? ''} onChange={(event) => setEmail({ ...email, username: event.target.value })} placeholder="通常填写完整邮箱地址" />{email.smtp_host.trim().toLowerCase() === 'smtp.qq.com' && <small className="field-help">QQ 邮箱填写完整邮箱地址，密码栏填写授权码。</small>}</label>
          <label className="field-stack"><span>密码或授权码</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={email.has_password ? '留空则复用已保存的密码' : '填写 SMTP 密码或授权码'} /></label>
          <label className="field-stack"><span>发件人名称</span><input value={email.from_name} onChange={(event) => setEmail({ ...email, from_name: event.target.value })} /></label>
          <label className="field-stack span-two"><span>发件邮箱</span><input type="email" value={email.from_email} onChange={(event) => setEmail({ ...email, from_email: event.target.value })} /></label>
          <label className="field-stack full-span"><span>收件邮箱</span><textarea rows={4} value={recipients} onChange={(event) => setRecipients(event.target.value)} placeholder={'researcher@example.com\nlab@example.edu'} /></label>
          <label className="field-stack full-span"><span>邮件主题前缀</span><input value={email.subject_prefix} onChange={(event) => setEmail({ ...email, subject_prefix: event.target.value })} /></label>
        </div>
        <div className="section-footer"><label className="toggle-label"><input type="checkbox" checked={email.enabled} onChange={(event) => setEmail({ ...email, enabled: event.target.checked })} /><span>启用邮件摘要</span></label><div className="button-group"><button className="ghost-button" onClick={() => testEmailMutation.mutate()} disabled={testEmailMutation.isPending || emailMutation.isPending}><Send size={16} /> 发送测试</button><button className="secondary-button" onClick={() => emailMutation.mutate()} disabled={emailMutation.isPending || testEmailMutation.isPending}><Save size={16} /> 保存邮件</button></div></div>
      </section>

      <section className="settings-section">
        <header><div className="section-icon security"><ShieldCheck size={19} /></div><div><h2>账号安全</h2><p>当前管理员：{authQuery.data?.user?.username ?? '-'}。修改密码后仅保留当前登录。</p></div></header>
        <div className="form-grid three-columns">
          <label className="field-stack"><span>当前密码</span><input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
          <label className="field-stack"><span>新密码</span><input type="password" autoComplete="new-password" minLength={12} maxLength={128} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /><small className="field-help">至少 12 个字符，避免与其他服务共用。</small></label>
          <label className="field-stack"><span>确认新密码</span><input type="password" autoComplete="new-password" minLength={12} maxLength={128} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} /></label>
        </div>
        <div className="section-footer"><span className="security-session-note"><KeyRound size={15} /> 会话会定期过期，连续登录失败会临时锁定账号。</span><button className="secondary-button" onClick={() => passwordMutation.mutate()} disabled={passwordMutation.isPending || !currentPassword || newPassword.length < 12 || !confirmPassword}><Save size={16} /> 更新密码</button></div>
      </section>
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
