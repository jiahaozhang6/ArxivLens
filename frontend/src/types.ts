export type Decision = 'unreviewed' | 'relevant' | 'maybe' | 'irrelevant'

export interface AdminUser {
  id: number
  username: string
  created_at: string
  password_changed_at: string
}

export interface AuthStatus {
  setup_required: boolean
  authenticated: boolean
  user: AdminUser | null
  session_expires_at: string | null
}

export interface Topic {
  id: number
  name: string
  query: string
  description: string | null
  relevance_prompt: string | null
  enabled: boolean
  max_results: number
  lookback_days: number
  analyze_pdf: boolean
  include_cross_list: boolean
  llm_profile_id: number | null
  created_at: string
  updated_at: string
}

export interface TopicQuerySuggestion {
  name: string
  query: string
  relevance_prompt: string
  keywords: string[]
}

export interface Analysis {
  id: number
  topic_id: number | null
  llm_profile_id: number | null
  provider: string
  model: string
  language: string
  source_mode: string
  paper_version: number
  prompt_version: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  summary: string | null
  research_question: string | null
  contributions: string[]
  methodology: string | null
  experiments: string | null
  limitations: string[]
  reading_advice: string | null
  relevance_reason: string | null
  keywords: string[]
  novelty_score: number | null
  rigor_score: number | null
  relevance_score: number | null
  model_routing: {
    preferred_profile_id: number
    fallback_used: boolean
    used: {
      profile_id: number
      name: string
      provider: string
      model: string
    }
    attempts: Array<{
      profile_id: number | null
      name: string
      provider: string
      model: string
      status: 'completed' | 'failed' | 'skipped'
      error: string
    }>
  } | null
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface PaperTopic {
  id: number
  name: string
  discovered_at: string
}

export interface Paper {
  id: number
  arxiv_id: string
  version: number
  title: string
  abstract: string
  authors: string[]
  categories: string[]
  primary_category: string | null
  published_at: string
  updated_at: string
  first_seen_at: string
  abs_url: string
  pdf_url: string
  doi: string | null
  journal_ref: string | null
  comment: string | null
  is_read: boolean
  is_starred: boolean
  decision: Decision
  personal_notes: string | null
  user_tags: string[]
  topics: PaperTopic[]
  latest_analysis: Analysis | null
  analysis_count: number
  analyses?: Analysis[]
}

export interface PaperChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: 'streaming' | 'completed' | 'failed' | 'cancelled'
  llm_profile_id: number | null
  provider: string | null
  model: string | null
  model_routing: Analysis['model_routing']
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface PaperChatSession {
  id: number
  paper_id: number
  title: string
  preferred_llm_profile_id: number | null
  created_at: string
  updated_at: string
  messages?: PaperChatMessage[]
}

export interface PaperListResponse {
  items: Paper[]
  page: number
  page_size: number
  total: number
  pages: number
  stats: {
    total: number
    unread: number
    starred: number
    relevant: number
    analyzed: number
  }
}

export interface PaperDateItem {
  date: string
  count: number
}

export interface PaperDatesResponse {
  dates: string[]
  items: PaperDateItem[]
}

export type PaperBulkAction =
  | 'mark_read'
  | 'mark_unread'
  | 'star'
  | 'unstar'
  | 'relevant'
  | 'maybe'
  | 'irrelevant'
  | 'unreviewed'
  | 'delete'

export interface LLMProfile {
  id: number
  name: string
  provider: string
  protocol: 'openai_compatible' | 'anthropic'
  base_url: string
  model: string
  enabled: boolean
  is_default: boolean
  temperature: number
  max_tokens: number
  supports_json_mode: boolean
  extra_body: Record<string, unknown>
  has_api_key: boolean
  created_at: string
  updated_at: string
}

export interface ModelOption {
  id: string
  label: string
}

export interface ModelDiscoveryResponse {
  models: ModelOption[]
}

export interface ProviderPreset {
  id: string
  label: string
  region: string
  protocol: 'openai_compatible' | 'anthropic'
  base_url: string
  model_hint: string
  api_key_url: string
}

export interface ScheduleSettings {
  enabled: boolean
  timezone: string
  hour: number
  minute: number
  digest_language: string
  public_base_url: string
  updated_at: string
}

export interface EmailSettings {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  username: string | null
  from_email: string
  from_name: string
  recipients: string[]
  security: 'starttls' | 'ssl' | 'plain'
  subject_prefix: string
  has_password: boolean
  updated_at: string
}

export interface AppSettings {
  schedule: ScheduleSettings
  email: EmailSettings
}

export interface DailyEmailResult {
  day: string
  papers: number
  recipients: number
}

export interface NetworkTimeStatus {
  status: 'synchronized' | 'stale' | 'system_fallback' | 'disabled'
  synchronized: boolean
  source: string | null
  current_time: string
  system_time: string
  offset_seconds: number
  round_trip_ms: number | null
  synchronized_at: string | null
  last_attempt_at: string | null
  error: string | null
  timezone: string
  schedule_time: string
  next_run_at: string
}

export interface RunLog {
  id: number
  job_type: string
  trigger: string
  status: 'running' | 'completed' | 'partial' | 'failed'
  progress_stage: 'starting' | 'fetching' | 'analyzing' | 'emailing' | 'completed' | 'failed' | string
  progress_current: number
  progress_total: number
  progress_percent: number
  started_at: string
  finished_at: string | null
  topics_processed: number
  papers_found: number
  papers_new: number
  analyses_completed: number
  analyses_failed: number
  emails_sent: number
  message: string | null
  error_details: { errors?: string[]; warnings?: string[]; retry_recommended?: boolean } | null
}

export interface SystemStatus {
  ready: boolean
  enabled_topics: number
  enabled_profiles: number
  topics_without_model: number
  schedule_enabled: boolean
  schedule_state: 'disabled' | 'pending' | 'starting' | 'running' | 'completed' | 'partial' | 'failed' | 'overdue'
  schedule_time: string
  schedule_timezone: string
  scheduled_today_at: string | null
  current_time: string
  email_enabled: boolean
  last_run: RunLog | null
}
