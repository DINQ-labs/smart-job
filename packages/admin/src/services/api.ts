import type {
  SessionInfo, AgentInfo, CommandLog, GatewayStats, BrowserSlot, PoolStatus, CachedJob, ChatRecord,
  AgentGatewayStatus, AIAgentSession, AIAgentSessionDetail, AgentConvSession, AgentConvEvent, ModeStats,
  AgentUser, SSESession, ResumeStatus, ResumeDetail, UserPreferences,
  ApiGatewaySystemStatus, AgentErrorRow, ClientErrorOverview, ChipConfig,
  CaptureSession, CaptureRequest, CandidateResume,
  AdminTask, AdminTaskStats, TemplateDag,
  PortalUser, AuthIdentity, AuthEvent, RefreshToken,
} from '../types'

const BASE = '/admin'
const AGENT_BASE = '/agent-gw'
// Portal API URL: 需要配置 nginx 代理 /portal 到 job-portal-api:8771
const PORTAL_BASE = '/portal'

function handleUnauthorized(status: number) {
  if (status === 401) {
    window.location.href = '/login'
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    handleUnauthorized(res.status)
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

async function agentGet<T>(path: string): Promise<T> {
  const res = await fetch(`${AGENT_BASE}${path}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function agentPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${AGENT_BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

async function agentDel<T>(path: string): Promise<T> {
  const res = await fetch(`${AGENT_BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

async function agentPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${AGENT_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    handleUnauthorized(res.status)
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    handleUnauthorized(res.status)
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

async function portalGet<T>(path: string): Promise<T> {
  const res = await fetch(`${PORTAL_BASE}${path}`)
  if (!res.ok) {
    handleUnauthorized(res.status)
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

async function portalPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${PORTAL_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(err.error || `HTTP ${res.status}`)
  }
  return res.json()
}

async function portalDel<T>(path: string): Promise<T> {
  const res = await fetch(`${PORTAL_BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  login: (password: string) =>
    post<{ ok: boolean; error?: string }>('/login', { password }),
  logout: () => post<{ ok: boolean }>('/logout'),

  getSessions: () => get<{ sessions: SessionInfo[] }>('/sessions'),
  getSession: (id: string) => get<SessionInfo>(`/sessions/${id}`),
  logoutSession: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/logout`),
  disconnectSession: (id: string) => post<{ ok: boolean }>(`/sessions/${id}/disconnect`),
  setProxy: (id: string, proxy_url: string) =>
    post<{ ok: boolean; proxy_url: string }>(`/sessions/${id}/set-proxy`, { proxy_url }),

  getProxyPool: () =>
    get<{ strategy: string; size: number; proxies: { url: string; in_use: number; sessions: string[] }[] }>('/proxy-pool'),
  updateProxyPool: (action: 'add' | 'remove', proxy_url: string) =>
    post<{ ok: boolean }>('/proxy-pool', { action, proxy_url }),
  sendCustomCommand: (id: string, method: string, path: string, body?: unknown) =>
    post<{ ok: boolean; result: unknown }>(`/sessions/${id}/command`, { method, path, body }),

  getAgents: () => get<{ agents: AgentInfo[] }>('/agents'),
  getCommands: (params: { session_id?: string; agent_id?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params.session_id) qs.set('session_id', params.session_id)
    if (params.agent_id) qs.set('agent_id', params.agent_id)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return get<{ commands: CommandLog[]; limit: number; offset: number }>(`/commands${q ? '?' + q : ''}`)
  },
  getStats: () => get<GatewayStats>('/stats'),

  // 全局错误日志：MCP/扩展工具执行失败
  getGatewayErrors: (params: {
    since_iso?: string; tool_name?: string; agent_id?: string;
    session_id?: string; keyword?: string; limit?: number; offset?: number;
  }) => {
    const qs = new URLSearchParams()
    if (params.since_iso) qs.set('since_iso', params.since_iso)
    if (params.tool_name) qs.set('tool_name', params.tool_name)
    if (params.agent_id) qs.set('agent_id', params.agent_id)
    if (params.session_id) qs.set('session_id', params.session_id)
    if (params.keyword) qs.set('keyword', params.keyword)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return get<{ errors: CommandLog[]; tool_names: string[]; limit: number; offset: number }>(
      `/errors${q ? '?' + q : ''}`,
    )
  },

  listCommandRegistry: (ext_name?: string) => {
    const q = ext_name ? `?ext_name=${encodeURIComponent(ext_name)}` : ''
    return get<{ commands: any[]; groups: Record<string, any[]> }>(`/command-registry${q}`)
  },
  updateCommandRegistry: (id: number, fields: Record<string, unknown>) =>
    patch<{ ok: boolean }>(`/command-registry/${id}`, fields),

  // 云端动态命令推送（Phase 1a）
  getCurrentDynamicConfig: () =>
    get<{ ok: boolean; config: any | null }>('/extension/config/current'),
  listDynamicConfigHistory: (limit = 50, offset = 0) =>
    get<{ ok: boolean; items: any[] }>(`/extension/config/history?limit=${limit}&offset=${offset}`),
  listDynamicConfigActiveSessions: () =>
    get<{ ok: boolean; total: number; sessions: any[] }>('/extension/config/active-sessions'),
  pushDynamicConfig: (payload: {
    version: string
    chains?: Record<string, any>
    dynamic_commands?: any[]
    notes?: string
    source_ref?: string
  }) =>
    post<{
      ok: boolean
      version: string
      total_sessions: number
      sent: number
      failed: number
      ack: any[]
      error?: string
    }>('/extension/config/push', payload),
  getDynamicConfigByVersion: (version: string) =>
    get<{ ok: boolean; config: any | null; error?: string }>(
      `/extension/config/${encodeURIComponent(version)}`,
    ),

  getPoolStatus: (platform?: string) => {
    const q = platform ? `?platform=${encodeURIComponent(platform)}` : ''
    return get<{ ok: boolean; stats: PoolStatus; slots: BrowserSlot[]; queue: any[] }>(`/pool/status${q}`)
  },
  setPoolCapacity: (platform: string, max_slots: number, idle_timeout_sec?: number) =>
    patch<{ ok: boolean }>('/pool/capacity', { platform, max_slots, idle_timeout_sec }),

  forceReleaseSlot: (browser_id: string) =>
    post<{ ok: boolean; browser_id: string; evicted_user: string }>(
      `/pool/slots/${encodeURIComponent(browser_id)}/force-release`
    ),

  // ── Job Cache ──────────────────────────────────────────────────────────────
  getJobCache: (params?: { platform?: string; keyword?: string; city_code?: string; has_detail?: boolean; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.platform) qs.set('platform', params.platform)
    if (params?.keyword) qs.set('keyword', params.keyword)
    if (params?.city_code) qs.set('city_code', params.city_code)
    if (params?.has_detail != null) qs.set('has_detail', String(params.has_detail))
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return get<{ jobs: CachedJob[]; limit: number; offset: number }>(
      `/job-cache${q ? '?' + q : ''}`
    )
  },
  getJobCacheDetail: (platform: string, externalId: string) =>
    get<CachedJob>(`/job-cache/${platform}/${externalId}`),
  fetchJobCacheDetail: (platform: string, externalId: string) =>
    post<{ ok: boolean; job: CachedJob; error?: string }>(`/job-cache/${platform}/${externalId}/fetch-detail`),

  // ── Chat Records ───────────────────────────────────────────────────────────
  getChatRecords: (params?: { keyword?: string; account_name?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.keyword) qs.set('keyword', params.keyword)
    if (params?.account_name) qs.set('account_name', params.account_name)
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return get<{ ok: boolean; records: ChatRecord[] }>(`/chat-records${q ? '?' + q : ''}`)
  },

  // ── System Monitor ──────────────────────────────────────────────────────────
  getSystemStatus: () =>
    get<ApiGatewaySystemStatus>('/system-status'),

  // ── Agent Gateway (job-agent-gateway :8769) ───────────────────────────────
  agent_getStatus: () =>
    agentGet<AgentGatewayStatus>('/admin/status'),

  agent_getSessions: () =>
    agentGet<{ sessions: AIAgentSession[] }>('/admin/sessions'),

  agent_getSessionDetail: (userId: string) =>
    agentGet<AIAgentSessionDetail>(`/admin/sessions/${encodeURIComponent(userId)}`),

  agent_abortSession: (userId: string) =>
    agentPost<{ ok: boolean; aborted: boolean }>(`/admin/sessions/${encodeURIComponent(userId)}/abort`),

  agent_deleteSession: (userId: string) =>
    agentDel<{ ok: boolean }>(`/admin/sessions/${encodeURIComponent(userId)}`),

  agent_getSSESessions: () =>
    agentGet<{ sessions: SSESession[] }>('/admin/sse/sessions'),

  agent_getUsers: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ users: AgentUser[]; limit: number; offset: number }>(
      `/admin/users${q ? '?' + q : ''}`
    )
  },

  agent_getHistory: (params?: { user_id?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.user_id) qs.set('user_id', params.user_id)
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ sessions: AgentConvSession[]; limit: number; offset: number }>(
      `/history${q ? '?' + q : ''}`
    )
  },

  agent_getHistoryEvents: (sessionId: number) =>
    agentGet<{ events: AgentConvEvent[]; session_id: number }>(
      `/history/${sessionId}`
    ),

  agent_getModeStats: (days = 7) =>
    agentGet<{ stats: ModeStats[]; days: number }>(
      `/admin/mode-stats?days=${days}`
    ),

  // 全局错误日志：agent 对话异常（Claude API / agent loop / 工具调用）
  agent_getErrors: (params: {
    since_iso?: string; user_id?: string; keyword?: string;
    limit?: number; offset?: number;
  }) => {
    const qs = new URLSearchParams()
    if (params.since_iso) qs.set('since_iso', params.since_iso)
    if (params.user_id) qs.set('user_id', params.user_id)
    if (params.keyword) qs.set('keyword', params.keyword)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{
      errors: AgentErrorRow[]; categories: string[]; limit: number; offset: number
    }>(`/admin/errors${q ? '?' + q : ''}`)
  },

  // ── Chip configs(动态快捷指令配置 — 数据 / 策略分离架构,数据层)─────────
  agent_listChips: (params: { role?: string; platform?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.role)     qs.set('role', params.role)
    if (params.platform) qs.set('platform', params.platform)
    const q = qs.toString()
    return agentGet<{ chips: ChipConfig[]; version: number }>(
      `/admin/chips${q ? '?' + q : ''}`,
    )
  },
  agent_createChip: (chip: Partial<ChipConfig>) =>
    agentPost<{ chip: ChipConfig; version: number }>('/admin/chips', chip),
  agent_patchChip: (id: number, patch: Partial<ChipConfig>) =>
    agentPatch<{ chip: ChipConfig; version: number }>(`/admin/chips/${id}`, patch),
  agent_deleteChip: (id: number) =>
    agentDel<{ ok: boolean; version: number }>(`/admin/chips/${id}`),
  agent_reorderChips: (items: Array<{ id: number; sort_order: number }>) =>
    agentPost<{ updated: number; version: number }>('/admin/chips/reorder', { items }),
  agent_listChipRules: () =>
    agentGet<{ rules: Array<{ id: string; role: string | null; platform: string | null;
                              description: string; enabled: boolean; references: string[] }> }>(
      '/admin/chip-rules',
    ),
  agent_getChipsVersion: () =>
    agentGet<{ version: number }>('/agent/cell/chips/version'),
  agent_previewChips: (params: { role: string; platform: string; lang?: string; user_id?: string }) => {
    const qs = new URLSearchParams()
    qs.set('role',     params.role)
    qs.set('platform', params.platform)
    qs.set('lang',     params.lang || 'zh')
    if (params.user_id) qs.set('user_id', params.user_id)
    return agentGet<{ chips: any[]; version: number; triggered_rules: string[]; pool_size: number }>(
      `/agent/cell/chips?${qs}`,
    )
  },

  // 前端 JS 错误聚合(audit P1 fix:来自 sidepanel window.error / unhandledrejection 上报)
  agent_getClientErrors: (params: { hours?: number; limit?: number } = {}) => {
    const qs = new URLSearchParams()
    qs.set('hours', String(params.hours ?? 24))
    qs.set('limit', String(params.limit ?? 20))
    return agentGet<ClientErrorOverview>(`/admin/client-errors?${qs}`)
  },

  agent_getResumeStatus: (userId: string) =>
    agentGet<ResumeStatus>(`/resume/status?user_id=${encodeURIComponent(userId)}`),

  agent_getResume: (userId: string) =>
    agentGet<ResumeDetail>(`/resume/${encodeURIComponent(userId)}`),

  agent_uploadResume: (userId: string, file: File) => {
    const form = new FormData()
    form.append('user_id', userId)
    form.append('file', file)
    return fetch(`${AGENT_BASE}/resume/upload`, { method: 'POST', body: form }).then(async res => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
        throw new Error(err.error || `HTTP ${res.status}`)
      }
      return res.json() as Promise<{ ok: boolean; resume_id: number; parse_status: string; message: string }>
    })
  },

  agent_retryResumeParse: (userId: string) =>
    agentPost<{ ok: boolean; resume_id: number; parse_status: string }>(
      `/resume/${encodeURIComponent(userId)}/retry`
    ),

  agent_deleteResume: (userId: string) =>
    agentDel<{ ok: boolean; deleted: boolean }>(
      `/resume/${encodeURIComponent(userId)}`
    ),

  agent_getPreferences: (userId: string) =>
    agentGet<{ ok: boolean; data: UserPreferences | null }>(
      `/user-preferences/${encodeURIComponent(userId)}`
    ),

  agent_suggestPreferences: (userId: string) =>
    agentGet<{ ok: boolean; suggestion: UserPreferences }>(
      `/user-preferences/${encodeURIComponent(userId)}/suggest`
    ),

  agent_savePreferences: (userId: string, prefs: Omit<UserPreferences, 'user_id'>) =>
    agentPost<{ ok: boolean }>(
      `/user-preferences/${encodeURIComponent(userId)}`,
      prefs
    ),

  // ── API Capture ─────────────────────────────────────────────────────────────

  capture_getSessions: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ sessions: CaptureSession[]; limit: number; offset: number }>(
      `/api/sessions${q ? '?' + q : ''}`
    )
  },

  capture_getSession: (sessionId: string) =>
    agentGet<CaptureSession>(`/api/sessions/${encodeURIComponent(sessionId)}`),

  capture_getRequests: (sessionId: string, params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ total: number; requests: CaptureRequest[] }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/requests${q ? '?' + q : ''}`
    )
  },

  capture_analyze: (sessionId: string) =>
    agentPost<{ analysis: string; session_id: string; model?: string }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/analyze`
    ),

  capture_delete: (sessionId: string) =>
    agentDel<{ status: string; deleted: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}`
    ),

  capture_downloadRaw: async (sessionId: string) => {
    const res = await fetch(`${AGENT_BASE}/api/sessions/${encodeURIComponent(sessionId)}/download`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="?([^";\n]+)"?/)
    const filename = match?.[1] || `capture_${sessionId}.json`
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  capture_downloadAnalysis: async (sessionId: string) => {
    const res = await fetch(`${AGENT_BASE}/api/sessions/${encodeURIComponent(sessionId)}/download-analysis`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="?([^";\n]+)"?/)
    const filename = match?.[1] || `analysis_${sessionId}.md`
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  capture_implement: (sessionId: string, instructions: string, target: string) =>
    fetch(`${AGENT_BASE}/api/sessions/${encodeURIComponent(sessionId)}/implement`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instructions, target }),
    }),

  // ── Candidate Resumes ──────────────────────────────────────────────────────

  candidateResume_list: (params?: { platform?: string; job_id?: string; user_id?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params?.platform) qs.set('platform', params.platform)
    if (params?.job_id) qs.set('job_id', params.job_id)
    if (params?.user_id) qs.set('user_id', params.user_id)
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ resumes: CandidateResume[]; limit: number; offset: number }>(
      `/candidate-resumes${q ? '?' + q : ''}`
    )
  },

  candidateResume_get: (platform: string, candidateId: string, userId?: string) => {
    const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
    return agentGet<CandidateResume>(
      `/candidate-resumes/${encodeURIComponent(platform)}/${encodeURIComponent(candidateId)}${qs}`
    )
  },

  candidateResume_downloadFile: async (platform: string, candidateId: string, userId?: string) => {
    const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
    const res = await fetch(
      `${AGENT_BASE}/candidate-resumes/${encodeURIComponent(platform)}/${encodeURIComponent(candidateId)}/file${qs}`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename\*=UTF-8''([^;\s]+)/)
    const fallback = disposition.match(/filename="?([^";\n]+)"?/)
    const filename = match ? decodeURIComponent(match[1]) : fallback?.[1] || `resume_${candidateId}.pdf`
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },

  candidateResume_delete: (platform: string, candidateId: string, userId?: string) => {
    const qs = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
    return agentDel<{ ok: boolean; deleted: boolean }>(
      `/candidate-resumes/${encodeURIComponent(platform)}/${encodeURIComponent(candidateId)}${qs}`
    )
  },

  // ── 长任务系统(admin 视角)──────────────────────
  admin_taskStats: () =>
    agentGet<{ stats: AdminTaskStats }>('/admin/tasks/stats'),

  admin_listTasks: (params?: {
    status?: string         // 'running,paused_user_action' 多值逗号分隔
    user_id?: string
    role?: string
    platform?: string
    template_id?: string
    keyword?: string
    limit?: number
    offset?: number
  }) => {
    const qs = new URLSearchParams()
    if (params?.status)      qs.set('status', params.status)
    if (params?.user_id)     qs.set('user_id', params.user_id)
    if (params?.role)        qs.set('role', params.role)
    if (params?.platform)    qs.set('platform', params.platform)
    if (params?.template_id) qs.set('template_id', params.template_id)
    if (params?.keyword)     qs.set('keyword', params.keyword)
    if (params?.limit != null)  qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return agentGet<{ tasks: AdminTask[]; total: number }>(
      `/admin/tasks${q ? '?' + q : ''}`
    )
  },

  admin_taskDetail: (taskId: number) =>
    agentGet<{ task: AdminTask }>(`/admin/tasks/${taskId}`),

  admin_forceCancelTask: (taskId: number) =>
    agentPost<{ ok: boolean; found_runner: boolean }>(
      `/admin/tasks/${taskId}/force-cancel`
    ),

  admin_templateDags: () =>
    agentGet<{ templates: TemplateDag[] }>('/admin/tasks/templates'),

  // ── MCP 调用观测面 ─────────────────────────────────
  admin_mcpMetrics: (params?: { hours?: number; bucket?: 'minute' | 'hour' }) => {
    const qs = new URLSearchParams()
    if (params?.hours != null) qs.set('hours', String(params.hours))
    if (params?.bucket)        qs.set('bucket', params.bucket)
    const q = qs.toString()
    return agentGet<{
      hours: number
      bucket: string
      overview: { total: number; ok: number; failed: number; risk: number; avg_ms: number }
      timeseries: Array<{ ts: string; platform: string; calls: number; risks: number }>
      risk_top:   Array<{ risk_signal: string; platform: string; hits: number; last_seen: string | null }>
      user_top:   Array<{ user_id: string; platform: string; calls: number; risks: number; avg_ms: number; last_call: string | null }>
    }>(`/admin/mcp-metrics${q ? '?' + q : ''}`)
  },

  // ── Portal Users (job-portal-api) ─────────────────────────────────────────────

  portal_listUsers: (params?: {
    q?: string
    role?: 'user' | 'staff' | 'admin'
    limit?: number
    offset?: number
  }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.role) qs.set('role', params.role)
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return portalGet<{ users: PortalUser[]; total: number }>(
      `/admin/users${q ? '?' + q : ''}`
    )
  },

  portal_getUser: (userId: string) =>
    portalGet<PortalUser>(`/admin/users/${encodeURIComponent(userId)}`),

  portal_updateUserRole: (userId: string, role: 'user' | 'staff' | 'admin') =>
    portalPatch<{ ok: boolean }>(`/admin/users/${encodeURIComponent(userId)}/role`, { role }),

  portal_setUserDisabled: (userId: string, disabled: boolean) =>
    portalPatch<{ ok: boolean }>(`/admin/users/${encodeURIComponent(userId)}/disabled`, { disabled }),

  portal_listUserIdentities: (userId: string) =>
    portalGet<{ identities: AuthIdentity[] }>(`/admin/users/${encodeURIComponent(userId)}/identities`),

  portal_unlinkIdentity: (userId: string, identityId: string) =>
    portalDel<{ ok: boolean }>(`/admin/users/${encodeURIComponent(userId)}/identities/${encodeURIComponent(identityId)}`),

  portal_listUserEvents: (userId: string, limit = 50) =>
    portalGet<{ events: AuthEvent[] }>(`/admin/users/${encodeURIComponent(userId)}/events?limit=${limit}`),

  portal_listUserTokens: (userId: string) =>
    portalGet<{ tokens: RefreshToken[] }>(`/admin/users/${encodeURIComponent(userId)}/tokens`),

  portal_revokeToken: (tokenId: string) =>
    portalDel<{ ok: boolean }>(`/admin/tokens/${encodeURIComponent(tokenId)}`),

  portal_revokeAllUserTokens: (userId: string) =>
    portalDel<{ ok: boolean; revoked: number }>(`/admin/users/${encodeURIComponent(userId)}/tokens`),

  // ── AutoFill 扩展：表单知识库 / 抓包（agent-gw /admin/autofill/*）──
  autofill_stats: () =>
    agentGet<{ ok: boolean; stats: any }>('/admin/autofill/stats'),

  autofill_listTemplates: (params: { q?: string; limit?: number; offset?: number } = {}) => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const s = qs.toString()
    return agentGet<{ ok: boolean; total: number; items: any[] }>(
      '/admin/autofill/templates' + (s ? '?' + s : ''))
  },

  autofill_getTemplate: (id: number) =>
    agentGet<{ ok: boolean; template: any }>(`/admin/autofill/templates/${id}`),

  autofill_patchTemplateField: (
    id: number, body: { ident: string; profile_key?: string; widget?: string },
  ) =>
    agentPatch<{ ok: boolean; field: any }>(`/admin/autofill/templates/${id}/fields`, body),

  autofill_deleteTemplate: (id: number) =>
    agentDel<{ ok: boolean }>(`/admin/autofill/templates/${id}`),

  autofill_listCaptures: (
    params: { processed?: string; site?: string; limit?: number; offset?: number } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.processed) qs.set('processed', params.processed)
    if (params.site) qs.set('site', params.site)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const s = qs.toString()
    return agentGet<{ ok: boolean; total: number; items: any[] }>(
      '/admin/autofill/captures' + (s ? '?' + s : ''))
  },

  autofill_getCapture: (id: number) =>
    agentGet<{ ok: boolean; capture: any }>(`/admin/autofill/captures/${id}`),

  autofill_enrichCapture: (id: number) =>
    agentPost<{ ok: boolean; merged: number }>(`/admin/autofill/captures/${id}/enrich`),

  autofill_deleteCapture: (id: number) =>
    agentDel<{ ok: boolean }>(`/admin/autofill/captures/${id}`),
}
