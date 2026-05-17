<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">API Capture</h1>
      <span v-if="sessions.length" class="count-badge">{{ sessions.length }} sessions</span>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="toolbar">
      <button class="refresh-btn" :disabled="loading" @click="load">{{ t('apiCapturePage.refresh') }}</button>
    </div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Session ID</th>
            <th>Name</th>
            <th>Tab URL</th>
            <th>Requests</th>
            <th>Created</th>
            <th>Analysis</th>
            <th>Actions / Download</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="sessions.length === 0 && !loading">
            <td colspan="7" class="empty-row">{{ t('apiCapturePage.emptySessions') }}</td>
          </tr>
          <tr
            v-for="s in sessions"
            :key="s.id"
            class="data-row"
            @click="openDetail(s)"
          >
            <td class="mono sm">{{ s.id }}</td>
            <td class="name-cell">{{ s.name || '\u2014' }}</td>
            <td class="url-cell" :title="s.tab_url">{{ truncUrl(s.tab_url) }}</td>
            <td class="num">{{ s.request_count }}</td>
            <td class="sm dim">{{ fmtDt(s.created_at) }}</td>
            <td>
              <span class="status-badge" :class="'st-' + s.analysis_status">
                {{ statusLabel(s.analysis_status) }}
              </span>
            </td>
            <td class="actions" @click.stop>
              <button
                class="action-btn analyze"
                :disabled="s.analysis_status === 'analyzing'"
                @click="analyze(s)"
              >{{ t('apiCapturePage.analyze') }}</button>
              <button class="action-btn download" @click="downloadRaw(s.id)">{{ t('apiCapturePage.capture') }}</button>
              <button
                v-if="s.analysis_status === 'done'"
                class="action-btn download"
                @click="downloadAnalysis(s.id)"
              >{{ t('apiCapturePage.analysisShort') }}</button>
              <button class="action-btn delete" @click="confirmDelete(s)">{{ t('apiCapturePage.delete') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="hasMore" class="pagination">
      <button :disabled="offset === 0" @click="paginate(-1)">{{ t('apiCapturePage.prevPage') }}</button>
      <span>{{ offset / limit + 1 }}</span>
      <button :disabled="sessions.length < limit" @click="paginate(1)">{{ t('apiCapturePage.nextPage') }}</button>
    </div>

    <!-- Detail drawer -->
    <Teleport to="body">
      <div v-if="drawer" class="drawer-overlay" @click.self="drawer = null">
        <div class="drawer">
          <div class="drawer-header">
            <div class="drawer-title-block">
              <span class="drawer-title">{{ drawer.sessionId }}</span>
              <span class="drawer-subtitle">{{ drawer.tabUrl }}</span>
              <div class="drawer-download-bar">
                <button class="action-btn download" @click="downloadRaw(drawer.sessionId)">{{ t('apiCapturePage.downloadRaw') }}</button>
                <button
                  v-if="drawer.analysisStatus === 'done'"
                  class="action-btn download"
                  @click="downloadAnalysis(drawer.sessionId)"
                >{{ t('apiCapturePage.downloadAnalysis') }}</button>
              </div>
            </div>
            <button class="drawer-close" @click="drawer = null">&#10005;</button>
          </div>
          <div class="drawer-tabs">
            <button
              class="tab-btn"
              :class="{ active: drawerTab === 'requests' }"
              @click="drawerTab = 'requests'"
            >{{ t('apiCapturePage.tabRequests', { n: drawer.total }) }}</button>
            <button
              class="tab-btn"
              :class="{ active: drawerTab === 'analysis' }"
              @click="drawerTab = 'analysis'"
            >Analysis</button>
            <button
              class="tab-btn"
              :class="{ active: drawerTab === 'implement' }"
              @click="drawerTab = 'implement'"
            >Implement</button>
          </div>
          <div class="drawer-body">
            <!-- Requests tab -->
            <template v-if="drawerTab === 'requests'">
              <div v-if="drawerLoading" class="dim center">{{ t('apiCapturePage.loading') }}</div>
              <div v-else class="req-list">
                <div
                  v-for="r in drawer.requests"
                  :key="r.id"
                  class="req-item"
                  @click="toggleExpand(r.id)"
                >
                  <div class="req-summary">
                    <span class="method-badge" :class="'m-' + r.method.toLowerCase()">{{ r.method }}</span>
                    <span class="status-code" :class="statusCodeClass(r.response_status)">{{ r.response_status ?? '?' }}</span>
                    <span class="req-url" :title="r.url">{{ urlPath(r.url) }}</span>
                    <span class="req-type dim">{{ r.resource_type }}</span>
                  </div>
                  <div v-if="expandedId === r.id" class="req-detail" @click.stop>
                    <div class="detail-section">
                      <div class="detail-label">URL</div>
                      <pre class="detail-code">{{ r.url }}</pre>
                    </div>
                    <div v-if="r.request_headers" class="detail-section">
                      <div class="detail-label">Request Headers</div>
                      <pre class="detail-code">{{ formatJson(r.request_headers) }}</pre>
                    </div>
                    <div v-if="r.request_body" class="detail-section">
                      <div class="detail-label">Request Body</div>
                      <pre class="detail-code">{{ tryFormatJson(r.request_body) }}</pre>
                    </div>
                    <div v-if="r.response_headers" class="detail-section">
                      <div class="detail-label">Response Headers</div>
                      <pre class="detail-code">{{ formatJson(r.response_headers) }}</pre>
                    </div>
                    <div v-if="r.response_body" class="detail-section">
                      <div class="detail-label">Response Body</div>
                      <pre class="detail-code long">{{ tryFormatJson(r.response_body) }}</pre>
                    </div>
                  </div>
                </div>
                <div v-if="drawer.requests.length === 0" class="dim center">{{ t('apiCapturePage.noRequests') }}</div>
                <div v-if="drawer.total > drawer.requests.length" class="dim center" style="padding:12px">
                  {{ t('apiCapturePage.showing', { shown: drawer.requests.length, total: drawer.total }) }}
                  <button class="action-btn" style="margin-left:8px" @click="loadMoreRequests">{{ t('apiCapturePage.loadMore') }}</button>
                </div>
              </div>
            </template>

            <!-- Analysis tab -->
            <template v-if="drawerTab === 'analysis'">
              <div v-if="drawer.analysisStatus === 'none'" class="center" style="padding:32px">
                <p class="dim" style="margin-bottom:12px">{{ t('apiCapturePage.notAnalyzed') }}</p>
                <button class="action-btn analyze" @click="analyzeFromDrawer">{{ t('apiCapturePage.startAnalyze') }}</button>
              </div>
              <div v-else-if="drawer.analysisStatus === 'analyzing'" class="center" style="padding:32px">
                <div class="spinner"></div>
                <p class="dim" style="margin-top:12px">{{ t('apiCapturePage.llmAnalyzing') }}</p>
              </div>
              <div v-else-if="drawer.analysisStatus === 'failed'" class="center" style="padding:32px">
                <p style="color:#f87171;margin-bottom:12px">{{ t('apiCapturePage.analysisFailed') }}</p>
                <button class="action-btn analyze" @click="analyzeFromDrawer">{{ t('apiCapturePage.retry') }}</button>
              </div>
              <div v-else class="analysis-content">
                <div v-if="drawer.analysisModel" class="dim" style="margin-bottom:8px;font-size:11px">
                  Model: {{ drawer.analysisModel }}
                </div>
                <pre class="analysis-text">{{ drawer.analysisText }}</pre>
              </div>
            </template>

            <!-- Implement tab -->
            <template v-if="drawerTab === 'implement'">
              <div v-if="drawer.analysisStatus !== 'done'" class="center dim" style="padding:32px">
                {{ t('apiCapturePage.implementNeedAnalysis') }}
              </div>
              <div v-else class="implement-panel">
                <div class="impl-field">
                  <label>{{ t('apiCapturePage.targetProject') }}</label>
                  <select v-model="implTarget">
                    <option value="job-api-gateway">{{ t('apiCapturePage.targetApiGateway') }}</option>
                    <option value="job-agent-gateway">{{ t('apiCapturePage.targetAgentGateway') }}</option>
                  </select>
                </div>
                <div class="impl-field">
                  <label>{{ t('apiCapturePage.implInstructions') }}</label>
                  <textarea v-model="implInstructions" rows="5" :placeholder="t('apiCapturePage.implInstructionsPh')"></textarea>
                </div>
                <button
                  class="action-btn analyze"
                  :disabled="implRunning || !implInstructions.trim()"
                  @click="startImplement"
                >{{ implRunning ? t('apiCapturePage.implementing') : t('apiCapturePage.startImplement') }}</button>
                <pre v-if="implOutput" ref="implTerminalRef" class="impl-terminal">{{ implOutput }}</pre>
                <div v-if="implExitCode !== null" class="impl-done" :class="implExitCode === 0 ? 'ok' : 'err'">
                  {{ t('apiCapturePage.exitCode', { code: implExitCode }) }}
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirm -->
    <Teleport to="body">
      <div v-if="deleteTarget" class="drawer-overlay" @click.self="deleteTarget = null">
        <div class="confirm-modal">
          <p>{{ t('apiCapturePage.confirmDeletePrefix') }}<code>{{ deleteTarget.id }}</code>{{ t('apiCapturePage.confirmDeleteSuffix') }}</p>
          <div class="confirm-actions">
            <button class="action-btn" @click="deleteTarget = null">{{ t('apiCapturePage.cancel') }}</button>
            <button class="action-btn delete" @click="doDelete">{{ t('apiCapturePage.delete') }}</button>
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
import type { CaptureSession, CaptureRequest } from '../types'

const { t } = useI18n()

const sessions = ref<CaptureSession[]>([])
const loading  = ref(false)
const error    = ref('')
const limit    = 50
const offset   = ref(0)

const hasMore = computed(() => offset.value > 0 || sessions.value.length >= limit)

// ── Load sessions ───────────────────────────────────────────────────────────

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.capture_getSessions({ limit, offset: offset.value })
    sessions.value = res.sessions
  } catch {
    error.value = t('apiCapturePage.loadFailed')
  } finally {
    loading.value = false
  }
}

