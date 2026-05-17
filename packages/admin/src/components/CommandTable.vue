<template>
  <div class="command-table-wrap">
    <table class="cmd-table">
      <thead>
        <tr>
          <th>{{ t('commandTable.colTool') }}</th>
          <th>{{ t('commandTable.colSession') }}</th>
          <th>Agent</th>
          <th>{{ t('commandTable.colStatus') }}</th>
          <th>Tier</th>
          <th>{{ t('commandTable.colDuration') }}</th>
          <th>{{ t('commandTable.colTime') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="commands.length === 0">
          <td colspan="8" class="empty">{{ t('commandTable.empty') }}</td>
        </tr>
        <tr
          v-for="cmd in commands"
          :key="cmd.id ?? cmd.request_id"
          :class="{ error: cmd.response_ok === false }"
        >
          <td class="tool">{{ cmd.tool_name || cmd.path }}</td>
          <td class="mono small">{{ cmd.session_id.slice(0, 10) }}…</td>
          <td class="mono small">{{ cmd.agent_id ? cmd.agent_id.slice(0, 10) + '…' : '—' }}</td>
          <td>
            <span v-if="cmd.response_ok === null" class="tag pending">{{ t('commandTable.statusPending') }}</span>
            <span v-else-if="cmd.response_ok" class="tag ok">{{ t('commandTable.statusOk') }}</span>
            <span v-else class="tag fail">{{ t('commandTable.statusFail') }}</span>
          </td>
          <td class="small">{{ cmd.user_tier || '-' }}</td>
          <td class="num">{{ cmd.duration_ms != null ? cmd.duration_ms.toFixed(0) + 'ms' : '-' }}</td>
          <td class="small">{{ fmtTime(cmd.started_at) }}</td>
          <td>
            <button class="detail-btn" @click="$emit('detail', cmd)">{{ t('commandTable.detail') }}</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="pagination" v-if="showPagination">
      <button :disabled="offset === 0" @click="$emit('prev')">{{ t('commandTable.prev') }}</button>
      <span>{{ offset + 1 }}–{{ offset + commands.length }}</span>
      <button :disabled="commands.length < limit" @click="$emit('next')">{{ t('commandTable.next') }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { CommandLog } from '../types'

const { t } = useI18n()

withDefaults(defineProps<{
  commands: CommandLog[]
  offset?: number
  limit?: number
  showPagination?: boolean
}>(), { offset: 0, limit: 20 })
defineEmits<{ prev: []; next: []; detail: [cmd: CommandLog] }>()

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<style scoped>
.command-table-wrap { overflow-x: auto; }
.cmd-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.cmd-table th {
  background: #0f172a;
  color: #64748b;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  border-bottom: 1px solid #1e293b;
}
.cmd-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #1e293b;
  color: #e2e8f0;
  vertical-align: middle;
}
.cmd-table tr:hover td { background: #1e293b; }
.cmd-table tr.error td { background: #2d1515; }
.tool { color: #93c5fd; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: monospace; }
.small { font-size: 11px; color: #94a3b8; }
.num { color: #fbbf24; }
.empty { text-align: center; color: #475569; padding: 24px; }
.tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
.tag.ok      { background: #14532d; color: #4ade80; }
.tag.fail    { background: #7f1d1d; color: #fca5a5; }
.tag.pending { background: #1e3a5f; color: #60a5fa; }
.detail-btn {
  background: #334155; color: #94a3b8; border: none;
  padding: 3px 9px; border-radius: 4px; cursor: pointer; font-size: 11px;
}
.detail-btn:hover { background: #475569; color: #e2e8f0; }
.pagination { display: flex; gap: 12px; align-items: center; justify-content: center; padding: 10px; }
.pagination button { background: #334155; color: #94a3b8; border: none; padding: 5px 12px; border-radius: 4px; cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.pagination span { color: #64748b; font-size: 12px; }
</style>
