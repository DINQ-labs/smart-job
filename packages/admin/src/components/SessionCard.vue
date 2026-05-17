<template>
  <div class="session-card" v-bind="$attrs" :class="{ disconnected: session.status === 'disconnected' }">
    <div class="card-header">
      <span class="status-dot" :class="session.status"></span>
      <span class="ext-name-tag" v-if="session.ext_name">{{ session.ext_name }}</span>
      <span class="display-id">#{{ session.display_id ?? '?' }}</span>
      <span class="badge" :class="session.status">{{ session.status === 'connected' ? t('sessionCard.online') : t('sessionCard.offline') }}</span>
    </div>
    <div class="card-body">
      <div class="info-row">
        <span class="k">{{ t('sessionCard.user') }}</span>
        <span class="v mono" :title="session.user_id">{{ session.user_id ? session.user_id.slice(0, 8) + '…' : '-' }}</span>
      </div>
      <div class="info-row">
        <span class="k">{{ t('sessionCard.account') }}</span>
        <span class="v">{{ session.account_name || t('sessionCard.notLoggedIn') }}</span>
      </div>
      <div class="info-row" v-if="session.app_user_id">
        <span class="k">{{ t('sessionCard.platformId') }}</span>
        <span class="v mono">{{ session.app_user_id }}</span>
      </div>
      <div class="info-row">
        <span class="k">IP</span>
        <span class="v mono">{{ session.ip_address || '-' }}</span>
      </div>
      <div class="info-row">
        <span class="k">{{ t('sessionCard.connectedAt') }}</span>
        <span class="v">{{ fmtTime(session.connected_at) }}</span>
      </div>
      <div class="info-row">
        <span class="k">{{ t('sessionCard.tokenCache') }}</span>
        <span class="v">{{ t('sessionCard.jobCountSuffix', { n: session.job_store_count ?? '-' }) }}</span>
      </div>
      <div class="info-row">
        <span class="k">{{ t('sessionCard.proxy') }}</span>
        <span class="v mono proxy-val" :class="{ active: currentProxy }" :title="currentProxy || t('sessionCard.directConnect')">
          {{ currentProxy || t('sessionCard.directConnect') }}
        </span>
      </div>
      <div class="info-row sid-row">
        <span class="k">Session ID</span>
        <span class="v mono sid" :title="session.session_id">{{ session.session_id.slice(0, 16) }}…</span>
      </div>
    </div>
    <!-- 代理选择行 -->
    <div class="proxy-row" v-if="session.status === 'connected'">
      <select v-model="proxyDraft" class="proxy-select" @change="onSelectChange">
        <option value="">{{ t('sessionCard.directConnectOption') }}</option>
        <option v-for="p in poolStore.proxies" :key="p.url" :value="p.url">
          {{ p.url }}  {{ p.in_use > 0 ? t('sessionCard.proxyInUse', { n: p.in_use }) : '' }}
        </option>
        <option value="__custom__">{{ t('sessionCard.customAddress') }}</option>
      </select>
      <button class="btn btn-proxy" :disabled="loading" @click="doSetProxy">
        {{ loading ? '…' : t('sessionCard.apply') }}
      </button>
    </div>
    <!-- 自定义代理输入框 -->
    <div class="proxy-row" v-if="session.status === 'connected' && showCustomInput">
      <input
        v-model="customProxy"
        class="proxy-input"
        :class="{ 'has-value': customProxy }"
        :placeholder="t('sessionCard.customProxyPlaceholder')"
        @keydown.enter="doSetProxy"
      />
    </div>
    <div class="card-actions" v-if="session.status === 'connected'">
      <button class="btn btn-warn" :disabled="loading" @click="askLogout">{{ t('sessionCard.logout') }}</button>
      <button class="btn btn-danger" :disabled="loading" @click="askDisconnect">{{ t('sessionCard.forceDisconnect') }}</button>
      <button class="btn btn-secondary" @click="openCustomCmd">{{ t('sessionCard.customCommand') }}</button>
    </div>
    <div class="card-actions" v-else>
      <span class="offline-hint">{{ t('sessionCard.offlineHint') }}</span>
    </div>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="error-toast">{{ errorMsg }}</div>
  </div>

  <!-- 自定义命令弹窗 -->
  <CustomCommandModal
    v-if="showModal"
    :session-id="session.session_id"
    :ext-name="session.ext_name"
    @close="showModal = false"
  />

  <!-- 确认弹窗 -->
  <ConfirmModal
    v-if="confirmState.visible"
    :title="confirmState.title"
    :message="confirmState.message"
    :confirm-text="confirmState.confirmText"
    :variant="confirmState.variant"
    @confirm="confirmState.onConfirm"
    @cancel="confirmState.visible = false"
  />
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { SessionInfo } from '../types'
import { api } from '../services/api'
import { useProxyPoolStore } from '../stores/proxyPool'
import CustomCommandModal from './CustomCommandModal.vue'
import ConfirmModal from './ConfirmModal.vue'

defineOptions({ inheritAttrs: false })
const props = defineProps<{ session: SessionInfo }>()
const poolStore = useProxyPoolStore()
const { t } = useI18n()

const loading = ref(false)
const showModal = ref(false)
const errorMsg = ref('')
const currentProxy = ref(props.session.proxy_url || '')
// proxyDraft: 当前 select 值（空=直连，"__custom__"=自定义，其他=池中代理）
const proxyDraft = ref(currentProxy.value || '')
const showCustomInput = ref(false)
const customProxy = ref('')

