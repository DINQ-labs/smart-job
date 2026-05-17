<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">{{ t('agentTasksPage.title') }}</h1>
        <div class="stats-chips">
          <span class="chip">{{ t('agentTasksPage.totalChip', { n: stats?.total ?? 0 }) }}</span>
          <span class="chip running">{{ t('agentTasksPage.runningChip', { n: stats?.running ?? 0 }) }}</span>
          <span class="chip paused">{{ t('agentTasksPage.pausedChip', { n: stats?.paused_user_action ?? 0 }) }}</span>
          <span class="chip completed">{{ t('agentTasksPage.completedChip', { n: stats?.completed ?? 0 }) }}</span>
          <span class="chip failed">{{ t('agentTasksPage.failedChip', { n: stats?.failed ?? 0 }) }}</span>
          <span class="chip cancelled">{{ t('agentTasksPage.cancelledChip', { n: stats?.cancelled ?? 0 }) }}</span>
        </div>
      </div>
      <div class="header-right">
        <label class="auto-refresh">
          <input type="checkbox" v-model="autoRefresh"> {{ t('agentTasksPage.autoRefresh') }}
        </label>
        <button class="refresh-btn" :disabled="loading" @click="refresh">
          {{ loading ? t('agentTasksPage.refreshing') : t('agentTasksPage.refresh') }}
        </button>
      </div>
    </div>

    <!-- 过滤栏 -->
    <div class="filter-bar">
      <select v-model="filter.status" @change="refresh">
        <option value="">{{ t('agentTasksPage.allStatuses') }}</option>
        <option value="running,paused_user_action,pending">{{ t('agentTasksPage.activeOnly') }}</option>
        <option value="running">{{ t('agentTasksPage.status.running') }}</option>
        <option value="paused_user_action">{{ t('agentTasksPage.status.paused_user_action') }}</option>
        <option value="pending">{{ t('agentTasksPage.status.pending') }}</option>
        <option value="completed">{{ t('agentTasksPage.statusCompletedOpt') }}</option>
        <option value="failed">{{ t('agentTasksPage.status.failed') }}</option>
        <option value="cancelled">{{ t('agentTasksPage.status.cancelled') }}</option>
      </select>
      <select v-model="filter.role" @change="refresh">
        <option value="">{{ t('agentTasksPage.allRoles') }}</option>
        <option value="jobseeker">{{ t('agentTasksPage.roleJobseeker') }}</option>
        <option value="recruiter">{{ t('agentTasksPage.roleRecruiter') }}</option>
      </select>
      <select v-model="filter.platform" @change="refresh">
        <option value="">{{ t('agentTasksPage.allPlatforms') }}</option>
        <option value="boss">Boss</option>
        <option value="linkedin">LinkedIn</option>
        <option value="indeed">Indeed</option>
      </select>
      <input
        type="search"
        v-model="filter.keyword"
        :placeholder="t('agentTasksPage.keywordPh')"
        @keyup.enter="refresh"
      />
      <input
        type="search"
        v-model="filter.user_id"
        placeholder="user_id"
        @keyup.enter="refresh"
      />
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>{{ t('agentTasksPage.colUser') }}</th>
            <th>{{ t('agentTasksPage.colRolePlatform') }}</th>
            <th>{{ t('agentTasksPage.colTemplate') }}</th>
            <th>{{ t('agentTasksPage.colTitle') }}</th>
            <th>{{ t('agentTasksPage.colStatus') }}</th>
            <th>{{ t('agentTasksPage.colProgress') }}</th>
            <th>{{ t('agentTasksPage.colSkipped') }}</th>
            <th>{{ t('agentTasksPage.colStart') }}</th>
            <th>{{ t('agentTasksPage.colHolder') }}</th>
            <th>{{ t('agentTasksPage.colActions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="tasks.length === 0 && !loading">
            <td colspan="11" class="empty-row">{{ t('agentTasksPage.emptyRow') }}</td>
          </tr>
          <tr
            v-for="task in tasks"
            :key="task.id"
            class="data-row"
            :class="rowClass(task)"
            @click="openDetail(task.id)"
          >
            <td class="mono">{{ task.id }}</td>
            <td class="mono sm" :title="task.user_id">{{ shortenId(task.user_id) }}</td>
            <td>
              <span class="role-badge" :class="'role-' + task.role">
                {{ task.role === 'jobseeker' ? t('agentTasksPage.roleJobseeker') : t('agentTasksPage.roleRecruiter') }}
              </span>
              <span class="platform-pill" :class="'platform-' + task.platform">
                {{ platformLabel(task.platform) }}
              </span>
            </td>
            <td class="sm mono">{{ task.template_id }}</td>
            <td class="title-cell">{{ task.title || '—' }}</td>
            <td>
              <span class="status-badge" :class="'status-' + task.status">
                {{ statusLabel(task.status) }}
              </span>
              <span v-if="task.paused_signal" class="signal-tag">{{ task.paused_signal }}</span>
            </td>
            <td>
              <div class="progress-cell">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: progressPct(task) + '%' }" />
                </div>
                <span class="progress-text">{{ progressText(task) }}</span>
              </div>
            </td>
            <td class="num">{{ task.skipped?.length ?? 0 }}</td>
            <td class="dim sm">{{ fmtTime(task.created_at) }}</td>
            <td class="mono sm" :title="task._lease_holder ?? ''">
              {{ shortenHolder(task._lease_holder) }}
            </td>
            <td class="action-cell" @click.stop>
              <button
                v-if="!isTerminal(task.status)"
                class="act-btn cancel"
                :disabled="!!actionLoading[task.id]"
                @click="forceCancel(task.id)"
              >{{ t('agentTasksPage.forceCancel') }}</button>
              <span v-else class="dim">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pagination">
      <button :disabled="filter.offset === 0" @click="prevPage">{{ t('agentTasksPage.prevPage') }}</button>
      <span>{{ filter.offset + 1 }} - {{ filter.offset + tasks.length }} / {{ total }}</span>
      <button :disabled="filter.offset + tasks.length >= total" @click="nextPage">{{ t('agentTasksPage.nextPage') }}</button>
    </div>

    <!-- 详情 drawer -->
    <Teleport to="body">
      <div v-if="detail" class="drawer-overlay" @click.self="detail = null">
        <div class="drawer">
          <div class="drawer-header">
            <span class="drawer-title">
              {{ t('agentTasksPage.taskHeader', { id: detail.id }) }}
              <span class="status-badge" :class="'status-' + detail.status">
                {{ statusLabel(detail.status) }}
              </span>
            </span>
            <button class="drawer-close" @click="detail = null">✕</button>
          </div>
          <div class="drawer-body">
            <div class="detail-meta">
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.colUser') }}</span><span class="mono">{{ detail.user_id }}</span></div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.colTemplate') }}</span><span class="mono">{{ detail.template_id }}</span></div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.metaRole') }}</span>{{ detail.role }} / {{ detail.platform }}</div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.colTitle') }}</span>{{ detail.title || '—' }}</div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.colProgress') }}</span>{{ progressText(detail) }} ({{ progressPct(detail) }}%)</div>
              <div v-if="detail.paused_signal" class="meta-row">
                <span class="meta-label">{{ t('agentTasksPage.riskSignal') }}</span>
                <span class="signal-tag">{{ detail.paused_signal }}</span>
                <span class="dim sm" v-if="detail.paused_at_idx != null">at idx={{ detail.paused_at_idx }}</span>
              </div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.metaCreated') }}</span>{{ fmtTime(detail.created_at) }}</div>
              <div class="meta-row" v-if="detail.started_at"><span class="meta-label">{{ t('agentTasksPage.colStart') }}</span>{{ fmtTime(detail.started_at) }}</div>
              <div class="meta-row" v-if="detail.paused_at"><span class="meta-label">{{ t('agentTasksPage.status.paused_user_action') }}</span>{{ fmtTime(detail.paused_at) }}</div>
              <div class="meta-row" v-if="detail.completed_at"><span class="meta-label">{{ t('agentTasksPage.status.completed') }}</span>{{ fmtTime(detail.completed_at) }}</div>
              <div class="meta-row" v-if="detail.cancelled_at"><span class="meta-label">{{ t('agentTasksPage.metaCancelled') }}</span>{{ fmtTime(detail.cancelled_at) }}</div>
              <div class="meta-row"><span class="meta-label">{{ t('agentTasksPage.heartbeat') }}</span>{{ fmtTime(detail.heartbeat_at) }}</div>
              <div class="meta-row" v-if="detail._lease_holder">
                <span class="meta-label">{{ t('agentTasksPage.leaseHolder') }}</span>
                <span class="mono sm">{{ detail._lease_holder }}</span>
              </div>
            </div>

            <div v-if="detail.result_summary" class="block">
              <div class="block-label">{{ t('agentTasksPage.resultSummary') }}</div>
              <pre class="block-text">{{ detail.result_summary }}</pre>
            </div>

            <div v-if="detail.error" class="block error-block">
              <div class="block-label">{{ t('agentTasksPage.errorLabel') }}</div>
              <pre class="block-text">{{ detail.error }}</pre>
            </div>

            <div v-if="detail.skipped?.length" class="block">
              <div class="block-label">{{ t('agentTasksPage.skippedLabel', { n: detail.skipped.length }) }}</div>
              <div v-for="(s, i) in detail.skipped" :key="i" class="skipped-row">
                <span class="mono dim">#{{ s.idx }}</span>
                <span class="skipped-name">{{ s.item_summary || '—' }}</span>
                <span class="signal-tag">{{ s.signal || s.reason || '—' }}</span>
              </div>
            </div>

            <div v-if="hasJsonContent(detail.inputs)" class="block">
              <div class="block-label">Inputs</div>
              <pre class="block-text json">{{ JSON.stringify(detail.inputs, null, 2) }}</pre>
            </div>

            <div v-if="hasJsonContent(detail.artifacts)" class="block">
              <div class="block-label">Artifacts</div>
              <pre class="block-text json">{{ JSON.stringify(detail.artifacts, null, 2) }}</pre>
            </div>
          </div>
          <div class="drawer-footer" v-if="!isTerminal(detail.status)">
            <button class="act-btn cancel" @click="forceCancel(detail.id)">
              {{ t('agentTasksPage.forceCancelTask') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { AdminTask, AdminTaskStats, TaskStatus } from '../types'

const { t } = useI18n()

const tasks = ref<AdminTask[]>([])
const total = ref(0)
const stats = ref<AdminTaskStats | null>(null)
const loading = ref(false)
const error = ref('')
const detail = ref<AdminTask | null>(null)
const actionLoading = reactive<Record<number, boolean>>({})
const autoRefresh = ref(true)

const filter = reactive({
  status: 'running,paused_user_action,pending' as string,
  role: '',
  platform: '',
  template_id: '',
  keyword: '',
  user_id: '',
  limit: 50,
  offset: 0,
})

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [statsRes, listRes] = await Promise.allSettled([
      api.admin_taskStats(),
      api.admin_listTasks({
        status: filter.status || undefined,
        role: filter.role || undefined,
        platform: filter.platform || undefined,
        template_id: filter.template_id || undefined,
        keyword: filter.keyword || undefined,
        user_id: filter.user_id || undefined,
        limit: filter.limit,
        offset: filter.offset,
      }),
    ])
    if (statsRes.status === 'fulfilled') stats.value = statsRes.value.stats
    if (listRes.status === 'fulfilled') {
      tasks.value = listRes.value.tasks
      total.value = listRes.value.total
    } else {
      error.value = t('agentTasksPage.fetchTasksFailed') + (listRes.reason?.message || t('agentTasksPage.unknownError'))
    }
  } finally {
    loading.value = false
  }
}

