export interface SessionInfo {
  session_id: string
  browser_id: string
  connected_at: string
  disconnected_at: string | null
  account_name: string
  user_id: string
  app_user_id: string
  status: 'connected' | 'disconnected'
  duration_sec?: number
  job_store_count?: number
  ip_address?: string
  display_id?: number
  ext_name?: string
  proxy_url?: string
}

export interface AgentInfo {
  agent_id: string
  connected_at: string
  disconnected_at: string | null
  bound_session: string | null
  bound_account: string
  user_id: string
  status: 'connected' | 'disconnected'
  request_count: number
}

export interface CommandLog {
  id: number
  session_id: string
  agent_id: string | null
  request_id: string
  tool_name: string
  method: string
  path: string
  request_body: string | null
  response_ok: boolean | null
  response_body: string | null
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  error: string | null
  user_tier?: string | null
}

// agent_conv_events 里 event_type='error' 的行（来自 job-agent-gateway /admin/errors）
export interface AgentErrorRow {
  id: number
  session_id: number
  user_id: string
  error_category: string | null   // 来自 tool_name 列：rate_limit / auth / overloaded / timeout / connection / payment ...
  error_message: string | null    // 来自 content 列：完整错误文本（含 traceback）
  duration_ms: number | null
  mode: string | null
  created_at: string
}

// chip_configs 表行(后端 admin CRUD 端点返回)
export interface ChipConfig {
  id: number
  key: string                // 代码规则 by key 引用
  role: 'jobseeker' | 'recruiter'
  platform: string           // boss / linkedin / indeed / ...
  label_zh: string
  label_en: string
  send_text_zh: string
  send_text_en: string
  icon: string               // 可选 emoji
  weight: number             // 0-100
  grp: string                // default / onboarding / shortcut / task
  enabled: boolean
  sort_order: number
  description: string        // admin 自留备注
  updated_at: string         // ISO
  updated_by: string
}

// 前端 JS 错误聚合(来自 client_errors 表,POST /errors/client 上报)
export interface ClientErrorTopRow {
  message: string         // 截断到 150 字符
  hits: number
  users: number
  last_ts: string         // ISO 字符串(后端做 datetime → isoformat)
}

export interface ClientErrorOverview {
  total: number
  users: number
  top: ClientErrorTopRow[]
}

export interface GatewayStats {
  active_sessions: number
  active_agents: number
  today_commands: number
  avg_duration_ms: number
  error_rate_pct: number
}

export interface BrowserSlot {
  browser_id: string
  platform: string
  slot_state: 'offline' | 'idle' | 'busy' | 'cleaning'
  app_user_id: string
  assigned_at: string
  idle_timeout_sec: number
  last_seen_at: string
  last_session_id: string
  display_name: string
  ip_address: string
  created_at: string
  updated_at: string
}

export interface PoolStatus {
  platform: string | null
  total: number
  idle: number
  busy: number
  cleaning: number
  offline: number
  queued: number
  queue_by_platform: Record<string, number>
  max_slots: number | Record<string, number>
}

export interface PoolPlatformConfig {
  platform: string
  max_slots: number
  idle_timeout_sec: number
  description: string
  updated_at: string
}

export type AdminEvent =
  | { event: 'init_snapshot'; sessions: SessionInfo[]; agents: AgentInfo[] }
  | { event: 'session_connected'; session_id: string; browser_id: string; connected_at: string; ip_address?: string; display_id?: number; ext_name?: string; user_id?: string }
  | { event: 'session_disconnected'; session_id: string; disconnected_at: string; user_id?: string }
  | { event: 'session_login'; session_id: string; user_id: string; account_name: string; app_user_id?: string }
  | { event: 'session_logout'; session_id: string }
  | { event: 'session_proxy_changed'; session_id: string; proxy_url: string }
  | { event: 'agent_connected'; agent_id: string; connected_at: string; bound_session?: string; bound_account?: string; user_id?: string }
  | { event: 'agent_disconnected'; agent_id: string; disconnected_at: string; user_id?: string }
  | { event: 'agent_bound'; agent_id: string; session_id: string; account_name?: string; user_id?: string }
  | { event: 'command_start'; session_id: string; agent_id: string | null; request_id: string; tool_name: string; started_at: string }
  | { event: 'command_end'; session_id: string; agent_id: string | null; request_id: string; tool_name: string; path: string; ok: boolean; duration_ms: number; ended_at: string; error?: string }
  | { event: 'heartbeat'; session_id: string; ts: number }
  | { event: 'pool_update'; stats: PoolStatus; slots: BrowserSlot[] }

export interface AIAgentSession {
  user_id: string
  app_user_id: string | null
  ext_session_id: string | null
  history_length: number
  turn_count: number
  running: boolean
  current_tool: string | null
  last_active_at: number | null
  current_mode: string | null
  // joined from browser session (boss-api-gateway)
  bound_browser_session_id: string | null
  account_name: string | null
}

