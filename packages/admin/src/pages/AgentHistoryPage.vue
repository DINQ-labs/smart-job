<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">{{ t('agentHistoryPage.title') }}</h1>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="toolbar">
      <input
        v-model="filterUserId"
        class="filter-input"
        :placeholder="t('agentHistoryPage.userIdPh')"
        @keydown.enter="search"
      />
      <select v-model="filterMode" class="filter-select" @change="search">
        <option value="">{{ t('agentHistoryPage.allModes') }}</option>
        <option value="search">{{ t('agentHistoryPage.modes.search') }}</option>
        <option value="evaluate">{{ t('agentHistoryPage.modes.evaluate') }}</option>
        <option value="apply">{{ t('agentHistoryPage.modes.apply') }}</option>
        <option value="interview">{{ t('agentHistoryPage.modes.interview') }}</option>
        <option value="compare">{{ t('agentHistoryPage.modes.compare') }}</option>
        <option value="recruiter">{{ t('agentHistoryPage.modes.recruiter') }}</option>
      </select>
      <button class="refresh-btn" :disabled="loading" @click="search">{{ t('agentHistoryPage.query') }}</button>
    </div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>User ID</th>
            <th>App User</th>
            <th>{{ t('agentHistoryPage.colMode') }}</th>
            <th>{{ t('agentHistoryPage.colStartTime') }}</th>
            <th>{{ t('agentHistoryPage.colEndTime') }}</th>
            <th>{{ t('agentHistoryPage.colTurns') }}</th>
            <th>{{ t('agentHistoryPage.colEvents') }}</th>
            <th>Tokens (in/out)</th>
            <th :title="t('agentHistoryPage.cacheTitle')">Cache (w/r)</th>
            <th>{{ t('agentHistoryPage.colCost') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="sessions.length === 0 && !loading">
            <td colspan="11" class="empty-row">{{ t('agentHistoryPage.emptyRow') }}</td>
          </tr>
          <tr
            v-for="s in sessions"
            :key="s.id"
            class="data-row"
            @click="openDetail(s)"
          >
            <td class="mono sm">{{ s.id }}</td>
            <td class="mono">{{ s.user_id }}</td>
            <td class="mono sm">{{ s.app_user_id ?? '—' }}</td>
            <td>
              <span v-if="s.primary_mode" class="mode-badge" :class="'mode-' + s.primary_mode">
                {{ modeLabel(s.primary_mode) }}
              </span>
              <span v-else class="dim">—</span>
            </td>
            <td class="sm dim">{{ fmtDt(s.started_at) }}</td>
            <td class="sm dim">{{ s.ended_at ? fmtDt(s.ended_at) : t('agentHistoryPage.inProgress') }}</td>
            <td class="num">{{ s.turn_count }}</td>
            <td class="num">{{ s.event_count }}</td>
            <td class="num sm">
              <span class="token-in">{{ fmtTokens(s.total_input_tokens) }}</span>
              <span class="tok-sep">/</span>
              <span class="token-out">{{ fmtTokens(s.total_output_tokens) }}</span>
            </td>
            <td class="num sm dim">
              {{ fmtTokens(s.total_cache_creation_input_tokens) }}
              <span class="tok-sep">/</span>
              {{ fmtTokens(s.total_cache_read_input_tokens) }}
            </td>
            <td class="num cost">{{ fmtUsd(s.total_cost_usd) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="hasMore" class="pagination">
      <button :disabled="offset === 0" @click="paginate(-1)">{{ t('agentHistoryPage.prevPage') }}</button>
      <span>{{ offset / limit + 1 }}</span>
      <button :disabled="sessions.length < limit" @click="paginate(1)">{{ t('agentHistoryPage.nextPage') }}</button>
    </div>

    <!-- Detail drawer -->
    <Teleport to="body">
      <div v-if="drawer" class="drawer-overlay" @click.self="drawer = null">
        <div class="drawer">
          <div class="drawer-header">
            <span class="drawer-title">{{ t('agentHistoryPage.drawerTitle', { id: drawer.session_id, user: drawer.user_id }) }}</span>
            <button class="drawer-close" @click="drawer = null">✕</button>
          </div>
          <div class="drawer-body">
            <div class="drawer-usage">
              <div class="usage-cell">
                <span class="usage-k">{{ t('agentHistoryPage.usageInput') }}</span>
                <span class="usage-v token-in">{{ fmtTokens(drawer.session.total_input_tokens) }}</span>
              </div>
              <div class="usage-cell">
                <span class="usage-k">{{ t('agentHistoryPage.usageOutput') }}</span>
                <span class="usage-v token-out">{{ fmtTokens(drawer.session.total_output_tokens) }}</span>
              </div>
              <div class="usage-cell">
                <span class="usage-k">{{ t('agentHistoryPage.usageCacheWrite') }}</span>
                <span class="usage-v dim">{{ fmtTokens(drawer.session.total_cache_creation_input_tokens) }}</span>
              </div>
              <div class="usage-cell">
                <span class="usage-k">{{ t('agentHistoryPage.usageCacheRead') }}</span>
                <span class="usage-v dim">{{ fmtTokens(drawer.session.total_cache_read_input_tokens) }}</span>
              </div>
              <div class="usage-cell">
                <span class="usage-k">{{ t('agentHistoryPage.usageCost') }}</span>
                <span class="usage-v cost">{{ fmtUsd(drawer.session.total_cost_usd) }}</span>
              </div>
            </div>
            <div v-if="drawerLoading" class="dim center">{{ t('agentHistoryPage.loading') }}</div>
            <div v-else class="msg-list">
              <template v-for="(ev, i) in drawer.events" :key="ev.id">
                <div
                  v-if="i === 0 || ev.turn_index !== drawer.events[i-1].turn_index"
                  class="turn-sep"
                >{{ t('agentHistoryPage.turn', { n: ev.turn_index }) }}</div>
                <div class="msg-item" :class="evClass(ev)">
                  <span class="msg-role">{{ evIcon(ev) }}</span>
                  <div class="msg-body">
                    <div class="msg-type-tag">{{ evLabel(ev) }}</div>
                    <span v-if="errCategoryLabel(ev)" class="err-category-badge">{{ errCategoryLabel(ev) }}</span>
                    <div v-else-if="ev.tool_name" class="tool-tag sm">{{ ev.tool_name }}</div>
                    <pre v-if="ev.content" class="msg-content">{{ ev.content }}</pre>
                    <span v-if="ev.ok === false" class="err-badge">{{ t('agentHistoryPage.failed') }}</span>
                  </div>
                </div>
              </template>
              <div v-if="!drawerLoading && drawer.events.length === 0" class="dim center">{{ t('agentHistoryPage.noEvents') }}</div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { AgentConvSession, AgentConvEvent } from '../types'

const { t } = useI18n()

const sessions     = ref<AgentConvSession[]>([])
const loading      = ref(false)
const error        = ref('')
const filterUserId = ref('')
const filterMode   = ref('')
const limit        = 50
const offset       = ref(0)

function modeLabel(m: string | null): string {
  const known = ['search', 'evaluate', 'apply', 'interview', 'compare', 'recruiter']
  if (m && known.includes(m)) return t('agentHistoryPage.modes.' + m)
  return m ?? '—'
}

const hasMore = computed(() => offset.value > 0 || sessions.value.length >= limit)

interface DrawerState {
  session_id: number
  user_id: string
  session: AgentConvSession
  events: AgentConvEvent[]
}
const drawer        = ref<DrawerState | null>(null)
const drawerLoading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.agent_getHistory({
      user_id: filterUserId.value || undefined,
      limit,
      offset: offset.value,
    })
    let list = res.sessions
    if (filterMode.value) {
      list = list.filter(s => s.primary_mode === filterMode.value)
    }
    sessions.value = list
  } catch {
    error.value = t('agentHistoryPage.loadFailed')
  } finally {
    loading.value = false
  }
}

function search() {
  offset.value = 0
  load()
}

function paginate(dir: 1 | -1) {
  offset.value = Math.max(0, offset.value + dir * limit)
  load()
}

async function openDetail(s: AgentConvSession) {
  drawerLoading.value = true
  drawer.value = { session_id: s.id, user_id: s.user_id, session: s, events: [] }
  try {
    const res = await api.agent_getHistoryEvents(s.id)
    drawer.value = { session_id: s.id, user_id: s.user_id, session: s, events: res.events }
  } finally {
    drawerLoading.value = false
  }
}

function evClass(ev: AgentConvEvent) {
  if (ev.event_type === 'user_message') return 'user'
  if (ev.event_type === 'assistant_text') return 'assistant'
  if (ev.event_type === 'error') return 'err'
  return 'tool'
}

function evIcon(ev: AgentConvEvent) {
  const m: Record<string, string> = {
    user_message: '👤', assistant_text: '🤖',
    tool_call: '🔧', tool_result: '📦', error: '⚠️', aborted: '⛔',
  }
  return m[ev.event_type] ?? '•'
}

function evLabel(ev: AgentConvEvent) {
  const known = ['user_message', 'assistant_text', 'tool_call', 'tool_result', 'error', 'aborted']
  return known.includes(ev.event_type) ? t('agentHistoryPage.eventLabels.' + ev.event_type) : ev.event_type
}

function errCategoryLabel(ev: AgentConvEvent): string {
  if (ev.event_type !== 'error' || !ev.tool_name) return ''
  const known = ['payment', 'auth', 'forbidden', 'rate_limit', 'overloaded', 'connection', 'timeout', 'unknown']
  return known.includes(ev.tool_name) ? t('agentHistoryPage.errCategories.' + ev.tool_name) : ev.tool_name
}

function fmtDt(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function fmtTokens(n: number | null | undefined): string {
  const v = n ?? 0
  if (v === 0) return '—'
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M'
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'K'
  return String(v)
}

function fmtUsd(n: number | null | undefined): string {
  const v = n ?? 0
  if (v === 0) return '—'
  if (v >= 1) return '$' + v.toFixed(3)
  if (v >= 0.01) return '$' + v.toFixed(4)
  return '$' + v.toFixed(6)
}

onMounted(load)
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 16px; }

.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

.toolbar { display: flex; gap: 8px; }
.filter-input {
  flex: 1; max-width: 360px; padding: 8px 12px;
  border: 1px solid #334155; border-radius: 6px;
  background: #1e293b; color: #e2e8f0; font-size: 13px; outline: none;
}
.filter-input:focus { border-color: #60a5fa; }
.filter-input::placeholder { color: #475569; }

.filter-select {
  padding: 8px 12px; border: 1px solid #334155; border-radius: 6px;
  background: #1e293b; color: #e2e8f0; font-size: 13px; outline: none;
}
.filter-select:focus { border-color: #60a5fa; }

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

.refresh-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid #334155; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; color: #cbd5e1; }
.data-table th {
  background: #1e293b; padding: 10px 12px; text-align: left;
  font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase;
  letter-spacing: 0.5px; border-bottom: 1px solid #334155; white-space: nowrap;
}
.data-row { border-bottom: 1px solid #1e293b; cursor: pointer; transition: background 0.1s; }
.data-row:hover { background: #1e293b; }
.data-row td { padding: 10px 12px; vertical-align: middle; }

.mono { font-family: 'SF Mono', monospace; font-size: 12px; }
.sm   { font-size: 12px; }
.dim  { color: #475569; }
.num  { text-align: center; }
.center { text-align: center; padding: 20px; }

.token-in  { color: #93c5fd; }
.token-out { color: #fca5a5; }
.tok-sep   { color: #475569; margin: 0 3px; }
.cost      { color: #fcd34d; font-family: 'SF Mono', monospace; font-size: 12px; }

.empty-row { text-align: center; color: #475569; padding: 32px !important; }

.pagination {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  padding: 12px; font-size: 13px; color: #64748b;
}
.pagination button {
  padding: 5px 12px; border-radius: 5px; font-size: 12px; cursor: pointer;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.pagination button:hover:not(:disabled) { background: #334155; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }

/* Drawer */
.drawer-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000;
  display: flex; justify-content: flex-end;
}
.drawer {
  width: 500px; max-width: 95vw; background: #0f172a;
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

.drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; }

.drawer-usage {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px;
  padding: 10px; margin-bottom: 12px;
  background: #1e293b; border-radius: 8px; border: 1px solid #334155;
}
.usage-cell { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.usage-k { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.3px; }
.usage-v { font-size: 13px; font-weight: 600; font-family: 'SF Mono', monospace; }

.msg-list { display: flex; flex-direction: column; gap: 6px; }

.turn-sep {
  font-size: 11px; font-weight: 600; color: #475569;
  text-transform: uppercase; letter-spacing: 0.5px;
  padding: 4px 0; border-top: 1px solid #1e293b; margin-top: 4px;
}

.msg-item {
  display: flex; gap: 8px; padding: 8px 10px; border-radius: 8px; font-size: 12px;
  background: #1e293b;
}
.msg-item.user      { background: #1e3a5f; }
.msg-item.err       { background: #3b1a1a; }
.msg-role { flex-shrink: 0; }
.msg-body { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.msg-type-tag { font-size: 10px; color: #64748b; font-weight: 600; text-transform: uppercase; }

.tool-tag {
  display: inline-block; padding: 2px 7px; border-radius: 6px;
  background: #1e3a5f; color: #60a5fa; font-size: 11px; font-family: monospace;
}
.tool-tag.sm { font-size: 10px; padding: 1px 5px; }

.msg-content {
  font-size: 12px; color: #cbd5e1; white-space: pre-wrap; word-break: break-word;
  font-family: 'SF Mono', monospace; background: #0f172a;
  padding: 6px 8px; border-radius: 5px; margin: 0; max-height: 200px; overflow-y: auto;
}
.err-badge {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  background: #7f1d1d; color: #f87171; font-size: 11px;
}
.err-category-badge {
  display: inline-block; padding: 2px 8px; border-radius: 6px;
  background: #7f1d1d; color: #fca5a5; font-size: 11px; font-weight: 600;
}
</style>
