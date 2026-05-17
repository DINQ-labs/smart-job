<template>
  <div class="growth-page">
    <div class="page-header">
      <h2>📊 {{ t('growthMetricsPage.title') }}</h2>
      <span class="date-label">{{ t('growthMetricsPage.dateLabel', { date: summary?.as_of || '...' }) }}</span>
      <select v-model="days" class="days-select" @change="refresh">
        <option :value="7">{{ t('growthMetricsPage.last7') }}</option>
        <option :value="14">{{ t('growthMetricsPage.last14') }}</option>
        <option :value="30">{{ t('growthMetricsPage.last30') }}</option>
      </select>
      <button class="refresh-btn" @click="refresh">{{ t('growthMetricsPage.refresh') }}</button>
    </div>

    <div v-if="loadError" class="error-banner">
      {{ t('growthMetricsPage.loadFailed', { error: loadError }) }}
    </div>

    <!-- KPI Bar -->
    <div v-if="summary" class="kpi-bar">
      <div class="kpi-item kpi-total">
        <div class="kpi-num">{{ summary.dau_today.total }}</div>
        <div class="kpi-label">{{ t('growthMetricsPage.dauTodayAll') }}</div>
      </div>
      <div
        v-for="(count, plat) in summary.dau_today.by_platform"
        :key="plat"
        class="kpi-item"
      >
        <div class="kpi-num">{{ count }}</div>
        <div class="kpi-label">{{ plat }}</div>
      </div>
    </div>

    <!-- DAU 折线图 -->
    <div v-if="summary" class="panel">
      <div class="panel-title">{{ t('growthMetricsPage.dauTrend', { days }) }}</div>
      <v-chart :option="dauChartOption" autoresize class="chart" style="height: 280px;" />
    </div>

    <div class="two-col">
      <!-- Funnel -->
      <div v-if="summary" class="panel">
        <div class="panel-title">{{ t('growthMetricsPage.funnelTitle') }}</div>
        <table class="funnel-table">
          <thead>
            <tr>
              <th>{{ t('growthMetricsPage.thStep') }}</th>
              <th>{{ t('growthMetricsPage.thUserCount') }}</th>
              <th>{{ t('growthMetricsPage.thRetention') }}</th>
              <th>{{ t('growthMetricsPage.thBar') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in funnelRows" :key="row.step">
              <td><strong>{{ row.label }}</strong></td>
              <td>{{ row.count }}</td>
              <td :class="{ 'pct-low': (row.pct ?? 100) < 50 && (row.pct ?? 0) > 0 }">
                {{ row.pct === null ? '—' : row.pct.toFixed(1) + '%' }}
              </td>
              <td>
                <div class="bar" :style="{ width: row.barPct + '%' }"></div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Top cost users -->
      <div v-if="summary" class="panel">
        <div class="panel-title">{{ t('growthMetricsPage.topCostTitle') }}</div>
        <table class="cost-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Platform</th>
              <th>Cost (USD)</th>
              <th>Turns</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in summary.top_cost_today" :key="user.user_id + user.platform">
              <td class="userid-cell" :title="user.user_id">{{ user.user_id.slice(0, 8) }}…</td>
              <td>{{ user.platform }}</td>
              <td>${{ user.cost_usd.toFixed(4) }}</td>
              <td>{{ user.turn_count }}</td>
            </tr>
            <tr v-if="!summary.top_cost_today?.length">
              <td colspan="4" class="empty-row">{{ t('growthMetricsPage.noCostRecord') }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="totalCost" class="cost-summary">
          {{ t('growthMetricsPage.topCostTotal', { total: totalCost.toFixed(4) }) }}
        </div>
      </div>
    </div>

    <div v-if="!summary && !loadError" class="loading">{{ t('growthMetricsPage.loading') }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer])

const { t } = useI18n()

interface DauSeriesRow {
  date: string
  dau: number
  platform?: string
}
interface CostUser {
  user_id: string
  platform: string
  cost_usd: number
  turn_count: number
}
interface MetricsSummary {
  as_of: string
  days: number
  dau_series: DauSeriesRow[]
  dau_today: { total: number; by_platform: Record<string, number> }
  funnel_today: Record<string, number>
  top_cost_today: CostUser[]
}

const days = ref<number>(7)
const summary = ref<MetricsSummary | null>(null)
const loadError = ref<string>('')

const FUNNEL_ORDER = [
  { step: 'welcome',          labelKey: 'growthMetricsPage.funnelWelcome' },
  { step: 'role_selected',    labelKey: 'growthMetricsPage.funnelRoleSelected' },
  { step: 'resume_uploaded',  labelKey: 'growthMetricsPage.funnelResumeUploaded' },
  { step: 'first_search',     labelKey: 'growthMetricsPage.funnelFirstSearch' },
  { step: 'first_apply',      labelKey: 'growthMetricsPage.funnelFirstApply' },
  { step: 'first_message',    labelKey: 'growthMetricsPage.funnelFirstMessage' },
]

const funnelRows = computed(() => {
  if (!summary.value) return []
  const counts = summary.value.funnel_today || {}
  const top = counts.welcome || 0
  return FUNNEL_ORDER.map((f) => {
    const count = counts[f.step] || 0
    const pct = top > 0 ? (count / top) * 100 : null
    const barPct = top > 0 ? (count / top) * 100 : 0
    return { ...f, label: t(f.labelKey), count, pct, barPct }
  })
})

const totalCost = computed(() => {
  if (!summary.value?.top_cost_today) return null
  return summary.value.top_cost_today.reduce((a, b) => a + (b.cost_usd || 0), 0)
})

// 跟 SystemMonitor 同款 echarts 暗主题
const _AXIS_STYLE = { color: '#64748b', fontSize: 10 }
const _PLATFORM_COLORS: Record<string, string> = {
  linkedin: '#60a5fa',
  boss:     '#4ade80',
  indeed:   '#a78bfa',
  unknown:  '#94a3b8',
  all:      '#22d3ee',
}

const dauChartOption = computed(() => {
  if (!summary.value) return {}
  const series = summary.value.dau_series || []
  // 按 platform 分组
  const byPlatform: Record<string, Record<string, number>> = {}
  const dateSet = new Set<string>()
  for (const r of series) {
    const plat = r.platform || 'all'
    if (!byPlatform[plat]) byPlatform[plat] = {}
    byPlatform[plat][r.date] = r.dau
    dateSet.add(r.date)
  }
  const dates = [...dateSet].sort()
  const platforms = Object.keys(byPlatform).sort()
  const seriesData = platforms.map((p) => ({
    name: p,
    type: 'line' as const,
    smooth: true,
    symbol: dates.length < 15 ? 'circle' : 'none',
    lineStyle: { color: _PLATFORM_COLORS[p] || '#60a5fa' },
    itemStyle: { color: _PLATFORM_COLORS[p] || '#60a5fa' },
    data: dates.map((d) => byPlatform[p][d] || 0),
  }))
  return {
    backgroundColor: 'transparent',
    grid: { top: 30, right: 20, bottom: 40, left: 50 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    legend: {
      top: 0,
      textStyle: { color: '#94a3b8', fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { ..._AXIS_STYLE, rotate: dates.length > 10 ? 30 : 0 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: _AXIS_STYLE,
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: seriesData,
  }
})

async function refresh() {
  loadError.value = ''
  try {
    const r = await fetch(`/agent-gw/admin/metrics/summary?days=${days.value}`)
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    summary.value = await r.json()
  } catch (e: unknown) {
    loadError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(refresh)
</script>

<style scoped>
/* 跟 SystemMonitorPage / App.vue 共用调色板:
   bg #0f172a / panel #1e293b / border #334155 /
   text-primary #e2e8f0 / text-2 #94a3b8 / text-mute #64748b /
   accent-blue #60a5fa / green #4ade80 / amber #f59e0b */
.growth-page {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-header h2 {
  margin: 0;
  font-size: 18px;
  color: #e2e8f0;
}
.date-label {
  font-size: 11px;
  color: #64748b;
}
.days-select {
  margin-left: auto;
  padding: 4px 12px;
  background: #334155;
  color: #e2e8f0;
  border: 1px solid #475569;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.days-select:hover {
  background: #475569;
}
.refresh-btn {
  padding: 4px 12px;
  background: #334155;
  color: #e2e8f0;
  border: 1px solid #475569;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.refresh-btn:hover {
  background: #475569;
}

.error-banner {
  background: #2a1818;
  border: 1px solid #7f1d1d;
  color: #fca5a5;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

.kpi-bar {
  display: flex;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px 20px;
  gap: 32px;
  justify-content: center;
  flex-wrap: wrap;
}
.kpi-item {
  text-align: center;
  min-width: 80px;
}
.kpi-item.kpi-total .kpi-num {
  color: #60a5fa;
}
.kpi-num {
  font-size: 22px;
  font-weight: 700;
  color: #e2e8f0;
}
.kpi-label {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px 16px;
}
.panel-title {
  font-weight: 600;
  font-size: 13px;
  color: #e2e8f0;
  margin-bottom: 10px;
}
.chart {
  width: 100%;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 1100px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}

.funnel-table,
.cost-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  color: #e2e8f0;
}
.funnel-table th,
.funnel-table td,
.cost-table th,
.cost-table td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid #334155;
}
.funnel-table th,
.cost-table th {
  background: #0f172a;
  font-weight: 500;
  color: #94a3b8;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.funnel-table tbody tr:hover,
.cost-table tbody tr:hover {
  background: #0f172a;
}
.funnel-table .bar {
  height: 10px;
  background: linear-gradient(90deg, #60a5fa, #a78bfa);
  border-radius: 3px;
  min-width: 0;
}
.pct-low {
  color: #f87171;
}
.userid-cell {
  font-family: ui-monospace, 'SF Mono', monospace;
  font-size: 11px;
  color: #94a3b8;
}
.empty-row {
  text-align: center;
  color: #64748b;
  padding: 20px;
}
.cost-summary {
  margin-top: 8px;
  font-size: 11px;
  color: #94a3b8;
  text-align: right;
}
.loading {
  padding: 40px;
  text-align: center;
  color: #64748b;
  font-size: 13px;
}
</style>