export interface AIAgentSessionDetail extends AIAgentSession {
  recent_messages: { role: string; text: string }[]
}

export interface MemoryInfo {
  rss_bytes: number
  vms_bytes: number
}

export interface DbPoolInfo {
  size: number
  min_size: number
  max_size: number
  free_size: number
}

export interface AgentGatewayStatus {
  ok: boolean
  service: string
  port: number
  model: string
  boss_gateway?: string
  job_api_gateway?: string
  active_sessions: number
  running_sessions: number
  active_sse_sessions?: number
  max_sessions?: number
  concurrent_turns?: number
  turn_slots_available?: number
  turn_slots_total?: number
  uptime_sec?: number
  memory?: MemoryInfo
  db_pool?: DbPoolInfo
  mode_distribution?: Record<string, number>
  available_modes?: { name: string; display_name: string; required_tier: string }[]
}

export interface ApiGatewaySystemStatus {
  service: string
  port: number
  uptime_sec: number
  memory: MemoryInfo
  db_pool: DbPoolInfo
}

export interface AgentConvSession {
  id: number
  user_id: string
  app_user_id: string | null
  ext_session_id: string | null
  role_type: string | null
  platform: string | null
  user_tier: string | null
  primary_mode: string | null
  mode_switches: number
  started_at: string
  ended_at: string | null
  event_count: number
  turn_count: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_creation_input_tokens: number
  total_cache_read_input_tokens: number
  total_cost_usd: number
}

export interface AgentConvEvent {
  id: number
  event_type: 'user_message' | 'assistant_text' | 'tool_call' | 'tool_result' | 'token_usage' | 'mode_detected' | 'turn_complete' | 'error' | 'aborted'
  turn_index: number
  tool_name: string | null
  content: string | null
  ok: boolean | null
  mode: string | null
  input_tokens: number | null
  output_tokens: number | null
  cache_creation_input_tokens: number | null
  cache_read_input_tokens: number | null
  cost_usd: number | null
  duration_ms: number | null
  created_at: string
}

export interface ModeStats {
  mode: string
  session_count: number
  total_turns: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_creation_input_tokens: number
  total_cache_read_input_tokens: number
  total_cost_usd: number
}

export interface CachedJob {
  id: number
  platform: string
  external_id: string
  title: string | null
  company: string | null
  city: string | null
  city_code: string | null
  country: string | null
  salary: string | null
  job_type: string | null
  experience: string | null
  education: string | null
  tags: string[] | null
  skills: string[] | null
  hr_name: string | null
  hr_title: string | null
  encrypt_boss_id: string | null
  list_security_id: string | null
  remote: boolean | null
  posted_at: string | null
  expires_at: string | null
  apply_url: string | null
  source_url: string | null
  industry: string | null
  company_size: string | null
  raw_list: Record<string, unknown> | null
  fetched_at: string
  search_session_id: string | null
  has_detail: boolean
  // detail fields (present when fetched individually)
  description?: string | null
  address?: string | null
  longitude?: number | null
  latitude?: number | null
  detail_security_id?: string | null
  raw_detail?: Record<string, unknown> | null
}

export interface AgentUser {
  user_id: string
  first_seen_at: string
  last_seen_at: string
  // resume fields (null if no resume)
  resume_id: number | null
  original_name: string | null
  parse_status: 'pending' | 'parsing' | 'done' | 'failed' | null
  uploaded_at: string | null
  parsed_at: string | null
  parse_error: string | null
  // derived from live sessions
  is_connected: boolean
  has_preferences: boolean
}

export interface ChatRecord {
  id: number
  platform: string
  external_id: string
  title: string | null
  company: string | null
  salary: string | null
  city: string | null
  hr_name: string | null
  hr_title: string | null
  encrypt_boss_id?: string | null
  chat_security_id: string | null
  account_name: string | null
  user_id: string | null
  session_id: string | null
  agent_id: string | null
  status: string | null
  raw_result?: Record<string, unknown> | null
  greeted_at: string
}

// ── Agent-gateway types (from job-agent-admin) ─────────────────────────────

export interface UserPreferences {
  user_id: string
  job_role: string
  city: string
  salary_range: string
  notes: string
}

export interface ResumeStatus {
  ok: boolean
  has_resume: boolean
  resume_id?: number
  parse_status?: 'pending' | 'parsing' | 'done' | 'failed'
  original_name?: string
  uploaded_at?: string
  parsed_at?: string
}

export interface WorkExperience {
  company: string
  title: string
  duration: string
  desc: string
}

export interface Education {
  school: string
  degree: string
  major: string
  duration: string
}

export interface Project {
  name: string
  duration: string
  desc: string
}

