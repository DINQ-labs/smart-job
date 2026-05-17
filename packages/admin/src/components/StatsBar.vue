<template>
  <div class="stats-bar">
    <div class="stat-item">
      <span class="stat-num" :class="{ active: stats.active_sessions > 0 }">{{ stats.active_sessions }}</span>
      <span class="stat-label">{{ t('statsBar.extOnline') }}</span>
    </div>
    <div class="stat-item">
      <span class="stat-num" :class="{ active: stats.active_agents > 0 }">{{ stats.active_agents }}</span>
      <span class="stat-label">{{ t('statsBar.agentOnline') }}</span>
    </div>
    <div class="stat-item">
      <span class="stat-num">{{ stats.today_commands }}</span>
      <span class="stat-label">{{ t('statsBar.todayCommands') }}</span>
    </div>
    <div class="stat-item">
      <span class="stat-num">{{ stats.avg_duration_ms.toFixed(0) }}ms</span>
      <span class="stat-label">{{ t('statsBar.avgDuration') }}</span>
    </div>
    <div class="stat-item">
      <span class="stat-num" :class="{ error: stats.error_rate_pct > 0 }">{{ stats.error_rate_pct.toFixed(1) }}%</span>
      <span class="stat-label">{{ t('statsBar.errorRate') }}</span>
    </div>
    <div class="stat-item ws-status">
      <span class="dot" :class="wsConnected ? 'green' : 'red'"></span>
      <span class="stat-label">{{ wsConnected ? t('statsBar.wsConnected') : t('statsBar.wsDisconnected') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { GatewayStats } from '../types'

const { t } = useI18n()

defineProps<{ stats: GatewayStats; wsConnected: boolean }>()
</script>

<style scoped>
.stats-bar {
  display: flex;
  gap: 24px;
  padding: 12px 20px;
  background: #1e293b;
  border-radius: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 80px;
}
.stat-num {
  font-size: 22px;
  font-weight: 700;
  color: #94a3b8;
  line-height: 1.2;
}
.stat-num.active { color: #4ade80; }
.stat-num.error { color: #f87171; }
.stat-label {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
}
.ws-status {
  flex-direction: row;
  gap: 6px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot.green { background: #4ade80; }
.dot.red { background: #f87171; }
</style>
