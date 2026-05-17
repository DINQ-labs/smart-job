<template>
  <div class="metrics-page">
    <div class="page-header">
      <h2>{{ t('mcpMetricsPage.title') }}</h2>
      <span class="auto-label">{{ t('mcpMetricsPage.autoRefresh') }}</span>
      <div class="hours-tabs">
        <button
          v-for="h in HOURS_PRESETS" :key="h.v"
          class="hours-chip" :class="{ active: hours === h.v }"
          @click="setHours(h.v)"
        >{{ h.label }}</button>
      </div>
      <button class="refresh-btn" @click="refresh">{{ t('mcpMetricsPage.refresh') }}</button>
    </div>

    <!-- KPI Bar -->
    <div class="kpi-bar">
      <div class="kpi-item">
        <div class="kpi-num">{{ overview.total.toLocaleString() }}</div>
        <div class="kpi-label">{{ t('mcpMetricsPage.kpiTotal') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num active">{{ overview.ok.toLocaleString() }}</div>
        <div class="kpi-label">{{ t('mcpMetricsPage.kpiOk', { pct: pctOk }) }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num" :class="{ warn: overview.risk > 0 }">{{ overview.risk.toLocaleString() }}</div>
        <div class="kpi-label">{{ t('mcpMetricsPage.kpiRisk') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num" :class="{ danger: overview.failed > 0 }">{{ overview.failed.toLocaleString() }}</div>
        <div class="kpi-label">{{ t('mcpMetricsPage.kpiFailed') }}</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-num">{{ overview.avg_ms }}<span class="unit">ms</span></div>
        <div class="kpi-label">{{ t('mcpMetricsPage.kpiAvg') }}</div>
      </div>
    </div>

    <!-- Charts -->
    <div class="charts-grid">
      <div class="panel">
        <div class="panel-title">{{ t('mcpMetricsPage.callsChartTitle', { bucket: bucketLabel }) }}</div>
        <v-chart v-if="chartPoints.length" :option="callsChartOption" autoresize class="chart" />
        <div v-else class="empty-hint">{{ t('mcpMetricsPage.noCallData') }}</div>
      </div>
      <div class="panel">
        <div class="panel-title">{{ t('mcpMetricsPage.riskChartTitle') }}</div>
        <v-chart v-if="chartPoints.length" :option="riskChartOption" autoresize class="chart" />
        <div v-else class="empty-hint">{{ t('mcpMetricsPage.noCallData') }}</div>
      </div>
    </div>

    <!-- Risk top -->
    <div class="panel">
      <div class="card-header">
        <span class="svc-name">{{ t('mcpMetricsPage.riskHotspot') }}</span>
        <span class="svc-port">{{ t('mcpMetricsPage.recentTop', { hours, n: 10 }) }}</span>
      </div>
      <table class="data-table" v-if="riskTop.length">
        <thead>
          <tr><th>{{ t('mcpMetricsPage.thSignal') }}</th><th>{{ t('mcpMetricsPage.thPlatform') }}</th><th class="num">{{ t('mcpMetricsPage.thHits') }}</th><th>{{ t('mcpMetricsPage.thLastHit') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in riskTop" :key="i">
            <td><code>{{ r.risk_signal }}</code></td>
            <td><span :class="`pf-pill pf-${r.platform}`">{{ r.platform || '-' }}</span></td>
            <td class="num"><b>{{ r.hits }}</b></td>
            <td class="dim">{{ formatTs(r.last_seen) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-hint">{{ t('mcpMetricsPage.noRiskHit') }}</div>
    </div>

    <!-- User top -->
    <div class="panel">
      <div class="card-header">
        <span class="svc-name">{{ t('mcpMetricsPage.userRanking') }}</span>
        <span class="svc-port">{{ t('mcpMetricsPage.recentTop', { hours, n: 20 }) }}</span>
      </div>
      <table class="data-table" v-if="userTop.length">
        <thead>
          <tr>
            <th>user_id</th><th>{{ t('mcpMetricsPage.thPlatform') }}</th>
            <th class="num">{{ t('mcpMetricsPage.thCalls') }}</th><th class="num">{{ t('mcpMetricsPage.thRisk') }}</th>
            <th class="num">avg ms</th><th>{{ t('mcpMetricsPage.thLastCall') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(u, i) in userTop" :key="i" :class="{ 'risk-row': u.risks > 0 }">
            <td class="mono">{{ shortId(u.user_id) }}</td>
            <td><span :class="`pf-pill pf-${u.platform}`">{{ u.platform || '-' }}</span></td>
            <td class="num">{{ u.calls }}</td>
            <td class="num">
              <span v-if="u.risks > 0" class="risk-badge">{{ u.risks }}</span>
              <span v-else class="dim">-</span>
            </td>
            <td class="num">{{ u.avg_ms }}</td>
            <td class="dim">{{ formatTs(u.last_call) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-hint">{{ t('mcpMetricsPage.noCallData') }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { api } from '../services/api'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const { t } = useI18n()

type Overview = { total: number; ok: number; failed: number; risk: number; avg_ms: number }
type TsPoint  = { ts: string; platform: string; calls: number; risks: number }
type RiskRow  = { risk_signal: string; platform: string; hits: number; last_seen: string | null }
type UserRow  = { user_id: string; platform: string; calls: number; risks: number; avg_ms: number; last_call: string | null }

const HOURS_PRESETS = [
  { v: 1,   label: '1h' },
  { v: 6,   label: '6h' },
  { v: 24,  label: '24h' },
  { v: 168, label: '7d' },
]
const PLATFORMS = ['boss', 'linkedin', 'indeed'] as const
const PLATFORM_COLORS: Record<string, string> = {
  boss:     '#4ade80',
  linkedin: '#60a5fa',
  indeed:   '#a78bfa',
}

const hours    = ref<number>(24)
const bucket   = ref<'minute' | 'hour'>('hour')
const overview = ref<Overview>({ total: 0, ok: 0, failed: 0, risk: 0, avg_ms: 0 })
const series   = ref<TsPoint[]>([])
const riskTop  = ref<RiskRow[]>([])
const userTop  = ref<UserRow[]>([])

let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  try {
    const data = await api.admin_mcpMetrics({ hours: hours.value })
    overview.value = data.overview
    series.value   = data.timeseries
    riskTop.value  = data.risk_top
    userTop.value  = data.user_top
    bucket.value   = (data.bucket as 'minute' | 'hour') || 'hour'
  } catch (e) {
    console.warn('[mcp-metrics] fetch failed', e)
  }
}

function setHours(v: number) {
  hours.value = v
  refresh()
}

const pctOk = computed(() => {
  const o = overview.value
  if (!o.total) return '0.0'
  return ((o.ok / o.total) * 100).toFixed(1)
})

const bucketLabel = computed(() => bucket.value === 'minute' ? t('mcpMetricsPage.perMinute') : t('mcpMetricsPage.perHour'))

// 把每条 (ts, platform) 行聚合成宽表 ChartPoint(ts → values per platform + risks)
type ChartPoint = { ts: string; values: Record<string, number>; risks: number }
const chartPoints = computed<ChartPoint[]>(() => {
  if (!series.value.length) return []
  const m = new Map<string, ChartPoint>()
  for (const p of series.value) {
    let cp = m.get(p.ts)
    if (!cp) {
      cp = { ts: p.ts, values: { boss: 0, linkedin: 0, indeed: 0 }, risks: 0 }
      m.set(p.ts, cp)
    }
    if (p.platform in cp.values) cp.values[p.platform] += p.calls
    cp.risks += p.risks
  }
  return [...m.values()].sort((a, b) => a.ts.localeCompare(b.ts))
})

const xAxisLabels = computed(() => chartPoints.value.map(p => formatBucket(p.ts)))

const chartGrid = { top: 30, right: 16, bottom: 24, left: 50 }
const chartAxisStyle = { color: '#64748b', fontSize: 10 }
const chartLineColor = { lineStyle: { color: '#334155' } }

const callsChartOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis' as const,
    backgroundColor: '#1e293b', borderColor: '#334155',
    textStyle: { color: '#e2e8f0', fontSize: 12 },
  },
  legend: {
    data: ['Boss直聘', 'LinkedIn', 'Indeed'],
    textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0,
  },
  grid: chartGrid,
  xAxis: {
    type: 'category' as const, data: xAxisLabels.value,
    axisLabel: chartAxisStyle, axisLine: chartLineColor,
    splitLine: { show: false },
  },
  yAxis: {
    type: 'value' as const,
    axisLabel: chartAxisStyle,
    splitLine: { lineStyle: { color: '#1e293b' } },
    minInterval: 1,
  },
  series: PLATFORMS.map((pf) => ({
    name: pf === 'boss' ? 'Boss直聘' : pf === 'linkedin' ? 'LinkedIn' : 'Indeed',
    type: 'line' as const,
    data: chartPoints.value.map(p => p.values[pf] || 0),
    smooth: true, symbol: 'none',
    lineStyle: { color: PLATFORM_COLORS[pf] },
    areaStyle: { color: rgba(PLATFORM_COLORS[pf], 0.10) },
  })),
}))

const riskChartOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis' as const,
    backgroundColor: '#1e293b', borderColor: '#334155',
    textStyle: { color: '#e2e8f0', fontSize: 12 },
  },
  grid: chartGrid,
  xAxis: {
    type: 'category' as const, data: xAxisLabels.value,
    axisLabel: chartAxisStyle, axisLine: chartLineColor,
    splitLine: { show: false },
  },
  yAxis: {
    type: 'value' as const,
    axisLabel: chartAxisStyle,
    splitLine: { lineStyle: { color: '#1e293b' } },
    minInterval: 1,
  },
  series: [{
    name: t('mcpMetricsPage.riskChartSeries'),
    type: 'line' as const,
    data: chartPoints.value.map(p => p.risks),
    smooth: true, symbol: 'circle', symbolSize: 6,
    lineStyle: { color: '#ef4444' },
    itemStyle: { color: '#ef4444' },
    areaStyle: { color: 'rgba(239,68,68,0.12)' },
  }],
}))

function rgba(hex: string, alpha: number): string {
  // 简单 hex → rgba 转换(只支持 #rrggbb)
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

function formatBucket(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    if (bucket.value === 'minute') return `${pad(d.getHours())}:${pad(d.getMinutes())}`
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:00`
  } catch { return iso }
}
function formatTs(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch { return iso }
}
function shortId(s: string): string {
  if (!s) return '-'
  return s.length > 12 ? s.slice(0, 8) + '…' : s
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 30_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.metrics-page {
  padding: 20px 24px;
  display: flex; flex-direction: column; gap: 16px;
}

/* page header */
.page-header {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.page-header h2 { margin: 0; font-size: 18px; color: #e2e8f0; }
.auto-label { font-size: 11px; color: #64748b; }
.hours-tabs { display: flex; gap: 4px; margin-left: 8px; }
.hours-chip {
  padding: 4px 10px;
  background: #334155; color: #cbd5e1;
  border: 1px solid #475569; border-radius: 4px;
  font-size: 11px; cursor: pointer;
}
.hours-chip:hover { background: #475569; }
.hours-chip.active {
  background: #4ade80; color: #0f172a;
  border-color: #4ade80; font-weight: 600;
}
.refresh-btn {
  margin-left: auto;
  padding: 4px 12px;
  background: #334155; color: #e2e8f0;
  border: 1px solid #475569; border-radius: 4px;
  cursor: pointer; font-size: 12px;
}
.refresh-btn:hover { background: #475569; }

/* KPI Bar */
.kpi-bar {
  display: flex; gap: 32px; justify-content: center;
  background: #1e293b; border: 1px solid #334155; border-radius: 8px;
  padding: 12px 20px;
}
.kpi-item { text-align: center; }
.kpi-num { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.kpi-num .unit { font-size: 12px; color: #64748b; margin-left: 2px; }
.kpi-num.active { color: #4ade80; }
.kpi-num.warn   { color: #f59e0b; }
.kpi-num.danger { color: #ef4444; }
.kpi-label { font-size: 11px; color: #64748b; margin-top: 2px; }

/* Charts */
.charts-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
}
.panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; padding: 14px 16px;
}
.panel-title {
  font-size: 13px; font-weight: 600; color: #94a3b8;
  margin-bottom: 8px;
}
.chart { height: 220px; width: 100%; }
.empty-hint {
  height: 220px;
  display: flex; align-items: center; justify-content: center;
  color: #64748b; font-size: 12px;
}

/* Card header (for tables) */
.card-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
}
.svc-name { font-size: 14px; font-weight: 600; color: #e2e8f0; }
.svc-port { font-size: 11px; color: #64748b; }

/* Tables */
.data-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
}
.data-table th {
  padding: 8px 10px; text-align: left;
  background: #0f172a; color: #94a3b8;
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  border-bottom: 1px solid #334155;
}
.data-table td {
  padding: 8px 10px;
  color: #e2e8f0;
  border-bottom: 1px solid #1e293b;
}
.data-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.data-table .dim { color: #64748b; font-size: 11px; }
.data-table tr.risk-row td { background: rgba(245, 158, 11, 0.08); }
.data-table code {
  background: #0f172a; padding: 2px 6px; border-radius: 3px;
  font-family: ui-monospace, monospace; font-size: 11px;
  color: #fbbf24;
}
.mono { font-family: ui-monospace, monospace; color: #cbd5e1; }

/* Platform pill */
.pf-pill {
  display: inline-block; padding: 1px 8px;
  border-radius: 10px; font-size: 10px; font-weight: 600;
  color: #0f172a;
}
.pf-boss     { background: #4ade80; }
.pf-linkedin { background: #60a5fa; }
.pf-indeed   { background: #a78bfa; }
.pf-pill:not(.pf-boss):not(.pf-linkedin):not(.pf-indeed) {
  background: #475569; color: #cbd5e1;
}
.risk-badge {
  background: #ef4444; color: #fff;
  padding: 1px 8px; border-radius: 10px;
  font-size: 10px; font-weight: 600;
}
</style>
