<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-header">
        <span class="title">{{ t('agentErrorDetailModal.title') }}</span>
        <span class="cat-tag">{{ row.error_category || 'unknown' }}</span>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">
        <div class="section">
          <div class="section-title">{{ t('agentErrorDetailModal.basicInfo') }}</div>
          <div class="kv-grid">
            <span class="k">{{ t('agentErrorDetailModal.time') }}</span>
            <span class="v">{{ fmtFull(row.created_at) }}</span>
            <span class="k">{{ t('agentErrorDetailModal.errorType') }}</span>
            <span class="v accent">{{ row.error_category || '-' }}</span>
            <span class="k">{{ t('agentErrorDetailModal.user') }}</span>
            <span class="v mono">{{ row.user_id || '-' }}</span>
            <span class="k">{{ t('agentErrorDetailModal.sessionId') }}</span>
            <span class="v mono">{{ row.session_id }}</span>
            <span class="k">{{ t('agentErrorDetailModal.duration') }}</span>
            <span class="v">{{ row.duration_ms != null ? row.duration_ms.toFixed(0) + ' ms' : '-' }}</span>
            <span class="k">{{ t('agentErrorDetailModal.mode') }}</span>
            <span class="v">{{ row.mode || '-' }}</span>
          </div>
        </div>

        <div class="section">
          <div class="section-title err-title">{{ t('agentErrorDetailModal.errorMessageTitle') }}</div>
          <pre class="code-block err-block">{{ row.error_message || t('agentErrorDetailModal.noContent') }}</pre>
        </div>

        <div class="section actions">
          <a
            class="link-btn"
            :href="`/agent-history?session=${row.session_id}`"
            target="_blank"
          >🔗 {{ t('agentErrorDetailModal.openInHistory') }}</a>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AgentErrorRow } from '../types'

const { t } = useI18n()

defineProps<{ row: AgentErrorRow }>()
defineEmits<{ close: [] }>()

function fmtFull(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  width: 720px; max-width: calc(100vw - 32px);
  max-height: 85vh;
  display: flex; flex-direction: column;
}
.modal-header {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #334155;
}
.title { font-size: 14px; font-weight: 600; color: #e2e8f0; flex: 1; }
.cat-tag {
  background: #4c1d95; color: #c4b5fd;
  padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;
}
.close-btn {
  background: transparent; border: none; color: #94a3b8;
  font-size: 16px; cursor: pointer; padding: 0 4px;
}
.close-btn:hover { color: #e2e8f0; }
.modal-body { padding: 14px 16px; overflow-y: auto; }
.section { margin-bottom: 16px; }
.section-title {
  font-size: 12px; font-weight: 600; color: #94a3b8;
  margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.4px;
}
.section-title.err-title { color: #f87171; }
.kv-grid {
  display: grid; grid-template-columns: 100px 1fr; gap: 6px 12px;
  font-size: 12px;
}
.k { color: #64748b; }
.v { color: #e2e8f0; }
.v.accent { color: #fbbf24; font-weight: 600; }
.v.mono { font-family: monospace; color: #93c5fd; }
.code-block {
  background: #0f172a; border: 1px solid #334155;
  border-radius: 4px; padding: 10px;
  font-family: monospace; font-size: 11px;
  white-space: pre-wrap; word-break: break-all;
  max-height: 320px; overflow-y: auto;
  color: #e2e8f0;
}
.code-block.err-block { color: #fca5a5; border-color: #7f1d1d; background: #1a0a0a; }
.actions { display: flex; }
.link-btn {
  background: #334155; color: #93c5fd; padding: 6px 12px;
  border-radius: 4px; font-size: 12px; text-decoration: none;
}
.link-btn:hover { background: #475569; }
</style>
