<template>
  <div class="cli-pool-page">
    <div class="page-header">
      <h1>{{ t('cliPoolPage.title') }}</h1>
      <span class="subtitle">{{ t('cliPoolPage.subtitle') }}</span>
      <button class="btn-refresh" @click="refresh">{{ t('cliPoolPage.refresh') }}</button>
    </div>

    <!-- 统计栏 -->
    <div class="stats-row">
      <div class="stat-box">
        <span class="stat-num">{{ cliSessions.length }}</span>
        <span class="stat-label">{{ t('cliPoolPage.processCount') }}</span>
      </div>
      <div class="stat-box loaded">
        <span class="stat-num">{{ totalLoadedAccounts }}</span>
        <span class="stat-label">{{ t('cliPoolPage.loadedAccounts') }}</span>
      </div>
    </div>

    <!-- 会话列表 -->
    <div class="section">
      <div v-if="cliSessions.length === 0" class="empty">
        {{ t('cliPoolPage.noProcesses') }}<br>
        {{ t('cliPoolPage.runHint1') }} <code>python boss_cli_server.py</code> {{ t('cliPoolPage.runHint2') }}
      </div>
      <div v-else class="session-cards">
        <div v-for="s in cliSessions" :key="s.session_id" class="session-card">
          <div class="card-header">
            <span class="session-badge">CLI</span>
            <span class="session-id mono">#{{ s.display_id ?? s.session_id.slice(0, 8) }}</span>
            <span class="connected-at">{{ t('cliPoolPage.connectedAt', { time: formatTime(s.connected_at) }) }}</span>
            <span class="status-dot connected" :title="t('cliPoolPage.connected')"></span>
            <div class="card-actions">
              <button class="btn-cmd" @click="openCmd(s.session_id)">{{ t('cliPoolPage.sendCommand') }}</button>
              <button class="btn-reload" @click="fetchStatus(s.session_id)" :title="t('cliPoolPage.refreshLoadedAccounts')">↻</button>
            </div>
          </div>

          <!-- 已加载 cookie_ids -->
          <div class="cookie-ids-section">
            <div class="cookie-ids-label">{{ t('cliPoolPage.loadedAccountsCookieId') }}</div>
            <div v-if="statusLoading[s.session_id]" class="hint-text">{{ t('cliPoolPage.loading') }}</div>
            <div v-else-if="!loadedIds[s.session_id]" class="hint-text">{{ t('cliPoolPage.clickToQuery') }}</div>
            <div v-else-if="loadedIds[s.session_id].length === 0" class="hint-text empty-ids">
              {{ t('cliPoolPage.noneNoRequests') }}
            </div>
            <div v-else class="id-chips">
              <span v-for="id in loadedIds[s.session_id]" :key="id" class="id-chip">
                {{ id }}
              </span>
            </div>
          </div>

          <div class="card-meta">
            <span v-if="s.ip_address" class="meta-item">IP: {{ s.ip_address }}</span>
            <span class="meta-item mono">{{ s.session_id }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>

    <!-- 发送命令弹窗 -->
    <CustomCommandModal
      v-if="activeSessionId"
      :session-id="activeSessionId"
      :ext-name="EXT_NAME_CLI"
      @close="activeSessionId = ''"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSessionsStore } from '../stores/sessions'

const { t } = useI18n()
import { api } from '../services/api'
import { formatTime } from '../utils/time'
import { EXT_NAME_CLI } from '../constants'
import CustomCommandModal from '../components/CustomCommandModal.vue'

const sessionsStore = useSessionsStore()

const cliSessions = computed(() =>
  sessionsStore.sessions.filter(s => s.ext_name === EXT_NAME_CLI)
)

// session_id → 已加载的 cookie_id 列表
const loadedIds = ref<Record<string, string[]>>({})
const statusLoading = ref<Record<string, boolean>>({})
const error = ref('')
const activeSessionId = ref('')

const totalLoadedAccounts = computed(() => {
  return Object.values(loadedIds.value).reduce((sum, ids) => sum + ids.length, 0)
})


async function fetchStatus(sessionId: string) {
  statusLoading.value[sessionId] = true
  try {
    const res = await api.sendCustomCommand(sessionId, 'GET', 'boss/get_session_status', undefined)
    // 响应格式: { ok: true, result: { ok: true, result: { data: { loaded_cookie_ids: [...] } } } }
    const inner = (res as any)?.result
    const data = inner?.result?.data ?? inner?.data ?? inner
    loadedIds.value[sessionId] = data?.loaded_cookie_ids ?? []
  } catch (e: any) {
    loadedIds.value[sessionId] = []
  } finally {
    statusLoading.value[sessionId] = false
  }
}

async function refresh() {
  error.value = ''
  await sessionsStore.fetchSessions()
  // 刷新所有已知 CLI 会话的状态
  await Promise.all(cliSessions.value.map(s => fetchStatus(s.session_id)))
}

function openCmd(sessionId: string) {
  activeSessionId.value = sessionId
}

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.cli-pool-page {
  padding: 24px;
  max-width: 1200px;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.page-header h1 {
  font-size: 20px;
  font-weight: 700;
  color: #e2e8f0;
}
.subtitle { font-size: 13px; color: #64748b; }
.btn-refresh {
  margin-left: auto;
  background: #334155;
  color: #94a3b8;
  border: 1px solid #475569;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-refresh:hover { background: #475569; color: #e2e8f0; }

.stats-row { display: flex; gap: 12px; margin-bottom: 24px; }
.stat-box {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 90px;
}
.stat-num { font-size: 28px; font-weight: 700; color: #e2e8f0; }
.stat-label { font-size: 12px; color: #64748b; }
.stat-box.loaded .stat-num { color: #34d399; }

.section { margin-bottom: 28px; }
.session-cards { display: flex; flex-direction: column; gap: 12px; }

.session-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.session-badge {
  background: #1e3a5f;
  color: #38bdf8;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.05em;
}
.session-id { font-size: 13px; color: #e2e8f0; font-weight: 600; }
.connected-at { font-size: 11px; color: #64748b; }
.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #475569;
}
.status-dot.connected { background: #34d399; }
.card-actions { margin-left: auto; display: flex; gap: 6px; }
.btn-cmd {
  background: #1d4ed8;
  color: white;
  border: none;
  border-radius: 5px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 600;
}
.btn-cmd:hover { background: #2563eb; }
.btn-reload {
  background: #334155;
  color: #94a3b8;
  border: 1px solid #475569;
  border-radius: 5px;
  padding: 5px 9px;
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}
.btn-reload:hover { background: #475569; color: #e2e8f0; }

.cookie-ids-section {
  background: #0f172a;
  border: 1px solid #1e3a5f;
  border-radius: 6px;
  padding: 10px 12px;
}
.cookie-ids-label { font-size: 11px; color: #64748b; margin-bottom: 6px; }
.hint-text { font-size: 12px; color: #475569; }
.empty-ids { font-style: italic; }
.id-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.id-chip {
  background: #1e3a5f;
  color: #7dd3fc;
  font-family: monospace;
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 12px;
}

.card-meta {
  display: flex;
  gap: 16px;
}
.meta-item { font-size: 11px; color: #475569; }

.mono { font-family: 'JetBrains Mono', monospace; }
.empty {
  color: #475569;
  font-size: 13px;
  padding: 30px 0;
  text-align: center;
  line-height: 2;
}
.empty code {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 4px;
  padding: 2px 8px;
  color: #38bdf8;
  font-family: monospace;
  font-size: 12px;
}
.error-msg { color: #ef4444; font-size: 13px; margin-top: 12px; }
</style>
