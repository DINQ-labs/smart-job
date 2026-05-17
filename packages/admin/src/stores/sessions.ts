import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SessionInfo, AdminEvent } from '../types'
import { api } from '../services/api'

export const useSessionsStore = defineStore('sessions', () => {
  const sessions = ref<SessionInfo[]>([])
  const loading = ref(false)

  async function fetchSessions() {
    loading.value = true
    try {
      const data = await api.getSessions()
      sessions.value = data.sessions.filter(s => s.status === 'connected')
    } finally {
      loading.value = false
    }
  }

  function handleEvent(event: AdminEvent) {
    if (event.event === 'init_snapshot') {
      sessions.value = event.sessions.filter(s => s.status === 'connected')
      return
    }
    if (event.event === 'session_connected') {
      const existing = sessions.value.find(s => s.session_id === event.session_id)
      if (!existing) {
        sessions.value.unshift({
          session_id: event.session_id,
          browser_id: event.browser_id,
          connected_at: event.connected_at,
          disconnected_at: null,
          account_name: '',
          user_id: event.user_id ?? '',
          app_user_id: '',
          status: 'connected',
          ip_address: event.ip_address,
          display_id: event.display_id,
          ext_name: event.ext_name,
        })
      } else {
        existing.status = 'connected'
        existing.disconnected_at = null
        if (event.ip_address) existing.ip_address = event.ip_address
        if (event.display_id) existing.display_id = event.display_id
        if (event.ext_name) existing.ext_name = event.ext_name
      }
      return
    }
    if (event.event === 'session_disconnected') {
      sessions.value = sessions.value.filter(s => s.session_id !== event.session_id)
      return
    }
    if (event.event === 'session_login') {
      const s = sessions.value.find(s => s.session_id === event.session_id)
      if (s) {
        s.account_name = event.account_name
        s.user_id = event.user_id
        if (event.app_user_id) s.app_user_id = event.app_user_id
      }
      return
    }
    if (event.event === 'session_logout') {
      const s = sessions.value.find(s => s.session_id === event.session_id)
      if (s) {
        s.account_name = ''
        s.user_id = ''
      }
      return
    }
    if (event.event === 'session_proxy_changed') {
      const s = sessions.value.find(s => s.session_id === event.session_id)
      if (s) s.proxy_url = event.proxy_url
      return
    }
  }

  return { sessions, loading, fetchSessions, handleEvent }
})