function paginate(dir: 1 | -1) {
  offset.value = Math.max(0, offset.value + dir * limit)
  load()
}

// ── Drawer ──────────────────────────────────────────────────────────────────

interface DrawerState {
  sessionId: string
  tabUrl: string
  total: number
  requests: CaptureRequest[]
  analysisStatus: string
  analysisText: string
  analysisModel: string
}
const drawer        = ref<DrawerState | null>(null)
const drawerLoading = ref(false)
const drawerTab     = ref<'requests' | 'analysis' | 'implement'>('requests')
const expandedId    = ref<number | null>(null)
const reqLimit      = 200

async function openDetail(s: CaptureSession) {
  drawerLoading.value = true
  drawerTab.value = 'requests'
  expandedId.value = null
  drawer.value = {
    sessionId: s.id,
    tabUrl: s.tab_url,
    total: s.request_count,
    requests: [],
    analysisStatus: s.analysis_status,
    analysisText: s.analysis_text ?? '',
    analysisModel: s.analysis_model ?? '',
  }
  try {
    const res = await api.capture_getRequests(s.id, { limit: reqLimit })
    if (drawer.value) {
      drawer.value.requests = res.requests
      drawer.value.total = res.total
    }
  } finally {
    drawerLoading.value = false
  }
}

async function loadMoreRequests() {
  if (!drawer.value) return
  const currentLen = drawer.value.requests.length
  try {
    const res = await api.capture_getRequests(drawer.value.sessionId, { limit: reqLimit, offset: currentLen })
    if (drawer.value) {
      drawer.value.requests.push(...res.requests)
    }
  } catch {}
}

