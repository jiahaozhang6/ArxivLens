import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BrainCircuit,
  Check,
  ChevronLeft,
  ChevronRight,
  Database,
  Eye,
  Search,
  Star,
  Trash2,
} from 'lucide-react'
import { api, formatDate, localDateString, toQuery } from '../api'
import { PaperDrawer } from '../components/PaperDrawer'
import type {
  NetworkTimeStatus,
  Paper,
  PaperBulkAction,
  PaperDatesResponse,
  PaperListResponse,
  Topic,
} from '../types'

function shiftDate(value: string, amount: number) {
  const date = new Date(value + 'T12:00:00')
  date.setDate(date.getDate() + amount)
  return localDateString(date)
}

function humanDate(value: string, today: string) {
  if (value === today) return '今天'
  if (value === shiftDate(today, -1)) return '昨天'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date(value + 'T12:00:00'))
}

function decisionLabel(paper: Paper) {
  if (paper.decision === 'relevant') return '值得精读'
  if (paper.decision === 'maybe') return '稍后判断'
  if (paper.decision === 'irrelevant') return '不相关'
  return '未判断'
}

export function LibraryPage() {
  const [day, setDay] = useState('')
  const [topicId, setTopicId] = useState('')
  const [state, setState] = useState('all')
  const [analysisStatus, setAnalysisStatus] = useState('all')
  const [sort, setSort] = useState('published')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkAction, setBulkAction] = useState<PaperBulkAction>('mark_read')
  const [selectedPaper, setSelectedPaper] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
      setSelectedIds(new Set())
    }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const topicsQuery = useQuery({ queryKey: ['topics'], queryFn: () => api<Topic[]>('/topics') })
  const timeQuery = useQuery({
    queryKey: ['network-time'],
    queryFn: () => api<NetworkTimeStatus>('/system/time'),
    staleTime: 60_000,
  })
  const datesQuery = useQuery({
    queryKey: ['paper-dates', topicId],
    queryFn: () => api<PaperDatesResponse>('/papers/dates' + toQuery({ topic_id: topicId, limit: 365 })),
  })
  const papersQuery = useQuery({
    queryKey: ['papers', 'library', day, topicId, state, analysisStatus, sort, debouncedSearch, page],
    queryFn: () => api<PaperListResponse>('/papers' + toQuery({
      day,
      topic_id: topicId,
      state,
      analysis_status: analysisStatus,
      sort,
      q: debouncedSearch,
      page,
      page_size: 30,
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
  const bulkMutation = useMutation({
    mutationFn: ({ paperIds, action }: { paperIds: number[]; action: PaperBulkAction }) =>
      api<{ affected: number }>('/papers/actions/bulk', {
        method: 'POST',
        body: JSON.stringify({ paper_ids: paperIds, action }),
      }),
    onSuccess: (data) => {
      setNotice(`已处理 ${data.affected} 篇论文`)
      setSelectedIds(new Set())
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
      void queryClient.invalidateQueries({ queryKey: ['paper-dates'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const analyzeMutation = useMutation({
    mutationFn: (paperIds: number[]) => api<{ submitted: number; skipped_paper_ids: number[] }>('/papers/actions/analyze', {
      method: 'POST',
      body: JSON.stringify({ paper_ids: paperIds, llm_profile_id: null, source_mode: null }),
    }),
    onSuccess: (data) => {
      const skipped = data.skipped_paper_ids.length
      setNotice(skipped ? `已提交 ${data.submitted} 篇，${skipped} 篇缺少可用模型` : `已提交 ${data.submitted} 篇论文进行解读`)
      setSelectedIds(new Set())
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })

  const data = papersQuery.data
  const pageIds = useMemo(() => data?.items.map((paper) => paper.id) ?? [], [data?.items])
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id))
  const today = timeQuery.data?.current_time
    ? localDateString(new Date(timeQuery.data.current_time))
    : localDateString()

  const changeFilter = (setter: (value: string) => void, value: string) => {
    setter(value)
    setPage(1)
    setSelectedIds(new Set())
  }
  const toggleSelection = (id: number) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const togglePage = () => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (allPageSelected) pageIds.forEach((id) => next.delete(id))
      else pageIds.forEach((id) => next.add(id))
      return next
    })
  }
  const runBulkAction = (paperIds = Array.from(selectedIds), action = bulkAction) => {
    if (!paperIds.length) return
    if (action === 'delete' && !window.confirm(`从数据库永久删除选中的 ${paperIds.length} 篇论文及其解读？`)) return
    bulkMutation.mutate({ paperIds, action })
  }
  const dateItems = datesQuery.data?.items ?? []

  return (
    <div className="library-page">
      <aside className="archive-rail" aria-label="收录日期">
        <div className="archive-rail-heading">
          <Database size={17} />
          <strong>收录日期</strong>
        </div>
        <label className="archive-calendar">
          <span>选择日期</span>
          <input type="date" value={day} max={today} onChange={(event) => changeFilter(setDay, event.target.value)} />
        </label>
        <button className={'archive-date ' + (!day ? 'active' : '')} onClick={() => changeFilter(setDay, '')}>
          <span>全部论文</span>
          <small>全库</small>
        </button>
        <div className="archive-date-list">
          {dateItems.map((item) => (
            <button key={item.date} className={'archive-date ' + (day === item.date ? 'active' : '')} onClick={() => changeFilter(setDay, item.date)}>
              <span>{humanDate(item.date, today)}</span>
              <small>{item.count}</small>
            </button>
          ))}
          {!datesQuery.isLoading && !dateItems.length && <span className="archive-empty">暂无收录记录</span>}
        </div>
      </aside>

      <div className="library-workspace">
        <section className="library-filter-band" aria-label="论文库筛选">
          <label className="search-field library-search">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题、摘要或 arXiv ID" />
          </label>
          <select value={topicId} onChange={(event) => { changeFilter(setTopicId, event.target.value); setDay('') }} aria-label="主题">
            <option value="">全部主题</option>
            {topicsQuery.data?.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
          </select>
          <select value={state} onChange={(event) => changeFilter(setState, event.target.value)} aria-label="阅读状态">
            <option value="all">全部状态</option>
            <option value="unread">未读</option>
            <option value="read">已读</option>
            <option value="starred">已收藏</option>
            <option value="relevant">值得精读</option>
            <option value="maybe">稍后判断</option>
            <option value="irrelevant">不相关</option>
          </select>
          <select value={analysisStatus} onChange={(event) => changeFilter(setAnalysisStatus, event.target.value)} aria-label="解读状态">
            <option value="all">全部解读</option>
            <option value="completed">已解读</option>
            <option value="missing">待解读</option>
            <option value="failed">解读失败</option>
          </select>
          <select value={sort} onChange={(event) => changeFilter(setSort, event.target.value)} aria-label="排序">
            <option value="published">最新发表</option>
            <option value="relevance">相关性优先</option>
            <option value="title">标题排序</option>
          </select>
        </section>

        <section className="library-summary" aria-label="论文库统计">
          <div><span>{day ? humanDate(day, today) : '全部日期'}</span><strong>{data?.stats.total ?? 0} 篇</strong></div>
          <span>{data?.stats.unread ?? 0} 未读 · {data?.stats.analyzed ?? 0} 已解读 · {data?.stats.starred ?? 0} 收藏 · {data?.stats.relevant ?? 0} 精读</span>
        </section>

        {selectedIds.size > 0 && (
          <div className="bulk-toolbar">
            <strong>已选 {selectedIds.size} 篇</strong>
            <button className="primary-button" onClick={() => analyzeMutation.mutate(Array.from(selectedIds))} disabled={analyzeMutation.isPending}><BrainCircuit size={16} /> {analyzeMutation.isPending ? '提交中' : '批量解读'}</button>
            <select value={bulkAction} onChange={(event) => setBulkAction(event.target.value as PaperBulkAction)} aria-label="批量操作">
              <option value="mark_read">标记为已读</option>
              <option value="mark_unread">标记为未读</option>
              <option value="star">添加收藏</option>
              <option value="unstar">取消收藏</option>
              <option value="relevant">设为值得精读</option>
              <option value="maybe">设为稍后判断</option>
              <option value="irrelevant">设为不相关</option>
              <option value="unreviewed">清除人工判断</option>
              <option value="delete">从数据库删除</option>
            </select>
            <button className={bulkAction === 'delete' ? 'danger-button' : 'secondary-button'} onClick={() => runBulkAction()} disabled={bulkMutation.isPending}>应用</button>
            <button className="ghost-button" onClick={() => setSelectedIds(new Set())}>取消选择</button>
          </div>
        )}

        <section className="library-table" aria-live="polite">
          <div className="library-table-head">
            <label title="选择本页"><input type="checkbox" checked={allPageSelected} onChange={togglePage} /></label>
            <span>论文</span><span>收录信息</span><span>科研状态</span><span>操作</span>
          </div>
          {papersQuery.isLoading ? (
            <div className="loading-list">{Array.from({ length: 6 }).map((_, index) => <div key={index} />)}</div>
          ) : papersQuery.isError ? (
            <div className="empty-state error-state"><strong>论文库读取失败</strong><span>{(papersQuery.error as Error).message}</span></div>
          ) : data?.items.length ? data.items.map((paper) => {
            const analysis = paper.latest_analysis
            return (
              <article className={'library-row ' + (selectedIds.has(paper.id) ? 'selected' : '')} key={paper.id}>
                <label className="library-select" title="选择论文"><input type="checkbox" checked={selectedIds.has(paper.id)} onChange={() => toggleSelection(paper.id)} /></label>
                <button className="library-paper-main" onClick={() => setSelectedPaper(paper.id)}>
                  <span className="library-paper-id">{paper.arxiv_id}v{paper.version}</span>
                  <h2>{paper.title}</h2>
                  <span>{paper.authors.slice(0, 5).join(', ')}{paper.authors.length > 5 ? ' 等' : ''}</span>
                </button>
                <div className="library-metadata">
                  <strong>{paper.primary_category ?? 'arXiv'} · {formatDate(paper.published_at)}</strong>
                  <span>{paper.topics.map((topic) => topic.name).join(' · ') || '未关联主题'}</span>
                </div>
                <div className="library-research-state">
                  <span className={'decision-label ' + paper.decision}>{decisionLabel(paper)}</span>
                  <span>{paper.is_read ? '已读' : '未读'} · {analysis?.status === 'completed' ? `相关性 ${analysis.relevance_score?.toFixed(1) ?? '-'}` : '待解读'}</span>
                </div>
                <div className="library-row-actions">
                  <button className={'icon-button ' + (paper.is_read ? 'selected' : '')} onClick={() => patchMutation.mutate({ id: paper.id, values: { is_read: !paper.is_read } })} title={paper.is_read ? '标记未读' : '标记已读'}><Check size={17} /></button>
                  <button className={'icon-button ' + (paper.is_starred ? 'starred' : '')} onClick={() => patchMutation.mutate({ id: paper.id, values: { is_starred: !paper.is_starred } })} title={paper.is_starred ? '取消收藏' : '收藏'}><Star size={17} fill={paper.is_starred ? 'currentColor' : 'none'} /></button>
                  <button className="icon-button" onClick={() => analyzeMutation.mutate([paper.id])} disabled={analyzeMutation.isPending} title="立即用云模型解读"><BrainCircuit size={17} /></button>
                  <button className="icon-button" onClick={() => setSelectedPaper(paper.id)} title="查看与管理"><Eye size={17} /></button>
                  <button className="icon-button danger" onClick={() => runBulkAction([paper.id], 'delete')} title="从数据库删除"><Trash2 size={17} /></button>
                </div>
              </article>
            )
          }) : (
            <div className="empty-state"><Database size={24} /><strong>没有符合条件的论文</strong><span>切换日期或清除筛选后再查看。</span></div>
          )}
        </section>

        {data && data.pages > 1 && (
          <nav className="pagination" aria-label="分页">
            <button className="icon-button" disabled={page <= 1} onClick={() => { setPage(page - 1); setSelectedIds(new Set()) }} title="上一页"><ChevronLeft size={18} /></button>
            <span>{page} / {data.pages}</span>
            <button className="icon-button" disabled={page >= data.pages} onClick={() => { setPage(page + 1); setSelectedIds(new Set()) }} title="下一页"><ChevronRight size={18} /></button>
          </nav>
        )}
      </div>

      <PaperDrawer paperId={selectedPaper} onClose={() => setSelectedPaper(null)} />
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
