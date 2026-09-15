import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpenCheck,
  BrainCircuit,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Search,
  Sparkles,
  Star,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react'
import { api, formatDate, localDateString, toQuery } from '../api'
import { PaperDrawer } from '../components/PaperDrawer'
import type { NetworkTimeStatus, Paper, PaperListResponse, Topic } from '../types'

type ReadingMode = 'unread' | 'all' | 'starred' | 'relevant'

function shiftDate(value: string, amount: number) {
  const date = new Date(value + 'T12:00:00')
  date.setDate(date.getDate() + amount)
  return localDateString(date)
}

function readerDateLabel(value: string, today: string) {
  const prefix = value === today ? '今天' : value === shiftDate(today, -1) ? '昨天' : ''
  const label = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date(value + 'T12:00:00'))
  return prefix ? `${prefix} · ${label}` : label
}

function ReaderScore({ label, value }: { label: string; value: number | null }) {
  return (
    <span className="reader-score" title={`${label}评分`}>
      <span>{label}</span>
      <strong>{value === null ? '-' : value.toFixed(1)}</strong>
    </span>
  )
}

function ReaderPaper({
  paper,
  onOpen,
  onPatch,
  onAnalyze,
  busy,
}: {
  paper: Paper
  onOpen: () => void
  onPatch: (values: Record<string, unknown>) => void
  onAnalyze: () => void
  busy: boolean
}) {
  const analysis = paper.latest_analysis
  const completed = analysis?.status === 'completed'
  return (
    <article className={'reader-paper ' + (paper.is_read ? 'read' : '')}>
      <div className="reader-paper-heading">
        <div className="reader-paper-meta">
          <span className="category-chip">{paper.primary_category ?? 'arXiv'}</span>
          {paper.topics.slice(0, 2).map((topic) => <span className="topic-chip" key={topic.id}>{topic.name}</span>)}
          <span>{formatDate(paper.published_at)}</span>
          <span>{paper.arxiv_id}v{paper.version}</span>
        </div>
        <button className={'icon-button ' + (paper.is_starred ? 'starred' : '')} onClick={() => onPatch({ is_starred: !paper.is_starred })} disabled={busy} title={paper.is_starred ? '取消收藏' : '收藏论文'}>
          <Star size={18} fill={paper.is_starred ? 'currentColor' : 'none'} />
        </button>
      </div>

      <button className="reader-paper-title" onClick={onOpen}>
        <h2>{paper.title}</h2>
        <span>{paper.authors.slice(0, 7).join(', ')}{paper.authors.length > 7 ? ' 等' : ''}</span>
      </button>

      {completed ? (
        <div className="reader-analysis-preview">
          <div className="reader-score-row">
            <ReaderScore label="相关" value={analysis.relevance_score} />
            <ReaderScore label="新颖" value={analysis.novelty_score} />
            <ReaderScore label="严谨" value={analysis.rigor_score} />
          </div>
          <p className="reader-summary">{analysis.summary}</p>
          <div className="reader-insight-grid">
            <div>
              <span>研究问题</span>
              <p>{analysis.research_question}</p>
            </div>
            <div>
              <span>阅读建议</span>
              <p>{analysis.reading_advice}</p>
            </div>
          </div>
          {analysis.contributions.length > 0 && (
            <div className="reader-contributions">
              <span>核心贡献</span>
              <ul>{analysis.contributions.slice(0, 3).map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          )}
        </div>
      ) : (
        <div className="reader-abstract-preview">
          <p>{paper.abstract}</p>
          <button className="secondary-button" onClick={onAnalyze} disabled={busy || analysis?.status === 'pending' || analysis?.status === 'running'}>
            <BrainCircuit size={16} /> {analysis?.status === 'pending' || analysis?.status === 'running' ? '云模型解读中' : analysis?.status === 'failed' ? '重新解读' : '生成中文解读'}
          </button>
        </div>
      )}

      <div className="reader-paper-footer">
        <div className="reader-decision-control" aria-label="人工阅读判断">
          <button className={paper.decision === 'relevant' ? 'active positive' : ''} onClick={() => onPatch({ decision: paper.decision === 'relevant' ? 'unreviewed' : 'relevant' })} title="值得精读"><ThumbsUp size={16} /><span>精读</span></button>
          <button className={paper.decision === 'maybe' ? 'active maybe' : ''} onClick={() => onPatch({ decision: paper.decision === 'maybe' ? 'unreviewed' : 'maybe' })} title="稍后判断"><CircleHelp size={16} /><span>稍后</span></button>
          <button className={paper.decision === 'irrelevant' ? 'active negative' : ''} onClick={() => onPatch({ decision: paper.decision === 'irrelevant' ? 'unreviewed' : 'irrelevant' })} title="不相关"><ThumbsDown size={16} /><span>忽略</span></button>
        </div>
        <div className="reader-card-actions">
          <button className={'ghost-button ' + (paper.is_read ? 'selected' : '')} onClick={() => onPatch({ is_read: !paper.is_read })} disabled={busy}><Check size={16} /> {paper.is_read ? '已读' : '标记已读'}</button>
          <button className="primary-button" onClick={onOpen}><BookOpenCheck size={16} /> 打开精读</button>
        </div>
      </div>
    </article>
  )
}

export function ReaderPage() {
  const initialToday = localDateString()
  const [selectedDay, setSelectedDay] = useState<string | null>(null)
  const [mode, setMode] = useState<ReadingMode>('unread')
  const [topicId, setTopicId] = useState('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedPaper, setSelectedPaper] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const timeQuery = useQuery({
    queryKey: ['network-time'],
    queryFn: () => api<NetworkTimeStatus>('/system/time'),
    staleTime: 60_000,
  })
  const topicsQuery = useQuery({ queryKey: ['topics'], queryFn: () => api<Topic[]>('/topics') })
  const correctedToday = timeQuery.data?.current_time
    ? localDateString(new Date(timeQuery.data.current_time))
    : initialToday
  const day = selectedDay ?? correctedToday

  const state = mode === 'all' ? 'all' : mode
  const papersQuery = useQuery({
    queryKey: ['papers', 'reader', day, topicId, state, debouncedSearch],
    queryFn: () => api<PaperListResponse>('/papers' + toQuery({
      day,
      topic_id: topicId,
      state,
      analysis_status: 'all',
      sort: 'relevance',
      q: debouncedSearch,
      page: 1,
      page_size: 100,
    })),
  })
  const patchMutation = useMutation({
    mutationFn: ({ id, values }: { id: number; values: Record<string, unknown> }) =>
      api('/papers/' + id, { method: 'PATCH', body: JSON.stringify(values) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
      if (selectedPaper) void queryClient.invalidateQueries({ queryKey: ['paper', selectedPaper] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const analyzeMutation = useMutation({
    mutationFn: (paperId: number) => api<{ analysis_id: number }>('/papers/' + paperId + '/actions/analyze', {
      method: 'POST',
      body: JSON.stringify({ topic_id: null, llm_profile_id: null, source_mode: null }),
    }),
    onSuccess: (data) => {
      setNotice(`解读任务 #${data.analysis_id} 已提交`)
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })

  const data = papersQuery.data
  const readCount = Math.max((data?.stats.total ?? 0) - (data?.stats.unread ?? 0), 0)
  const progress = data?.stats.total ? Math.round(readCount / data.stats.total * 100) : 0
  const changeDay = (value: string) => {
    setSelectedDay(value)
    setSelectedPaper(null)
  }
  const modes: Array<{ id: ReadingMode; label: string; count: number }> = [
    { id: 'unread', label: '待阅读', count: data?.stats.unread ?? 0 },
    { id: 'all', label: '全部', count: data?.stats.total ?? 0 },
    { id: 'starred', label: '收藏', count: data?.stats.starred ?? 0 },
    { id: 'relevant', label: '精读', count: data?.stats.relevant ?? 0 },
  ]

  return (
    <div className="reader-page">
      <section className="reader-overview">
        <div>
          <span className="eyebrow">DAILY READING</span>
          <h2>{readerDateLabel(day, correctedToday)}的研究简报</h2>
          <p>{data?.stats.total ?? 0} 篇收录 · {data?.stats.analyzed ?? 0} 篇已有中文解读</p>
        </div>
        <div className="reader-progress" aria-label={`阅读进度 ${progress}%`}>
          <div><span>阅读进度</span><strong>{readCount} / {data?.stats.total ?? 0}</strong></div>
          <span className="reader-progress-track"><span style={{ width: `${progress}%` }} /></span>
        </div>
      </section>

      <section className="reader-toolbar" aria-label="阅读筛选">
        <div className="reader-date-control">
          <button className="icon-button" onClick={() => changeDay(shiftDate(day, -1))} title="前一天"><ChevronLeft size={18} /></button>
          <label><CalendarDays size={16} /><input type="date" value={day} max={correctedToday} onChange={(event) => changeDay(event.target.value)} /></label>
          <button className="icon-button" onClick={() => changeDay(shiftDate(day, 1))} disabled={day >= correctedToday} title="后一天"><ChevronRight size={18} /></button>
        </div>
        <label className="search-field reader-search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="在当日论文中搜索" /></label>
        <select value={topicId} onChange={(event) => setTopicId(event.target.value)} aria-label="研究主题">
          <option value="">全部研究主题</option>
          {topicsQuery.data?.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
        </select>
      </section>

      <div className="reader-layout">
        <aside className="reader-queue">
          <div className="reader-queue-title"><Sparkles size={17} /><strong>阅读队列</strong></div>
          <div className="reader-mode-tabs">
            {modes.map((item) => <button key={item.id} className={mode === item.id ? 'active' : ''} onClick={() => setMode(item.id)}><span>{item.label}</span><small>{item.count}</small></button>)}
          </div>
          <div className="reader-queue-note">
            <strong>{timeQuery.data?.synchronized ? '网络时间已校准' : '使用系统时间'}</strong>
            <span>{timeQuery.data?.source ?? '本机时钟'}</span>
          </div>
        </aside>

        <section className="reader-feed" aria-live="polite">
          {papersQuery.isLoading ? (
            <div className="reader-loading">{Array.from({ length: 4 }).map((_, index) => <div key={index} />)}</div>
          ) : papersQuery.isError ? (
            <div className="empty-state error-state"><strong>研究简报读取失败</strong><span>{(papersQuery.error as Error).message}</span></div>
          ) : data?.items.length ? (
            data.items.map((paper) => <ReaderPaper key={paper.id} paper={paper} onOpen={() => setSelectedPaper(paper.id)} busy={patchMutation.isPending || analyzeMutation.isPending} onPatch={(values) => patchMutation.mutate({ id: paper.id, values })} onAnalyze={() => analyzeMutation.mutate(paper.id)} />)
          ) : (
            <div className="reader-empty"><BookOpenCheck size={26} /><strong>{mode === 'unread' ? '这一天的论文已经读完' : '没有符合当前条件的论文'}</strong><span>切换日期、阅读队列或研究主题继续查看。</span></div>
          )}
        </section>
      </div>

      <PaperDrawer paperId={selectedPaper} onClose={() => setSelectedPaper(null)} />
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
