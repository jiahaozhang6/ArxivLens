import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, ExternalLink, FlaskConical, KeyRound, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { api } from '../api'
import type { LLMProfile, ModelDiscoveryResponse, ModelOption, ProviderPreset } from '../types'

type ProfileForm = {
  name: string
  provider: string
  protocol: 'openai_compatible' | 'anthropic'
  base_url: string
  model: string
  api_key: string
  enabled: boolean
  is_default: boolean
  temperature: number
  max_tokens: number
  supports_json_mode: boolean
  extra_body: string
}

const emptyForm: ProfileForm = {
  name: '',
  provider: 'deepseek',
  protocol: 'openai_compatible',
  base_url: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  api_key: '',
  enabled: true,
  is_default: true,
  temperature: 0.2,
  max_tokens: 3000,
  supports_json_mode: true,
  extra_body: '{}',
}

function toForm(profile: LLMProfile): ProfileForm {
  return {
    name: profile.name,
    provider: profile.provider,
    protocol: profile.protocol,
    base_url: profile.base_url,
    model: profile.model,
    api_key: '',
    enabled: profile.enabled,
    is_default: profile.is_default,
    temperature: profile.temperature,
    max_tokens: profile.max_tokens,
    supports_json_mode: profile.supports_json_mode,
    extra_body: JSON.stringify(profile.extra_body ?? {}, null, 2),
  }
}

