<template>
  <div class="monitor-page">
    <div class="page-header">
      <h2>{{ t('systemMonitorPage.title') }}</h2>
      <span class="auto-label">{{ t('systemMonitorPage.autoRefresh') }}</span>
      <button class="refresh-btn" @click="refresh">{{ t('systemMonitorPage.refresh') }}</button>
    </div>

    <!-- KPI Bar -->
    <div class="kpi-bar">
      <div class="kpi-item">
        <div class="kpi-num">{{ formatMB(agentStatus.memory?.rss_bytes) }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.agentMemory') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num">{{ formatMB(apiSysStatus.memory?.rss_bytes) }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.apiMemory') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num" :class="{ active: agentStatus.active_sessions }">{{ (agentStatus.active_sessions ?? 0) + (agentStatus.active_sse_sessions ?? 0) }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.activeSessions') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num" :class="{ active: agentStatus.running_sessions }">{{ agentStatus.running_sessions ?? 0 }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.runningTurn') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num" :class="{ warn: turnPressure }">{{ agentStatus.turn_slots_available ?? '-' }} / {{ agentStatus.turn_slots_total ?? '-' }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.turnSlots') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num">{{ poolStats?.busy ?? 0 }} / {{ poolStats?.total ?? 0 }}</div>
        <div class="kpi-label">{{ t('systemMonitorPage.browserPool') }}</div>
      </div>
    </div>

    <!-- Two service cards -->
    <div class="services-grid">
      <!-- Agent Gateway -->
      <div class="panel">
        <div class="card-header">
          <span class="svc-name">job-agent-gateway</span>
          <span class="svc-port">:8769</span>
          <span class="dot" :class="agentOnline ? 'green' : 'red'" />
        </div>
        <div class="metrics-grid">
          <div class="metric-box">
            <div class="metric-val" :class="{ warn: agentMemWarn }">{{ formatMB(agentStatus.memory?.rss_bytes) }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.rssMemory') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ formatUptime(agentStatus.uptime_sec) }}</div>
            <div class="metric-label">Uptime</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ agentStatus.active_sessions ?? 0 }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.wsSessions') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val" :class="{ active: agentStatus.running_sessions }">{{ agentStatus.running_sessions ?? 0 }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.running') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val" :class="{ warn: turnPressure }">{{ agentStatus.turn_slots_available ?? '-' }} / {{ agentStatus.turn_slots_total ?? '-' }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.turnSlots') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ agentStatus.active_sse_sessions ?? 0 }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.sseSessions') }}</div>
          </div>
        </div>
        <div class="pool-section">
          <div class="pool-label">{{ t('systemMonitorPage.dbPool') }}</div>
          <div class="pool-bar-track">
            <div class="pool-bar-fill" :style="{ width: agentDbPct + '%' }" :class="{ warn: agentDbPct > 70, danger: agentDbPct > 90 }" />
          </div>
          <div class="pool-text">{{ agentDbUsed }} / {{ agentStatus.db_pool?.max_size ?? '-' }} {{ t('systemMonitorPage.idleSuffix', { n: agentStatus.db_pool?.free_size ?? '-' }) }}</div>
        </div>
      </div>

      <!-- API Gateway -->
      <div class="panel">
        <div class="card-header">
          <span class="svc-name">job-api-gateway</span>
          <span class="svc-port">:8767</span>
          <span class="dot" :class="apiOnline ? 'green' : 'red'" />
        </div>
        <div class="metrics-grid">
          <div class="metric-box">
            <div class="metric-val" :class="{ warn: apiMemWarn }">{{ formatMB(apiSysStatus.memory?.rss_bytes) }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.rssMemory') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ formatUptime(apiSysStatus.uptime_sec) }}</div>
            <div class="metric-label">Uptime</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ apiStats.active_sessions }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.activeExtensions') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ apiStats.active_agents }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.agentsOnline') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val">{{ apiStats.today_commands }}</div>
            <div class="metric-label">{{ t('systemMonitorPage.todayCommands') }}</div>
          </div>
          <div class="metric-box">
            <div class="metric-val" :class="{ danger: apiStats.error_rate_pct > 5 }">{{ apiStats.error_rate_pct?.toFixed(1) }}%</div>
            <div class="metric-label">{{ t('systemMonitorPage.errorRate') }}</div>
          </div>
        </div>
        <div class="pool-section">
          <div class="pool-label">{{ t('systemMonitorPage.dbPool') }}</div>
          <div class="pool-bar-track">
            <div class="pool-bar-fill" :style="{ width: apiDbPct + '%' }" :class="{ warn: apiDbPct > 70, danger: apiDbPct > 90 }" />
          </div>
          <div class="pool-text">{{ apiDbUsed }} / {{ apiSysStatus.db_pool?.max_size ?? '-' }} {{ t('systemMonitorPage.idleSuffix', { n: apiSysStatus.db_pool?.free_size ?? '-' }) }}</div>
        </div>
        <div class="pool-section">
          <div class="pool-label">{{ t('systemMonitorPage.browserPool') }}</div>
          <div class="pool-bar-track">
            <div class="pool-bar-fill blue" :style="{ width: browserPct + '%' }" />
          </div>
          <div class="pool-text">
            idle:{{ poolStats?.idle ?? 0 }}
            busy:{{ poolStats?.busy ?? 0 }}
            cleaning:{{ poolStats?.cleaning ?? 0 }}
            offline:{{ poolStats?.offline ?? 0 }}
            <span v-if="poolStats?.queued" class="queued-badge">{{ t('systemMonitorPage.queued') }}:{{ poolStats.queued }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Charts -->
    <div class="charts-grid">
      <div class="panel">
        <div class="panel-title">{{ t('systemMonitorPage.memTrend') }}</div>
        <v-chart :option="memChartOption" autoresize class="chart" />
      </div>
      <div class="panel">
        <div class="panel-title">{{ t('systemMonitorPage.concTrend') }}</div>
        <v-chart :option="concChartOption" autoresize class="chart" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { api } from '../services/api'
import type { AgentGatewayStatus, ApiGatewaySystemStatus, GatewayStats, PoolStatus } from '../types'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const { t } = useI18n()

const agentStatus = ref<AgentGatewayStatus>({ ok: false, service: '', port: 0, model: '', active_sessions: 0, running_sessions: 0 })
const apiSysStatus = ref<ApiGatewaySystemStatus>({ service: '', port: 0, uptime_sec: 0, memory: { rss_bytes: 0, vms_bytes: 0 }, db_pool: { size: 0, min_size: 0, max_size: 0, free_size: 0 } })
const apiStats = reactive<GatewayStats>({ active_sessions: 0, active_agents: 0, today_commands: 0, avg_duration_ms: 0, error_rate_pct: 0 })
const poolStats = ref<PoolStatus | null>(null)
const agentOnline = ref(false)
const apiOnline = ref(false)

// History buffers (max 60 points = 10 min @ 10s)
const MAX_POINTS = 60
const memHistory = reactive<{ t: string; agent: number; api: number }[]>([])
const concHistory = reactive<{ t: string; ws: number; sse: number; running: number; ext: number }[]>([])

// Computed
const agentDbUsed = computed(() => {
  const p = agentStatus.value.db_pool
  return p ? p.size - p.free_size : 0
})
const agentDbPct = computed(() => {
  const p = agentStatus.value.db_pool
  return p?.max_size ? Math.round((agentDbUsed.value / p.max_size) * 100) : 0
})
const apiDbUsed = computed(() => {
  const p = apiSysStatus.value.db_pool
  return p ? p.size - p.free_size : 0
})
const apiDbPct = computed(() => {
  const p = apiSysStatus.value.db_pool
  return p?.max_size ? Math.round((apiDbUsed.value / p.max_size) * 100) : 0
})
const browserPct = computed(() => {
  const s = poolStats.value
  return s?.total ? Math.round((s.busy / s.total) * 100) : 0
})
const agentMemWarn = computed(() => (agentStatus.value.memory?.rss_bytes ?? 0) > 500 * 1024 * 1024)
const apiMemWarn = computed(() => (apiSysStatus.value.memory?.rss_bytes ?? 0) > 500 * 1024 * 1024)
const turnPressure = computed(() => (agentStatus.value.turn_slots_available ?? 999) <= 2)

// Helpers
function formatMB(bytes?: number) {
  if (!bytes) return '0 MB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
function formatUptime(sec?: number) {
  if (!sec) return '-'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}
function timeLabel() {
  const d = new Date()
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`
}

// Refresh
async function refresh() {
  const [agentRes, apiSysRes, apiStatsRes, poolRes] = await Promise.allSettled([
    api.agent_getStatus(),
    api.getSystemStatus(),
    api.getStats(),
    api.getPoolStatus(),
  ])

  if (agentRes.status === 'fulfilled') {
    agentStatus.value = agentRes.value
    agentOnline.value = true
  } else {
    agentOnline.value = false
  }

  if (apiSysRes.status === 'fulfilled') {
    apiSysStatus.value = apiSysRes.value
    apiOnline.value = true
  } else {
    apiOnline.value = false
  }

  if (apiStatsRes.status === 'fulfilled') {
    Object.assign(apiStats, apiStatsRes.value)
  }

  if (poolRes.status === 'fulfilled') {
    poolStats.value = poolRes.value.stats
  }

  // Push history
  const t = timeLabel()
  memHistory.push({
    t,
    agent: (agentStatus.value.memory?.rss_bytes ?? 0) / 1024 / 1024,
    api: (apiSysStatus.value.memory?.rss_bytes ?? 0) / 1024 / 1024,
  })
  if (memHistory.length > MAX_POINTS) memHistory.shift()

  concHistory.push({
    t,
    ws: agentStatus.value.active_sessions ?? 0,
    sse: agentStatus.value.active_sse_sessions ?? 0,
    running: agentStatus.value.running_sessions ?? 0,
    ext: apiStats.active_sessions ?? 0,
  })
  if (concHistory.length > MAX_POINTS) concHistory.shift()
}

// Charts
const chartGrid = { top: 30, right: 16, bottom: 24, left: 50 }
const chartAxisStyle = { color: '#64748b', fontSize: 10 }
const chartLineColor = { lineStyle: { color: '#334155' } }

const memChartOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' as const, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 12 } },
  legend: { data: ['Agent RSS', 'API RSS'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
  grid: chartGrid,
  xAxis: { type: 'category' as const, data: memHistory.map(p => p.t), axisLabel: chartAxisStyle, axisLine: chartLineColor, splitLine: { show: false } },
  yAxis: { type: 'value' as const, axisLabel: { ...chartAxisStyle, formatter: '{value} MB' }, splitLine: { lineStyle: { color: '#1e293b' } } },
  series: [
    { name: 'Agent RSS', type: 'line' as const, data: memHistory.map(p => p.agent.toFixed(1)), smooth: true, symbol: 'none', lineStyle: { color: '#a78bfa' }, areaStyle: { color: 'rgba(167,139,250,0.1)' } },
    { name: 'API RSS', type: 'line' as const, data: memHistory.map(p => p.api.toFixed(1)), smooth: true, symbol: 'none', lineStyle: { color: '#60a5fa' }, areaStyle: { color: 'rgba(96,165,250,0.1)' } },
  ],
}))

const concChartOption = computed(() => {
  const nameWs = t('systemMonitorPage.wsSessions')
  const nameSse = t('systemMonitorPage.sseSessions')
  const nameRunning = t('systemMonitorPage.runningTurn')
  const nameExt = t('systemMonitorPage.activeExtensions')
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' as const, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 12 } },
    legend: { data: [nameWs, nameSse, nameRunning, nameExt], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
    grid: chartGrid,
    xAxis: { type: 'category' as const, data: concHistory.map(p => p.t), axisLabel: chartAxisStyle, axisLine: chartLineColor, splitLine: { show: false } },
    yAxis: { type: 'value' as const, axisLabel: chartAxisStyle, splitLine: { lineStyle: { color: '#1e293b' } }, minInterval: 1 },
    series: [
      { name: nameWs, type: 'line' as const, data: concHistory.map(p => p.ws), smooth: true, symbol: 'none', lineStyle: { color: '#4ade80' } },
      { name: nameSse, type: 'line' as const, data: concHistory.map(p => p.sse), smooth: true, symbol: 'none', lineStyle: { color: '#22d3ee' } },
      { name: nameRunning, type: 'line' as const, data: concHistory.map(p => p.running), smooth: true, symbol: 'none', lineStyle: { color: '#f59e0b' } },
      { name: nameExt, type: 'line' as const, data: concHistory.map(p => p.ext), smooth: true, symbol: 'none', lineStyle: { color: '#60a5fa' } },
    ],
  }
})

// Lifecycle
let timer: ReturnType<typeof setInterval>
onMounted(() => { refresh(); timer = setInterval(refresh, 10_000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.monitor-page { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }
.page-header { display: flex; align-items: center; gap: 12px; }
.page-header h2 { margin: 0; font-size: 18px; color: #e2e8f0; }
.auto-label { font-size: 11px; color: #64748b; }
.refresh-btn { margin-left: auto; padding: 4px 12px; background: #334155; color: #e2e8f0; border: 1px solid #475569; border-radius: 4px; cursor: pointer; font-size: 12px; }
.refresh-btn:hover { background: #475569; }

/* KPI Bar */
.kpi-bar { display: flex; background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 12px 20px; gap: 32px; justify-content: center; }
.kpi-item { text-align: center; }
.kpi-num { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.kpi-num.active { color: #4ade80; }
.kpi-num.warn { color: #f59e0b; }
.kpi-label { font-size: 11px; color: #64748b; margin-top: 2px; }

/* Service cards */
.services-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px 16px; }
.card-header { display: flex; align-items: center; gap: 6px; margin-bottom: 12px; }
.svc-name { font-size: 14px; font-weight: 600; color: #e2e8f0; }
.svc-port { font-size: 12px; color: #64748b; }
.dot { width: 8px; height: 8px; border-radius: 50%; margin-left: auto; }
.dot.green { background: #4ade80; }
.dot.red { background: #f87171; }

/* Metrics grid */
.metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; }
.metric-box { text-align: center; padding: 6px 4px; background: #0f172a; border-radius: 6px; }
.metric-val { font-size: 16px; font-weight: 600; color: #e2e8f0; }
.metric-val.active { color: #4ade80; }
.metric-val.warn { color: #f59e0b; }
.metric-val.danger { color: #ef4444; }
.metric-label { font-size: 10px; color: #64748b; margin-top: 2px; }

/* Pool bars */
.pool-section { margin-top: 10px; }
.pool-label { font-size: 11px; color: #94a3b8; margin-bottom: 4px; font-weight: 500; }
.pool-bar-track { height: 8px; background: #0f172a; border-radius: 4px; overflow: hidden; }
.pool-bar-fill { height: 100%; border-radius: 4px; background: #4ade80; transition: width 0.5s ease; }
.pool-bar-fill.warn { background: #f59e0b; }
.pool-bar-fill.danger { background: #ef4444; }
.pool-bar-fill.blue { background: #60a5fa; }
.pool-text { font-size: 11px; color: #64748b; margin-top: 3px; }
.queued-badge { color: #f59e0b; font-weight: 600; }

/* Charts */
.charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel-title { font-size: 13px; font-weight: 600; color: #94a3b8; margin-bottom: 8px; }
.chart { height: 200px; width: 100%; }
</style>