// 当父组件传入的 proxy_url 变化时（WS 事件）同步
watch(() => props.session.proxy_url, (v) => {
  currentProxy.value = v || ''
  if (v && v !== '__custom__') proxyDraft.value = v
})

// 首次挂载时加载代理池（若还未加载）
if (poolStore.proxies.length === 0) poolStore.fetch()

function onSelectChange() {
  showCustomInput.value = proxyDraft.value === '__custom__'
  if (proxyDraft.value !== '__custom__') customProxy.value = ''
}

const confirmState = reactive({
  visible: false,
  title: '',
  message: '',
  confirmText: t('sessionCard.confirm'),
  variant: 'danger' as 'danger' | 'warn',
  onConfirm: () => {},
})

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function showError(msg: string) {
  errorMsg.value = msg
  setTimeout(() => { errorMsg.value = '' }, 3000)
}

function askLogout() {
  confirmState.title = t('sessionCard.logoutTitle', { id: props.session.display_id })
  confirmState.message = t('sessionCard.logoutMessage')
  confirmState.confirmText = t('sessionCard.logout')
  confirmState.variant = 'warn'
  confirmState.onConfirm = doLogout
  confirmState.visible = true
}

function askDisconnect() {
  confirmState.title = t('sessionCard.disconnectTitle', { id: props.session.display_id })
  confirmState.message = t('sessionCard.disconnectMessage')
  confirmState.confirmText = t('sessionCard.forceDisconnect')
  confirmState.variant = 'danger'
  confirmState.onConfirm = doDisconnect
  confirmState.visible = true
}

async function doLogout() {
  confirmState.visible = false
  loading.value = true
  try {
    await api.logoutSession(props.session.session_id)
  } catch (e: any) {
    showError(t('sessionCard.logoutFailed', { msg: e.message }))
  } finally {
    loading.value = false
  }
}

async function doDisconnect() {
  confirmState.visible = false
  loading.value = true
  try {
    await api.disconnectSession(props.session.session_id)
  } catch (e: any) {
    showError(t('sessionCard.disconnectFailed', { msg: e.message }))
  } finally {
    loading.value = false
  }
}

function openCustomCmd() {
  showModal.value = true
}

async function doSetProxy() {
  const url = proxyDraft.value === '__custom__'
    ? customProxy.value.trim()
    : proxyDraft.value
  loading.value = true
  try {
    await api.setProxy(props.session.session_id, url)
    currentProxy.value = url
    if (url !== '__custom__') showCustomInput.value = false
    // 刷新池中使用数
    poolStore.fetch()
  } catch (e: any) {
    showError(t('sessionCard.setProxyFailed', { msg: e.message }))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.session-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px;
  transition: opacity 0.2s;
  position: relative;
}
.session-card.disconnected { opacity: 0.55; }
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.status-dot {
  width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
}
.status-dot.connected { background: #4ade80; }
.status-dot.disconnected { background: #64748b; }
.ext-name-tag { font-size: 11px; font-weight: 600; color: #7dd3fc; background: #0c2a3e; border: 1px solid #1e4a6e; border-radius: 4px; padding: 1px 6px; }
.display-id { font-size: 13px; font-weight: 700; color: #e2e8f0; flex: 1; }
.sid-row .sid { font-size: 11px; color: #64748b; }
.badge { font-size: 10px; padding: 2px 7px; border-radius: 10px; font-weight: 600; }
.badge.connected { background: #14532d; color: #4ade80; }
.badge.disconnected { background: #1e293b; color: #64748b; border: 1px solid #334155; }
.card-body { margin-bottom: 12px; }
.info-row { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 12px; }
.k { color: #64748b; }
.v { color: #e2e8f0; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.v.mono { font-family: monospace; }
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.btn { padding: 5px 10px; border: none; border-radius: 5px; cursor: pointer; font-size: 12px; font-weight: 500; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-warn { background: #92400e; color: #fcd34d; }
.btn-warn:hover:not(:disabled) { background: #b45309; }
.btn-danger { background: #7f1d1d; color: #fca5a5; }
.btn-danger:hover:not(:disabled) { background: #991b1b; }
.btn-secondary { background: #334155; color: #94a3b8; }
.btn-secondary:hover { background: #475569; }
.offline-hint { font-size: 11px; color: #475569; }

.proxy-val { font-size: 11px; color: #64748b; }
.proxy-val.active { color: #f59e0b; }

.proxy-row {
  display: flex;
  gap: 5px;
  align-items: center;
  margin-bottom: 6px;
}
.proxy-select {
  flex: 1;
  background: #0f172a;
  border: 1px solid #334155;
  color: #e2e8f0;
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 11px;
  min-width: 0;
  cursor: pointer;
}
.proxy-select:focus { outline: none; border-color: #f59e0b; }
.proxy-input {
  flex: 1;
  background: #0f172a;
  border: 1px solid #f59e0b;
  color: #e2e8f0;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-family: monospace;
  min-width: 0;
}
.proxy-input:focus { outline: none; border-color: #fbbf24; }
.btn-proxy { background: #1c3a1c; color: #86efac; font-size: 11px; padding: 4px 8px; }
.btn-proxy:hover:not(:disabled) { background: #166534; }

.error-toast {
  margin-top: 8px;
  padding: 6px 10px;
  background: #450a0a;
  border: 1px solid #7f1d1d;
  border-radius: 5px;
  font-size: 11px;
  color: #fca5a5;
}
</style>
