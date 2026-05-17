<template>
  <div class="errors-page">
    <div class="page-header">
      <h2>
        🚨 {{ t('errorLogsPage.title') }}
        <span class="ws-dot" :class="{ on: wsConnected }" :title="wsConnected ? t('errorLogsPage.wsConnected') : t('errorLogsPage.wsDisconnected')" />
      </h2>
      <div class="meta">
        <span v-if="store.lastUpdated" class="dim">
          {{ t('errorLogsPage.lastRefresh', { time: fmtTime(store.lastUpdated) }) }}
        </span>
        <button class="btn-secondary" :disabled="store.loading" @click="reload">
          {{ store.loading ? t('errorLogsPage.loading') : t('errorLogsPage.refresh') }}
        </button>
      </div>
    </div>

    <!-- 过滤条 -->
    <div class="filters panel">
      <div class="filter-group">
        <label>{{ t('errorLogsPage.timeRange') }}</label>
        <div class="seg">
          <button
            v-for="r in (['1h', '24h', '7d'] as const)"
            :key="r"
            :class="{ active: store.filters.range === r }"
            @click="setRange(r)"
          >{{ r }}</button>
        </div>
      </div>

      <div class="filter-group">
        <label>{{ t('errorLogsPage.errorSource') }}</label>
        <div class="seg">
          <button :class="{ active: store.filters.source === 'all' }"     @click="setSource('all')">{{ t('errorLogsPage.sourceAll') }}</button>
          <button :class="{ active: store.filters.source === 'gateway' }" @click="setSource('gateway')">{{ t('errorLogsPage.sourceGateway') }}</button>
          <button :class="{ active: store.filters.source === 'agent' }"   @click="setSource('agent')">{{ t('errorLogsPage.sourceAgent') }}</button>
        </div>
      </div>

      <div class="filter-group">
        <label>tool_name</label>
        <select
          v-model="store.filters.tool_name"
          :disabled="store.filters.source === 'agent'"
          class="filter-input"
        >
          <option value="">{{ t('errorLogsPage.optionAll') }}</option>
          <option v-for="t in store.toolNames" :key="t" :value="t">{{ t }}</option>
        </select>
      </div>

      <div class="filter-group">
        <label>{{ store.filters.source === 'agent' ? 'user_id' : 'agent_id' }}</label>
        <input v-model="store.filters.agent_id" :placeholder="t('errorLogsPage.blankMeansAll')" class="filter-input" />
      </div>

      <div class="filter-group">
        <label>session_id</label>
        <input
          v-model="store.filters.session_id"
          :disabled="store.filters.source === 'agent'"
          :placeholder="t('errorLogsPage.blankMeansAll')"
          class="filter-input"
        />
      </div>

      <div class="filter-group grow">
        <label>{{ t('errorLogsPage.keyword') }}</label>
        <input v-model="store.filters.keyword" :placeholder="t('errorLogsPage.keywordPh')" class="filter-input full" />
      </div>

      <div class="filter-group end">
        <button class="btn-primary" @click="reload">{{ t('errorLogsPage.apply') }}</button>
      </div>
    </div>

    <!-- 统计 -->
    <div class="stats panel">
      <div class="stat">
        <div class="stat-label">{{ t('errorLogsPage.statHits') }}</div>
        <div class="stat-value">{{ store.merged.length }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ t('errorLogsPage.sourceGateway') }}</div>
        <div class="stat-value">{{ store.gatewayErrors.length }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ t('errorLogsPage.sourceAgent') }}</div>
        <div class="stat-value">{{ store.agentErrors.length }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ t('errorLogsPage.statLatest') }}</div>
        <div class="stat-value small">{{ latestTs }}</div>
      </div>
    </div>

    <!-- 表格 -->
    <div class="panel">
      <ErrorTable
        :rows="store.merged"
        :empty-text="t('errorLogsPage.emptyText', { range: store.filters.range })"
        @detail="onDetail"
      />
      <div v-if="store.merged.length === 0 && store.filters.range !== '7d'" class="empty-hint-row">
        {{ t('errorLogsPage.notSeeingHint') }}
        <button class="link-btn" @click="setRange('7d')">{{ t('errorLogsPage.expandTo7d') }}</button>
      </div>
    </div>

    <CommandDetailModal
      v-if="selectedGw"
      :cmd="selectedGw"
      @close="selectedGw = null"
    />
    <AgentErrorDetailModal
      v-if="selectedAgent"
      :row="selectedAgent"
      @close="selectedAgent = null"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ErrorTable from '../components/ErrorTable.vue'
