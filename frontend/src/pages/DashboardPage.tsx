import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Search,
  Star,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react'
import { api, formatDate, localDateString, toQuery } from '../api'
import { PaperDrawer } from '../components/PaperDrawer'
import type { Paper, PaperListResponse, Topic } from '../types'

function shiftDate(value: string, amount: number) {
  const date = new Date(value + 'T12:00:00')
  date.setDate(date.getDate() + amount)
  return localDateString(date)
}

function Score({ label, value }: { label: string; value: number | null }) {
  return (
    <span className="score" title={label + '评分'}>
      <span>{label}</span>
      <strong>{value === null ? '-' : value.toFixed(1)}</strong>
    </span>
  )
}

function PaperRow({
  paper,
  onOpen,
  onPatch,
  saving,
}: {
  paper: Paper
  onOpen: () => void
  onPatch: (values: Partial<Pick<Paper, 'is_read' | 'is_starred' | 'decision'>>) => void
  saving: boolean
}) {
  const analysis = paper.latest_analysis
  return (
    <article className={'paper-row ' + (paper.is_read ? 'paper-read' : '')}>
      <div className="paper-state-column">
        <button
          className={'icon-button ' + (paper.is_read ? 'selected' : '')}
          onClick={() => onPatch({ is_read: !paper.is_read })}
          disabled={saving}
          title={paper.is_read ? '标记为未读' : '标记为已读'}
        >
          <Check size={18} />
        </button>
        <button
          className={'icon-button ' + (paper.is_starred ? 'starred' : '')}
          onClick={() => onPatch({ is_starred: !paper.is_starred })}
          disabled={saving}
          title={paper.is_starred ? '取消星标' : '添加星标'}
        >
          <Star size={18} fill={paper.is_starred ? 'currentColor' : 'none'} />
        </button>
      </div>

      <button className="paper-main" onClick={onOpen}>
        <div className="paper-meta-row">
          <span className="category-chip">{paper.primary_category ?? 'arXiv'}</span>
          {paper.topics.slice(0, 3).map((topic) => (
            <span key={topic.id} className="topic-chip">{topic.name}</span>
          ))}
          <span className="meta-text">{formatDate(paper.published_at)}</span>
          <span className="meta-text">v{paper.version}</span>
        </div>
        <h2>{paper.title}</h2>
        <p className="authors">{paper.authors.slice(0, 8).join(', ')}</p>
        {analysis?.status === 'completed' ? (
          <p className="paper-summary">{analysis.summary}</p>
        ) : analysis?.status === 'failed' ? (
          <p className="analysis-state error-text">解读失败，打开详情可重新运行</p>
        ) : analysis ? (
          <p className="analysis-state">云模型正在解读</p>
        ) : (
          <p className="analysis-state">尚未解读</p>
        )}
        {analysis?.keywords?.length ? (
          <div className="keyword-row">
            {analysis.keywords.slice(0, 5).map((keyword) => (
              <span key={keyword}>{keyword}</span>
            ))}
          </div>
        ) : null}
      </button>

      <div className="paper-evaluation">
        <div className="score-row">
          <Score label="相关" value={analysis?.relevance_score ?? null} />
          <Score label="新颖" value={analysis?.novelty_score ?? null} />
          <Score label="严谨" value={analysis?.rigor_score ?? null} />
        </div>
        <div className="decision-control" aria-label="人工相关性">
          <button
            className={paper.decision === 'relevant' ? 'active positive' : ''}
            onClick={() => onPatch({ decision: paper.decision === 'relevant' ? 'unreviewed' : 'relevant' })}
            title="值得精读"
          >
            <ThumbsUp size={16} />
          </button>
          <button
            className={paper.decision === 'maybe' ? 'active maybe' : ''}
            onClick={() => onPatch({ decision: paper.decision === 'maybe' ? 'unreviewed' : 'maybe' })}
            title="稍后判断"
          >
            <CircleHelp size={16} />
          </button>
          <button
            className={paper.decision === 'irrelevant' ? 'active negative' : ''}
            onClick={() => onPatch({ decision: paper.decision === 'irrelevant' ? 'unreviewed' : 'irrelevant' })}
            title="不相关"
          >
            <ThumbsDown size={16} />
          </button>
        </div>
      </div>
    </article>
  )
}