async function openDetail(taskId: number) {
  try {
    const r = await api.admin_taskDetail(taskId)
    detail.value = r.task
  } catch (e: any) {
    error.value = t('agentTasksPage.fetchDetailFailed') + (e.message || e)
  }
}

async function forceCancel(taskId: number) {
  if (!confirm(t('agentTasksPage.forceCancelConfirm', { id: taskId }))) return
  actionLoading[taskId] = true
  try {
    await api.admin_forceCancelTask(taskId)
    await refresh()
    if (detail.value?.id === taskId) {
      // 重拉详情显示新状态
      const r = await api.admin_taskDetail(taskId)
      detail.value = r.task
    }
  } catch (e: any) {
    error.value = t('agentTasksPage.forceCancelFailed') + (e.message || e)
  } finally {
    delete actionLoading[taskId]
  }
}

function prevPage() {
  filter.offset = Math.max(0, filter.offset - filter.limit)
  refresh()
}
function nextPage() {
  filter.offset = filter.offset + filter.limit
  refresh()
}

function statusLabel(s: TaskStatus): string {
  const known: TaskStatus[] = ['pending', 'running', 'paused_user_action', 'completed', 'failed', 'cancelled']
  return known.includes(s) ? t('agentTasksPage.status.' + s) : s
}
function isTerminal(s: TaskStatus): boolean {
  return s === 'completed' || s === 'failed' || s === 'cancelled'
}
function platformLabel(p: string): string {
  return ({ boss: 'Boss', linkedin: 'LinkedIn', indeed: 'Indeed' } as Record<string, string>)[p] || p
}

