import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  FileSearch,
  Pencil,
  Plus,
  SearchCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { api, formatDate } from '../api'
import type { LLMProfile, Topic, TopicQuerySuggestion } from '../types'

type TopicForm = {
  name: string
  query: string
  description: string
  relevance_prompt: string
  enabled: boolean
  max_results: number
  lookback_days: number
  analyze_pdf: boolean
  include_cross_list: boolean
  llm_profile_id: string
}

type CategoryPreset = {
  id: string
  label: string
  categories: string[]
}

const categoryPresets: CategoryPreset[] = [
  { id: 'ai-ml', label: '人工智能与机器学习', categories: ['cs.AI', 'cs.LG', 'stat.ML'] },
  { id: 'language', label: '大模型与自然语言', categories: ['cs.CL', 'cs.AI', 'cs.LG'] },
  { id: 'vision', label: '计算机视觉与多模态', categories: ['cs.CV', 'cs.AI', 'eess.IV'] },
  { id: 'robotics', label: '机器人与具身智能', categories: ['cs.RO', 'cs.AI', 'cs.LG'] },
  { id: 'agents', label: '智能体与世界模型', categories: ['cs.AI', 'cs.LG', 'cs.RO', 'cs.CV'] },
  { id: 'all-ai', label: '全部 AI 方向', categories: ['cs.AI', 'cs.LG', 'stat.ML', 'cs.CL', 'cs.CV', 'cs.RO'] },
  { id: 'all', label: '不限 arXiv 学科', categories: [] },
]

const emptyForm: TopicForm = {
  name: '',
  query: '',
  description: '',
  relevance_prompt: '',
  enabled: true,
  max_results: 10,
  lookback_days: 4,
  analyze_pdf: false,
  include_cross_list: false,
  llm_profile_id: '',
}

function toForm(topic: Topic): TopicForm {
  return {
    name: topic.name,
    query: topic.query,
    description: topic.description ?? '',
    relevance_prompt: topic.relevance_prompt ?? '',
    enabled: topic.enabled,
    max_results: topic.max_results,
    lookback_days: topic.lookback_days,
    analyze_pdf: topic.analyze_pdf,
    include_cross_list: topic.include_cross_list,
    llm_profile_id: topic.llm_profile_id ? String(topic.llm_profile_id) : '',
  }
}

function categoriesFromQuery(query: string) {
  return Array.from(query.matchAll(/\bcat:([A-Za-z.]+)/g), (match) => match[1])
}

function keywordsFromQuery(query: string) {
  return Array.from(query.matchAll(/\b(?:all|ti|abs):"([^"]+)"/g), (match) => match[1])
    .filter((value, index, values) => values.indexOf(value) === index)
    .slice(0, 8)
}

function presetForQuery(query: string) {
  const categories = categoriesFromQuery(query).sort().join(',')
  return categoryPresets.find((preset) => preset.categories.slice().sort().join(',') === categories)?.id
    ?? 'all-ai'
}

function topicScope(query: string) {
  const categories = categoriesFromQuery(query)
  if (!categories.length) return '全部 arXiv 学科'
  const exact = categoryPresets.find(
    (preset) => preset.categories.slice().sort().join(',') === categories.slice().sort().join(','),
  )
  return exact?.label ?? categories.join(' · ')
}