function toggleExpand(id: number) {
  expandedId.value = expandedId.value === id ? null : id
}

// ── Analyze ─────────────────────────────────────────────────────────────────

async function analyze(s: CaptureSession) {
  s.analysis_status = 'analyzing'
  try {
    const res = await api.capture_analyze(s.id)
    s.analysis_status = 'done'
    s.analysis_text = res.analysis
    if (drawer.value && drawer.value.sessionId === s.id) {
      drawer.value.analysisStatus = 'done'
      drawer.value.analysisText = res.analysis
      drawer.value.analysisModel = res.model ?? ''
    }
  } catch {
    s.analysis_status = 'failed'
    if (drawer.value && drawer.value.sessionId === s.id) {
      drawer.value.analysisStatus = 'failed'
    }
  }
}

async function analyzeFromDrawer() {
  if (!drawer.value) return
  const s = sessions.value.find(x => x.id === drawer.value!.sessionId)
  if (s) {
    drawer.value.analysisStatus = 'analyzing'
    await analyze(s)
  }
}

// ── Download ────────────────────────────────────────────────────────────────

async function downloadRaw(sessionId: string) {
  try {
    await api.capture_downloadRaw(sessionId)
  } catch {
    error.value = t('apiCapturePage.downloadRawFailed')
  }
}

async function downloadAnalysis(sessionId: string) {
  try {
    await api.capture_downloadAnalysis(sessionId)
  } catch {
    error.value = t('apiCapturePage.downloadAnalysisFailed')
  }
}

// ── Delete ──────────────────────────────────────────────────────────────────

const deleteTarget = ref<CaptureSession | null>(null)

function confirmDelete(s: CaptureSession) {
  deleteTarget.value = s
}

