<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-header">
        <span class="title">{{ t('commandDetailModal.title') }}</span>
        <div class="header-meta">
          <span class="tag" :class="statusClass">{{ statusLabel }}</span>
          <span v-if="cmd.duration_ms != null" class="duration">{{ cmd.duration_ms.toFixed(0) }}ms</span>
        </div>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">

        <!-- ── 执行链路 ── -->
        <div class="section">
          <div class="section-title">{{ t('commandDetailModal.execChain') }}</div>
          <div class="chain">

            <div class="chain-node" :class="cmd.agent_id ? 'node-agent' : 'node-http'">
              <div class="node-icon">{{ cmd.agent_id ? '🤖' : '🌐' }}</div>
              <div class="node-info">
                <span class="node-label">{{ cmd.agent_id ? t('commandDetailModal.initiatedByAgent') : t('commandDetailModal.directHttpCall') }}</span>
                <span v-if="cmd.agent_id" class="node-id">{{ cmd.agent_id }}</span>
              </div>
            </div>

            <div class="chain-arrow">↓</div>

            <div class="chain-node node-session">
              <div class="node-icon">🔌</div>
              <div class="node-info">
                <span class="node-label">{{ t('commandDetailModal.extension') }}{{ sessionDisplayLabel }}</span>
                <span class="node-id">{{ cmd.session_id }}</span>
              </div>
            </div>

            <div class="chain-arrow">↓</div>

            <div class="chain-node node-cmd">
              <div class="node-icon">⚙️</div>
              <div class="node-info">
                <span class="node-label">{{ cmd.tool_name || t('commandDetailModal.customCommand') }}</span>
                <span class="node-id">{{ cmd.method }} {{ cmd.path }}</span>
              </div>
            </div>

            <div class="chain-arrow">↓</div>

            <div class="chain-node" :class="resultNodeClass">
              <div class="node-icon">{{ resultIcon }}</div>
              <div class="node-info">
                <span class="node-label">{{ statusLabel }}</span>
                <span class="node-id">{{ cmd.duration_ms != null ? cmd.duration_ms.toFixed(1) + ' ms' : t('commandDetailModal.inProgress') }}</span>
              </div>
            </div>

          </div>
        </div>

        <!-- ── 时间信息 ── -->
        <div class="section">
          <div class="section-title">{{ t('commandDetailModal.execInfo') }}</div>
          <div class="kv-grid">
            <span class="k">{{ t('commandDetailModal.startTime') }}</span><span class="v">{{ fmtFull(cmd.started_at) }}</span>
            <span class="k">{{ t('commandDetailModal.endTime') }}</span><span class="v">{{ cmd.ended_at ? fmtFull(cmd.ended_at) : '—' }}</span>
            <span class="k">{{ t('commandDetailModal.duration') }}</span><span class="v accent">{{ cmd.duration_ms != null ? cmd.duration_ms.toFixed(1) + ' ms' : t('commandDetailModal.inProgress') }}</span>
            <span class="k">Request ID</span><span class="v mono small">{{ cmd.request_id }}</span>
          </div>
        </div>

        <!-- ── 请求参数 ── -->
        <div class="section" v-if="prettyBody !== null">
          <div class="section-title">{{ t('commandDetailModal.requestParams') }}</div>
          <pre class="code-block">{{ prettyBody }}</pre>
        </div>
        <div class="section" v-else>
          <div class="section-title">{{ t('commandDetailModal.requestParams') }}</div>
          <span class="empty-hint">{{ t('commandDetailModal.noParams') }}</span>
        </div>

        <!-- ── 返回结果 ── -->
        <div class="section" v-if="prettyResponse !== null">
          <div class="section-title">{{ t('commandDetailModal.responseResult') }}</div>
          <pre class="code-block response">{{ prettyResponse }}</pre>
        </div>
        <div class="section" v-else-if="cmd.response_ok === null">
          <div class="section-title">{{ t('commandDetailModal.responseResult') }}</div>
          <span class="empty-hint">{{ t('commandDetailModal.responsePending') }}</span>
        </div>

        <!-- ── 错误信息 ── -->
        <div class="section" v-if="cmd.error">
          <div class="section-title err-title">{{ t('commandDetailModal.errorInfo') }}</div>
          <pre class="code-block err-block">{{ cmd.error }}</pre>
        </div>

      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CommandLog } from '../types'
import { useSessionsStore } from '../stores/sessions'

const { t } = useI18n()

const props = defineProps<{ cmd: CommandLog }>()
defineEmits<{ close: [] }>()

const sessionsStore = useSessionsStore()

// ── 会话展示名 ──────────────────────────────────────────────────────────────
const sessionDisplayLabel = computed(() => {
  const s = sessionsStore.sessions.find(s => s.session_id === props.cmd.session_id)
  return s?.display_id ? ` #${s.display_id}` : ''
})

