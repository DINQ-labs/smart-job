<template>
  <div class="dc-page">
    <div class="page-header">
      <h2>{{ t('dynamicCommandsPage.title') }}</h2>
      <span class="subtitle">{{ t('dynamicCommandsPage.subtitle') }}</span>
      <button class="refresh-btn" @click="loadAll">{{ t('dynamicCommandsPage.refresh') }}</button>
    </div>

    <div v-if="loading" class="loading">{{ t('dynamicCommandsPage.loading') }}</div>

    <div class="layout">
      <!-- 左：编辑器 -->
      <section class="editor-col">
        <div class="section-title">{{ t('dynamicCommandsPage.jsonConfig') }}</div>
        <div class="version-row">
          <span class="meta-label">{{ t('dynamicCommandsPage.currentProduction') }}</span>
          <span class="mono current-version">
            {{ current?.version || t('dynamicCommandsPage.none') }}
          </span>
          <span v-if="current" class="meta-extra">
            by {{ current.created_by || '?' }} · {{ formatTs(current.created_at) }}
          </span>
        </div>

        <textarea
          v-model="payloadText"
          class="json-editor"
          spellcheck="false"
          placeholder='{"version": "...", "dynamic_commands": [...]}'
        ></textarea>

        <div class="actions">
          <button class="btn secondary" @click="loadCurrentToEditor">{{ t('dynamicCommandsPage.loadFromProduction') }}</button>
          <button class="btn secondary" @click="prefillTemplate">{{ t('dynamicCommandsPage.fillTemplate') }}</button>
          <button class="btn secondary" @click="validateLocally">{{ t('dynamicCommandsPage.validateLocally') }}</button>
          <button class="btn primary" :disabled="pushing" @click="pushNow">
            {{ pushing ? t('dynamicCommandsPage.pushing') : t('dynamicCommandsPage.pushToExtensions', { n: activeSessions.length }) }}
          </button>
        </div>

        <div v-if="opError" class="op-error">{{ opError }}</div>
        <div v-if="opSuccess" class="op-success">{{ opSuccess }}</div>
      </section>

      <!-- 右：连接的扩展 + ACK -->
      <aside class="sidebar-col">
        <div class="section-title">{{ t('dynamicCommandsPage.willBeAffected', { n: activeSessions.length }) }}</div>
        <div v-if="activeSessions.length === 0" class="empty">
          {{ t('dynamicCommandsPage.noConnectedExtensions') }}
        </div>
        <table v-else class="sess-table">
          <thead>
            <tr>
              <th>account</th>
              <th>session</th>
              <th>display</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in activeSessions" :key="s.session_id">
              <td>{{ s.account_name || '—' }}</td>
              <td class="mono sm" :title="s.session_id">{{ shortId(s.session_id) }}</td>
              <td>{{ s.display_id || '—' }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="lastAck.length" class="section-title ack-title">{{ t('dynamicCommandsPage.lastPushResult') }}</div>
        <table v-if="lastAck.length" class="sess-table">
          <thead>
            <tr>
              <th>session</th>
              <th>{{ t('dynamicCommandsPage.result') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in lastAck" :key="a.session_id">
              <td class="mono sm">{{ shortId(a.session_id) }}</td>
              <td>
                <span v-if="a.sent" class="ack-ok">✓ {{ t('dynamicCommandsPage.sent') }}</span>
                <span v-else class="ack-fail" :title="a.error">✗ {{ a.error || t('dynamicCommandsPage.failed') }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </aside>
    </div>

    <!-- 历史 -->
    <section class="history">
      <div class="section-title">{{ t('dynamicCommandsPage.historyVersions', { n: history.length }) }}</div>
      <table v-if="history.length" class="history-table">
        <thead>
          <tr>
            <th>version</th>
            <th>by</th>
            <th>commands</th>
            <th>chains</th>
            <th>notes</th>
            <th>created</th>
            <th>{{ t('dynamicCommandsPage.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in history" :key="h.version">
            <td class="mono">{{ h.version }}</td>
            <td>{{ h.created_by || '—' }}</td>
            <td class="num">{{ h.commands_count }}</td>
            <td class="num">{{ h.chains_count }}</td>
            <td class="notes-cell" :title="h.notes">{{ h.notes || '—' }}</td>
            <td>{{ formatTs(h.created_at) }}</td>
            <td>
              <button class="btn-link" @click="rollbackTo(h.version)">{{ t('dynamicCommandsPage.rollback') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">{{ t('dynamicCommandsPage.noHistory') }}</div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'

const { t } = useI18n()

const loading = ref(false)
const pushing = ref(false)
const current = ref<any | null>(null)
const history = ref<any[]>([])
const activeSessions = ref<any[]>([])
const payloadText = ref('')
const lastAck = ref<any[]>([])
const opError = ref('')
const opSuccess = ref('')

const TEMPLATE = `{
  "version": "${nextVersionStamp()}",
  "notes": "${t('dynamicCommandsPage.templateNotes')}",
  "dynamic_commands": [
    {
      "path": "boss/test_ping",
      "description": "${t('dynamicCommandsPage.templateDescription')}",
      "params": [],
      "requires": null,
      "produces": null,
      "requestBuilder": {
        "method": "GET",
        "url": "/wapi/zppassport/get/wt"
      }
    }
  ]
}`

function nextVersionStamp(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}

function shortId(id: string): string {
  return id ? id.slice(0, 8) : '—'
}

function formatTs(s: string | null | undefined): string {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleString()
  } catch {
    return s
  }
}

async function loadCurrent() {
  const res = await api.getCurrentDynamicConfig()
  current.value = res.config
}

async function loadHistory() {
  const res = await api.listDynamicConfigHistory(50, 0)
  history.value = res.items || []
}

async function loadActive() {
  const res = await api.listDynamicConfigActiveSessions()
  activeSessions.value = res.sessions || []
}

async function loadAll() {
  loading.value = true
  opError.value = ''
  opSuccess.value = ''
  try {
    await Promise.all([loadCurrent(), loadHistory(), loadActive()])
  } catch (e: any) {
    opError.value = t('dynamicCommandsPage.loadFailed', { msg: e?.message || e })
  } finally {
    loading.value = false
  }
}

function loadCurrentToEditor() {
  if (!current.value) {
    opError.value = t('dynamicCommandsPage.noProductionToLoad')
    return
  }
  const body = {
    version: nextVersionStamp(),       // bump 版本号避免去重
    notes: t('dynamicCommandsPage.derivedFrom', { version: current.value.version }),
    chains: current.value.chains || {},
    dynamic_commands: current.value.dynamic_commands || [],
  }
  payloadText.value = JSON.stringify(body, null, 2)
  opError.value = ''
}

function prefillTemplate() {
  payloadText.value = TEMPLATE
  opError.value = ''
}

function validateLocally(): { ok: boolean; payload?: any; error?: string } {
  opError.value = ''
  let parsed: any
  try {
    parsed = JSON.parse(payloadText.value)
  } catch (e: any) {
    opError.value = t('dynamicCommandsPage.jsonParseFailed', { msg: e?.message || e })
    return { ok: false }
  }
  if (!parsed.version) {
    opError.value = t('dynamicCommandsPage.missingVersionField')
    return { ok: false }
  }
  const cmds = parsed.dynamic_commands || []
  const chains = parsed.chains || {}
  if (cmds.length === 0 && Object.keys(chains).length === 0) {
    opError.value = t('dynamicCommandsPage.commandsOrChainsRequired')
    return { ok: false }
  }
  for (let i = 0; i < cmds.length; i++) {
    const c = cmds[i]
    if (!c.path || !c.path.includes('/')) {
      opError.value = t('dynamicCommandsPage.pathRequired', { i })
      return { ok: false }
    }
    if (!c.requestBuilder?.url || !c.requestBuilder?.method) {
      opError.value = t('dynamicCommandsPage.requestBuilderRequired', { i })
      return { ok: false }
    }
  }
  opSuccess.value = t('dynamicCommandsPage.validationPassed', { cmds: cmds.length, chains: Object.keys(chains).length })
  return { ok: true, payload: parsed }
}

async function pushNow() {
  const v = validateLocally()
  if (!v.ok) return
  if (!confirm(t('dynamicCommandsPage.confirmPush', { n: activeSessions.value.length }))) return
  opError.value = ''
  opSuccess.value = ''
  pushing.value = true
  lastAck.value = []
  try {
    const res = await api.pushDynamicConfig(v.payload)
    if (!res.ok) {
      opError.value = res.error || t('dynamicCommandsPage.pushFailed')
    } else {
      opSuccess.value = t('dynamicCommandsPage.pushSuccess', { sent: res.sent, total: res.total_sessions, failed: res.failed })
      lastAck.value = res.ack || []
      await loadAll()
    }
  } catch (e: any) {
    opError.value = t('dynamicCommandsPage.pushException', { msg: e?.message || e })
  } finally {
    pushing.value = false
  }
}

async function rollbackTo(version: string) {
  if (!confirm(t('dynamicCommandsPage.confirmRollback', { version }))) return
  pushing.value = true
  opError.value = ''
  opSuccess.value = ''
  lastAck.value = []
  try {
    const res = await api.getDynamicConfigByVersion(version)
    if (!res.ok || !res.config) {
      opError.value = res.error || t('dynamicCommandsPage.versionNotFound', { version })
      return
    }
    const newPayload = {
      version: nextVersionStamp(),
      notes: t('dynamicCommandsPage.rolledBackFrom', { version }),
      source_ref: version,
      chains: res.config.chains || {},
      dynamic_commands: res.config.dynamic_commands || [],
    }
    const pushRes = await api.pushDynamicConfig(newPayload)
    if (!pushRes.ok) {
      opError.value = pushRes.error || t('dynamicCommandsPage.pushFailed')
    } else {
      opSuccess.value = t('dynamicCommandsPage.rollbackSuccess', { sent: pushRes.sent, total: pushRes.total_sessions })
      lastAck.value = pushRes.ack || []
      payloadText.value = JSON.stringify(newPayload, null, 2)
      await loadAll()
    }
  } catch (e: any) {
    opError.value = t('dynamicCommandsPage.rollbackFailed', { msg: e?.message || e })
  } finally {
    pushing.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.dc-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header h2 {
  font-size: 18px;
  color: #e2e8f0;
  margin: 0;
}

.subtitle {
  color: #64748b;
  font-size: 12px;
}

.refresh-btn {
  margin-left: auto;
  background: #334155;
  color: #94a3b8;
  border: none;
  padding: 6px 14px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 12px;
}
.refresh-btn:hover { background: #475569; color: #e2e8f0; }

.loading {
  padding: 12px;
  color: #64748b;
  font-size: 12px;
}

.layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 12px;
  margin-bottom: 12px;
}

.editor-col, .sidebar-col, .history {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 8px;
}

.ack-title {
  margin-top: 16px;
}

.version-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #94a3b8;
}

.meta-label {
  color: #64748b;
}

.meta-extra {
  color: #64748b;
  margin-left: 8px;
}

.json-editor {
  width: 100%;
  min-height: 360px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  background: #0f172a;
  border: 1px solid #334155;
  color: #e2e8f0;
  border-radius: 5px;
  padding: 10px;
  resize: vertical;
}
.json-editor::placeholder { color: #475569; }

.actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.btn {
  padding: 6px 14px;
  border: none;
  border-radius: 5px;
  cursor: pointer;
  font-size: 12px;
}

.btn.primary {
  background: #3b82f6;
  color: white;
}
.btn.primary:hover { background: #2563eb; }
.btn.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn.secondary {
  background: #334155;
  color: #94a3b8;
}
.btn.secondary:hover { background: #475569; color: #e2e8f0; }

.btn-link {
  color: #60a5fa;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  font-size: 12px;
  text-decoration: underline;
}
.btn-link:hover { color: #93c5fd; }

.op-error, .op-success {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: 5px;
  font-size: 12px;
}

.op-error {
  background: #2d1515;
  color: #fca5a5;
  border: 1px solid #7f1d1d;
}

.op-success {
  background: #14532d;
  color: #4ade80;
  border: 1px solid #14532d;
}

.sess-table, .history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.sess-table th, .history-table th {
  background: #0f172a;
  color: #64748b;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  border-bottom: 1px solid #1e293b;
}

.sess-table td, .history-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #1e293b;
  color: #e2e8f0;
  vertical-align: middle;
}

.sess-table tr:hover td,
.history-table tr:hover td { background: #0f172a; }

.empty {
  padding: 16px;
  color: #475569;
  font-size: 12px;
  text-align: center;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #93c5fd;
}

.mono.sm {
  font-size: 11px;
}

.num {
  text-align: right;
  color: #fbbf24;
}

.notes-cell {
  max-width: 280px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #94a3b8;
}

.ack-ok {
  color: #4ade80;
}

.ack-fail {
  color: #fca5a5;
}

.current-version {
  font-weight: 600;
  color: #93c5fd;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
