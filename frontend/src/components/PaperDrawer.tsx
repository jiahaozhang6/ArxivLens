import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen,
  Check,
  Download,
  ExternalLink,
  FileText,
  RefreshCw,
  Save,
  Star,
  X,
} from 'lucide-react'
import { api, formatDate, formatDateTime } from '../api'
import type { Analysis, LLMProfile, Paper, Topic } from '../types'

function ScoreMeter({ label, value }: { label: string; value: number | null }) {
  const safeValue = value ?? 0
  return (
    <div className="score-meter">
      <div><span>{label}</span><strong>{value === null ? '-' : value.toFixed(1)}</strong></div>
      <span className="meter-track"><span style={{ width: String(safeValue * 10) + '%' }} /></span>
    </div>
  )
}

function AnalysisView({ analysis }: { analysis: Analysis }) {
  if (analysis.status === 'failed') {
    return (
      <div className="analysis-error">
        <strong>本次解读失败</strong>
        <p>{analysis.error_message}</p>
      </div>
    )
  }
  if (analysis.status !== 'completed') {
    return <div className="analysis-pending"><RefreshCw size={18} className="spin" /> 云模型正在解读</div>
  }
  return (
    <div className="analysis-content">
      <div className="analysis-meta">
        <span>{analysis.provider}</span>
        <span>{analysis.model}</span>
        <span>{analysis.source_mode === 'tex' ? 'LaTeX 正文' : analysis.source_mode === 'html' ? 'HTML 正文' : analysis.source_mode === 'pdf' ? 'PDF 正文' : '摘要'}</span>
        <span>{formatDateTime(analysis.completed_at)}</span>
      </div>
      {analysis.error_message && <p className="inline-warning">{analysis.error_message}</p>}
      <div className="score-grid">
        <ScoreMeter label="相关性" value={analysis.relevance_score} />
        <ScoreMeter label="新颖性" value={analysis.novelty_score} />
        <ScoreMeter label="严谨性" value={analysis.rigor_score} />
      </div>
      <section className="detail-section">
        <h3>核心结论</h3>
        <p>{analysis.summary}</p>
      </section>
      <section className="detail-section">
        <h3>研究问题</h3>
        <p>{analysis.research_question}</p>
      </section>
      <section className="detail-section">
        <h3>主要贡献</h3>
        <ul>{analysis.contributions.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
      <section className="detail-section two-column-detail">
        <div><h3>方法</h3><p>{analysis.methodology}</p></div>
        <div><h3>实验与证据</h3><p>{analysis.experiments}</p></div>
      </section>
      <section className="detail-section">
        <h3>局限与风险</h3>
        <ul>{analysis.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
      <section className="detail-section two-column-detail">
        <div><h3>与主题的关系</h3><p>{analysis.relevance_reason}</p></div>
        <div><h3>阅读建议</h3><p>{analysis.reading_advice}</p></div>
      </section>
      <div className="keyword-row detail-keywords">
        {analysis.keywords.map((keyword) => <span key={keyword}>{keyword}</span>)}
      </div>
    </div>
  )
}

export function PaperDrawer({ paperId, onClose }: { paperId: number | null; onClose: () => void }) {
  const [notes, setNotes] = useState('')
  const [tags, setTags] = useState('')
  const [topicId, setTopicId] = useState('')
  const [profileId, setProfileId] = useState('')
  const [sourceMode, setSourceMode] = useState<'abstract' | 'pdf'>('abstract')
  const [analysisId, setAnalysisId] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const initializedPaperId = useRef<number | null>(null)
  const queryClient = useQueryClient()

  const paperQuery = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => api<Paper>('/papers/' + paperId),
    enabled: Boolean(paperId),
    refetchInterval: (query) => {
      const state = query.state.data?.latest_analysis?.status
      return state === 'pending' || state === 'running' ? 3000 : false
    },
  })
  const profilesQuery = useQuery({
    queryKey: ['llm-profiles'],
    queryFn: () => api<LLMProfile[]>('/llm-profiles'),
    enabled: Boolean(paperId),
  })
  const topicsQuery = useQuery({
    queryKey: ['topics'],
    queryFn: () => api<Topic[]>('/topics'),
    enabled: Boolean(paperId),
  })

  useEffect(() => {
    const paper = paperQuery.data
    if (!paperId) {
      initializedPaperId.current = null
      return
    }
    if (!paper || !topicsQuery.data || initializedPaperId.current === paper.id) return
    initializedPaperId.current = paper.id
    setNotes(paper.personal_notes ?? '')
    setTags(paper.user_tags.join(', '))
    setAnalysisId(paper.latest_analysis?.id ?? null)
    const firstTopic = paper.topics[0]
    setTopicId(firstTopic ? String(firstTopic.id) : '')
    const topic = topicsQuery.data?.find((item) => item.id === firstTopic?.id)
    setSourceMode(topic?.analyze_pdf ? 'pdf' : 'abstract')
  }, [paperId, paperQuery.data, topicsQuery.data])

  const defaultProfile =
    profilesQuery.data?.find((profile) => profile.is_default) ?? profilesQuery.data?.[0]
  const selectedProfileId = profileId || (defaultProfile ? String(defaultProfile.id) : '')

  const patchMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api('/papers/' + paperId, { method: 'PATCH', body: JSON.stringify(values) }),
    onSuccess: () => {
      setNotice('已保存')
      void queryClient.invalidateQueries({ queryKey: ['paper', paperId] })
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const analyzeMutation = useMutation({
    mutationFn: () =>
      api<{ analysis_id: number }>('/papers/' + paperId + '/actions/analyze', {
        method: 'POST',
        body: JSON.stringify({
          topic_id: topicId ? Number(topicId) : null,
          llm_profile_id: selectedProfileId ? Number(selectedProfileId) : null,
          source_mode: sourceMode,
        }),
      }),
    onSuccess: (data) => {
      setNotice('解读任务 #' + data.analysis_id + ' 已提交')
      void queryClient.invalidateQueries({ queryKey: ['paper', paperId] })
      void queryClient.invalidateQueries({ queryKey: ['papers'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })

  const paper = paperQuery.data
  const selectedAnalysis = useMemo(
    () => paper?.analyses?.find((item) => item.id === analysisId) ?? paper?.latest_analysis ?? null,
    [analysisId, paper],
  )

  useEffect(() => {
    if (!paperId) return
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [paperId, onClose])

  if (!paperId) return null

  return (
    <div className="drawer-layer">
      <button className="drawer-scrim" onClick={onClose} aria-label="关闭论文详情" />
      <aside className="paper-drawer" role="dialog" aria-modal="true" aria-label="论文详情">
        <header className="drawer-header">
          <div>
            <span className="drawer-kicker">{paper ? paper.arxiv_id + 'v' + paper.version : '读取中'}</span>
            <h2>{paper?.title ?? '正在读取论文'}</h2>
          </div>
          <button className="icon-button" onClick={onClose} title="关闭">
            <X size={20} />
          </button>
        </header>

        {paperQuery.isError ? (
          <div className="empty-state error-state">{(paperQuery.error as Error).message}</div>
        ) : !paper ? (
          <div className="drawer-loading" />
        ) : (
          <div className="drawer-body">
            <div className="paper-toolbar">
              <button
                className={'icon-text-button ' + (paper.is_read ? 'selected' : '')}
                onClick={() => patchMutation.mutate({ is_read: !paper.is_read })}
              >
                <Check size={16} /> {paper.is_read ? '已读' : '标记已读'}
              </button>
              <button
                className={'icon-text-button ' + (paper.is_starred ? 'starred' : '')}
                onClick={() => patchMutation.mutate({ is_starred: !paper.is_starred })}
              >
                <Star size={16} fill={paper.is_starred ? 'currentColor' : 'none'} /> 星标
              </button>
              <a className="icon-text-button" href={paper.abs_url} target="_blank" rel="noreferrer">
                <ExternalLink size={16} /> arXiv
              </a>
              <a className="icon-text-button" href={paper.pdf_url} target="_blank" rel="noreferrer">
                <FileText size={16} /> PDF
              </a>
              <a className="icon-text-button" href={'/api/papers/' + paper.id + '/export/markdown'}>
                <Download size={16} /> Markdown
              </a>
            </div>

            <div className="paper-bibliography">
              <span>{paper.authors.join(', ')}</span>
              <span>{formatDate(paper.published_at)} · {paper.categories.join(', ')}</span>
              {paper.comment && <span>{paper.comment}</span>}
            </div>

            <section className="abstract-section">
              <h3>Abstract</h3>
              <p>{paper.abstract}</p>
            </section>

            <section className="analysis-section">
              <div className="section-heading-row">
                <div>
                  <h3>结构化解读</h3>
                  <span>{paper.analysis_count} 个版本</span>
                </div>
                {paper.analyses && paper.analyses.length > 1 && (
                  <select
                    value={analysisId ?? ''}
                    onChange={(event) => setAnalysisId(Number(event.target.value))}
                    aria-label="解读版本"
                  >
                    {paper.analyses.map((analysis) => (
                      <option key={analysis.id} value={analysis.id}>
                        #{analysis.id} {analysis.model} · {formatDateTime(analysis.created_at)}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {selectedAnalysis ? (
                <AnalysisView analysis={selectedAnalysis} />
              ) : (
                <div className="empty-analysis"><BookOpen size={20} /> 尚无解读</div>
              )}
            </section>

            <section className="reanalyze-section">
              <div className="section-heading-row"><h3>重新解读</h3></div>
              <div className="inline-form four-columns">
                <label><span>主题</span><select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
                  <option value="">不指定主题</option>
                  {paper.topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
                </select></label>
                <label><span>云模型</span><select value={selectedProfileId} onChange={(event) => setProfileId(event.target.value)}>
                  {profilesQuery.data?.filter((profile) => profile.enabled).map((profile) => (
                    <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>
                  ))}
                </select></label>
                <label><span>内容来源</span><select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as 'abstract' | 'pdf')}>
                  <option value="abstract">摘要</option>
                  <option value="pdf">PDF 正文</option>
                </select></label>
                <button className="secondary-button" onClick={() => analyzeMutation.mutate()} disabled={analyzeMutation.isPending || !selectedProfileId}>
                  <RefreshCw size={16} className={analyzeMutation.isPending ? 'spin' : ''} /> 重新解读
                </button>
              </div>
            </section>

            <section className="notes-section">
              <div className="section-heading-row"><h3>科研笔记</h3></div>
              <label className="field-stack"><span>标签</span><input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="例如：待读、baseline、复现实验" /></label>
              <label className="field-stack"><span>个人笔记</span><textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={7} /></label>
              <button
                className="secondary-button"
                onClick={() => patchMutation.mutate({ personal_notes: notes, user_tags: tags.split(',') })}
                disabled={patchMutation.isPending}
              >
                <Save size={16} /> 保存笔记
              </button>
            </section>
          </div>
        )}
        {notice && <button className="drawer-notice" onClick={() => setNotice(null)}>{notice}</button>}
      </aside>
    </div>
  )
}