export interface ParsedResume {
  name: string | null
  phone: string | null
  email: string | null
  work_years: string | null
  degree: string | null
  target_salary_raw: string | null
  target_salary_min: number | null
  target_salary_max: number | null
  target_cities: string[] | null
  target_positions: string[] | null
  work_experience: WorkExperience[] | null
  education: Education[] | null
  skills: string[] | null
  projects: Project[] | null
  self_evaluation: string | null
}

export interface ResumeDetail {
  ok: boolean
  parse_status: 'pending' | 'parsing' | 'done' | 'failed'
  error?: string | null
  parsed?: ParsedResume
}

export interface SSESession {
  user_id: string
  app_user_id: string | null
  running: boolean
  current_tool: string | null
  turn_count: number
  history_length: number
  last_active_at: number | null
}

// ── API Capture ─────────────────────────────────────────────────────────────

export interface CaptureSession {
  id: string
  name: string
  tab_url: string
  request_count: number
  analysis_status: 'none' | 'analyzing' | 'done' | 'failed'
  analysis_text?: string | null
  analysis_model?: string | null
  analyzed_at?: string | null
  created_at: string
  updated_at: string
}

export interface CaptureRequest {
  id: number
  session_id: string
  request_id: string
  resource_type: string
  timestamp: number
  method: string
  url: string
  request_headers: Record<string, string> | null
  request_body: string | null
  response_status: number | null
  response_status_text: string | null
  response_headers: Record<string, string> | null
  response_mime_type: string | null
  response_body: string | null
  created_at: string
}

export interface CandidateResume {
  id: number
  platform: string
  candidate_id: string
  candidate_name: string | null
  candidate_meta: Record<string, any> | null
  file_path: string | null
  file_size: number | null
  file_type: string
  parse_status: string
  raw_text: string | null
  parsed_data: Record<string, any> | null
  source_job_id: string | null
  source_url: string | null
  downloaded_by: string | null
  created_at: string
  updated_at: string
}

// ── 长任务系统(Phase B/C/E) ──────────────────────────────
export type TaskStatus =
  | 'pending' | 'running' | 'paused_user_action'
  | 'completed' | 'failed' | 'cancelled'

export interface TaskSkippedItem {
  idx: number
  signal?: string
  reason?: string
  item_summary?: string
}

export interface AdminTask {
  id: number
  user_id: string
  role: 'jobseeker' | 'recruiter'
  platform: 'boss' | 'linkedin' | 'indeed'
  template_id: string
  title: string
  status: TaskStatus
  inputs: Record<string, any>
  progress: Record<string, any>
  paused_signal: string | null
  paused_at_idx: number | null
  skipped: TaskSkippedItem[]
  result_summary: string | null
  artifacts: Record<string, any>
  error: string | null
  created_at: string
  started_at: string | null
  paused_at: string | null
  resumed_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  heartbeat_at: string
  _lease_holder?: string | null
}

export interface AdminTaskStats {
  pending: number
  running: number
  paused_user_action: number
  completed: number
  failed: number
  cancelled: number
  total: number
}

export interface TemplateStep {
  id: string
  title: string
  op_type: string
  iter_items: boolean
  fn_name: string
  fn_module: string
}

export interface TemplateDag {
  id: string
  role: 'jobseeker' | 'recruiter'
  title: string
  description: string
  emoji: string
  estimated_min: number
  supported_platforms: string[]
  steps_by_platform: Record<string, TemplateStep[]>
}

// ── Portal Users (job-portal-api) ─────────────────────────────────────────────

export interface PortalUser {
  id: string
  email: string | null
  email_verified_at: string | null
  phone: string | null
  phone_verified_at: string | null
  name: string | null
  image: string | null
  locale: string
  role: 'user' | 'staff' | 'admin'
  disabled_at: string | null
  deleted_at: string | null
  last_login_at: string | null
  metadata: Record<string, any>
  created_at: string
  updated_at: string
  identities_count?: number
}

export interface AuthIdentity {
  id: string
  user_id: string
  kind: 'email_password' | 'phone_otp' | 'oauth' | 'webauthn' | 'magic_link'
  provider: string
  subject: string
  union_id: string | null
  email_at_link: string | null
  name_at_link: string | null
  image_at_link: string | null
  linked_at: string
  last_used_at: string | null
}

export interface AuthEvent {
  id: number
  user_id: string | null
  event_type: string
  provider: string | null
  ip_hash: string | null
  user_agent: string | null
  metadata: Record<string, any>
  created_at: string
}

export interface RefreshToken {
  id: string
  user_id: string
  expires_at: string
  revoked_at: string | null
  replaced_by: string | null
  user_agent: string | null
  ip_hash: string | null
  client: string | null
  device_id: string | null
  device_name: string | null
  last_seen_at: string | null
  created_at: string
}
