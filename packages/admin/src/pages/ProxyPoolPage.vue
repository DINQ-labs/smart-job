<template>
  <div class="pp-page">
    <div class="page-header">
      <h2>{{ t('proxyPoolPage.title') }}</h2>
      <span class="subtitle">{{ t('proxyPoolPage.subtitle', { strategy: store.strategy || '-', n: store.proxies.length }) }}</span>
      <button class="refresh-btn" @click="store.fetch()">{{ t('proxyPoolPage.refresh') }}</button>
    </div>

    <div v-if="store.loading" class="loading">{{ t('proxyPoolPage.loading') }}</div>
    <div v-else-if="store.error" class="error-msg">{{ store.error }}</div>

    <template v-else>
      <!-- 添加代理 -->
      <div class="add-row">
        <input
          v-model="newProxy"
          class="add-input"
          :placeholder="t('proxyPoolPage.addPlaceholder')"
          @keydown.enter="doAdd"
        />
        <button class="btn-add" :disabled="!newProxy.trim() || adding" @click="doAdd">
          {{ adding ? t('proxyPoolPage.adding') : t('proxyPoolPage.add') }}
        </button>
      </div>
      <div v-if="addError" class="error-msg small">{{ addError }}</div>
      <div v-if="removeError" class="error-msg small">{{ removeError }}</div>

      <!-- 代理列表 -->
      <div v-if="store.proxies.length === 0" class="empty">{{ t('proxyPoolPage.emptyProxies') }}</div>
      <table v-else class="proxy-table">
        <thead>
          <tr>
            <th>{{ t('proxyPoolPage.thAddress') }}</th>
            <th style="width:80px">{{ t('proxyPoolPage.thProtocol') }}</th>
            <th style="width:90px">{{ t('proxyPoolPage.thInUse') }}</th>
            <th style="width:60px">{{ t('proxyPoolPage.thActions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in store.proxies" :key="p.url">
            <td class="mono">{{ p.url }}</td>
            <td><span :class="['scheme-badge', schemeClass(p.url)]">{{ scheme(p.url) }}</span></td>
            <td class="center">
              <span v-if="p.in_use > 0" class="in-use-badge">{{ t('proxyPoolPage.sessionCount', { n: p.in_use }) }}</span>
              <span v-else class="idle-badge">{{ t('proxyPoolPage.idle') }}</span>
            </td>
            <td class="center">
              <button class="btn-remove" @click="doRemove(p.url)" :disabled="removing === p.url">
                {{ removing === p.url ? '…' : t('proxyPoolPage.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 使用说明 -->
      <div class="help-box">
        <div class="help-title">{{ t('proxyPoolPage.helpTitle') }}</div>
        <ul>
          <li>{{ t('proxyPoolPage.help1') }}</li>
          <li>{{ t('proxyPoolPage.help2Pre') }}<strong>{{ store.strategy }}</strong>{{ t('proxyPoolPage.help2Post') }}</li>
          <li>{{ t('proxyPoolPage.help3') }}</li>
          <li>{{ t('proxyPoolPage.help4') }}<code>http://</code>、<code>https://</code>、<code>socks5://</code>、<code>socks4://</code></li>
          <li>{{ t('proxyPoolPage.help5') }}<code>.env</code>{{ t('proxyPoolPage.help5Mid') }}<code>PROXY_ASSIGN_STRATEGY=round_robin|random|sticky</code></li>
        </ul>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProxyPoolStore } from '../stores/proxyPool'

const { t } = useI18n()
const store = useProxyPoolStore()
const newProxy = ref('')
const adding = ref(false)
const addError = ref('')
const removing = ref('')
const removeError = ref('')

onMounted(() => store.fetch())

function scheme(url: string) {
  try { return new URL(url).protocol.replace(':', '') } catch { return '?' }
}
function schemeClass(url: string) {
  const s = scheme(url)
  return s === 'http' ? 'http' : s === 'https' ? 'https' : 'socks'
}

async function doAdd() {
  const url = newProxy.value.trim()
  if (!url) return
  adding.value = true
  addError.value = ''
  try {
    await store.addProxy(url)
    newProxy.value = ''
  } catch (e: any) {
    addError.value = t('proxyPoolPage.addFailed', { msg: e.message })
  } finally {
    adding.value = false
  }
}

async function doRemove(url: string) {
  removing.value = url
  removeError.value = ''
  try {
    await store.removeProxy(url)
  } catch (e: any) {
    removeError.value = t('proxyPoolPage.removeFailed', { msg: e.message })
  } finally {
    removing.value = ''
  }
}
</script>

<style scoped>
.pp-page { padding: 16px; background: #0f172a; min-height: 100vh; color: #e2e8f0; }

.page-header {
  display: flex; align-items: center; gap: 12px; margin-bottom: 20px;
}
.page-header h2 { font-size: 18px; color: #e2e8f0; }
.subtitle { font-size: 12px; color: #475569; flex: 1; }
.refresh-btn {
  background: #334155; color: #94a3b8; border: none;
  padding: 5px 12px; border-radius: 5px; cursor: pointer; font-size: 12px;
}
.refresh-btn:hover { background: #475569; }

.add-row { display: flex; gap: 8px; margin-bottom: 6px; }
.add-input {
  flex: 1; background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
  padding: 7px 12px; border-radius: 5px; font-size: 12px; font-family: monospace;
}
.add-input:focus { outline: none; border-color: #60a5fa; }
.btn-add {
  background: #1d4ed8; color: white; border: none;
  padding: 7px 16px; border-radius: 5px; cursor: pointer; font-size: 12px;
  white-space: nowrap;
}
.btn-add:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-add:hover:not(:disabled) { background: #2563eb; }

.proxy-table {
  width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 20px;
}
.proxy-table th {
  background: #1e293b; color: #64748b; font-weight: 600;
  padding: 7px 12px; text-align: left; border-bottom: 1px solid #334155;
}
.proxy-table td {
  padding: 8px 12px; border-bottom: 1px solid #1e293b; vertical-align: middle;
}
.proxy-table tr:hover td { background: #1e293b40; }
.mono { font-family: monospace; font-size: 11px; color: #38bdf8; }
.center { text-align: center; }

.scheme-badge {
  font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; font-family: monospace;
}
.scheme-badge.http  { background: #1e3a5f; color: #93c5fd; }
.scheme-badge.https { background: #1a3a2f; color: #6ee7b7; }
.scheme-badge.socks { background: #2d1f4e; color: #c4b5fd; }

.in-use-badge { background: #92400e; color: #fcd34d; font-size: 10px; padding: 2px 6px; border-radius: 8px; }
.idle-badge   { background: #1e293b; color: #475569; font-size: 10px; padding: 2px 6px; border-radius: 8px; }

.btn-remove {
  background: #450a0a; color: #fca5a5; border: none;
  padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
}
.btn-remove:hover:not(:disabled) { background: #7f1d1d; }
.btn-remove:disabled { opacity: 0.5; cursor: not-allowed; }

.help-box {
  background: #1e293b; border: 1px solid #334155; border-radius: 6px;
  padding: 14px 16px; font-size: 12px;
}
.help-title { font-weight: 600; color: #94a3b8; margin-bottom: 8px; }
.help-box ul { padding-left: 16px; color: #64748b; line-height: 2; }
.help-box li { list-style: disc; }
.help-box code { background: #0f172a; color: #38bdf8; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
.help-box strong { color: #e2e8f0; }

.loading { color: #475569; padding: 40px; text-align: center; }
.error-msg { color: #f87171; padding: 12px 0; }
.error-msg.small { font-size: 11px; margin-bottom: 8px; }
.empty { color: #475569; padding: 30px; text-align: center; }
</style>
