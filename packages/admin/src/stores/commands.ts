import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { CommandLog, AdminEvent } from '../types'
import { api } from '../services/api'

const MAX_LIVE_EVENTS = 200

export const useCommandsStore = defineStore('commands', () => {
  const commands = ref<CommandLog[]>([])
  const liveEvents = ref<AdminEvent[]>([])
  const loading = ref(false)
  const total = ref(0)

  // 实时进行中的命令（command_start 到 command_end）
  const inFlight = ref<Map<string, Partial<CommandLog>>>(new Map())

  async function fetchCommands(params: { session_id?: string; agent_id?: string; limit?: number; offset?: number } = {}) {
    loading.value = true
    try {
      const data = await api.getCommands({ limit: 100, ...params })
      commands.value = data.commands
    } finally {
      loading.value = false
    }
  }

  function handleEvent(event: AdminEvent) {
    // 记录到实时事件流
    liveEvents.value.unshift(event as AdminEvent)
    if (liveEvents.value.length > MAX_LIVE_EVENTS) {
      liveEvents.value = liveEvents.value.slice(0, MAX_LIVE_EVENTS)
    }

    if (event.event === 'command_start') {
      inFlight.value.set(event.request_id, {
        request_id: event.request_id,
        session_id: event.session_id,
        agent_id: event.agent_id ?? null,
        tool_name: event.tool_name,
        started_at: event.started_at,
        response_ok: null,
        ended_at: null,
        duration_ms: null,
        error: null,
      })
      return
    }
    if (event.event === 'command_end') {
      const partial = inFlight.value.get(event.request_id)
      if (partial) {
        inFlight.value.delete(event.request_id)
        const existing = commands.value.find(c => c.request_id === event.request_id)
        if (existing) {
          // 已在列表里（DB 初始加载过）：直接更新
          existing.response_ok = event.ok
          existing.ended_at = event.ended_at
          existing.duration_ms = event.duration_ms
        } else {
          // 新命令（本次会话实时产生）：追加到列表头部
          commands.value.unshift({
            id: 0,
            session_id: partial.session_id ?? '',
            agent_id: partial.agent_id ?? null,
            request_id: event.request_id,
            tool_name: partial.tool_name ?? '',
            method: '',
            path: '',
            request_body: null,
            response_ok: event.ok,
            response_body: null,
            started_at: partial.started_at ?? event.ended_at,
            ended_at: event.ended_at,
            duration_ms: event.duration_ms,
            error: null,
          })
        }
      }
      return
    }
  }

  // 响应时间序列（最近 50 条已完成命令）
  function getResponseTimeSeries(): { name: string; value: number }[] {
    return commands.value
      .filter(c => c.duration_ms != null)
      .slice(0, 50)
      .reverse()
      .map(c => ({ name: c.tool_name || c.path, value: c.duration_ms! }))
  }

  // 工具调用频率
  function getToolFrequency(): { name: string; value: number }[] {
    const freq = new Map<string, number>()
    for (const c of commands.value) {
      const key = c.tool_name || c.path || 'unknown'
      freq.set(key, (freq.get(key) || 0) + 1)
    }
    return Array.from(freq.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
  }

  return {
    commands,
    liveEvents,
    loading,
    total,
    inFlight,
    fetchCommands,
    handleEvent,
    getResponseTimeSeries,
    getToolFrequency,
  }
})