export function TopicsPage() {
  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState<TopicForm>(emptyForm)
  const [researchFocus, setResearchFocus] = useState('')
  const [categoryPresetId, setCategoryPresetId] = useState('ai-ml')
  const [queryNeedsGeneration, setQueryNeedsGeneration] = useState(true)
  const [relevanceEdited, setRelevanceEdited] = useState(false)
  const [suggestedKeywords, setSuggestedKeywords] = useState<string[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [preview, setPreview] = useState<Array<Record<string, unknown>>>([])
  const [previewComplete, setPreviewComplete] = useState(false)
  const queryClient = useQueryClient()
  const topicsQuery = useQuery({ queryKey: ['topics'], queryFn: () => api<Topic[]>('/topics') })
  const profilesQuery = useQuery({
    queryKey: ['llm-profiles'],
    queryFn: () => api<LLMProfile[]>('/llm-profiles'),
  })

  const selectedPreset = categoryPresets.find((preset) => preset.id === categoryPresetId)
    ?? categoryPresets[0]

  const prepareForm = async () => {
    if (!queryNeedsGeneration && form.query.trim()) return form
    if (!researchFocus.trim()) throw new Error('请输入想追踪的研究方向')
    const suggestion = await api<TopicQuerySuggestion>('/topics/actions/suggest-query', {
      method: 'POST',
      body: JSON.stringify({
        research_focus: researchFocus.trim(),
        categories: selectedPreset.categories,
        llm_profile_id: form.llm_profile_id ? Number(form.llm_profile_id) : null,
      }),
    })
    const prepared = {
      ...form,
      name: form.name.trim() || suggestion.name,
      query: suggestion.query,
      description: researchFocus.trim(),
      relevance_prompt: relevanceEdited ? form.relevance_prompt : suggestion.relevance_prompt,
    }
    setForm(prepared)
    setSuggestedKeywords(suggestion.keywords)
    setQueryNeedsGeneration(false)
    return prepared
  }

  const saveMutation = useMutation({
    mutationFn: async (runAfterSave: boolean) => {
      const prepared = await prepareForm()
      const payload = {
        ...prepared,
        description: researchFocus.trim() || prepared.description,
        llm_profile_id: prepared.llm_profile_id ? Number(prepared.llm_profile_id) : null,
      }
      const topic = editingId === 'new'
        ? api<Topic>('/topics', { method: 'POST', body: JSON.stringify(payload) })
        : api<Topic>('/topics/' + editingId, { method: 'PUT', body: JSON.stringify(payload) })
      return { topic: await topic, runAfterSave }
    },
    onSuccess: ({ topic, runAfterSave }) => {
      setEditingId(null)
      setNotice('主题订阅已保存')
      void queryClient.invalidateQueries({ queryKey: ['topics'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
      if (runAfterSave) runTopicMutation.mutate(topic.id)
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api('/topics/' + id, { method: 'PUT', body: JSON.stringify({ enabled }) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['topics'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api('/topics/' + id, { method: 'DELETE' }),
    onSuccess: () => {
      setNotice('主题已删除，历史论文仍保留')
      void queryClient.invalidateQueries({ queryKey: ['topics'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const previewMutation = useMutation({
    mutationFn: async () => {
      const prepared = await prepareForm()
      return api<{ items: Array<Record<string, unknown>> }>('/topics/actions/preview', {
        method: 'POST',
        body: JSON.stringify({
          query: prepared.query,
          max_results: 5,
          lookback_days: prepared.lookback_days,
          include_cross_list: prepared.include_cross_list,
        }),
      })
    },
    onSuccess: (data) => {
      setPreview(data.items)
      setPreviewComplete(true)
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const runTopicMutation = useMutation({
    mutationFn: (topicId: number) => api<{ run_id: number }>('/jobs/runs', {
      method: 'POST',
      body: JSON.stringify({ topic_ids: [topicId], send_email: false }),
    }),
    onSuccess: (data) => {
      setNotice(`检索与解读任务 #${data.run_id} 已启动`)
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })

  const openNew = () => {
    setForm({ ...emptyForm })
    setResearchFocus('')
    setCategoryPresetId('ai-ml')
    setQueryNeedsGeneration(true)
    setRelevanceEdited(false)
    setSuggestedKeywords([])
    setPreview([])
    setPreviewComplete(false)
    setEditingId('new')
  }
  const openEdit = (topic: Topic) => {
    setForm(toForm(topic))
    setResearchFocus(topic.description?.trim() || topic.name)
    setCategoryPresetId(presetForQuery(topic.query))
    setQueryNeedsGeneration(false)
    setRelevanceEdited(Boolean(topic.relevance_prompt))
    setSuggestedKeywords(keywordsFromQuery(topic.query))
    setPreview([])
    setPreviewComplete(false)
    setEditingId(topic.id)
  }
  const updateResearchFocus = (value: string) => {
    setResearchFocus(value)
    setQueryNeedsGeneration(true)
    setPreviewComplete(false)
  }
  const updateCategoryPreset = (value: string) => {
    setCategoryPresetId(value)
    setQueryNeedsGeneration(true)
    setPreviewComplete(false)
  }
  const working = saveMutation.isPending || previewMutation.isPending

  return (
    <div className="management-page">
      <div className="page-action-row">
        <div>
          <strong>{topicsQuery.data?.length ?? 0} 个研究订阅</strong>
          <span>使用自然语言描述方向，系统自动生成 arXiv 检索规则</span>
        </div>
        <button className="primary-button" onClick={openNew}><Plus size={16} /> 新建订阅</button>
      </div>

      <div className="settings-table">
        <div className="settings-table-head">
          <span>订阅</span><span>范围与数量</span><span>模型</span><span>状态</span><span />
        </div>
        {topicsQuery.isLoading ? <div className="table-loading" /> : topicsQuery.data?.length ? (
          topicsQuery.data.map((topic) => {
            const profile = profilesQuery.data?.find((item) => item.id === topic.llm_profile_id)
            return (
              <div className="settings-table-row" key={topic.id}>
                <div><strong>{topic.name}</strong><span>{topic.description || '使用高级检索式订阅'}</span></div>
                <div><strong>{topicScope(topic.query)}</strong><span>每次最多 {topic.max_results} 篇 · 回溯 {topic.lookback_days} 天 · {topic.include_cross_list ? '含交叉投稿' : '仅首次投稿'} · {topic.analyze_pdf ? '正文解读' : '摘要解读'}</span></div>
                <div><strong>{profile?.name ?? '默认模型'}</strong><span>{profile?.model ?? '跟随全局默认'}</span></div>
                <button
                  className={'switch ' + (topic.enabled ? 'on' : '')}
                  role="switch"
                  aria-checked={topic.enabled}
                  onClick={() => toggleMutation.mutate({ id: topic.id, enabled: !topic.enabled })}
                ><span /></button>
                <div className="row-actions">
                  <button className="icon-button" onClick={() => runTopicMutation.mutate(topic.id)} disabled={runTopicMutation.isPending} title="立即检索并解读"><SearchCheck size={17} /></button>
                  <button className="icon-button" onClick={() => openEdit(topic)} title="编辑"><Pencil size={17} /></button>
                  <button
                    className="icon-button danger"
                    onClick={() => window.confirm('删除此主题？历史论文和解读不会删除。') && deleteMutation.mutate(topic.id)}
                    title="删除"
                  ><Trash2 size={17} /></button>
                </div>
              </div>
            )
          })
        ) : (
          <div className="empty-state"><FileSearch size={24} /><strong>还没有研究订阅</strong><span>输入一句研究方向即可创建。</span></div>
        )}
      </div>

      {editingId !== null && (
        <div className="modal-layer">
          <button className="modal-scrim" onClick={() => setEditingId(null)} aria-label="关闭" />
          <section className="form-modal topic-modal" role="dialog" aria-modal="true" aria-label="主题订阅">
            <header><div><span className="eyebrow">RESEARCH TOPIC</span><h2>{editingId === 'new' ? '新建研究订阅' : '编辑研究订阅'}</h2></div><button className="icon-button" onClick={() => setEditingId(null)} title="关闭"><X size={20} /></button></header>

            <div className="topic-simple-form">
              <label className="field-stack topic-focus-field">
                <span>想追踪什么研究？</span>
                <textarea
                  rows={4}
                  value={researchFocus}
                  onChange={(event) => updateResearchFocus(event.target.value)}
                  placeholder="例如：关注大语言模型的长链推理、测试时计算扩展和可验证推理"
                  autoFocus
                />
              </label>
              <label className="field-stack">
                <span>arXiv 学科范围</span>
                <select value={categoryPresetId} onChange={(event) => updateCategoryPreset(event.target.value)}>
                  {categoryPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
                </select>
              </label>
              <div className={'query-readiness ' + (queryNeedsGeneration ? '' : 'ready')}>
                <Sparkles size={18} />
                <div>
                  <strong>{queryNeedsGeneration ? '等待生成检索规则' : '检索规则已就绪'}</strong>
                  <span>{suggestedKeywords.length ? suggestedKeywords.join(' · ') : '保存或预览时由云模型自动生成'}</span>
                </div>
                <span className={'status-badge ' + (queryNeedsGeneration ? '' : 'success')}>{queryNeedsGeneration ? '待生成' : '已生成'}</span>
              </div>
            </div>

            <details className="advanced-topic-settings">
              <summary><SlidersHorizontal size={16} /><span>高级设置</span><ChevronDown size={16} /></summary>
              <div className="form-grid two-columns">
                <label className="field-stack"><span>订阅名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="留空则自动生成" /></label>
                <label className="field-stack"><span>云模型</span><select value={form.llm_profile_id} onChange={(event) => { setForm({ ...form, llm_profile_id: event.target.value }); setQueryNeedsGeneration(true) }}><option value="">使用全局默认模型</option>{profilesQuery.data?.filter((item) => item.enabled).map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}</select></label>
                <label className="field-stack full-span"><span>arXiv 检索式</span><textarea rows={3} spellCheck={false} value={form.query} onChange={(event) => { setForm({ ...form, query: event.target.value }); setQueryNeedsGeneration(false); setSuggestedKeywords(keywordsFromQuery(event.target.value)); setPreviewComplete(false) }} placeholder="可手动覆盖自动生成结果" /></label>
                <label className="field-stack full-span"><span>相关性判断标准</span><textarea rows={3} value={form.relevance_prompt} onChange={(event) => { setForm({ ...form, relevance_prompt: event.target.value }); setRelevanceEdited(true) }} placeholder="留空则自动生成" /></label>
                <label className="field-stack"><span>每次最多论文数</span><input type="number" min={1} max={100} value={form.max_results} onChange={(event) => setForm({ ...form, max_results: Number(event.target.value) })} /></label>
                <label className="field-stack"><span>回溯天数</span><input type="number" min={1} max={30} value={form.lookback_days} onChange={(event) => setForm({ ...form, lookback_days: Number(event.target.value) })} /></label>
              </div>
              <div className="checkbox-row">
                <label><input type="checkbox" checked={form.analyze_pdf} onChange={(event) => setForm({ ...form, analyze_pdf: event.target.checked })} /><span>优先读取正文（LaTeX / HTML / PDF）</span></label>
                <label><input type="checkbox" checked={form.include_cross_list} onChange={(event) => setForm({ ...form, include_cross_list: event.target.checked })} /><span>包含交叉分类投稿</span></label>
                <label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span>启用每日检索</span></label>
              </div>
            </details>

            {previewComplete && (
              <div className="preview-results">
                <strong>最近匹配</strong>
                {preview.length ? preview.map((item) => <div key={String(item.arxiv_id)}><span>{String(item.primary_category ?? '')} · {formatDate(String(item.published_at))}</span><p>{String(item.title)}</p></div>) : <div className="preview-empty">当前回溯范围内没有匹配论文</div>}
              </div>
            )}
            <div className="modal-actions">
              <button className="secondary-button" onClick={() => previewMutation.mutate()} disabled={working || (!researchFocus.trim() && !form.query.trim())}><SearchCheck size={16} /> {previewMutation.isPending ? '正在生成并检索' : '预览匹配'}</button>
              <button className="ghost-button" onClick={() => saveMutation.mutate(false)} disabled={working || (!researchFocus.trim() && !form.query.trim())}>仅保存</button>
              <button className="primary-button" onClick={() => saveMutation.mutate(true)} disabled={working || (!researchFocus.trim() && !form.query.trim())}><Sparkles size={16} /> {saveMutation.isPending ? '正在保存' : '保存并立即检索'}</button>
            </div>
          </section>
        </div>
      )}

      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
