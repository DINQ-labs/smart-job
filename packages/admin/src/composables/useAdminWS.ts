import { ref, onUnmounted } from 'vue'
import type { AdminEvent } from '../types'

const RECONNECT_BASE = 1000
const RECONNECT_MAX = 30000

export function useAdminWS(onEvent: (event: AdminEvent) => void) {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let reconnectAttempt = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  function getDelay() {
    const base = Math.min(RECONNECT_BASE * Math.pow(2, reconnectAttempt++), RECONNECT_MAX)
    return base * (0.8 + Math.random() * 0.4)
  }

  function connect() {
    if (stopped) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/admin/ws`)

    ws.onopen = () => {
      connected.value = true
      reconnectAttempt = 0
    }

    ws.onclose = (ev) => {
      connected.value = false
      ws = null
      if (ev.code === 4401) {
        // 认证失败，不再重连，跳转登录页
        stopped = true
        window.location.href = '/login'
        return
      }
      if (!stopped) {
        const delay = getDelay()
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    ws.onerror = () => {
      ws?.close()
    }

    ws.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data)
        if (typeof parsed !== 'object' || parsed === null || typeof parsed.event !== 'string') return
        onEvent(parsed as AdminEvent)
      } catch (_) {}
    }
  }

  connect()

  onUnmounted(() => {
    stopped = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws?.close()
  })

  return { connected }
}
