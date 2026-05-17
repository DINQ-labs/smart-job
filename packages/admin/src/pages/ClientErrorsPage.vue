<template>
  <div class="ce-page">
    <div class="page-header">
      <h2>🪟 {{ t('clientErrorsPage.title') }}</h2>
      <div class="meta">
        <span v-if="lastUpdated" class="dim">{{ t('clientErrorsPage.lastRefresh', { time: fmtTime(lastUpdated) }) }}</span>
        <button class="btn-secondary" :disabled="loading" @click="reload">
          {{ loading ? t('clientErrorsPage.loading') : t('clientErrorsPage.refresh') }}
        </button>
      </div>
    </div>

    <!-- 过滤条 -->
    <div class="filters panel">
      <div class="filter-group">
        <label>{{ t('clientErrorsPage.timeRange') }}</label>
        <div class="seg">
          <button
            v-for="h in TIME_OPTIONS"
            :key="h.label"
            :class="{ active: hours === h.value }"
            @click="setHours(h.value)"
          >{{ h.label }}</button>
        </div>
      </div>
      <div class="filter-group">
        <label>Top N</label>
        <select v-model.number="limit" @change="reload" class="filter-input">
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
      </div>
    </div>

    <!-- 统计 -->
    <div class="stats panel">
      <div class="stat">
        <div class="stat-label">{{ t('clientErrorsPage.totalErrors') }}</div>
        <div class="stat-value">{{ data?.total ?? '-' }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ t('clientErrorsPage.affectedUsers') }}</div>
        <div class="stat-value">{{ data?.users ?? '-' }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ t('clientErrorsPage.distinctErrors') }}</div>
        <div class="stat-value">{{ data?.top.length ?? '-' }}</div>
      </div>
      <div class="stat" v-if="errorMsg">
        <div class="stat-label">⚠️ {{ t('clientErrorsPage.loadError') }}</div>
        <div class="stat-value small">{{ errorMsg }}</div>
      </div>
    </div>

    <!-- Top 表 -->
    <div class="panel">
      <table v-if="data && data.top.length" class="ce-table">
        <thead>
          <tr>
            <th class="rank">#</th>
            <th>{{ t('clientErrorsPage.thMessage') }}</th>
            <th class="num">{{ t('clientErrorsPage.thHits') }}</th>
            <th class="num">{{ t('clientErrorsPage.thUsers') }}</th>
            <th class="ts">{{ t('clientErrorsPage.thRecent') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in data.top" :key="row.message + idx">
            <td class="rank">{{ idx + 1 }}</td>
            <td class="msg">
              <code>{{ row.message }}</code>
            </td>
            <td class="num">{{ row.hits }}</td>
            <td class="num">{{ row.users }}</td>
            <td class="ts dim">{{ fmtTime(row.last_ts) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else-if="!loading" class="empty">
        {{ t('clientErrorsPage.emptyText', { range: hoursLabel }) }}
        <div class="hint">
          {{ t('clientErrorsPage.emptyHint') }}
        </div>
      </div>
      <div v-else class="empty">{{ t('clientErrorsPage.loading') }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { ClientErrorOverview } from '../types'

const { t } = useI18n()

const TIME_OPTIONS = [
  { label: '1h',  value: 1 },
  { label: '6h',  value: 6 },
  { label: '24h', value: 24 },
  { label: '7d',  value: 168 },
] as const

const hours = ref<number>(24)
const limit = ref<number>(20)
const data = ref<ClientErrorOverview | null>(null)
const loading = ref(false)
const errorMsg = ref<string>('')
const lastUpdated = ref<string>('')

const hoursLabel = computed(() => {
  const opt = TIME_OPTIONS.find(o => o.value === hours.value)
  return opt?.label ?? `${hours.value}h`
})

async function reload() {
  loading.value = true
  errorMsg.value = ''
  try {
    data.value = await api.agent_getClientErrors({ hours: hours.value, limit: limit.value })
    lastUpdated.value = new Date().toISOString()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
    data.value = null
  } finally {
    loading.value = false
  }
}

function setHours(h: number) {
  hours.value = h
  reload()
}

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

let pollTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  reload()
  // 30s 轮询(前端错误不需要 WS,聚合接口本身廉价)
  pollTimer = setInterval(reload, 30_000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.ce-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex; align-items: center; gap: 16px; margin-bottom: 16px;
}
.page-header h2 { font-size: 18px; color: #e2e8f0; }
.meta { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.dim { color: #64748b; font-size: 11px; }

.btn-secondary {
  border: none; border-radius: 5px; padding: 6px 14px; font-size: 12px; cursor: pointer;
  background: #334155; color: #94a3b8;
}
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; padding: 12px; margin-bottom: 12px;
}

.filters { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
.filter-group { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.filter-group label { font-size: 11px; color: #64748b; }
.filter-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 10px; border-radius: 5px; font-size: 12px;
}

.seg { display: inline-flex; background: #0f172a; border: 1px solid #334155; border-radius: 5px; overflow: hidden; }
.seg button {
  background: transparent; border: none; color: #94a3b8;
  padding: 6px 12px; font-size: 12px; cursor: pointer;
}
.seg button.active { background: #1d4ed8; color: white; }
.seg button:hover:not(.active) { background: #1e293b; }

.stats { display: flex; gap: 24px; }
.stat { display: flex; flex-direction: column; gap: 2px; min-width: 120px; }
.stat-label { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.4px; }
.stat-value { font-size: 18px; color: #e2e8f0; font-weight: 600; }
.stat-value.small { font-size: 12px; color: #ef4444; font-weight: 400; word-break: break-word; }

.ce-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
}
.ce-table th, .ce-table td {
  padding: 8px 10px; border-bottom: 1px solid #1e293b;
  text-align: left; vertical-align: top;
}
.ce-table th {
  color: #64748b; font-weight: 500; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.4px;
  border-bottom-color: #334155;
}
.ce-table tr:hover td { background: #1e293b; }
.ce-table .rank { color: #64748b; width: 36px; text-align: right; }
.ce-table .num  { text-align: right; width: 72px; color: #f59e0b; font-weight: 600; }
.ce-table .ts   { width: 160px; white-space: nowrap; }
.ce-table .msg code {
  background: transparent; color: #fca5a5; font-family: 'SF Mono', Menlo, monospace;
  font-size: 11.5px; word-break: break-word; line-height: 1.5;
}

.empty {
  text-align: center; padding: 32px 12px;
  color: #64748b; font-size: 13px;
}
.empty .hint {
  margin-top: 12px; font-size: 11px; color: #475569; line-height: 1.6;
  max-width: 580px; margin-left: auto; margin-right: auto;
}
</style>
