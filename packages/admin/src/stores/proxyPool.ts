import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../services/api'

export interface ProxyEntry {
  url: string
  in_use: number
  sessions: string[]
}

export const useProxyPoolStore = defineStore('proxyPool', () => {
  const proxies = ref<ProxyEntry[]>([])
  const strategy = ref('')
  const loading = ref(false)
  const error = ref('')

  async function fetch() {
    loading.value = true
    error.value = ''
    try {
      const data = await api.getProxyPool()
      proxies.value = data.proxies
      strategy.value = data.strategy
    } catch (e: any) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function addProxy(url: string) {
    await api.updateProxyPool('add', url)
    await fetch()
  }

  async function removeProxy(url: string) {
    await api.updateProxyPool('remove', url)
    await fetch()
  }

  return { proxies, strategy, loading, error, fetch, addProxy, removeProxy }
})
