import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Check,
  ExternalLink,
  FileText,
  RefreshCw,
  Save,
  Star,
} from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, formatDate, formatDateTime } from '../api'
import { useAuthStatus } from '../auth'
import type { Analysis, Paper } from '../types'

function Score({ label, value }: { label: string; value: number | null }) {
  return <span className="paper-reading-score"><small>{label}</small><strong>{value === null ? '-' : value.toFixed(1)}</strong></span>
}

function DeepAnalysis({ analysis }: { analysis: Analysis | null }) {
  if (!analysis) return <div className="paper-reading-analysis-empty">尚无后台深度解读。</div>
  if (analysis.status === 'pending' || analysis.status === 'running') {
    return <div className="paper-reading-analysis-empty"><RefreshCw size={17} className="spin" /> 后台深度解读正在进行</div>
  }
  if (analysis.status === 'failed') {
    return <div className="paper-reading-analysis-empty error-text">{analysis.error_message ?? '深度解读失败'}</div>
  }
  return (
    <div className="paper-reading-analysis">
      <div className="paper-reading-analysis-meta">
        <span>{analysis.model}</span><span>{analysis.source_mode}</span><span>{formatDateTime(analysis.completed_at)}</span>
        {analysis.model_routing?.fallback_used && <span className="fallback-chip">自动切换模型</span>}
      </div>
      <div className="paper-reading-scores">
        <Score label="相关性" value={analysis.relevance_score} />
        <Score label="新颖性" value={analysis.novelty_score} />
        <Score label="严谨性" value={analysis.rigor_score} />
      </div>
      <section><h2>核心结论</h2><p>{analysis.summary}</p></section>
      <section><h2>研究问题</h2><p>{analysis.research_question}</p></section>
      <section><h2>主要贡献</h2><ul>{analysis.contributions.map((item) => <li key={item}>{item}</li>)}</ul></section>
      <section><h2>方法</h2><p>{analysis.methodology}</p></section>
      <section><h2>实验与证据</h2><p>{analysis.experiments}</p></section>
      <section><h2>局限与风险</h2><ul>{analysis.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section>
      <section><h2>阅读建议</h2><p>{analysis.reading_advice}</p></section>
    </div>
  )
}

export function PaperReadingPage() {
  const paperId = Number(useParams().paperId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const authQuery = useAuthStatus()
  const readOnly = authQuery.data?.role === 'guest'
  const [notes, setNotes] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const initializedPaperRef = useRef<number | null>(null)

  const paperQuery = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => api<Paper>(`/papers/${paperId}`),
    enabled: Number.isInteger(paperId) && paperId > 0,
    refetchInterval: (query) => {
      const state = query.state.data?.latest_analysis?.status
      return state === 'pending' || state === 'running' ? 3000 : false
    },
  })
  useEffect(() => {
    const paper = paperQuery.data
    if (!paper || initializedPaperRef.current === paper.id) return
    initializedPaperRef.current = paper.id
    setNotes(paper.personal_notes ?? '')
  }, [paperQuery.data])

  const patchMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => api(`/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(values) }),
    onSuccess: () => {
      setNotice('已保存')
      void queryClient.invalidateQueries({ queryKey: ['paper', paperId] })
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  if (!Number.isInteger(paperId) || paperId <= 0) return <div className="reader-empty"><strong>论文编号无效</strong></div>
  if (paperQuery.isLoading) return <div className="paper-reading-loading" />
  if (paperQuery.isError || !paperQuery.data) return <div className="reader-empty"><strong>论文读取失败</strong><span>{(paperQuery.error as Error)?.message}</span></div>

  const paper = paperQuery.data
  const analysis = paper.latest_analysis
  return (
    <div className="paper-reading-page">
      <div className="paper-reading-topbar">
        <button className="icon-text-button" onClick={() => navigate(-1)}><ArrowLeft size={16} /> 返回简报</button>
        <div className="paper-reading-actions">
          {!readOnly && <button className={`icon-text-button ${paper.is_read ? 'selected' : ''}`} onClick={() => patchMutation.mutate({ is_read: !paper.is_read })}><Check size={16} /> {paper.is_read ? '已读' : '标记已读'}</button>}
          {!readOnly && <button className={`icon-text-button ${paper.is_starred ? 'starred' : ''}`} onClick={() => patchMutation.mutate({ is_starred: !paper.is_starred })}><Star size={16} fill={paper.is_starred ? 'currentColor' : 'none'} /> 收藏</button>}
          <a className="icon-button" href={paper.abs_url} target="_blank" rel="noreferrer" title="打开 arXiv"><ExternalLink size={17} /></a>
          <a className="icon-button" href={paper.pdf_url} target="_blank" rel="noreferrer" title="打开 PDF"><FileText size={17} /></a>
        </div>
      </div>

      <header className="paper-reading-heading">
        <div className="reader-paper-meta"><span className="category-chip">{paper.primary_category ?? 'arXiv'}</span><span>{paper.arxiv_id}v{paper.version}</span><span>{formatDate(paper.published_at)}</span></div>
        <h1>{paper.title}</h1>
        <p>{paper.authors.join(', ')}</p>
      </header>

      <div className="paper-reading-layout">
        <main className="paper-reading-document">
          <section className="paper-reading-abstract"><div className="paper-reading-section-title"><h2>Abstract</h2><span>原始摘要</span></div><p>{paper.abstract}</p></section>
          <section className="paper-reading-deep">
            <div className="paper-reading-section-title">
              <div><h2>深度结构化解读</h2><span>后台任务 · 可归档、检索和邮件发送</span></div>
            </div>
            <DeepAnalysis analysis={analysis} />
          </section>
          {!readOnly && <section className="paper-reading-notes">
            <div className="paper-reading-section-title"><div><h2>科研笔记</h2><span>随论文长期保存</span></div></div>
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={6} placeholder="记录可复现实验、相关工作、疑问和后续研究想法…" />
            <button className="secondary-button" onClick={() => patchMutation.mutate({ personal_notes: notes })} disabled={patchMutation.isPending}><Save size={16} /> 保存笔记</button>
          </section>}
        </main>
      </div>
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
