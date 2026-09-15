import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, History, Plus, Send, Square, Trash2, UserRound } from 'lucide-react'
import { api, formatDateTime, streamApi } from '../api'
import type { LLMProfile, PaperChatMessage, PaperChatSession } from '../types'

const QUICK_QUESTIONS = [
  '用三句话概括核心创新',
  '详细解释方法流程和关键技术',
  '实验设计是否足以支持论文结论？',
  '这篇论文有哪些局限和可改进方向？',
  '它对我的后续科研有什么可复用的启发？',
]

function temporaryMessage(role: 'user' | 'assistant', content: string): PaperChatMessage {
  return {
    id: -Date.now() - (role === 'assistant' ? 1 : 0),
    role,
    content,
    status: role === 'assistant' ? 'streaming' : 'completed',
    llm_profile_id: null,
    provider: null,
    model: null,
    model_routing: null,
    error_message: null,
    created_at: new Date().toISOString(),
    completed_at: role === 'assistant' ? null : new Date().toISOString(),
  }
}

export function PaperChatPanel({ paperId }: { paperId: number }) {
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null)
  const [profileId, setProfileId] = useState('')
  const [question, setQuestion] = useState('')
  const [liveMessages, setLiveMessages] = useState<PaperChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamModel, setStreamModel] = useState('')
  const [streamError, setStreamError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const messageEndRef = useRef<HTMLDivElement | null>(null)
  const initializedSessionRef = useRef<number | null>(null)
  const queryClient = useQueryClient()

  const profilesQuery = useQuery({
    queryKey: ['llm-profiles'],
    queryFn: () => api<LLMProfile[]>('/llm-profiles'),
  })
  const sessionsQuery = useQuery({
    queryKey: ['paper-chat-sessions', paperId],
    queryFn: () => api<PaperChatSession[]>(`/papers/${paperId}/chat/sessions`),
  })
  const effectiveSessionId = activeSessionId ?? sessionsQuery.data?.[0]?.id ?? null
  const sessionQuery = useQuery({
    queryKey: ['paper-chat-session', paperId, effectiveSessionId],
    queryFn: () => api<PaperChatSession>(`/papers/${paperId}/chat/sessions/${effectiveSessionId}`),
    enabled: effectiveSessionId !== null,
  })

  useEffect(() => {
    const loaded = sessionQuery.data
    if (streaming || !loaded || initializedSessionRef.current === loaded.id) return
    initializedSessionRef.current = loaded.id
    setLiveMessages(loaded.messages ?? [])
  }, [sessionQuery.data, streaming])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [liveMessages])

  useEffect(() => () => abortRef.current?.abort(), [])

  const enabledProfiles = useMemo(
    () => profilesQuery.data?.filter((profile) => profile.enabled) ?? [],
    [profilesQuery.data],
  )
  const defaultProfile = enabledProfiles.find((profile) => profile.is_default) ?? enabledProfiles[0]
  const selectedProfileId = profileId
    || (sessionQuery.data?.preferred_llm_profile_id ? String(sessionQuery.data.preferred_llm_profile_id) : '')
    || (defaultProfile ? String(defaultProfile.id) : '')

  const createSession = async () => {
    const created = await api<PaperChatSession>(`/papers/${paperId}/chat/sessions`, {
      method: 'POST',
      body: JSON.stringify({
        title: null,
        llm_profile_id: selectedProfileId ? Number(selectedProfileId) : null,
      }),
    })
    setActiveSessionId(created.id)
    initializedSessionRef.current = created.id
    setLiveMessages([])
    await queryClient.invalidateQueries({ queryKey: ['paper-chat-sessions', paperId] })
    return created.id
  }

  const deleteMutation = useMutation({
    mutationFn: (sessionId: number) => api(`/papers/${paperId}/chat/sessions/${sessionId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      setActiveSessionId(null)
      setLiveMessages([])
      await queryClient.invalidateQueries({ queryKey: ['paper-chat-sessions', paperId] })
    },
    onError: (error: Error) => setStreamError(error.message),
  })

  const sendQuestion = async (preset?: string) => {
    const content = (preset ?? question).trim()
    if (!content || streaming || !selectedProfileId) return
    setQuestion('')
    setStreamError(null)
    setStreamModel('正在连接模型')
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller

    let sessionId = effectiveSessionId
    try {
      if (sessionId === null) sessionId = await createSession()
      const userMessage = temporaryMessage('user', content)
      const assistantMessage = temporaryMessage('assistant', '')
      setLiveMessages((current) => [...current, userMessage, assistantMessage])
      let streamFailed = false
      await streamApi(
        `/papers/${paperId}/chat/sessions/${sessionId}/messages/stream`,
        { content, llm_profile_id: Number(selectedProfileId) },
        (event, data) => {
          if (event === 'model') {
            const profile = data.profile as { name?: string; model?: string } | undefined
            setStreamModel(`${profile?.name ?? '云模型'} · ${profile?.model ?? ''}`)
          } else if (event === 'reset') {
            setLiveMessages((current) => current.map((message, index) => (
              index === current.length - 1 ? { ...message, content: '' } : message
            )))
            setStreamModel('首选模型异常，正在切换备用模型')
          } else if (event === 'delta') {
            const text = String(data.text ?? '')
            setLiveMessages((current) => current.map((message, index) => (
              index === current.length - 1
                ? { ...message, content: message.content + text }
                : message
            )))
          } else if (event === 'done') {
            const warning = data.warning ? String(data.warning) : null
            if (warning) setStreamModel(warning)
          } else if (event === 'error') {
            streamFailed = true
            setStreamError(String(data.message ?? '模型生成失败'))
          }
        },
        controller.signal,
      )
      if (!streamFailed) setStreamModel('回答已完成')
    } catch (error) {
      if ((error as Error).name === 'AbortError') {
        setStreamModel('已停止生成')
      } else {
        setStreamError((error as Error).message)
      }
    } finally {
      abortRef.current = null
      setStreaming(false)
      if (sessionId !== null) {
        await queryClient.invalidateQueries({ queryKey: ['paper-chat-session', paperId, sessionId] })
      }
      await queryClient.invalidateQueries({ queryKey: ['paper-chat-sessions', paperId] })
    }
  }

  return (
    <section className="paper-chat-panel" aria-label="论文 AI 问答">
      <header className="paper-chat-header">
        <div><Bot size={18} /><span><strong>论文问答</strong><small>{streamModel || '基于摘要与已有解读'}</small></span></div>
        <button className="icon-button" onClick={() => void createSession()} disabled={streaming} title="新建对话"><Plus size={18} /></button>
      </header>

      <div className="paper-chat-controls">
        <label><Bot size={15} /><select value={selectedProfileId} onChange={(event) => setProfileId(event.target.value)} disabled={streaming} aria-label="问答模型">
          {enabledProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}
        </select></label>
        <label><History size={15} /><select value={effectiveSessionId ?? ''} onChange={(event) => { initializedSessionRef.current = null; setProfileId(''); setActiveSessionId(event.target.value ? Number(event.target.value) : null) }} disabled={streaming} aria-label="对话历史">
          {!sessionsQuery.data?.length && <option value="">新对话</option>}
          {sessionsQuery.data?.map((item) => <option key={item.id} value={item.id}>{item.title} · {formatDateTime(item.updated_at)}</option>)}
        </select></label>
        {effectiveSessionId !== null && <button className="icon-button danger" onClick={() => deleteMutation.mutate(effectiveSessionId)} disabled={streaming || deleteMutation.isPending} title="删除当前对话"><Trash2 size={16} /></button>}
      </div>

      <div className="paper-chat-messages" aria-live="polite">
        {!liveMessages.length ? (
          <div className="paper-chat-empty">
            <Bot size={24} />
            <strong>从一个具体科研问题开始</strong>
            <span>快速问答使用摘要和已有解读，不会阻塞后台深度分析。</span>
            <div className="quick-question-list">
              {QUICK_QUESTIONS.map((item) => <button key={item} onClick={() => void sendQuestion(item)} disabled={!selectedProfileId}>{item}</button>)}
            </div>
          </div>
        ) : liveMessages.map((message) => (
          <article className={`chat-message ${message.role}`} key={message.id}>
            <span className="chat-avatar">{message.role === 'assistant' ? <Bot size={16} /> : <UserRound size={16} />}</span>
            <div>
              <div className="chat-message-meta"><strong>{message.role === 'assistant' ? '科研助手' : '我'}</strong>{message.model && <span>{message.model}</span>}</div>
              <p>{message.content || (message.status === 'streaming' ? '正在思考…' : '未生成内容')}</p>
              {message.error_message && <small className="chat-message-warning">{message.error_message}</small>}
            </div>
          </article>
        ))}
        {streamError && <div className="paper-chat-error">{streamError}</div>}
        <div ref={messageEndRef} />
      </div>

      <div className="paper-chat-composer">
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void sendQuestion()
            }
          }}
          rows={3}
          maxLength={4000}
          placeholder="询问方法、实验、局限或研究启发…"
          disabled={streaming}
        />
        {streaming ? (
          <button className="secondary-button" onClick={() => abortRef.current?.abort()}><Square size={15} /> 停止</button>
        ) : (
          <button className="primary-button" onClick={() => void sendQuestion()} disabled={!question.trim() || !selectedProfileId}><Send size={16} /> 发送</button>
        )}
      </div>
    </section>
  )
}
