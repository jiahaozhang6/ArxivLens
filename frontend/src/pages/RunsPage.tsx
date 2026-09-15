import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, LoaderCircle, MinusCircle } from 'lucide-react'
import { api, formatDateTime } from '../api'
import type { RunLog } from '../types'

const statusInfo = {
  running: { label: '运行中', icon: LoaderCircle },
  completed: { label: '完成', icon: CheckCircle2 },
  partial: { label: '部分完成', icon: MinusCircle },
  failed: { label: '失败', icon: AlertTriangle },
}

export function RunsPage() {
  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: () => api<RunLog[]>('/jobs/runs?limit=100'),
    refetchInterval: (query) => query.state.data?.some((run) => run.status === 'running') ? 4000 : 20_000,
  })

  return (
    <div className="management-page">
      <div className="page-action-row"><div><strong>任务历史</strong><span>抓取、解读和邮件投递的运行结果</span></div></div>
      <div className="run-table">
        <div className="run-table-head"><span>状态</span><span>开始时间</span><span>触发</span><span>主题</span><span>论文</span><span>解读</span><span>邮件</span></div>
        {runsQuery.isLoading ? <div className="table-loading" /> : runsQuery.data?.length ? runsQuery.data.map((run) => {
          const item = statusInfo[run.status]
          const Icon = item.icon
          return (
            <details className="run-row" key={run.id}>
              <summary>
                <span className={'run-status ' + run.status}><Icon size={16} className={run.status === 'running' ? 'spin' : ''} /> {item.label}</span>
                <span>{formatDateTime(run.started_at)}</span>
                <span>{run.trigger === 'manual' ? '手动' : '计划'}</span>
                <span>{run.topics_processed}</span>
                <span>{run.papers_new} 新 / {run.papers_found} 命中</span>
                <span>{run.analyses_completed} 新解读 / {run.analyses_failed} 失败</span>
                <span>{run.emails_sent}</span>
              </summary>
              <div className="run-detail">
                <p>{run.message || '暂无运行摘要'}</p>
                {run.finished_at && <span>结束：{formatDateTime(run.finished_at)}</span>}
                {run.error_details?.errors?.map((error, index) => <code key={index}>{error}</code>)}
                {run.error_details?.warnings?.map((warning, index) => <code className="run-warning" key={index}>{warning}</code>)}
              </div>
            </details>
          )
        }) : <div className="empty-state"><strong>尚无运行记录</strong><span>点击右上角“立即更新”可执行第一次任务。</span></div>}
      </div>
    </div>
  )
}