import CommandDetailModal from '../components/CommandDetailModal.vue'
import AgentErrorDetailModal from '../components/AgentErrorDetailModal.vue'
import { useErrorsStore, type UnifiedErrorRow } from '../stores/errors'
import { useAdminWS } from '../composables/useAdminWS'
import type { CommandLog, AgentErrorRow } from '../types'

const { t } = useI18n()

const store = useErrorsStore()
const selectedGw = ref<CommandLog | null>(null)
const selectedAgent = ref<AgentErrorRow | null>(null)

// WS 推送：实时把新失败的命令塞进来
const { connected: wsConnected } = useAdminWS((ev) => {
  store.handleAdminEvent(ev)
})

// agent 侧没 WS，10s 轮询一次
let pollTimer: ReturnType<typeof setInterval> | null = null

async function reload() {
  await store.fetchAll()
}

function setRange(r: '1h' | '24h' | '7d') {
  store.filters.range = r
  reload()
}

function setSource(s: 'all' | 'gateway' | 'agent') {
  store.filters.source = s
  reload()
}

function onDetail(row: UnifiedErrorRow) {
  if (row.source === 'gateway') selectedGw.value = row.raw as CommandLog
  else                          selectedAgent.value = row.raw as AgentErrorRow
}

const latestTs = computed(() => {
  const top = store.merged[0]
  return top ? fmtTime(top.ts) : '-'
})

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

// 关键词输入做防抖
let keywordTimer: ReturnType<typeof setTimeout> | null = null
watch(() => store.filters.keyword, () => {
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => reload(), 500)
})

onMounted(() => {
  reload()
  pollTimer = setInterval(() => {
    // 只刷 agent 侧（gateway 走 WS）
    if (store.filters.source !== 'gateway') store.fetchAgent()
  }, 10_000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (keywordTimer) clearTimeout(keywordTimer)
})
</script>

<style scoped>
.errors-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex; align-items: center; gap: 16px; margin-bottom: 16px;
}
.page-header h2 { font-size: 18px; color: #e2e8f0; display: flex; align-items: center; gap: 10px; }
.ws-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #475569;
  display: inline-block;
}
.ws-dot.on { background: #4ade80; box-shadow: 0 0 4px #4ade80; }
.meta { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.dim { color: #64748b; font-size: 11px; }
.btn-primary, .btn-secondary {
  border: none; border-radius: 5px; padding: 6px 14px; font-size: 12px; cursor: pointer;
}
.btn-primary { background: #3b82f6; color: white; }
.btn-secondary { background: #334155; color: #94a3b8; }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; padding: 12px; margin-bottom: 12px;
}
.filters {
  display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end;
}
.filter-group { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.filter-group.grow { flex: 1; min-width: 220px; }
.filter-group.end { margin-left: auto; }
.filter-group label { font-size: 11px; color: #64748b; }
.filter-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 10px; border-radius: 5px; font-size: 12px;
}
.filter-input.full { width: 100%; }
.filter-input:disabled { opacity: 0.4; cursor: not-allowed; }
.seg { display: inline-flex; background: #0f172a; border: 1px solid #334155; border-radius: 5px; overflow: hidden; }
.seg button {
  background: transparent; border: none; color: #94a3b8;
  padding: 6px 12px; font-size: 12px; cursor: pointer;
}
.seg button.active { background: #1d4ed8; color: white; }
.seg button:hover:not(.active) { background: #1e293b; }
.stats { display: flex; gap: 24px; }
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat-label { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.4px; }
.stat-value { font-size: 18px; color: #e2e8f0; font-weight: 600; }
.stat-value.small { font-size: 12px; color: #94a3b8; font-weight: 400; }
.empty-hint-row {
  text-align: center; color: #64748b; padding: 12px; font-size: 12px;
}
.link-btn {
  background: transparent; border: none; color: #60a5fa;
  cursor: pointer; padding: 0; text-decoration: underline; font-size: 12px;
}
</style>
