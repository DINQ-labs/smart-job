<template>
  <div class="chart-wrap">
    <div class="chart-title">{{ t('commandFreqChart.title') }}</div>
    <v-chart class="chart" :option="option" autoresize />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const { t } = useI18n()

const props = defineProps<{
  data: { name: string; value: number }[]
}>()

const option = computed(() => {
  const top10 = props.data.slice(0, 10)
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { top: 10, right: 10, bottom: 60, left: 50 },
    xAxis: {
      type: 'category',
      data: top10.map(d => d.name.replace('boss_', '')),
      axisLabel: { color: '#475569', fontSize: 9, rotate: 30 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#475569', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [{
      type: 'bar',
      data: top10.map(d => d.value),
      itemStyle: { color: '#818cf8', borderRadius: [3, 3, 0, 0] },
    }],
  }
})
</script>

<style scoped>
.chart-wrap { height: 180px; }
.chart-title { font-size: 12px; color: #64748b; margin-bottom: 6px; }
.chart { height: 160px; width: 100%; }
</style>
