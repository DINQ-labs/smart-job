<template>
  <div class="error-table-wrap">
    <table class="err-table">
      <thead>
        <tr>
          <th>{{ t('errorTable.colTime') }}</th>
          <th>{{ t('errorTable.colSource') }}</th>
          <th>{{ t('errorTable.colCategory') }}</th>
          <th>{{ t('errorTable.colSummary') }}</th>
          <th>{{ t('errorTable.colSessionUser') }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="rows.length === 0">
          <td colspan="6" class="empty">{{ emptyText || t('errorTable.empty') }}</td>
        </tr>
        <tr
          v-for="row in rows"
          :key="row.id"
          class="err-row"
        >
          <td class="small mono">{{ fmtTime(row.ts) }}</td>
          <td>
            <span class="src-tag" :class="row.source">
              {{ row.source === 'gateway' ? t('errorTable.sourceGateway') : 'Agent' }}
            </span>
          </td>
          <td class="cat">{{ row.category || '-' }}</td>
          <td class="msg" :title="row.message">{{ truncate(row.message, 200) }}</td>
          <td class="mono small">
            <template v-if="row.source === 'gateway'">
              {{ row.session_id ? String(row.session_id).slice(0, 10) + '…' : '—' }}
              <template v-if="row.agent_id"> / {{ row.agent_id.slice(0, 10) }}…</template>
            </template>
            <template v-else>
              {{ row.user_id || '—' }} <span class="dim">#{{ row.session_id }}</span>
            </template>
          </td>
          <td>
            <button class="detail-btn" @click="$emit('detail', row)">{{ t('errorTable.detail') }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { UnifiedErrorRow } from '../stores/errors'

const { t } = useI18n()

defineProps<{
  rows: UnifiedErrorRow[]
  emptyText?: string
}>()

defineEmits<{ detail: [row: UnifiedErrorRow] }>()

function fmtTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    hour12: false, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function truncate(s: string, n: number): string {
  if (!s) return '-'
  return s.length > n ? s.slice(0, n) + '…' : s
}
</script>

<style scoped>
.error-table-wrap { overflow-x: auto; }
.err-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.err-table th {
  background: #0f172a;
  color: #64748b;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  border-bottom: 1px solid #1e293b;
}
.err-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #1e293b;
  color: #e2e8f0;
  vertical-align: middle;
}
.err-table tr.err-row td { background: #2d1515; }
.err-table tr.err-row:hover td { background: #3a1d1d; }
.mono { font-family: monospace; }
.small { font-size: 11px; color: #94a3b8; }
.empty { text-align: center; color: #475569; padding: 24px; }
.cat { color: #fbbf24; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.msg { color: #fca5a5; max-width: 480px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.src-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
.src-tag.gateway { background: #1e3a5f; color: #60a5fa; }
.src-tag.agent   { background: #4c1d95; color: #c4b5fd; }
.dim { color: #64748b; }
.detail-btn {
  background: #334155; color: #94a3b8; border: none;
  padding: 3px 9px; border-radius: 4px; cursor: pointer; font-size: 11px;
}
.detail-btn:hover { background: #475569; color: #e2e8f0; }
</style>
