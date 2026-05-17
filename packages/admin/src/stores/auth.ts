import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const authenticated = ref(false)
  const checked = ref(false)
  const authEnabled = ref(true)

  async function check(): Promise<boolean> {
    try {
      const res = await fetch('/admin/me')
      if (res.ok) {
        const data = await res.json()
        authenticated.value = data.authenticated
        authEnabled.value = data.auth_enabled ?? true
      } else {
        authenticated.value = false
      }
    } catch {
      authenticated.value = false
    }
    checked.value = true
    return authenticated.value
  }

  async function login(password: string): Promise<{ ok: boolean; error?: string }> {
    try {
      const res = await fetch('/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const data = await res.json()
      if (res.ok && data.ok) {
        authenticated.value = true
        checked.value = true
        return { ok: true }
      }
      return { ok: false, error: data.error || '登录失败' }
    } catch {
      return { ok: false, error: '网络错误' }
    }
  }

  async function logout(): Promise<void> {
    try {
      await fetch('/admin/logout', { method: 'POST' })
    } catch {}
    authenticated.value = false
    checked.value = false
  }

  return { authenticated, checked, authEnabled, check, login, logout }
})