function rowClass(t: AdminTask): string {
  if (t.status === 'paused_user_action') return 'paused'
  if (t.status === 'running') return 'running'
  if (t.status === 'failed') return 'failed'
  return ''
}

function progressPct(t: AdminTask): number {
  const p = t.progress || {}
  if (p.total) return Math.round(((p.current || 0) / p.total) * 100)
  if (t.status === 'completed') return 100
  return 0
}

function progressText(t: AdminTask): string {
  const p = t.progress || {}
  if (p.total) return `${p.current || 0}/${p.total}`
  if (p.phase) return p.phase as string
  return t.status === 'completed' ? '✓' : '—'
}

function hasJsonContent(v: any): boolean {
  if (!v) return false
  if (typeof v !== 'object') return false
  return Object.keys(v).length > 0
}

function shortenId(id: string): string {
  if (!id) return '—'
  return id.length > 12 ? id.slice(0, 12) + '…' : id
}

function shortenHolder(holder: string | null | undefined): string {
  if (!holder) return '—'
  return holder.length > 16 ? holder.slice(0, 16) + '…' : holder
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

let timer: ReturnType<typeof setInterval> | null = null
function startTimer() {
  if (timer) clearInterval(timer)
  if (autoRefresh.value) {
    timer = setInterval(refresh, 5000)
  }
}

watch(autoRefresh, startTimer)

onMounted(() => {
  refresh()
  startTimer()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 14px; }
.page-header {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
}
.header-left { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }

.stats-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.chip.running   { background: #1a3a2a; color: #4ade80; border-color: #166534; }
.chip.paused    { background: #3d2e0a; color: #fbbf24; border-color: #854d0e; }
.chip.completed { background: #0a2540; color: #60a5fa; border-color: #1e3a5f; }
.chip.failed    { background: #3b1a1a; color: #f87171; border-color: #7f1d1d; }
.chip.cancelled { background: #1e293b; color: #64748b; }

.header-right { display: flex; align-items: center; gap: 10px; }
.auto-refresh {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: #94a3b8; cursor: pointer;
}
.refresh-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.filter-bar {
  display: flex; gap: 8px; flex-wrap: wrap;
  padding: 10px; background: #1e293b; border-radius: 8px; border: 1px solid #334155;
}
.filter-bar select, .filter-bar input {
  padding: 6px 10px; border-radius: 5px; font-size: 12px;
  background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
}
.filter-bar input { min-width: 180px; }

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

.table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid #334155; }
.data-table {
  width: 100%; border-collapse: collapse; font-size: 12px; color: #cbd5e1;
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
.data-row.running { background: rgba(74, 222, 128, 0.05); }
.data-row.paused {
  background: rgba(251, 191, 36, 0.07);
  border-left: 2px solid #f59e0b;
}
.data-row.failed { background: rgba(248, 113, 113, 0.05); }
.data-row td { padding: 9px 12px; vertical-align: middle; }

.empty-row { text-align: center; padding: 32px; color: #64748b; }

.mono { font-family: 'SF Mono', monospace; font-size: 11px; }
.sm   { font-size: 11px; }
.dim  { color: #475569; }
.num  { text-align: center; }

.title-cell {
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.role-badge {
  display: inline-block; padding: 2px 6px; border-radius: 4px;
  font-size: 10px; font-weight: 600; margin-right: 4px;
}
.role-jobseeker { background: #1e3a5f; color: #60a5fa; }
.role-recruiter { background: #3d2e0a; color: #fbbf24; }

.platform-pill {
  display: inline-block; padding: 2px 6px; border-radius: 4px;
  font-size: 10px; font-weight: 600;
}
.platform-boss     { background: #0a3d2a; color: #4ade80; }
.platform-linkedin { background: #1e3a5f; color: #60a5fa; }
.platform-indeed   { background: #2d1b3d; color: #c084fc; }

.status-badge {
  display: inline-block; padding: 3px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.status-pending     { background: #1e293b; color: #94a3b8; }
.status-running     { background: #1a3a2a; color: #4ade80; }
.status-paused_user_action { background: #3d2e0a; color: #fbbf24; }
.status-completed   { background: #0a2540; color: #60a5fa; }
.status-failed      { background: #3b1a1a; color: #f87171; }
.status-cancelled   { background: #1e293b; color: #64748b; }

.signal-tag {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  background: #3b1a1a; color: #fca5a5;
  font-size: 10px; font-family: monospace; margin-left: 4px;
}

.progress-cell { display: flex; align-items: center; gap: 6px; min-width: 100px; }
.progress-bar {
  flex: 1; height: 4px; background: #1e293b; border-radius: 2px; overflow: hidden;
}
.progress-fill { height: 100%; background: #4ade80; transition: width 0.3s; }
.progress-text { font-size: 10px; color: #94a3b8; min-width: 36px; text-align: right; }

.action-cell { white-space: nowrap; }
.act-btn {
  padding: 4px 10px; border-radius: 4px; font-size: 11px;
  border: 1px solid #334155; cursor: pointer;
}
.act-btn.cancel {
  background: transparent; color: #f87171; border-color: #7f1d1d;
}
.act-btn.cancel:hover:not(:disabled) { background: #3b1a1a; }

.pagination {
  display: flex; align-items: center; gap: 12px; justify-content: center;
  font-size: 12px; color: #94a3b8;
}
.pagination button {
  padding: 5px 12px; border-radius: 5px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
  font-size: 12px;
}
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }

/* Drawer */
.drawer-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5);
  display: flex; justify-content: flex-end; z-index: 100;
}
.drawer {
  width: 560px; max-width: 90vw; height: 100vh;
  background: #0f172a; display: flex; flex-direction: column;
  border-left: 1px solid #334155;
}
.drawer-header {
  padding: 14px 18px; border-bottom: 1px solid #334155;
  display: flex; align-items: center; justify-content: space-between;
}
.drawer-title { font-size: 15px; font-weight: 600; color: #e2e8f0; display: flex; gap: 8px; align-items: center; }
.drawer-close {
  background: transparent; border: none; color: #64748b;
  font-size: 16px; cursor: pointer; padding: 4px 8px;
}
.drawer-close:hover { color: #e2e8f0; }
.drawer-body {
  flex: 1; overflow-y: auto; padding: 16px 18px;
  display: flex; flex-direction: column; gap: 14px;
}
.drawer-footer {
  padding: 12px 18px; border-top: 1px solid #334155;
  display: flex; justify-content: flex-end;
}

.detail-meta { display: flex; flex-direction: column; gap: 6px; }
.meta-row {
  display: flex; align-items: center; gap: 10px; font-size: 12px;
}
.meta-label {
  width: 90px; flex-shrink: 0; color: #64748b;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
}

.block {
  background: #1e293b; border-radius: 6px; padding: 10px 12px;
  border: 1px solid #334155;
}
.error-block { background: #2a1414; border-color: #7f1d1d; }
.block-label {
  font-size: 10px; letter-spacing: 0.5px; text-transform: uppercase;
  color: #64748b; margin-bottom: 6px; font-weight: 600;
}
.block-text {
  margin: 0; font-size: 12px; color: #cbd5e1;
  white-space: pre-wrap; word-wrap: break-word; font-family: inherit;
}
.block-text.json { font-family: 'SF Mono', monospace; font-size: 11px; }
.error-block .block-text { color: #fca5a5; }

.skipped-row {
  display: flex; gap: 8px; align-items: center;
  padding: 4px 0; font-size: 12px;
}
.skipped-name { flex: 1; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
