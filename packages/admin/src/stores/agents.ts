import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AgentInfo, AdminEvent } from '../types'
import { api } from '../services/api'

export const useAgentsStore = defineStore('agents', () => {
  const agents = ref<AgentInfo[]>([])
  const loading = ref(false)

  async function fetchAgents() {
    loading.value = true
    try {
      const data = await api.getAgents()
      agents.value = data.agents.filter(a => a.status === 'connected')
    } finally {
      loading.value = false
    }
  }

  function handleEvent(event: AdminEvent) {
    if (event.event === 'init_snapshot') {
      agents.value = event.agents.filter(a => a.status === 'connected')
      return
    }
    if (event.event === 'agent_connected') {
      const existing = agents.value.find(a => a.agent_id === event.agent_id)
      if (!existing) {
        agents.value.unshift({
          agent_id: event.agent_id,
          connected_at: event.connected_at,
          disconnected_at: null,
          bound_session: event.bound_session ?? null,
          bound_account: event.bound_account ?? '',
          user_id: event.user_id ?? '',
          status: 'connected',
          request_count: 0,
        })
      } else {
        existing.status = 'connected'
        existing.disconnected_at = null
        if (event.bound_session) {
          existing.bound_session = event.bound_session
          if (event.bound_account) existing.bound_account = event.bound_account
        }
      }
      return
    }
    if (event.event === 'agent_disconnected') {
      agents.value = agents.value.filter(a => a.agent_id !== event.agent_id)
      return
    }
    if (event.event === 'agent_bound') {
      const a = agents.value.find(a => a.agent_id === event.agent_id)
      if (a) {
        a.bound_session = event.session_id
        if (event.account_name) a.bound_account = event.account_name
        if (event.user_id) a.user_id = event.user_id
      }
      return
    }
  }

  return { agents, loading, fetchAgents, handleEvent }
})
