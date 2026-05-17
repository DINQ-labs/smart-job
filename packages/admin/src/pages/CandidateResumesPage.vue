<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">{{ t('candidateResumesPage.title') }}</h1>
      <span v-if="resumes.length" class="count-badge">{{ t('candidateResumesPage.countBadge', { n: resumes.length }) }}</span>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <!-- Filters -->
    <div class="toolbar">
      <select v-model="filters.platform" class="filter-input" @change="load">
        <option value="">{{ t('candidateResumesPage.allPlatforms') }}</option>
        <option value="indeed">Indeed</option>
        <option value="boss">Boss</option>
        <option value="linkedin">LinkedIn</option>
      </select>
      <select v-model="filters.status" class="filter-input" @change="applyClientFilter">
        <option value="">{{ t('candidateResumesPage.allStatuses') }}</option>
        <option value="pending">{{ t('candidateResumesPage.statusEnum.pending') }}</option>
        <option value="done">{{ t('candidateResumesPage.statusEnum.done') }}</option>
        <option value="failed">{{ t('candidateResumesPage.statusEnum.failed') }}</option>
        <option value="no_file">{{ t('candidateResumesPage.statusEnum.noFile') }}</option>
      </select>
      <input
        v-model="filters.name"
        :placeholder="t('candidateResumesPage.searchNamePh')"
        class="filter-input"
        @input="applyClientFilter"
      />
      <input
        v-model="filters.user_id"
        :placeholder="t('candidateResumesPage.userIdFilterPh')"
        class="filter-input"
        @keyup.enter="load"
      />
      <button class="refresh-btn" :disabled="loading" @click="load">{{ t('candidateResumesPage.refresh') }}</button>
    </div>

    <!-- Table -->
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t('candidateResumesPage.cols.platform') }}</th>
            <th>{{ t('candidateResumesPage.cols.name') }}</th>
            <th>{{ t('candidateResumesPage.cols.candidateId') }}</th>
            <th>{{ t('candidateResumesPage.cols.summary') }}</th>
            <th>{{ t('candidateResumesPage.cols.file') }}</th>
            <th>{{ t('candidateResumesPage.cols.parse') }}</th>
            <th>{{ t('candidateResumesPage.cols.downloadedBy') }}</th>
            <th>{{ t('candidateResumesPage.cols.time') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="filtered.length === 0 && !loading">
            <td colspan="8" class="empty-row">{{ t('candidateResumesPage.emptyResumes') }}</td>
          </tr>
          <tr
            v-for="r in filtered"
            :key="r.id"
            class="data-row"
            @click="openDrawer(r)"
          >
            <td><span class="platform-badge" :class="'p-' + r.platform">{{ r.platform }}</span></td>
            <td class="name-cell">{{ r.candidate_name || '\u2014' }}</td>
            <td class="mono sm">{{ r.candidate_id }}</td>
            <td class="meta-cell">{{ metaSummary(r) }}</td>
            <td class="num" @click.stop>
              <template v-if="r.file_path">
                <span class="dim">{{ fmtSize(r.file_size) }}</span>
                <button class="action-btn download" @click="downloadFile(r)">{{ t('candidateResumesPage.download') }}</button>
              </template>
              <span v-else class="dim">&mdash;</span>
            </td>
            <td><span class="status-badge" :class="'st-' + parseStatusKey(r)">{{ parseStatusLabel(r) }}</span></td>
            <td class="mono sm">{{ truncId(r.downloaded_by) }}</td>
            <td class="sm dim">{{ fmtDt(r.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="resumes.length > 0" class="pagination">
      <button :disabled="offset === 0" @click="paginate(-1)">{{ t('candidateResumesPage.prevPage') }}</button>
      <span>{{ offset / limit + 1 }}</span>
      <button :disabled="resumes.length < limit" @click="paginate(1)">{{ t('candidateResumesPage.nextPage') }}</button>
    </div>

    <!-- Detail Drawer -->
    <Teleport to="body">
      <div v-if="drawer" class="drawer-overlay" @click.self="drawer = null">
        <div class="drawer">
          <div class="drawer-header">
            <div class="drawer-title-block">
              <span class="drawer-title">{{ drawer.candidate_name || drawer.candidate_id }}</span>
              <span class="drawer-subtitle">
                <span class="platform-badge" :class="'p-' + drawer.platform">{{ drawer.platform }}</span>
                {{ drawer.candidate_id }}
              </span>
              <div v-if="drawer.file_path" class="drawer-download-bar">
                <button class="action-btn download" @click="downloadFile(drawer)">{{ t('candidateResumesPage.downloadPdf') }}</button>
              </div>
            </div>
            <button class="drawer-close" @click="drawer = null">&#10005;</button>
          </div>
          <div class="drawer-tabs">
            <button class="tab-btn" :class="{ active: drawerTab === 'meta' }" @click="drawerTab = 'meta'">{{ t('candidateResumesPage.tabMeta') }}</button>
            <button class="tab-btn" :class="{ active: drawerTab === 'text' }" @click="drawerTab = 'text'">{{ t('candidateResumesPage.tabText') }}</button>
          </div>
          <div class="drawer-body">
            <!-- Meta tab -->
            <template v-if="drawerTab === 'meta'">
              <div v-if="drawerLoading" class="dim center">{{ t('candidateResumesPage.loading') }}</div>
              <div v-else class="meta-grid">
                <div v-for="[label, value] in drawerMetaFields" :key="label" class="meta-field">
                  <div class="meta-label">{{ label }}</div>
                  <div class="meta-value">{{ value }}</div>
                </div>
                <!-- Experience list -->
                <template v-if="drawerExperience.length">
                  <div class="meta-section-title">{{ t('candidateResumesPage.workExperience') }}</div>
                  <div v-for="(exp, i) in drawerExperience" :key="i" class="exp-item">
                    <span class="exp-title">{{ exp.title || exp.company }}</span>
                    <span v-if="exp.company && exp.title" class="dim"> @ {{ exp.company }}</span>
                    <span v-if="exp.from" class="dim sm"> ({{ exp.from }}{{ exp.to ? '-' + exp.to : '-' + t('candidateResumesPage.present') }})</span>
                  </div>
                </template>
                <!-- Match highlights -->
                <template v-if="drawerHighlights.length">
                  <div class="meta-section-title">{{ t('candidateResumesPage.matchHighlights') }}</div>
                  <div class="highlights">
                    <span v-for="h in drawerHighlights" :key="h" class="highlight-tag">{{ h }}</span>
                  </div>
                </template>
              </div>
            </template>

            <!-- Text tab -->
            <template v-if="drawerTab === 'text'">
              <div v-if="!drawerDetail" class="dim center" style="padding:32px">{{ t('candidateResumesPage.loading') }}</div>
              <div v-else-if="drawerDetail.parse_status === 'done' && drawerDetail.raw_text" class="text-content">
                <pre class="resume-text">{{ drawerDetail.raw_text }}</pre>
              </div>
              <div v-else-if="drawerDetail.parse_status === 'pending'" class="center dim" style="padding:32px">
                {{ t('candidateResumesPage.textParsing') }}
              </div>
              <div v-else class="center dim" style="padding:32px">
                {{ drawerDetail.file_path ? t('candidateResumesPage.parseFailedOrNoText') : t('candidateResumesPage.noFileDownloaded') }}
              </div>
            </template>
          </div>
          <!-- Delete -->
          <div class="drawer-footer">
            <button class="action-btn delete" @click="confirmDelete">{{ t('candidateResumesPage.deleteRecord') }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirm -->
    <Teleport to="body">
      <div v-if="deleteTarget" class="drawer-overlay" @click.self="deleteTarget = null">
        <div class="confirm-modal">
          <p>{{ t('candidateResumesPage.confirmDeletePrefix') }}<code>{{ deleteTarget.candidate_name || deleteTarget.candidate_id }}</code>{{ t('candidateResumesPage.confirmDeleteSuffix') }}</p>
          <div class="confirm-actions">
            <button class="action-btn" @click="deleteTarget = null">{{ t('candidateResumesPage.cancel') }}</button>
            <button class="action-btn delete" @click="doDelete">{{ t('candidateResumesPage.delete') }}</button>
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
import type { CandidateResume } from '../types'

const { t } = useI18n()

const resumes  = ref<CandidateResume[]>([])
const loading  = ref(false)
const error    = ref('')
const limit    = 50
const offset   = ref(0)

const filters = ref({ platform: '', status: '', name: '', user_id: '' })

// ── Filtered list (client-side status + name filter) ──

const filtered = computed(() => {
  let list = resumes.value
  const s = filters.value.status
  if (s === 'no_file') {
    list = list.filter(r => !r.file_path)
  } else if (s) {
    list = list.filter(r => r.parse_status === s)
  }
  const n = filters.value.name.trim().toLowerCase()
  if (n) {
    list = list.filter(r => (r.candidate_name || '').toLowerCase().includes(n))
  }
  return list
})

function applyClientFilter() { /* computed auto-updates */ }

// ── Load ──

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.candidateResume_list({
      platform: filters.value.platform || undefined,
      user_id: filters.value.user_id || undefined,
      limit,
      offset: offset.value,
    })
    resumes.value = res.resumes
  } catch {
    error.value = t('candidateResumesPage.loadFailed')
  } finally {
    loading.value = false
  }
}

function paginate(dir: 1 | -1) {
  offset.value = Math.max(0, offset.value + dir * limit)
  load()
}

// ── Drawer ──

const drawer       = ref<CandidateResume | null>(null)
const drawerDetail = ref<CandidateResume | null>(null)
const drawerLoading = ref(false)
const drawerTab    = ref<'meta' | 'text'>('meta')

async function openDrawer(r: CandidateResume) {
  drawer.value = r
  drawerDetail.value = null
  drawerTab.value = 'meta'
  drawerLoading.value = true
  try {
    drawerDetail.value = await api.candidateResume_get(r.platform, r.candidate_id, r.downloaded_by || undefined)
    // merge meta into drawer for display
    if (drawerDetail.value.candidate_meta) {
      drawer.value = { ...r, candidate_meta: drawerDetail.value.candidate_meta }
    }
  } catch {
    drawerDetail.value = r
  } finally {
    drawerLoading.value = false
  }
}

const drawerMetaFields = computed<[string, string][]>(() => {
  const meta = parseMeta(drawer.value?.candidate_meta)
  const fields: [string, string][] = []
  if (drawer.value?.platform) fields.push([t('candidateResumesPage.fields.platform'), drawer.value.platform])
  if (drawer.value?.candidate_id) fields.push([t('candidateResumesPage.fields.candidateId'), drawer.value.candidate_id])
  if (meta.phone) fields.push([t('candidateResumesPage.fields.phone'), meta.phone])
  if (meta.email) fields.push([t('candidateResumesPage.fields.email'), meta.email])
  if (meta.location) fields.push([t('candidateResumesPage.fields.location'), meta.location])
  if (meta.country) fields.push([t('candidateResumesPage.fields.country'), meta.country])
  if (meta.currentTitle) {
    const title = meta.currentCompany ? `${meta.currentTitle} @ ${meta.currentCompany}` : meta.currentTitle
    fields.push([t('candidateResumesPage.fields.currentTitle'), title])
  }
  if (meta.currentWork) fields.push([t('candidateResumesPage.fields.currentWork'), meta.currentWork])
  if (meta.salary) fields.push([t('candidateResumesPage.fields.salary'), meta.salary])
  if (meta.city) fields.push([t('candidateResumesPage.fields.city'), meta.city])
  if (meta.workYear) fields.push([t('candidateResumesPage.fields.workYear'), meta.workYear])
  if (meta.degree) fields.push([t('candidateResumesPage.fields.degree'), meta.degree])
  if (meta.school) fields.push([t('candidateResumesPage.fields.school'), meta.school])
  if (meta.milestone) fields.push([t('candidateResumesPage.fields.milestone'), meta.milestone])
  if (meta.sourceType) fields.push([t('candidateResumesPage.fields.sourceType'), meta.sourceType])
  if (meta.submissionUuid) fields.push(['Submission UUID', meta.submissionUuid])
  if (meta.candidateId) fields.push(['Candidate ID', meta.candidateId])
  if (meta.geekId) fields.push(['Geek ID', String(meta.geekId)])
  if (meta.securityId) fields.push(['Security ID', meta.securityId])
  if (drawer.value?.source_job_id) fields.push([t('candidateResumesPage.fields.sourceJob'), drawer.value.source_job_id])
  if (drawer.value?.downloaded_by) fields.push([t('candidateResumesPage.fields.downloadedBy'), drawer.value.downloaded_by])
  if (drawer.value?.file_size) fields.push([t('candidateResumesPage.fields.fileSize'), fmtSize(drawer.value.file_size)])
  if (drawer.value?.parse_status) fields.push([t('candidateResumesPage.fields.parseStatus'), parseStatusLabel(drawer.value)])
  return fields
})

const drawerExperience = computed<any[]>(() => {
  const meta = parseMeta(drawer.value?.candidate_meta)
  return Array.isArray(meta.experience) ? meta.experience : []
})

const drawerHighlights = computed<string[]>(() => {
  const meta = parseMeta(drawer.value?.candidate_meta)
  return Array.isArray(meta.matchHighlights) ? meta.matchHighlights : []
})

// ── Download ──

async function downloadFile(r: CandidateResume) {
  try {
    await api.candidateResume_downloadFile(r.platform, r.candidate_id, r.downloaded_by || undefined)
  } catch {
    error.value = t('candidateResumesPage.downloadFailed')
  }
}

// ── Delete ──

const deleteTarget = ref<CandidateResume | null>(null)

function confirmDelete() {
  if (drawer.value) deleteTarget.value = drawer.value
}

async function doDelete() {
  if (!deleteTarget.value) return
  const { platform, candidate_id } = deleteTarget.value
  try {
    await api.candidateResume_delete(platform, candidate_id, deleteTarget.value.downloaded_by || undefined)
    resumes.value = resumes.value.filter(r => !(r.platform === platform && r.candidate_id === candidate_id))
    if (drawer.value?.candidate_id === candidate_id) drawer.value = null
  } catch {}
  deleteTarget.value = null
}

// ── Helpers ──

function parseMeta(meta: any): Record<string, any> {
  if (!meta) return {}
  if (typeof meta === 'string') {
    try { return JSON.parse(meta) } catch { return {} }
  }
  return meta
}

function metaSummary(r: CandidateResume): string {
  const meta = parseMeta(r.candidate_meta)
  const parts: string[] = []
  if (meta.currentTitle) parts.push(meta.currentTitle)
  else if (meta.currentWork) parts.push(meta.currentWork)
  if (meta.location || meta.city) parts.push(meta.location || meta.city)
  if (meta.phone) parts.push(meta.phone)
  return parts.join(' | ') || '\u2014'
}

function parseStatusKey(r: CandidateResume): string {
  if (!r.file_path) return 'none'
  return r.parse_status
}

function parseStatusLabel(r: CandidateResume): string {
  if (!r.file_path) return t('candidateResumesPage.statusEnum.noFile')
  const m: Record<string, string> = {
    pending: t('candidateResumesPage.statusEnum.pending'),
    done: t('candidateResumesPage.statusEnum.done'),
    failed: t('candidateResumesPage.statusEnum.failedShort'),
  }
  return m[r.parse_status] ?? r.parse_status
}

function fmtSize(bytes: number | null): string {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + 'B'
  return (bytes / 1024).toFixed(0) + 'KB'
}

function truncId(id: string | null): string {
  if (!id) return '\u2014'
  return id.length > 12 ? id.slice(0, 8) + '...' : id
}

function fmtDt(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
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

.toolbar { display: flex; gap: 8px; flex-wrap: wrap; }
.filter-input {
  padding: 7px 10px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #e2e8f0; border: 1px solid #334155; outline: none;
}
.filter-input:focus { border-color: #60a5fa; }
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
.empty-row { text-align: center; color: #475569; padding: 32px !important; }
.name-cell { font-weight: 500; color: #e2e8f0; }
.meta-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: #94a3b8; }

.platform-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600; text-transform: capitalize;
}
.p-indeed   { background: #172554; color: #60a5fa; }
.p-boss     { background: #1a2e05; color: #a3e635; }
.p-linkedin { background: #082f49; color: #38bdf8; }
.p-unknown  { background: #1e293b; color: #64748b; }

.status-badge {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.st-none    { background: #1e293b; color: #64748b; }
.st-pending { background: #172554; color: #60a5fa; }
.st-done    { background: #052e16; color: #4ade80; }
.st-failed  { background: #3b1a1a; color: #f87171; }

.action-btn {
  padding: 4px 10px; border-radius: 5px; font-size: 11px; cursor: pointer;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.action-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.action-btn.download { color: #4ade80; border-color: #166534; }
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
.drawer-title { font-size: 16px; font-weight: 600; color: #e2e8f0; }
.drawer-subtitle { font-size: 12px; color: #475569; display: flex; align-items: center; gap: 6px; }
.drawer-download-bar { display: flex; gap: 6px; margin-top: 8px; }
.drawer-close {
  width: 28px; height: 28px; border-radius: 6px; color: #64748b; background: transparent;
  border: none; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.drawer-close:hover { background: #1e293b; color: #e2e8f0; }

.drawer-tabs { display: flex; border-bottom: 1px solid #334155; }
.tab-btn {
  flex: 1; padding: 10px; text-align: center; font-size: 13px;
  background: transparent; color: #64748b; border: none; cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab-btn.active { color: #60a5fa; border-bottom-color: #60a5fa; }
.tab-btn:hover:not(.active) { color: #94a3b8; }

.drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; }

.drawer-footer {
  padding: 12px 20px; border-top: 1px solid #334155; display: flex; justify-content: flex-end;
}

/* Meta grid */
.meta-grid { display: flex; flex-direction: column; gap: 8px; }
.meta-field { display: flex; gap: 8px; font-size: 13px; }
.meta-label { color: #64748b; min-width: 100px; flex-shrink: 0; font-weight: 500; }
.meta-value { color: #e2e8f0; word-break: break-all; }
.meta-section-title {
  margin-top: 12px; font-size: 12px; font-weight: 600; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #1e293b;
  padding-bottom: 4px;
}
.exp-item { font-size: 13px; padding: 4px 0; color: #cbd5e1; }
.exp-title { font-weight: 500; color: #e2e8f0; }
.highlights { display: flex; flex-wrap: wrap; gap: 6px; }
.highlight-tag {
  padding: 2px 8px; border-radius: 10px; font-size: 11px;
  background: #052e16; color: #4ade80; font-weight: 500;
}

/* Resume text */
.text-content { padding: 4px; }
.resume-text {
  font-size: 13px; color: #cbd5e1; white-space: pre-wrap; word-break: break-word;
  line-height: 1.6; margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #1e293b; padding: 12px; border-radius: 8px; max-height: 500px; overflow-y: auto;
}

/* Confirm modal */
.confirm-modal {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  background: #1e293b; border: 1px solid #334155; border-radius: 12px;
  padding: 24px; max-width: 400px; text-align: center;
}
.confirm-modal p { font-size: 14px; color: #e2e8f0; margin-bottom: 16px; }
.confirm-modal code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.confirm-actions { display: flex; gap: 8px; justify-content: center; }
</style>
