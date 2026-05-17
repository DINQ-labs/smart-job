<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">{{ t('agentSessionsPage.title') }}</h1>
        <div class="gw-status" :class="gwOnline ? 'online' : 'offline'">
          <span class="gw-dot" />
          {{ gwOnline ? t('agentSessionsPage.online', { model: gwStatus?.model ?? '' }) : t('agentSessionsPage.offline') }}
        </div>
      </div>
      <div class="header-right">
        <div class="stats-chips">
          <span class="chip">{{ t('agentSessionsPage.totalChip', { n: allSessions.length }) }}</span>
          <span class="chip running">{{ t('agentSessionsPage.runningChip', { n: runningSessions.length }) }}</span>
          <span class="chip idle">{{ t('agentSessionsPage.idleChip', { n: idleSessions.length }) }}</span>
        </div>
        <button class="refresh-btn" :disabled="loading" @click="refresh">
          {{ loading ? t('agentSessionsPage.refreshing') : t('agentSessionsPage.refresh') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>User ID</th>
            <th>App User</th>
            <th>{{ t('agentSessionsPage.colMode') }}</th>
            <th>{{ t('agentSessionsPage.colStatus') }}</th>
            <th>{{ t('agentSessionsPage.colCurrentTool') }}</th>
            <th>{{ t('agentSessionsPage.colTurns') }}</th>
            <th>{{ t('agentSessionsPage.colMessages') }}</th>
            <th>{{ t('agentSessionsPage.colAccount') }}</th>
            <th>{{ t('agentSessionsPage.colLastActive') }}</th>
            <th>{{ t('agentSessionsPage.colActions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="allSessions.length === 0 && !loading">
            <td colspan="10" class="empty-row">{{ t('agentSessionsPage.emptyRow') }}</td>
          </tr>
          <tr
            v-for="sess in allSessions"
            :key="sess.user_id"
            class="data-row"
            :class="{ running: sess.running }"
            @click="openDetail(sess)"
          >
            <td class="mono">{{ sess.user_id }}</td>
            <td class="mono">{{ sess.app_user_id ?? '—' }}</td>
            <td>
              <span class="mode-badge" :class="'mode-' + (sess.current_mode || 'search')">
                {{ modeLabel(sess.current_mode) }}
              </span>
            </td>
            <td>
              <span class="status-badge" :class="sess.running ? 'running' : 'idle'">
                {{ sess.running ? t('agentSessionsPage.statusRunning') : t('agentSessionsPage.statusIdle') }}
              </span>
            </td>
            <td class="tool-cell">
              <span v-if="sess.current_tool" class="tool-tag">{{ sess.current_tool }}</span>
              <span v-else class="dim">—</span>
            </td>
            <td class="num">{{ sess.turn_count }}</td>
            <td class="num">{{ sess.history_length }}</td>
            <td>{{ sess.account_name ?? '—' }}</td>
            <td class="dim sm">{{ fmtTime(sess.last_active_at) }}</td>
            <td class="action-cell" @click.stop>
              <button
                v-if="sess.running"
                class="act-btn abort"
                :disabled="!!actionLoading[sess.user_id]"
                @click="abort(sess.user_id)"
              >{{ t('agentSessionsPage.abort') }}</button>
              <button
                class="act-btn kick"
                :disabled="!!actionLoading[sess.user_id]"
                @click="kick(sess.user_id)"
              >{{ t('agentSessionsPage.kick') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Detail drawer -->
    <Teleport to="body">
      <div v-if="detail" class="drawer-overlay" @click.self="detail = null">
        <div class="drawer">
          <div class="drawer-header">
            <span class="drawer-title">{{ t('agentSessionsPage.drawerTitle', { id: detail.user_id }) }}</span>
            <button class="drawer-close" @click="detail = null">✕</button>
          </div>
          <div class="drawer-body">
            <div class="detail-meta">
              <div class="meta-row"><span class="meta-label">App User</span><span class="mono">{{ detail.app_user_id ?? '—' }}</span></div>
              <div class="meta-row"><span class="meta-label">Ext Session</span><span class="mono">{{ detail.ext_session_id ?? '—' }}</span></div>
              <div class="meta-row"><span class="meta-label">{{ t('agentSessionsPage.colMode') }}</span>
                <span class="mode-badge" :class="'mode-' + (detail.current_mode || 'search')">
                  {{ modeLabel(detail.current_mode) }}
                </span>
              </div>
              <div class="meta-row"><span class="meta-label">{{ t('agentSessionsPage.colStatus') }}</span>
                <span class="status-badge" :class="detail.running ? 'running' : 'idle'">
                  {{ detail.running ? t('agentSessionsPage.statusRunning') : t('agentSessionsPage.statusIdle') }}
                </span>
              </div>
              <div v-if="detail.current_tool" class="meta-row">
                <span class="meta-label">{{ t('agentSessionsPage.colCurrentTool') }}</span>
                <span class="tool-tag">{{ detail.current_tool }}</span>
              </div>
              <div class="meta-row"><span class="meta-label">{{ t('agentSessionsPage.colTurns') }}</span>{{ detail.turn_count }}</div>
              <div class="meta-row"><span class="meta-label">{{ t('agentSessionsPage.totalMessages') }}</span>{{ detail.history_length }}</div>
              <div class="meta-row"><span class="meta-label">{{ t('agentSessionsPage.colLastActive') }}</span>{{ fmtTime(detail.last_active_at) }}</div>
            </div>

            <div class="msg-section">
              <div class="msg-section-title">{{ t('agentSessionsPage.recentMessages') }}</div>
              <div class="msg-list">
                <div
                  v-for="(m, i) in detail.recent_messages"
                  :key="i"
                  class="msg-item"
                  :class="m.role"
                >
                  <span class="msg-role">{{ m.role === 'user' ? '👤' : '🤖' }}</span>
                  <span class="msg-text">{{ m.text || t('agentSessionsPage.toolCall') }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="drawer-footer">
            <button v-if="detail.running" class="act-btn abort" @click="abort(detail!.user_id)">{{ t('agentSessionsPage.abortTask') }}</button>
            <button class="act-btn kick" @click="kick(detail!.user_id)">{{ t('agentSessionsPage.kickAndClear') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { AIAgentSession, AIAgentSessionDetail, AgentGatewayStatus } from '../types'

const { t } = useI18n()

const sessions      = ref<AIAgentSession[]>([])
const gwStatus      = ref<AgentGatewayStatus | null>(null)
const gwOnline      = ref(false)
const loading       = ref(false)
const error         = ref('')
const detail        = ref<AIAgentSessionDetail | null>(null)
const actionLoading = ref<Record<string, boolean>>({})

const allSessions     = computed(() => sessions.value)
const runningSessions = computed(() => sessions.value.filter(s => s.running))
const idleSessions    = computed(() => sessions.value.filter(s => !s.running))

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [statusRes, sessRes] = await Promise.allSettled([
      api.agent_getStatus(),
      api.agent_getSessions(),
    ])
    if (statusRes.status === 'fulfilled') {
      gwStatus.value = statusRes.value
      gwOnline.value = true
    } else {
      gwOnline.value = false
    }
    if (sessRes.status === 'fulfilled') {
      sessions.value = sessRes.value.sessions
    } else {
      error.value = t('agentSessionsPage.gatewayUnreachable')
    }
  } finally {
    loading.value = false
  }
}

async function openDetail(sess: AIAgentSession) {
  try {
    detail.value = await api.agent_getSessionDetail(sess.user_id)
  } catch {
    detail.value = { ...sess, recent_messages: [] }
  }
}

async function abort(userId: string) {
  actionLoading.value[userId] = true
  try {
    await api.agent_abortSession(userId)
    await refresh()
    if (detail.value?.user_id === userId) detail.value = null
  } finally {
    delete actionLoading.value[userId]
  }
}

async function kick(userId: string) {
  actionLoading.value[userId] = true
  try {
    await api.agent_deleteSession(userId)
    sessions.value = sessions.value.filter(s => s.user_id !== userId)
    if (detail.value?.user_id === userId) detail.value = null
  } catch {
    error.value = t('agentSessionsPage.actionFailed')
  } finally {
    delete actionLoading.value[userId]
  }
}

function modeLabel(m: string | null): string {
  const key = m ?? 'search'
  const known = ['search', 'evaluate', 'apply', 'interview', 'compare', 'recruiter']
  return known.includes(key) ? t('agentSessionsPage.modes.' + key) : (m ?? 'search')
}

function fmtTime(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

let timer: ReturnType<typeof setInterval>
onMounted(() => {
  refresh()
  timer = setInterval(refresh, 10_000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 16px; }

.page-header {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }

.gw-status {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 4px 10px; border-radius: 20px; font-weight: 500;
}
.gw-status.online  { background: #0d3321; color: #4ade80; }
.gw-status.offline { background: #3b1a1a; color: #f87171; }
.gw-dot {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0;
}

.header-right { display: flex; align-items: center; gap: 10px; }

.stats-chips { display: flex; gap: 6px; }
.chip {
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.chip.running { background: #1a3a2a; color: #4ade80; border-color: #166534; }
.chip.idle    { background: #1e293b; color: #94a3b8; }

.refresh-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
  transition: all 0.12s;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

.table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid #334155; }
.data-table {
  width: 100%; border-collapse: collapse; font-size: 13px; color: #cbd5e1;
}
.data-table th {
  background: #1e293b; padding: 10px 12px; text-align: left;
  font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase;
  letter-spacing: 0.5px; border-bottom: 1px solid #334155; white-space: nowrap;
}
.data-row {
  border-bottom: 1px solid #1e293b; cursor: pointer; transition: background 0.1s;
}
.data-row:hover { background: #1e293b; }
.data-row.running { background: rgba(74, 222, 128, 0.04); }
.data-row td { padding: 10px 12px; vertical-align: middle; }

.mono { font-family: 'SF Mono', monospace; font-size: 12px; }
.sm   { font-size: 12px; }
.dim  { color: #475569; }
.num  { text-align: center; }

.status-badge {
  display: inline-block; padding: 3px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.status-badge.running { background: #1a3a2a; color: #4ade80; }
.status-badge.idle    { background: #1e293b; color: #64748b; }

.tool-tag {
  display: inline-block; padding: 2px 7px; border-radius: 6px;
  background: #1e3a5f; color: #60a5fa; font-size: 11px; font-family: monospace;
}

.mode-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.mode-search    { background: #172554; color: #60a5fa; }
.mode-evaluate  { background: #431407; color: #fb923c; }
.mode-apply     { background: #052e16; color: #4ade80; }
.mode-interview { background: #3b0764; color: #c084fc; }
.mode-compare   { background: #4c0519; color: #fb7185; }
.mode-recruiter { background: #042f2e; color: #2dd4bf; }

.action-cell { display: flex; gap: 6px; }
.act-btn {
  padding: 5px 10px; border-radius: 5px; font-size: 12px; cursor: pointer; border: 1px solid;
  transition: all 0.12s; white-space: nowrap;
}
.act-btn.abort { background: #3b1a1a; color: #f87171; border-color: #7f1d1d; }
.act-btn.abort:hover:not(:disabled) { background: #7f1d1d; }
.act-btn.kick  { background: #1e293b; color: #94a3b8; border-color: #334155; }
.act-btn.kick:hover:not(:disabled)  { background: #334155; color: #e2e8f0; }
.act-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.empty-row { text-align: center; color: #475569; padding: 32px !important; }

/* Drawer */
.drawer-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000;
  display: flex; justify-content: flex-end;
}
.drawer {
  width: 480px; max-width: 95vw; background: #0f172a;
  border-left: 1px solid #334155; display: flex; flex-direction: column;
  animation: slideIn 0.2s ease;
}
@keyframes slideIn { from { transform: translateX(100%) } to { transform: translateX(0) } }

.drawer-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #334155;
}
.drawer-title { font-size: 15px; font-weight: 600; color: #e2e8f0; }
.drawer-close {
  width: 28px; height: 28px; border-radius: 6px; color: #64748b; background: transparent;
  border: none; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center;
}
.drawer-close:hover { background: #1e293b; color: #e2e8f0; }

.drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 20px; }

.detail-meta { display: flex; flex-direction: column; gap: 8px; }
.meta-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #cbd5e1; }
.meta-label { width: 90px; color: #64748b; font-size: 12px; flex-shrink: 0; }

.msg-section { display: flex; flex-direction: column; gap: 8px; }
.msg-section-title { font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
.msg-list { display: flex; flex-direction: column; gap: 6px; }
.msg-item {
  display: flex; gap: 8px; padding: 8px 10px; border-radius: 8px; font-size: 12px;
  background: #1e293b;
}
.msg-item.user      { align-self: flex-end; background: #1e3a5f; }
.msg-item.assistant { align-self: flex-start; }
.msg-role { flex-shrink: 0; }
.msg-text { color: #cbd5e1; line-height: 1.5; word-break: break-word; }

.drawer-footer {
  padding: 12px 20px; border-top: 1px solid #334155; display: flex; gap: 8px;
}
</style>