async function doDelete() {
  if (!deleteTarget.value) return
  const sid = deleteTarget.value.id
  try {
    await api.capture_delete(sid)
    sessions.value = sessions.value.filter(s => s.id !== sid)
    if (drawer.value && drawer.value.sessionId === sid) {
      drawer.value = null
    }
  } catch {}
  deleteTarget.value = null
}

// ── Implement ───────────────────────────────────────────────────────────────

const implTarget = ref('job-api-gateway')
const implInstructions = ref('')
const implRunning = ref(false)
const implOutput = ref('')
const implExitCode = ref<number | null>(null)
const implTerminalRef = ref<HTMLElement | null>(null)

async function startImplement() {
  if (!drawer.value) return
  implRunning.value = true
  implOutput.value = ''
  implExitCode.value = null

  try {
    const resp = await api.capture_implement(
      drawer.value.sessionId, implInstructions.value, implTarget.value
    )
    if (!resp.ok || !resp.body) {
      const err = await resp.text()
      implOutput.value = `Error: ${err}`
      implRunning.value = false
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          if (data.type === 'output') {
            implOutput.value += data.text
            // auto-scroll terminal
            if (implTerminalRef.value) {
              implTerminalRef.value.scrollTop = implTerminalRef.value.scrollHeight
            }
          }
          if (data.type === 'done') implExitCode.value = data.exit_code
          if (data.type === 'error') implOutput.value += `\nError: ${data.text}\n`
        } catch {}
      }
    }
  } catch (e) {
    implOutput.value += `\nConnection error: ${e}\n`
  } finally {
    implRunning.value = false
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtDt(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function truncUrl(url: string): string {
  if (!url) return '—'
  try {
    const u = new URL(url)
    const path = u.pathname.length > 40 ? u.pathname.slice(0, 40) + '...' : u.pathname
    return u.hostname + path
  } catch { return url.slice(0, 60) }
}

function urlPath(url: string): string {
  try {
    const u = new URL(url)
    const full = u.pathname + u.search
    return full.length > 80 ? full.slice(0, 80) + '...' : full
  } catch { return url.slice(0, 80) }
}

function statusLabel(s: string): string {
  const m: Record<string, string> = {
    none: t('apiCapturePage.statusEnum.none'),
    analyzing: t('apiCapturePage.statusEnum.analyzing'),
    done: t('apiCapturePage.statusEnum.done'),
    failed: t('apiCapturePage.statusEnum.failed'),
  }
  return m[s] ?? s
}

function statusCodeClass(code: number | null): string {
  if (!code) return ''
  if (code < 300) return 'sc-2xx'
  if (code < 400) return 'sc-3xx'
  if (code < 500) return 'sc-4xx'
  return 'sc-5xx'
}

function formatJson(obj: unknown): string {
  if (typeof obj === 'string') {
    try { return JSON.stringify(JSON.parse(obj), null, 2) } catch { return obj }
  }
  return JSON.stringify(obj, null, 2)
}

function tryFormatJson(text: string): string {
  try {
    const parsed = JSON.parse(text)
    return JSON.stringify(parsed, null, 2)
  } catch { return text }
}

onMounted(load)
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 16px; }

.page-header { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.count-badge {
  padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 600;
  background: #172554; color: #60a5fa;
}

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

.toolbar { display: flex; gap: 8px; }
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
.sm { font-size: 12px; }
.dim { color: #475569; }
.num { text-align: center; }
.center { text-align: center; }
.url-cell { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.empty-row { text-align: center; color: #475569; padding: 32px !important; }

.status-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.st-none      { background: #1e293b; color: #64748b; }
.st-analyzing { background: #172554; color: #60a5fa; }
.st-done      { background: #052e16; color: #4ade80; }
.st-failed    { background: #3b1a1a; color: #f87171; }

.actions { display: flex; gap: 6px; }
.action-btn {
  padding: 4px 10px; border-radius: 5px; font-size: 11px; cursor: pointer;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.action-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.action-btn.analyze { color: #60a5fa; border-color: #1d4ed8; }
.action-btn.analyze:hover:not(:disabled) { background: #172554; }
.action-btn.download {
  color: #4ade80; border-color: #166534; text-decoration: none; display: inline-flex; align-items: center;
}
.action-btn.download:hover { background: #052e16; }
.action-btn.delete { color: #f87171; border-color: #7f1d1d; }
.action-btn.delete:hover { background: #3b1a1a; }

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
  width: 600px; max-width: 95vw; background: #0f172a;
  border-left: 1px solid #334155; display: flex; flex-direction: column;
  animation: slideIn 0.2s ease;
}
@keyframes slideIn { from { transform: translateX(100%) } to { transform: translateX(0) } }

.drawer-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #334155;
}
.drawer-title-block { display: flex; flex-direction: column; gap: 4px; overflow: hidden; }
.drawer-title { font-size: 14px; font-weight: 600; color: #e2e8f0; font-family: 'SF Mono', monospace; }
.drawer-subtitle { font-size: 12px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drawer-download-bar { display: flex; gap: 6px; margin-top: 6px; }
.drawer-close {
  width: 28px; height: 28px; border-radius: 6px; color: #64748b; background: transparent;
  border: none; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.drawer-close:hover { background: #1e293b; color: #e2e8f0; }

.drawer-tabs {
  display: flex; border-bottom: 1px solid #334155;
}
.tab-btn {
  flex: 1; padding: 10px; text-align: center; font-size: 13px;
  background: transparent; color: #64748b; border: none; cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab-btn.active { color: #60a5fa; border-bottom-color: #60a5fa; }
.tab-btn:hover:not(.active) { color: #94a3b8; }

.drawer-body { flex: 1; overflow-y: auto; padding: 12px 16px; }

/* Request list */
.req-list { display: flex; flex-direction: column; gap: 4px; }
.req-item {
  border-radius: 6px; border: 1px solid #1e293b; overflow: hidden; cursor: pointer;
}
.req-item:hover { border-color: #334155; }
.req-summary {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px; font-size: 12px;
}
.method-badge {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  font-size: 10px; font-weight: 700; min-width: 36px; text-align: center;
  font-family: 'SF Mono', monospace;
}
.m-get    { background: #052e16; color: #4ade80; }
.m-post   { background: #172554; color: #60a5fa; }
.m-put    { background: #431407; color: #fb923c; }
.m-patch  { background: #431407; color: #fb923c; }
.m-delete { background: #3b1a1a; color: #f87171; }
.m-options { background: #1e293b; color: #64748b; }
.m-head   { background: #1e293b; color: #64748b; }

.status-code { font-family: 'SF Mono', monospace; font-size: 11px; font-weight: 600; min-width: 28px; }
.sc-2xx { color: #4ade80; }
.sc-3xx { color: #fbbf24; }
.sc-4xx { color: #fb923c; }
.sc-5xx { color: #f87171; }

.req-url { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #cbd5e1; }
.req-type { font-size: 10px; }

.req-detail { padding: 0 10px 10px; }
.detail-section { margin-top: 8px; }
.detail-label { font-size: 10px; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
.detail-code {
  font-size: 11px; color: #cbd5e1; white-space: pre-wrap; word-break: break-all;
  font-family: 'SF Mono', monospace; background: #1e293b;
  padding: 8px 10px; border-radius: 6px; margin: 0; max-height: 200px; overflow-y: auto;
}
.detail-code.long { max-height: 400px; }

/* Analysis */
.analysis-content { padding: 4px; }
.analysis-text {
  font-size: 13px; color: #cbd5e1; white-space: pre-wrap; word-break: break-word;
  line-height: 1.6; margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.spinner {
  width: 24px; height: 24px; border: 3px solid #334155; border-top-color: #60a5fa;
  border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto;
}
@keyframes spin { to { transform: rotate(360deg) } }

/* Confirm modal */
.confirm-modal {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  background: #1e293b; border: 1px solid #334155; border-radius: 12px;
  padding: 24px; max-width: 400px; text-align: center;
}
.confirm-modal p { font-size: 14px; color: #e2e8f0; margin-bottom: 16px; }
.confirm-modal code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.confirm-actions { display: flex; gap: 8px; justify-content: center; }

.name-cell { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: #94a3b8; }

/* Implement panel */
.implement-panel { display: flex; flex-direction: column; gap: 12px; }
.impl-field { display: flex; flex-direction: column; gap: 4px; }
.impl-field label { font-size: 12px; color: #64748b; font-weight: 600; }
.impl-field select, .impl-field textarea {
  width: 100%; padding: 8px 10px; background: #1e293b; color: #e2e8f0;
  border: 1px solid #334155; border-radius: 6px; font-size: 13px; outline: none;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.impl-field select:focus, .impl-field textarea:focus { border-color: #60a5fa; }
.impl-field textarea { resize: vertical; min-height: 80px; }
.impl-terminal {
  background: #0a0a0a; color: #d4d4d4; font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 12px; padding: 12px; border-radius: 8px; margin: 0;
  max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
  line-height: 1.5;
}
.impl-done { font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 6px; text-align: center; }
.impl-done.ok { background: #052e16; color: #4ade80; }
.impl-done.err { background: #3b1a1a; color: #f87171; }
</style>
