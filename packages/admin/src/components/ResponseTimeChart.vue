<template>
  <div class="chart-wrap">
    <div class="chart-title">{{ t('responseTimeChart.title') }}</div>
    <v-chart class="chart" :option="option" autoresize />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const { t } = useI18n()

const props = defineProps<{
  data: { name: string; value: number }[]
}>()

const option = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    formatter: (params: any[]) => {
      const p = params[0]
      return `${p.dataIndex + 1}. ${p.name}<br/>${p.value?.toFixed(0)}ms`
    },
  },
  grid: { top: 10, right: 10, bottom: 20, left: 50 },
  xAxis: {
    type: 'category',
    data: props.data.map((_, i) => String(i + 1)),
    axisLabel: { color: '#475569', fontSize: 10 },
    axisLine: { lineStyle: { color: '#334155' } },
  },
  yAxis: {
    type: 'value',
    name: 'ms',
    nameTextStyle: { color: '#475569', fontSize: 10 },
    axisLabel: { color: '#475569', fontSize: 10 },
    splitLine: { lineStyle: { color: '#1e293b' } },
  },
  series: [{
    type: 'line',
    data: props.data.map(d => d.value),
    smooth: true,
    lineStyle: { color: '#60a5fa', width: 2 },
    itemStyle: { color: '#60a5fa' },
    areaStyle: { color: 'rgba(96,165,250,0.1)' },
    symbol: 'circle',
    symbolSize: 4,
  }],
}))
</script>

<style scoped>
.chart-wrap { height: 180px; }
.chart-title { font-size: 12px; color: #64748b; margin-bottom: 6px; }
.chart { height: 160px; width: 100%; }
</style>
