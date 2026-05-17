<template>
  <div class="pool-page">
    <div class="page-header">
      <h1>{{ t('browserPoolPage.title') }}</h1>
      <button class="btn-refresh" @click="refresh">{{ t('browserPoolPage.refresh') }}</button>
    </div>

    <!-- 容量配置 -->
    <div class="config-bar">
      <select v-model="configPlatform" class="select-sm">
        <option value="bosszp">{{ t('browserPoolPage.platformBosszp') }}</option>
        <option value="linkedin">LinkedIn</option>
      </select>
      <label class="config-label">{{ t('browserPoolPage.maxSlots') }}</label>
      <input v-model.number="configMaxSlots" type="number" min="1" max="500" class="input-sm" />
      <label class="config-label">{{ t('browserPoolPage.idleTimeoutHours') }}</label>
      <input v-model.number="configTimeoutHours" type="number" min="0" max="72" class="input-sm" />
      <button class="btn-save" @click="saveConfig">{{ t('browserPoolPage.saveConfig') }}</button>
      <span v-if="configMsg" class="config-msg">{{ configMsg }}</span>
    </div>

    <!-- 统计栏 -->
    <div class="stats-row" v-if="stats">
      <div class="stat-box">
        <span class="stat-num">{{ stats.total }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statTotal') }}</span>
      </div>
      <div class="stat-box idle">
        <span class="stat-num">{{ stats.idle }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statIdle') }}</span>
      </div>
      <div class="stat-box busy">
        <span class="stat-num">{{ stats.busy }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statBusy') }}</span>
      </div>
      <div class="stat-box cleaning">
        <span class="stat-num">{{ stats.cleaning }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statCleaning') }}</span>
      </div>
      <div class="stat-box offline">
        <span class="stat-num">{{ stats.offline }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statOffline') }}</span>
      </div>
      <div class="stat-box queued">
        <span class="stat-num">{{ stats.queued }}</span>
        <span class="stat-label">{{ t('browserPoolPage.statQueued') }}</span>
      </div>
    </div>

    <!-- 排队列表 -->
    <div class="section" v-if="queue.length > 0">
      <h2>{{ t('browserPoolPage.waitingQueue', { n: queue.length }) }}</h2>
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t('browserPoolPage.thUserId') }}</th>
            <th>{{ t('browserPoolPage.thPlatform') }}</th>
            <th>{{ t('browserPoolPage.thTimeoutSec') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in queue" :key="item.app_user_id">
            <td class="mono">{{ item.app_user_id }}</td>
            <td>{{ item.platform }}</td>
            <td>{{ item.timeout_sec }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 槽位列表 -->
    <div class="section">
      <h2>{{ t('browserPoolPage.browserSlots') }}</h2>
      <div v-if="slots.length === 0" class="empty">{{ t('browserPoolPage.noSlots') }}</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>Browser ID</th>
            <th>{{ t('browserPoolPage.thDisplayName') }}</th>
            <th>{{ t('browserPoolPage.thPlatform') }}</th>
            <th>{{ t('browserPoolPage.thState') }}</th>
            <th>{{ t('browserPoolPage.thUser') }}</th>
            <th>{{ t('browserPoolPage.thAssignedAt') }}</th>
            <th>{{ t('browserPoolPage.thLastSeen') }}</th>
            <th>IP</th>
            <th>{{ t('browserPoolPage.thActions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="slot in slots" :key="slot.browser_id">
            <td class="mono">{{ slot.browser_id.slice(0, 12) }}...</td>
            <td>{{ slot.display_name || '-' }}</td>
            <td>{{ slot.platform }}</td>
            <td>
              <span :class="['badge', slot.slot_state]">{{ stateLabel(slot.slot_state) }}</span>
            </td>
            <td class="mono">{{ slot.app_user_id || '-' }}</td>
            <td>{{ formatTime(slot.assigned_at) }}</td>
            <td>{{ formatTime(slot.last_seen_at) }}</td>
            <td>{{ slot.ip_address || '-' }}</td>
            <td>
              <button
                v-if="slot.slot_state !== 'offline' && slot.slot_state !== 'idle'"
                class="btn-force"
                @click="forceRelease(slot.browser_id)"
              >{{ t('browserPoolPage.forceRelease') }}</button>
              <span v-else class="no-action">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>
  </div>

  <!-- 强制释放确认弹窗 -->
  <ConfirmModal
    v-if="pendingReleaseId"
    :title="t('browserPoolPage.confirmReleaseTitle')"
    :message="t('browserPoolPage.confirmReleaseMessage', { id: pendingReleaseId.slice(0, 12) })"
    :confirm-text="t('browserPoolPage.forceRelease')"
    variant="warn"
    @confirm="doForceRelease"
    @cancel="pendingReleaseId = ''"
  />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'

const { t } = useI18n()
import { formatTime } from '../utils/time'
import ConfirmModal from '../components/ConfirmModal.vue'
import type { BrowserSlot, PoolStatus } from '../types'

const stats = ref<PoolStatus | null>(null)
const slots = ref<BrowserSlot[]>([])
const queue = ref<Array<{ app_user_id: string; platform: string; timeout_sec: number }>>([])
const error = ref('')

const pendingReleaseId = ref('')

const configPlatform = ref('bosszp')
const configMaxSlots = ref(50)
const configTimeoutHours = ref(2)
const configMsg = ref('')

let pollTimer: ReturnType<typeof setInterval> | null = null

function stateLabel(state: string): string {
  const map: Record<string, string> = {
    idle: t('browserPoolPage.statIdle'),
    busy: t('browserPoolPage.statBusy'),
    cleaning: t('browserPoolPage.statCleaning'),
    offline: t('browserPoolPage.statOffline'),
  }
  return map[state] ?? state
}


async function refresh() {
  try {
    const data = await api.getPoolStatus()
    stats.value = data.stats
    slots.value = data.slots
    queue.value = data.queue
    error.value = ''
  } catch (e: any) {
    error.value = e.message || t('browserPoolPage.loadFailed')
  }
}

async function saveConfig() {
  try {
    await api.setPoolCapacity(configPlatform.value, configMaxSlots.value, configTimeoutHours.value * 3600)
    configMsg.value = t('browserPoolPage.savedMsg')
    setTimeout(() => { configMsg.value = '' }, 2000)
  } catch (e: any) {
    configMsg.value = t('browserPoolPage.saveFailedMsg', { msg: e.message || e })
  }
}

function forceRelease(browserId: string) {
  pendingReleaseId.value = browserId
}

async function doForceRelease() {
  const browserId = pendingReleaseId.value
  pendingReleaseId.value = ''
  try {
    await api.forceReleaseSlot(browserId)
    await refresh()
  } catch (e: any) {
    error.value = t('browserPoolPage.forceReleaseFailed', { msg: e.message || e })
  }
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.pool-page {
  padding: 24px;
  max-width: 1400px;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}
.page-header h1 {
  font-size: 20px;
  font-weight: 700;
  color: #e2e8f0;
}
.btn-refresh {
  background: #334155;
  color: #94a3b8;
  border: 1px solid #475569;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-refresh:hover { background: #475569; color: #e2e8f0; }

.config-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.config-label { color: #94a3b8; font-size: 13px; }
.select-sm, .input-sm {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 5px;
  color: #e2e8f0;
  padding: 5px 10px;
  font-size: 13px;
}
.input-sm { width: 80px; }
.btn-save {
  background: #1d4ed8;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
}
.btn-save:hover { background: #2563eb; }
.config-msg { color: #34d399; font-size: 13px; }

.stats-row {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}
.stat-box {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 90px;
}
.stat-num { font-size: 28px; font-weight: 700; color: #e2e8f0; }
.stat-label { font-size: 12px; color: #64748b; }
.stat-box.idle .stat-num { color: #34d399; }
.stat-box.busy .stat-num { color: #60a5fa; }
.stat-box.cleaning .stat-num { color: #f59e0b; }
.stat-box.offline .stat-num { color: #475569; }
.stat-box.queued .stat-num { color: #a78bfa; }

.section { margin-bottom: 28px; }
.section h2 { font-size: 15px; font-weight: 600; color: #94a3b8; margin-bottom: 12px; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th {
  text-align: left;
  padding: 8px 12px;
  color: #64748b;
  border-bottom: 1px solid #334155;
  font-weight: 500;
}
.data-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #1e293b;
  color: #cbd5e1;
}
.data-table tr:hover td { background: #1e293b; }
.mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
}
.badge.idle { background: #064e3b; color: #34d399; }
.badge.busy { background: #1e3a5f; color: #60a5fa; }
.badge.cleaning { background: #451a03; color: #f59e0b; animation: pulse 1.5s infinite; }
.badge.offline { background: #1e293b; color: #475569; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.btn-force {
  background: transparent;
  border: 1px solid #ef4444;
  color: #ef4444;
  border-radius: 5px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.btn-force:hover { background: #7f1d1d; }
.no-action { color: #334155; }

.empty { color: #475569; font-size: 14px; padding: 20px 0; }
.error-msg { color: #ef4444; font-size: 13px; margin-top: 12px; }
</style>
