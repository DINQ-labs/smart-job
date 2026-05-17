<template>
  <div class="agent-card">
    <div class="card-header">
      <span class="status-dot connected"></span>
      <span class="agent-id" :title="agent.agent_id">{{ agent.agent_id.slice(0, 16) }}…</span>
      <span class="badge connected">{{ t('agentCard.online') }}</span>
    </div>
    <div class="info-row" v-if="agent.user_id">
      <span class="k">{{ t('agentCard.user') }}</span>
      <span class="v mono" :title="agent.user_id">{{ agent.user_id.slice(0, 8) }}…</span>
    </div>
    <div class="info-row">
      <span class="k">{{ t('agentCard.boundAccount') }}</span>
      <span class="v" :class="agent.bound_account ? 'account-name' : 'unbound'">
        {{ agent.bound_account || t('agentCard.unbound') }}
      </span>
    </div>
    <div class="info-row" v-if="agent.bound_session">
      <span class="k">{{ t('agentCard.sessionId') }}</span>
      <span class="v mono">{{ agent.bound_session.slice(0, 16) }}…</span>
    </div>
    <div class="info-row">
      <span class="k">{{ t('agentCard.requestCount') }}</span>
      <span class="v">{{ agent.request_count }}</span>
    </div>
    <div class="info-row">
      <span class="k">{{ t('agentCard.connectedAt') }}</span>
      <span class="v">{{ fmtTime(agent.connected_at) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AgentInfo } from '../types'

const { t } = useI18n()

defineProps<{ agent: AgentInfo }>()

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.agent-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px 14px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.status-dot {
  width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
}
.status-dot.connected { background: #60a5fa; }
.agent-id { font-family: monospace; font-size: 12px; color: #94a3b8; flex: 1; }
.badge { font-size: 10px; padding: 2px 7px; border-radius: 10px; font-weight: 600; }
.badge.connected { background: #1e3a5f; color: #60a5fa; }
.info-row { display: flex; justify-content: space-between; margin-bottom: 3px; font-size: 12px; }
.k { color: #64748b; }
.v { color: #e2e8f0; }
.v.mono { font-family: monospace; font-size: 11px; }
.account-name { color: #34d399; font-weight: 600; }
.unbound { color: #f59e0b; font-style: italic; }
</style>