// ── 状态 ────────────────────────────────────────────────────────────────────
const statusLabel = computed(() => {
  if (props.cmd.response_ok === null) return t('commandDetailModal.statusPending')
  return props.cmd.response_ok ? t('commandDetailModal.statusSuccess') : t('commandDetailModal.statusFail')
})
const statusClass = computed(() => {
  if (props.cmd.response_ok === null) return 'pending'
  return props.cmd.response_ok ? 'ok' : 'fail'
})
const resultIcon = computed(() => {
  if (props.cmd.response_ok === null) return '⏳'
  return props.cmd.response_ok ? '✅' : '❌'
})
const resultNodeClass = computed(() => {
  if (props.cmd.response_ok === null) return 'node-pending'
  return props.cmd.response_ok ? 'node-ok' : 'node-fail'
})

// ── JSON 美化 ────────────────────────────────────────────────────────────────
function prettyJson(raw: string | null): string | null {
  if (!raw) return null
  try { return JSON.stringify(JSON.parse(raw), null, 2) } catch { return raw }
}
const prettyBody = computed(() => prettyJson(props.cmd.request_body))
const prettyResponse = computed(() => prettyJson(props.cmd.response_body))

function fmtFull(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false, fractionalSecondDigits: 3 } as Intl.DateTimeFormatOptions)
}
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.65);
  display: flex; align-items: center; justify-content: center; z-index: 200;
}
.modal {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px;
  width: 600px; max-height: 88vh; display: flex; flex-direction: column;
}
.modal-header {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px; border-bottom: 1px solid #334155;
}
.title { font-weight: 700; color: #e2e8f0; font-size: 14px; }
.header-meta { display: flex; align-items: center; gap: 8px; }
.duration { font-size: 12px; color: #fbbf24; }
.close-btn { margin-left: auto; background: none; border: none; color: #64748b; cursor: pointer; font-size: 16px; }

.modal-body { overflow-y: auto; flex: 1; padding: 16px; display: flex; flex-direction: column; gap: 18px; }

.section { display: flex; flex-direction: column; gap: 8px; }
.section-title {
  font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase;
  letter-spacing: .06em; padding-bottom: 4px; border-bottom: 1px solid #1e3a5f;
}
.err-title { color: #f87171; border-bottom-color: #7f1d1d; }
.empty-hint { font-size: 12px; color: #475569; }

/* ── Chain ── */
.chain { display: flex; flex-direction: column; align-items: flex-start; gap: 0; }
.chain-node {
  display: flex; align-items: center; gap: 10px;
  background: #0f172a; border: 1px solid #334155; border-radius: 7px;
  padding: 10px 14px; width: 100%;
}
.node-agent  { border-left: 3px solid #818cf8; }
.node-http   { border-left: 3px solid #475569; }
.node-session { border-left: 3px solid #38bdf8; }
.node-cmd    { border-left: 3px solid #fbbf24; }
.node-ok     { border-left: 3px solid #4ade80; }
.node-fail   { border-left: 3px solid #f87171; }
.node-pending { border-left: 3px solid #60a5fa; }

.node-icon { font-size: 18px; flex-shrink: 0; }
.node-info { display: flex; flex-direction: column; gap: 2px; overflow: hidden; }
.node-label { font-size: 13px; font-weight: 600; color: #e2e8f0; }
.node-id { font-family: monospace; font-size: 11px; color: #64748b; word-break: break-all; }

.chain-arrow { color: #334155; font-size: 16px; padding: 3px 0 3px 20px; }

/* ── KV grid ── */
.kv-grid { display: grid; grid-template-columns: 90px 1fr; gap: 6px 12px; font-size: 12px; }
.k { color: #64748b; }
.v { color: #e2e8f0; }
.v.mono { font-family: monospace; font-size: 11px; word-break: break-all; }
.v.small { font-size: 11px; }
.v.accent { color: #fbbf24; }

/* ── Code blocks ── */
.code-block {
  background: #0f172a; border: 1px solid #334155; border-radius: 6px;
  padding: 10px 12px; font-size: 11px; font-family: monospace;
  color: #93c5fd; overflow-x: auto; max-height: 260px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-word; margin: 0;
}
.code-block.response { color: #4ade80; }
.code-block.err-block { color: #f87171; border-color: #7f1d1d; }

/* ── Status tags ── */
.tag { font-size: 10px; padding: 2px 7px; border-radius: 4px; font-weight: 700; }
.tag.ok      { background: #14532d; color: #4ade80; }
.tag.fail    { background: #7f1d1d; color: #fca5a5; }
.tag.pending { background: #1e3a5f; color: #60a5fa; }
</style>