export function ModelsPage() {
  const [editingId, setEditingId] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState<ProfileForm>(emptyForm)
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const profilesQuery = useQuery({
    queryKey: ['llm-profiles'],
    queryFn: () => api<LLMProfile[]>('/llm-profiles'),
  })
  const presetsQuery = useQuery({
    queryKey: ['llm-presets'],
    queryFn: () => api<ProviderPreset[]>('/llm-profiles/presets'),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      let extraBody: Record<string, unknown>
      try {
        extraBody = JSON.parse(form.extra_body || '{}')
      } catch {
        throw new Error('附加请求参数必须是合法 JSON')
      }
      const payload: Record<string, unknown> = {
        name: form.name,
        provider: form.provider,
        protocol: form.protocol,
        base_url: form.base_url,
        model: form.model,
        enabled: form.enabled,
        is_default: form.is_default,
        temperature: form.temperature,
        max_tokens: form.max_tokens,
        supports_json_mode: form.supports_json_mode,
        extra_body: extraBody,
      }
      if (editingId === 'new' || form.api_key) payload.api_key = form.api_key
      return editingId === 'new'
        ? api<LLMProfile>('/llm-profiles', { method: 'POST', body: JSON.stringify(payload) })
        : api<LLMProfile>('/llm-profiles/' + editingId, { method: 'PUT', body: JSON.stringify(payload) })
    },
    onSuccess: () => {
      setEditingId(null)
      setNotice('云模型配置已保存')
      void queryClient.invalidateQueries({ queryKey: ['llm-profiles'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api('/llm-profiles/' + id, { method: 'DELETE' }),
    onSuccess: () => {
      setNotice('云模型配置已删除')
      void queryClient.invalidateQueries({ queryKey: ['llm-profiles'] })
      void queryClient.invalidateQueries({ queryKey: ['topics'] })
      void queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (error: Error) => setNotice(error.message),
  })
  const testMutation = useMutation({
    mutationFn: (id: number) => api<{ ok: boolean }>('/llm-profiles/' + id + '/actions/test', { method: 'POST' }),
    onSuccess: () => setNotice('连接和结构化输出测试通过'),
    onError: (error: Error) => setNotice('测试失败：' + error.message),
  })
  const discoverModelsMutation = useMutation({
    mutationFn: () => api<ModelDiscoveryResponse>('/llm-profiles/actions/models', {
      method: 'POST',
      body: JSON.stringify({
        profile_id: typeof editingId === 'number' ? editingId : null,
        protocol: form.protocol,
        base_url: form.base_url,
        api_key: form.api_key.trim() || null,
      }),
    }),
    onSuccess: (data) => {
      setModelOptions(data.models)
      setNotice(`已获取 ${data.models.length} 个可用模型`)
    },
    onError: (error: Error) => {
      setModelOptions([])
      setNotice('获取模型失败：' + error.message)
    },
  })

  const applyPreset = (providerId: string) => {
    const preset = presetsQuery.data?.find((item) => item.id === providerId)
    if (!preset) return
    setModelOptions([])
    setForm({
      ...form,
      provider: preset.id,
      protocol: preset.protocol,
      base_url: preset.base_url,
      model: preset.model_hint,
      name: editingId === 'new' ? preset.label : form.name,
      supports_json_mode: preset.protocol === 'openai_compatible',
    })
  }
  const openNew = () => {
    setForm({ ...emptyForm, is_default: !(profilesQuery.data?.length) })
    setModelOptions([])
    setEditingId('new')
  }
  const openEdit = (profile: LLMProfile) => {
    setForm(toForm(profile))
    setModelOptions([])
    setEditingId(profile.id)
  }
  const selectedPreset = presetsQuery.data?.find((item) => item.id === form.provider)
  const editingProfile = typeof editingId === 'number'
    ? profilesQuery.data?.find((profile) => profile.id === editingId)
    : null
  const canDiscoverModels = Boolean(
    form.base_url.trim() && (form.api_key.trim() || editingProfile?.has_api_key),
  )

  return (
    <div className="management-page">
      <div className="page-action-row">
        <div><strong>{profilesQuery.data?.length ?? 0} 个云模型配置</strong><span>API 密钥由后端加密存储，前端不会回显</span></div>
        <button className="primary-button" onClick={openNew}><Plus size={16} /> 添加云模型</button>
      </div>

      <div className="provider-strip" aria-label="支持的云模型平台">
        {presetsQuery.data?.filter((item) => item.id !== 'custom').map((preset) => (
          <span key={preset.id}><Cloud size={15} /> {preset.label}<small>{preset.region === 'CN' ? '国内' : '国外'}</small></span>
        ))}
      </div>

      <div className="settings-table model-table">
        <div className="settings-table-head"><span>配置</span><span>供应商与模型</span><span>接口</span><span>状态</span><span /></div>
        {profilesQuery.data?.length ? profilesQuery.data.map((profile) => {
          const preset = presetsQuery.data?.find((item) => item.id === profile.provider)
          return (
            <div className="settings-table-row" key={profile.id}>
              <div><strong>{profile.name}{profile.is_default && <em className="default-badge">默认</em>}</strong><span>{profile.has_api_key ? '密钥已配置' : '缺少 API 密钥'}</span></div>
              <div><strong>{preset?.label ?? profile.provider}</strong><span>{profile.model}</span></div>
              <div className="endpoint-cell"><code>{profile.base_url}</code><span>{profile.protocol === 'anthropic' ? 'Anthropic Messages' : 'OpenAI compatible'}</span></div>
              <span className={'status-badge ' + (profile.enabled ? 'success' : '')}>{profile.enabled ? '启用' : '停用'}</span>
              <div className="row-actions">
                <button className="icon-button" onClick={() => testMutation.mutate(profile.id)} disabled={testMutation.isPending || !profile.has_api_key} title="测试连接"><FlaskConical size={17} /></button>
                <button className="icon-button" onClick={() => openEdit(profile)} title="编辑"><Pencil size={17} /></button>
                <button className="icon-button danger" onClick={() => window.confirm('删除此云模型配置？') && deleteMutation.mutate(profile.id)} title="删除"><Trash2 size={17} /></button>
              </div>
            </div>
          )
        }) : <div className="empty-state"><KeyRound size={24} /><strong>还没有云模型配置</strong><span>添加一个国内或国外云模型 API 后即可自动解读论文。</span></div>}
      </div>

      {editingId !== null && (
        <div className="modal-layer">
          <button className="modal-scrim" onClick={() => setEditingId(null)} aria-label="关闭" />
          <section className="form-modal wide-modal" role="dialog" aria-modal="true" aria-label="云模型配置">
            <header><div><span className="eyebrow">CLOUD LLM</span><h2>{editingId === 'new' ? '添加云模型' : '编辑云模型'}</h2></div><button className="icon-button" onClick={() => setEditingId(null)} title="关闭"><X size={20} /></button></header>
            <div className="form-grid two-columns">
              <label className="field-stack"><span>配置名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label className="field-stack"><span>供应商</span><select value={form.provider} onChange={(event) => applyPreset(event.target.value)}>{presetsQuery.data?.map((preset) => <option key={preset.id} value={preset.id}>{preset.label} · {preset.region === 'CN' ? '国内' : preset.region === 'Global' ? '国外' : '自定义'}</option>)}</select></label>
              <label className="field-stack full-span"><span>API Base URL</span><input value={form.base_url} onChange={(event) => { setModelOptions([]); setForm({ ...form, base_url: event.target.value }) }} /></label>
              <div className="field-stack">
                <span className="field-label-row"><label htmlFor="model-id">模型 ID</label><button type="button" className="field-action-button" onClick={() => discoverModelsMutation.mutate()} disabled={!canDiscoverModels || discoverModelsMutation.isPending} title="从云平台获取当前密钥可用的模型"><RefreshCw size={13} className={discoverModelsMutation.isPending ? 'spin' : ''} /> 获取模型</button></span>
                <input id="model-id" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} placeholder="可手动填写模型 ID" />
                {modelOptions.length > 0 && <select className="model-option-select" value={modelOptions.some((item) => item.id === form.model) ? form.model : ''} onChange={(event) => event.target.value && setForm({ ...form, model: event.target.value })} aria-label="选择云模型"><option value="">从 {modelOptions.length} 个模型中选择</option>{modelOptions.map((item) => <option key={item.id} value={item.id}>{item.label === item.id ? item.id : `${item.label} · ${item.id}`}</option>)}</select>}
              </div>
              <label className="field-stack"><span className="field-label-row"><span>API 密钥</span>{selectedPreset?.api_key_url && <a href={selectedPreset.api_key_url} target="_blank" rel="noreferrer">管理密钥 <ExternalLink size={12} /></a>}</span><input type="password" autoComplete="new-password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={editingId === 'new' ? 'sk-...' : '留空则保留原密钥'} /></label>
              <label className="field-stack"><span>协议</span><select value={form.protocol} onChange={(event) => { setModelOptions([]); setForm({ ...form, protocol: event.target.value as ProfileForm['protocol'] }) }}><option value="openai_compatible">OpenAI compatible</option><option value="anthropic">Anthropic Messages</option></select></label>
              <label className="field-stack"><span>最大输出 tokens</span><input type="number" min={128} max={128000} value={form.max_tokens} onChange={(event) => setForm({ ...form, max_tokens: Number(event.target.value) })} /></label>
              <label className="field-stack"><span>Temperature</span><input type="number" min={0} max={2} step={0.1} value={form.temperature} onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })} /></label>
              <label className="field-stack full-span"><span>附加请求参数 JSON</span><textarea rows={4} spellCheck={false} value={form.extra_body} onChange={(event) => setForm({ ...form, extra_body: event.target.value })} /></label>
            </div>
            <div className="checkbox-row">
              <label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span>启用配置</span></label>
              <label><input type="checkbox" checked={form.is_default} onChange={(event) => setForm({ ...form, is_default: event.target.checked })} /><span>设为默认模型</span></label>
              <label><input type="checkbox" checked={form.supports_json_mode} onChange={(event) => setForm({ ...form, supports_json_mode: event.target.checked })} /><span>请求 JSON mode</span></label>
            </div>
            <div className="modal-actions"><button className="primary-button" onClick={() => saveMutation.mutate()} disabled={!form.name || !form.base_url || !form.model || saveMutation.isPending}>保存配置</button></div>
          </section>
        </div>
      )}
      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}</button>}
    </div>
  )
}
