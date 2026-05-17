import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../services/api'
import type { CommandLog, AgentErrorRow, AdminEvent } from '../types'

const MAX_BUFFER = 500    // 防止 WS 长时间挂着把内存撑爆

export type ErrorSource = 'all' | 'gateway' | 'agent'

export interface UnifiedErrorRow {
  source: 'gateway' | 'agent'
  id: string                  // unique key（gw 用 request_id，agent 用 ace-{id}）
  ts: string                  // ISO 时间，倒序排
  category: string            // gateway: tool_name；agent: error_category
  message: string             // 错误摘要 / 完整文本
  agent_id?: string | null
  session_id?: string | number | null
  user_id?: string | null
  path?: string | null
  duration_ms?: number | null
  raw: CommandLog | AgentErrorRow
}

function gwToUnified(c: CommandLog): UnifiedErrorRow {
  return {
    source: 'gateway',
    id: `gw-${c.request_id}`,
    ts: c.ended_at || c.started_at,
    category: c.tool_name || c.path,
    message: c.error || '(无错误信息)',
    agent_id: c.agent_id,
    session_id: c.session_id,
    path: c.path,
    duration_ms: c.duration_ms,
    raw: c,
  }
}

function agToUnified(e: AgentErrorRow): UnifiedErrorRow {
  return {
    source: 'agent',
    id: `ag-${e.id}`,
    ts: e.created_at,
    category: e.error_category || 'unknown',
    message: e.error_message || '(无错误信息)',
    user_id: e.user_id,
    session_id: e.session_id,
    duration_ms: e.duration_ms,
    raw: e,
  }
}

export const useErrorsStore = defineStore('errors', () => {
  const gatewayErrors = ref<CommandLog[]>([])
  const agentErrors = ref<AgentErrorRow[]>([])
  const toolNames = ref<string[]>([])
  const agentCategories = ref<string[]>([])
  const loading = ref(false)
  const lastUpdated = ref<string>('')

  // 过滤参数 —— 由页面绑定 v-model
  const filters = ref({
    source: 'all' as ErrorSource,
    range: '24h' as '1h' | '24h' | '7d',
    tool_name: '',
    agent_id: '',
    session_id: '',
    keyword: '',
  })

  function sinceIso(): string {
    const now = Date.now()
    const ms = filters.value.range === '1h'
      ? 3600_000
      : filters.value.range === '7d'
        ? 7 * 24 * 3600_000
        : 24 * 3600_000
    return new Date(now - ms).toISOString()
  }

  async function fetchGateway(limit = 200, offset = 0) {
    if (filters.value.source === 'agent') return
    const r = await api.getGatewayErrors({
      since_iso: sinceIso(),
      tool_name: filters.value.tool_name || undefined,
      agent_id: filters.value.agent_id || undefined,
      session_id: filters.value.session_id || undefined,
      keyword: filters.value.keyword || undefined,
      limit, offset,
    })
    gatewayErrors.value = r.errors
    toolNames.value = r.tool_names
  }

  async function fetchAgent(limit = 200, offset = 0) {
    if (filters.value.source === 'gateway') return
    // agent 侧的 user_id 复用 agent_id 输入框（语义类似：标识用户/Agent）
    const r = await api.agent_getErrors({
      since_iso: sinceIso(),
      user_id: filters.value.agent_id || undefined,
      keyword: filters.value.keyword || undefined,
      limit, offset,
    })
    agentErrors.value = r.errors
    agentCategories.value = r.categories
  }

  async function fetchAll() {
    loading.value = true
    try {
      await Promise.all([fetchGateway(), fetchAgent()])
      lastUpdated.value = new Date().toISOString()
    } finally {
      loading.value = false
    }
  }

  /** WS 推送：command_end 事件且 ok=false → push 到 gatewayErrors 头部。 */
  function handleAdminEvent(ev: AdminEvent) {
    if (ev.event !== 'command_end' || ev.ok !== false) return
    // 转换成 CommandLog 形态（缺的字段先空）
    const row: CommandLog = {
      id: 0,
      session_id: ev.session_id,
      agent_id: ev.agent_id ?? null,
      request_id: ev.request_id,
      tool_name: ev.tool_name || '',
      method: '',
      path: ev.path || '',
      request_body: null,
      response_ok: false,
      response_body: null,
      started_at: ev.ended_at,
      ended_at: ev.ended_at,
      duration_ms: ev.duration_ms,
      error: ev.error || '',
    }
    // 先做客户端过滤；若不匹配当前过滤则忽略，避免列表里出现"看不见的"行
    if (filters.value.source === 'agent') return
    if (filters.value.tool_name && row.tool_name !== filters.value.tool_name) return
    if (filters.value.agent_id && row.agent_id !== filters.value.agent_id) return
    if (filters.value.session_id && row.session_id !== filters.value.session_id) return
    if (filters.value.keyword && !(row.error || '').toLowerCase().includes(filters.value.keyword.toLowerCase())) return
    // 去重（重连后可能重复）
    if (gatewayErrors.value.some(x => x.request_id === row.request_id)) return
    gatewayErrors.value.unshift(row)
    if (gatewayErrors.value.length > MAX_BUFFER) {
      gatewayErrors.value.length = MAX_BUFFER
    }
  }

  const merged = computed<UnifiedErrorRow[]>(() => {
    const rows: UnifiedErrorRow[] = []
    if (filters.value.source !== 'agent') {
      for (const c of gatewayErrors.value) rows.push(gwToUnified(c))
    }
    if (filters.value.source !== 'gateway') {
      for (const a of agentErrors.value) rows.push(agToUnified(a))
    }
    rows.sort((a, b) => (b.ts || '').localeCompare(a.ts || ''))
    return rows
  })

  return {
    gatewayErrors, agentErrors, toolNames, agentCategories,
    filters, loading, lastUpdated,
    merged,
    fetchAll, fetchGateway, fetchAgent,
    handleAdminEvent,
  }
})