export function DashboardPage() {
  const initialPaper = Number(new URLSearchParams(window.location.search).get('paper')) || null
  const [day, setDay] = useState(localDateString())
  const [topicId, setTopicId] = useState('')
  const [state, setState] = useState('all')
  const [analysisStatus, setAnalysisStatus] = useState('all')
  const [sort, setSort] = useState('relevance')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selectedPaper, setSelectedPaper] = useState<number | null>(initialPaper)
  const queryClient = useQueryClient()

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const topicsQuery = useQuery({
    queryKey: ['topics'],
    queryFn: () => api<Topic[]>('/topics'),
  })
  const papersQuery = useQuery({
    queryKey: ['papers', day, topicId, state, analysisStatus, sort, debouncedSearch, page],
    queryFn: () =>
      api<PaperListResponse>(
        '/papers' +
          toQuery({
            day,
            topic_id: topicId,
            state,
            analysis_status: analysisStatus,
            sort,
            q: debouncedSearch,
            page,
            page_size: 20,
          }),
      ),
  })
  const patchMutation = useMutation({
    mutationFn: ({ id, values }: { id: number; values: Record<string, unknown> }) =>
      api('/papers/' + id, { method: 'PATCH', body: JSON.stringify(values) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
      if (selectedPaper) void queryClient.invalidateQueries({ queryKey: ['paper', selectedPaper] })
    },
  })

  const openPaper = (id: number) => {
    setSelectedPaper(id)
    const url = new URL(window.location.href)
    url.searchParams.set('paper', String(id))
    window.history.replaceState({}, '', url)
  }
  const closePaper = () => {
    setSelectedPaper(null)
    const url = new URL(window.location.href)
    url.searchParams.delete('paper')
    window.history.replaceState({}, '', url)
  }
  const changeDay = (value: string) => {
    setDay(value)
    setPage(1)
  }
  const data = papersQuery.data
  const today = localDateString()

  return (
    <div className="dashboard-page">
      <section className="filter-band" aria-label="论文筛选">
        <div className="date-navigator">
          <button className="icon-button" onClick={() => changeDay(shiftDate(day, -1))} title="前一天">
            <ArrowLeft size={18} />
          </button>
          <input type="date" value={day} max={today} onChange={(event) => changeDay(event.target.value)} />
          <button
            className="icon-button"
            onClick={() => changeDay(shiftDate(day, 1))}
            disabled={day >= today}
            title="后一天"
          >
            <ArrowRight size={18} />
          </button>
        </div>
        <label className="search-field">
          <Search size={17} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="标题、摘要或 arXiv ID"
          />
        </label>
        <select value={topicId} onChange={(event) => { setTopicId(event.target.value); setPage(1) }} aria-label="主题">
          <option value="">全部主题</option>
          {topicsQuery.data?.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
        </select>
        <select value={state} onChange={(event) => { setState(event.target.value); setPage(1) }} aria-label="阅读状态">
          <option value="all">全部状态</option>
          <option value="unread">未读</option>
          <option value="read">已读</option>
          <option value="starred">星标</option>
          <option value="relevant">值得精读</option>
          <option value="maybe">稍后判断</option>
          <option value="irrelevant">不相关</option>
        </select>
        <select
          value={analysisStatus}
          onChange={(event) => { setAnalysisStatus(event.target.value); setPage(1) }}
          aria-label="解读状态"
        >
          <option value="all">全部解读</option>
          <option value="completed">已解读</option>
          <option value="missing">待解读</option>
          <option value="failed">解读失败</option>
        </select>
        <select value={sort} onChange={(event) => { setSort(event.target.value); setPage(1) }} aria-label="排序">
          <option value="relevance">相关性优先</option>
          <option value="published">最新发表</option>
          <option value="title">标题排序</option>
        </select>
      </section>

      <section className="metric-band" aria-label="当日统计">
        <div><span>收录</span><strong>{data?.stats.total ?? 0}</strong></div>
        <div><span>未读</span><strong>{data?.stats.unread ?? 0}</strong></div>
        <div><span>已解读</span><strong>{data?.stats.analyzed ?? 0}</strong></div>
        <div><span>值得精读</span><strong>{data?.stats.relevant ?? 0}</strong></div>
        <div><span>星标</span><strong>{data?.stats.starred ?? 0}</strong></div>
      </section>

      <section className="paper-list" aria-live="polite">
        {papersQuery.isLoading ? (
          <div className="loading-list">{Array.from({ length: 5 }).map((_, index) => <div key={index} />)}</div>
        ) : papersQuery.isError ? (
          <div className="empty-state error-state">
            <strong>论文列表读取失败</strong>
            <span>{(papersQuery.error as Error).message}</span>
          </div>
        ) : data?.items.length ? (
          data.items.map((paper) => (
            <PaperRow
              key={paper.id}
              paper={paper}
              onOpen={() => openPaper(paper.id)}
              saving={patchMutation.isPending}
              onPatch={(values) => patchMutation.mutate({ id: paper.id, values })}
            />
          ))
        ) : (
          <div className="empty-state">
            <strong>这一天还没有匹配论文</strong>
            <span>可以切换日期，或在主题订阅中检查检索式和回溯天数。</span>
          </div>
        )}
      </section>

      {data && data.pages > 1 && (
        <nav className="pagination" aria-label="分页">
          <button className="icon-button" disabled={page <= 1} onClick={() => setPage(page - 1)} title="上一页">
            <ChevronLeft size={18} />
          </button>
          <span>{page} / {data.pages}</span>
          <button className="icon-button" disabled={page >= data.pages} onClick={() => setPage(page + 1)} title="下一页">
            <ChevronRight size={18} />
          </button>
        </nav>
      )}

      <PaperDrawer paperId={selectedPaper} onClose={closePaper} />
    </div>
  )
}
