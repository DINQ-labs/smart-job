<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">{{ t('agentUsersPage.title') }}</h1>
      </div>
      <div class="header-right">
        <div class="stats-chips">
          <span class="chip">{{ t('agentUsersPage.chips.registered', { n: users.length }) }}</span>
          <span class="chip connected">{{ t('agentUsersPage.chips.online', { n: connectedCount }) }}</span>
          <span class="chip has-resume">{{ t('agentUsersPage.chips.hasResume', { n: withResumeCount }) }}</span>
          <span class="chip done">{{ t('agentUsersPage.chips.parseDone', { n: resumeDoneCount }) }}</span>
          <span v-if="resumePendingCount > 0" class="chip pending">{{ t('agentUsersPage.chips.parsing', { n: resumePendingCount }) }}</span>
          <span v-if="resumeFailedCount > 0" class="chip failed">{{ t('agentUsersPage.chips.failed', { n: resumeFailedCount }) }}</span>
          <span class="chip pref">{{ t('agentUsersPage.chips.hasPrefs', { n: withPrefsCount }) }}</span>
        </div>
        <button class="refresh-btn" :disabled="loading" @click="load">
          {{ loading ? t('agentUsersPage.refreshing') : t('agentUsersPage.refresh') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t('agentUsersPage.cols.userId') }}</th>
            <th>{{ t('agentUsersPage.cols.connStatus') }}</th>
            <th>{{ t('agentUsersPage.cols.prefs') }}</th>
            <th>{{ t('agentUsersPage.cols.resumeStatus') }}</th>
            <th>{{ t('agentUsersPage.cols.fileName') }}</th>
            <th>{{ t('agentUsersPage.cols.uploadedAt') }}</th>
            <th>{{ t('agentUsersPage.cols.parsedAt') }}</th>
            <th>{{ t('agentUsersPage.cols.firstSeen') }}</th>
            <th>{{ t('agentUsersPage.cols.lastSeen') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="users.length === 0 && !loading">
            <td colspan="9" class="empty-row">{{ t('agentUsersPage.emptyUsers') }}</td>
          </tr>
          <tr
            v-for="u in users"
            :key="u.user_id"
            class="data-row"
            @click="openDetail(u)"
          >
            <td class="mono">{{ u.user_id }}</td>
            <td>
              <span class="status-badge" :class="u.is_connected ? 'connected' : 'offline'">
                {{ u.is_connected ? t('agentUsersPage.online') : t('agentUsersPage.offline') }}
              </span>
            </td>
            <td>
              <span v-if="u.has_preferences" class="pref-badge filled">{{ t('agentUsersPage.filled') }}</span>
              <span v-else class="dim">—</span>
            </td>
            <td>
              <span v-if="!u.resume_id" class="dim">—</span>
              <span v-else class="resume-badge" :class="u.parse_status ?? ''">
                {{ resumeLabel(u.parse_status) }}
              </span>
            </td>
            <td class="sm dim">{{ u.original_name ?? '—' }}</td>
            <td class="sm dim">{{ fmtDt(u.uploaded_at) }}</td>
            <td class="sm dim">{{ fmtDt(u.parsed_at) }}</td>
            <td class="sm dim">{{ fmtDt(u.first_seen_at) }}</td>
            <td class="sm dim">{{ fmtDt(u.last_seen_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > limit" class="pagination">
      <button :disabled="offset === 0" @click="paginate(-1)">{{ t('agentUsersPage.prevPage') }}</button>
      <span>{{ offset / limit + 1 }}</span>
      <button :disabled="users.length < limit" @click="paginate(1)">{{ t('agentUsersPage.nextPage') }}</button>
    </div>

    <!-- Detail + Resume drawer -->
    <Teleport to="body">
      <div v-if="detail" class="drawer-overlay" @click.self="closeDrawer">
        <div class="drawer">
          <div class="drawer-header">
            <span class="drawer-title">{{ t('agentUsersPage.drawerTitle') }} — {{ detail.user_id }}</span>
            <button class="drawer-close" @click="closeDrawer">✕</button>
          </div>
          <div class="drawer-body">

            <!-- User info -->
            <div class="section-label">{{ t('agentUsersPage.basicInfo') }}</div>
            <div class="meta-section">
              <div class="meta-row">
                <span class="meta-label">{{ t('agentUsersPage.cols.userId') }}</span>
                <span class="mono">{{ detail.user_id }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('agentUsersPage.cols.connStatus') }}</span>
                <span class="status-badge" :class="detail.is_connected ? 'connected' : 'offline'">
                  {{ detail.is_connected ? t('agentUsersPage.online') : t('agentUsersPage.offline') }}
                </span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('agentUsersPage.cols.firstSeen') }}</span>
                <span>{{ fmtDt(detail.first_seen_at) }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('agentUsersPage.cols.lastSeen') }}</span>
                <span>{{ fmtDt(detail.last_seen_at) }}</span>
              </div>
            </div>

            <!-- Preferences section -->
            <div class="section-label">
              <span>{{ t('agentUsersPage.jobPrefs') }}</span>
              <button v-if="!detail.has_preferences" class="section-action" @click="loadSuggest">
                {{ suggestLoading ? t('agentUsersPage.aiSuggesting') : t('agentUsersPage.aiSuggest') }}
              </button>
            </div>

            <div v-if="prefsLoading" class="dim" style="font-size:13px">{{ t('agentUsersPage.loading') }}</div>

            <div v-else-if="prefs || prefsEdit" class="prefs-card">
              <!-- View mode -->
              <template v-if="!prefsEditMode">
                <div class="prefs-grid">
                  <div class="pref-item">
                    <span class="pref-label">{{ t('agentUsersPage.targetRole') }}</span>
                    <span class="pref-value">{{ prefs?.job_role || '—' }}</span>
                  </div>
                  <div class="pref-item">
                    <span class="pref-label">{{ t('agentUsersPage.targetCity') }}</span>
                    <span class="pref-value">{{ prefs?.city || '—' }}</span>
                  </div>
                  <div class="pref-item">
                    <span class="pref-label">{{ t('agentUsersPage.expectedSalary') }}</span>
                    <span class="pref-value">{{ prefs?.salary_range || '—' }}</span>
                  </div>
                  <div class="pref-item full">
                    <span class="pref-label">{{ t('agentUsersPage.otherNotes') }}</span>
                    <span class="pref-value">{{ prefs?.notes || '—' }}</span>
                  </div>
                </div>
                <div class="prefs-actions">
                  <button class="action-btn secondary sm" @click="startEdit">{{ t('agentUsersPage.edit') }}</button>
                </div>
              </template>

              <!-- Edit mode -->
              <template v-else>
                <div class="edit-grid">
                  <div class="edit-field">
                    <label class="edit-label">{{ t('agentUsersPage.targetRole') }}</label>
                    <input v-model="prefsEdit!.job_role" class="edit-input" :placeholder="t('agentUsersPage.targetRolePh')" />
                  </div>
                  <div class="edit-field">
                    <label class="edit-label">{{ t('agentUsersPage.targetCity') }}</label>
                    <input v-model="prefsEdit!.city" class="edit-input" :placeholder="t('agentUsersPage.targetCityPh')" />
                  </div>
                  <div class="edit-field">
                    <label class="edit-label">{{ t('agentUsersPage.expectedSalary') }}</label>
                    <select v-model="prefsEdit!.salary_range" class="edit-input">
                      <option value="">{{ t('agentUsersPage.unlimited') }}</option>
                      <option v-for="opt in salaryOptions" :key="opt" :value="opt">{{ opt }}</option>
                    </select>
                  </div>
                  <div class="edit-field full">
                    <label class="edit-label">{{ t('agentUsersPage.otherNotes') }}</label>
                    <textarea v-model="prefsEdit!.notes" class="edit-textarea"
                      :placeholder="t('agentUsersPage.notesPh')" rows="3" />
                  </div>
                </div>
                <div class="prefs-actions">
                  <button class="action-btn primary sm" :disabled="prefsSaving" @click="savePrefs">
                    {{ prefsSaving ? t('agentUsersPage.saving') : t('agentUsersPage.save') }}
                  </button>
                  <button class="action-btn secondary sm" @click="prefsEditMode = false">{{ t('agentUsersPage.cancel') }}</button>
                </div>
              </template>
            </div>

            <!-- No prefs yet -->
            <div v-else class="prefs-empty">
              <span class="dim">{{ t('agentUsersPage.noPrefs') }}</span>
              <button class="action-btn secondary sm" @click="startEdit">{{ t('agentUsersPage.fillManually') }}</button>
            </div>

            <!-- Resume section -->
            <div class="section-label">{{ t('agentUsersPage.resumeMgmt') }}</div>

            <!-- No resume: upload form -->
            <template v-if="!detail.resume_id">
              <div class="upload-zone" :class="{ dragover: isDragover }"
                @dragover.prevent="isDragover = true"
                @dragleave="isDragover = false"
                @drop.prevent="onDrop">
                <div class="upload-icon">📄</div>
                <div class="upload-hint">{{ t('agentUsersPage.dragHint') }}</div>
                <label class="upload-btn">
                  {{ t('agentUsersPage.chooseFile') }}
                  <input type="file" accept=".pdf,.docx,.doc" class="file-input" @change="onFileChange" />
                </label>
                <div class="upload-note">{{ t('agentUsersPage.uploadNote') }}</div>
              </div>
              <div v-if="uploadFile" class="selected-file">
                <span class="file-icon">📎</span>
                <span class="file-name">{{ uploadFile.name }}</span>
                <span class="file-size">{{ fmtSize(uploadFile.size) }}</span>
              </div>
              <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
              <button v-if="uploadFile" class="action-btn primary" :disabled="uploading" @click="doUpload">
                {{ uploading ? t('agentUsersPage.uploading') : t('agentUsersPage.uploadAndParse') }}
              </button>
            </template>

            <!-- Has resume: status + actions -->
            <template v-else>
              <div class="resume-card">
                <div class="resume-card-row">
                  <div class="resume-filename">
                    <span class="file-icon">📄</span>
                    {{ detail.original_name ?? t('agentUsersPage.unknownFile') }}
                  </div>
                  <span class="resume-badge lg" :class="resumeParseStatus">
                    {{ resumeLabel(resumeParseStatus) }}
                  </span>
                </div>
                <div class="resume-card-meta">
                  <span>{{ t('agentUsersPage.uploadedOn', { t: fmtDt(detail.uploaded_at) }) }}</span>
                  <span v-if="detail.parsed_at">{{ t('agentUsersPage.parsedOn', { t: fmtDt(detail.parsed_at) }) }}</span>
                </div>
                <div v-if="resumeParseStatus === 'failed' && detail.parse_error" class="parse-error">
                  {{ detail.parse_error }}
                </div>
                <div v-if="resumeParseStatus === 'parsing' || resumeParseStatus === 'pending'" class="parse-progress">
                  <span class="spinner" /> {{ t('agentUsersPage.parsingProgress') }}
                </div>
              </div>

              <!-- Replace file upload -->
              <div class="replace-section">
                <div class="replace-label">{{ t('agentUsersPage.reupload') }}</div>
                <div class="replace-row">
                  <label class="upload-btn sm">
                    {{ t('agentUsersPage.chooseFile') }}
                    <input type="file" accept=".pdf,.docx,.doc" class="file-input" @change="onFileChange" />
                  </label>
                  <span v-if="uploadFile" class="file-name-sm">{{ uploadFile.name }}</span>
                </div>
                <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
                <button v-if="uploadFile" class="action-btn primary sm" :disabled="uploading" @click="doUpload">
                  {{ uploading ? t('agentUsersPage.uploading') : t('agentUsersPage.replaceAndReparse') }}
                </button>
              </div>

              <!-- Action buttons -->
              <div class="resume-actions">
                <button
                  v-if="resumeParseStatus === 'failed' || resumeParseStatus === 'done'"
                  class="action-btn secondary"
                  :disabled="actionBusy"
                  @click="retryParse"
                >{{ t('agentUsersPage.reparse') }}</button>
                <button
                  v-if="resumeParseStatus === 'done'"
                  class="action-btn view"
                  @click="toggleParsed"
                >{{ showParsed ? t('agentUsersPage.hideParsed') : t('agentUsersPage.viewParsed') }}</button>
                <button class="action-btn danger" :disabled="actionBusy" @click="confirmDelete = true">
                  {{ t('agentUsersPage.deleteResume') }}
                </button>
              </div>

              <!-- Confirm delete -->
              <div v-if="confirmDelete" class="confirm-box">
                <span>{{ t('agentUsersPage.confirmDeleteMsg') }}</span>
                <div class="confirm-btns">
                  <button class="action-btn danger sm" :disabled="actionBusy" @click="doDelete">{{ t('agentUsersPage.confirmDelete') }}</button>
                  <button class="action-btn secondary sm" @click="confirmDelete = false">{{ t('agentUsersPage.cancel') }}</button>
                </div>
              </div>
            </template>

            <!-- Parsed result panel -->
            <template v-if="showParsed && parsedResume">
              <div class="parsed-panel">
                <div class="parsed-section-title">{{ t('agentUsersPage.basicInfo') }}</div>
                <div class="parsed-grid">
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.name') }}</span><span>{{ parsedResume.name ?? '—' }}</span></div>
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.phone') }}</span><span>{{ parsedResume.phone ?? '—' }}</span></div>
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.email') }}</span><span>{{ parsedResume.email ?? '—' }}</span></div>
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.workYears') }}</span><span>{{ parsedResume.work_years ?? '—' }}</span></div>
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.degree') }}</span><span>{{ parsedResume.degree ?? '—' }}</span></div>
                  <div class="parsed-item"><span class="pi-label">{{ t('agentUsersPage.expectedSalary') }}</span><span>{{ parsedResume.target_salary_raw ?? '—' }}</span></div>
                </div>
                <div v-if="parsedResume.target_cities?.length" class="parsed-section-title">{{ t('agentUsersPage.expectedCities') }}</div>
                <div v-if="parsedResume.target_cities?.length" class="tag-list">
                  <span v-for="c in parsedResume.target_cities" :key="c" class="tag">{{ c }}</span>
                </div>
                <div v-if="parsedResume.target_positions?.length" class="parsed-section-title">{{ t('agentUsersPage.expectedPositions') }}</div>
                <div v-if="parsedResume.target_positions?.length" class="tag-list">
                  <span v-for="p in parsedResume.target_positions" :key="p" class="tag">{{ p }}</span>
                </div>
                <div v-if="parsedResume.skills?.length" class="parsed-section-title">{{ t('agentUsersPage.skills') }}</div>
                <div v-if="parsedResume.skills?.length" class="tag-list">
                  <span v-for="s in parsedResume.skills" :key="s" class="tag skill">{{ s }}</span>
                </div>
                <template v-if="parsedResume.work_experience?.length">
                  <div class="parsed-section-title">{{ t('agentUsersPage.workExperience') }}</div>
                  <div v-for="(w, i) in parsedResume.work_experience" :key="i" class="exp-card">
                    <div class="exp-header">
                      <span class="exp-company">{{ w.company }}</span>
                      <span class="exp-title">{{ w.title }}</span>
                      <span class="exp-dur dim">{{ w.duration }}</span>
                    </div>
                    <div class="exp-desc">{{ w.desc }}</div>
                  </div>
                </template>
                <template v-if="parsedResume.education?.length">
                  <div class="parsed-section-title">{{ t('agentUsersPage.education') }}</div>
                  <div v-for="(e, i) in parsedResume.education" :key="i" class="exp-card">
                    <div class="exp-header">
                      <span class="exp-company">{{ e.school }}</span>
                      <span class="exp-title">{{ e.major }} · {{ e.degree }}</span>
                      <span class="exp-dur dim">{{ e.duration }}</span>
                    </div>
                  </div>
                </template>
                <template v-if="parsedResume.self_evaluation">
                  <div class="parsed-section-title">{{ t('agentUsersPage.selfEvaluation') }}</div>
                  <div class="self-eval">{{ parsedResume.self_evaluation }}</div>
                </template>
              </div>
            </template>

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
import type { AgentUser, ParsedResume, UserPreferences } from '../types'

const { t } = useI18n()

const users   = ref<AgentUser[]>([])
const loading = ref(false)
const error   = ref('')
const limit   = 100
const offset  = ref(0)
const total   = ref(0)

// Drawer state
const detail     = ref<AgentUser | null>(null)
const uploadFile = ref<File | null>(null)
const uploading  = ref(false)
const uploadError = ref('')
const isDragover = ref(false)
const actionBusy = ref(false)
const confirmDelete = ref(false)
const showParsed = ref(false)
const parsedResume = ref<ParsedResume | null>(null)

// Preferences state
const prefs        = ref<UserPreferences | null>(null)
const prefsEdit    = ref<Omit<UserPreferences, 'user_id'> | null>(null)
const prefsLoading = ref(false)
const prefsEditMode = ref(false)
const prefsSaving  = ref(false)
const suggestLoading = ref(false)

const salaryOptions = ['3k以下', '3-5k', '5-10k', '10-20k', '20-30k', '30-50k', '50k+']

const resumeParseStatus = computed(() =>
  (detail.value?.parse_status ?? null) as AgentUser['parse_status']
)

const connectedCount    = computed(() => users.value.filter(u => u.is_connected).length)
const withResumeCount   = computed(() => users.value.filter(u => u.resume_id).length)
const resumeDoneCount   = computed(() => users.value.filter(u => u.parse_status === 'done').length)
const resumePendingCount = computed(() => users.value.filter(u => u.parse_status === 'pending' || u.parse_status === 'parsing').length)
const resumeFailedCount = computed(() => users.value.filter(u => u.parse_status === 'failed').length)
const withPrefsCount    = computed(() => users.value.filter(u => u.has_preferences).length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.agent_getUsers({ limit, offset: offset.value })
    users.value = res.users
    total.value = res.users.length + offset.value
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.gatewayUnreachable')
  } finally {
    loading.value = false
  }
}

function paginate(dir: 1 | -1) {
  offset.value = Math.max(0, offset.value + dir * limit)
  load()
}

async function loadPreferences(userId: string) {
  prefs.value = null
  prefsEdit.value = null
  prefsEditMode.value = false
  prefsLoading.value = true
  try {
    const res = await api.agent_getPreferences(userId)
    prefs.value = res.data
  } catch { /* ignore */ } finally {
    prefsLoading.value = false
  }
}

async function loadSuggest() {
  if (!detail.value || suggestLoading.value) return
  suggestLoading.value = true
  try {
    const res = await api.agent_suggestPreferences(detail.value.user_id)
    const { user_id: _uid, ...fields } = res.suggestion
    prefsEdit.value = fields
    prefsEditMode.value = true
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.aiSuggestFailed')
  } finally {
    suggestLoading.value = false
  }
}

function startEdit() {
  prefsEdit.value = {
    job_role: prefs.value?.job_role ?? '',
    city: prefs.value?.city ?? '',
    salary_range: prefs.value?.salary_range ?? '',
    notes: prefs.value?.notes ?? '',
  }
  prefsEditMode.value = true
}

async function savePrefs() {
  if (!detail.value || !prefsEdit.value) return
  prefsSaving.value = true
  try {
    await api.agent_savePreferences(detail.value.user_id, prefsEdit.value)
    await loadPreferences(detail.value.user_id)
    // Update has_preferences in local list
    const idx = users.value.findIndex(u => u.user_id === detail.value!.user_id)
    if (idx >= 0) users.value[idx] = { ...users.value[idx], has_preferences: true }
    if (detail.value) detail.value = { ...detail.value, has_preferences: true }
    prefsEditMode.value = false
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.saveFailed')
  } finally {
    prefsSaving.value = false
  }
}

function openDetail(u: AgentUser) {
  detail.value = { ...u }
  uploadFile.value = null
  uploadError.value = ''
  confirmDelete.value = false
  showParsed.value = false
  parsedResume.value = null
  loadPreferences(u.user_id)
}

function closeDrawer() {
  detail.value = null
  uploadFile.value = null
  uploadError.value = ''
  confirmDelete.value = false
  showParsed.value = false
  parsedResume.value = null
  prefs.value = null
  prefsEdit.value = null
  prefsEditMode.value = false
}

function onFileChange(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files?.[0]) {
    uploadFile.value = files[0]
    uploadError.value = ''
  }
}

function onDrop(e: DragEvent) {
  isDragover.value = false
  const file = e.dataTransfer?.files[0]
  if (!file) return
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!['pdf', 'docx', 'doc'].includes(ext)) {
    uploadError.value = t('agentUsersPage.unsupportedFormat')
    return
  }
  uploadFile.value = file
  uploadError.value = ''
}

async function doUpload() {
  if (!detail.value || !uploadFile.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    await api.agent_uploadResume(detail.value.user_id, uploadFile.value)
    uploadFile.value = null
    // Refresh user status
    await refreshUserResume(detail.value.user_id)
  } catch (e: unknown) {
    uploadError.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.uploadFailed')
  } finally {
    uploading.value = false
  }
}

async function retryParse() {
  if (!detail.value) return
  actionBusy.value = true
  try {
    await api.agent_retryResumeParse(detail.value.user_id)
    await refreshUserResume(detail.value.user_id)
    showParsed.value = false
    parsedResume.value = null
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.reparseFailed')
  } finally {
    actionBusy.value = false
  }
}

async function doDelete() {
  if (!detail.value) return
  actionBusy.value = true
  try {
    await api.agent_deleteResume(detail.value.user_id)
    // Clear resume fields on local copy
    detail.value = { ...detail.value, resume_id: null, original_name: null,
      parse_status: null, uploaded_at: null, parsed_at: null, parse_error: null }
    confirmDelete.value = false
    showParsed.value = false
    parsedResume.value = null
    await load()
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.deleteFailed')
  } finally {
    actionBusy.value = false
  }
}

async function toggleParsed() {
  if (showParsed.value) {
    showParsed.value = false
    return
  }
  if (parsedResume.value) {
    showParsed.value = true
    return
  }
  if (!detail.value) return
  try {
    const res = await api.agent_getResume(detail.value.user_id)
    if (res.ok && res.parsed) {
      parsedResume.value = res.parsed
      showParsed.value = true
    }
  } catch (e: unknown) {
    error.value = (e instanceof Error ? e.message : null) ?? t('agentUsersPage.getParsedFailed')
  }
}

async function refreshUserResume(userId: string) {
  try {
    const res = await api.agent_getResumeStatus(userId)
    if (detail.value && detail.value.user_id === userId) {
      detail.value = {
        ...detail.value,
        resume_id: res.resume_id ?? null,
        original_name: res.original_name ?? null,
        parse_status: (res.parse_status ?? null) as AgentUser['parse_status'],
        uploaded_at: res.uploaded_at ?? null,
        parsed_at: res.parsed_at ?? null,
      }
    }
    // Update in list
    const idx = users.value.findIndex(u => u.user_id === userId)
    if (idx >= 0 && detail.value) {
      users.value[idx] = { ...users.value[idx], ...detail.value }
    }
  } catch { /* ignore */ }
}

function resumeLabel(status: string | null | undefined): string {
  const m: Record<string, string> = {
    pending: t('agentUsersPage.resumeStatusEnum.pending'),
    parsing: t('agentUsersPage.resumeStatusEnum.parsing'),
    done: t('agentUsersPage.resumeStatusEnum.done'),
    failed: t('agentUsersPage.resumeStatusEnum.failed'),
  }
  return status ? (m[status] ?? status) : t('agentUsersPage.resumeStatusEnum.notUploaded')
}

function fmtDt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

onMounted(load)
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 16px; }

.page-header {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.header-right { display: flex; align-items: center; gap: 10px; }

.stats-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155;
}
.chip.connected { background: #1a3a2a; color: #4ade80; border-color: #166534; }
.chip.has-resume { background: #1e3a5f; color: #60a5fa; border-color: #1d4ed8; }
.chip.done    { background: #1a3a2a; color: #4ade80; border-color: #166534; }
.chip.pending { background: #2d2a1a; color: #fbbf24; border-color: #92400e; }
.chip.failed  { background: #3b1a1a; color: #f87171; border-color: #7f1d1d; }

.refresh-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

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

.status-badge {
  display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
}
.status-badge.connected { background: #1a3a2a; color: #4ade80; }
.status-badge.offline   { background: #1e293b; color: #64748b; }

.resume-badge {
  display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
}
.resume-badge.done    { background: #1a3a2a; color: #4ade80; }
.resume-badge.pending, .resume-badge.parsing { background: #2d2a1a; color: #fbbf24; }
.resume-badge.failed  { background: #3b1a1a; color: #f87171; }
.resume-badge.lg { font-size: 12px; padding: 4px 10px; }

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
  width: 480px; max-width: 95vw; background: #0f172a;
  border-left: 1px solid #334155; display: flex; flex-direction: column;
  animation: slideIn 0.2s ease;
}
@keyframes slideIn { from { transform: translateX(100%) } to { transform: translateX(0) } }

.drawer-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #334155; flex-shrink: 0;
}
.drawer-title { font-size: 15px; font-weight: 600; color: #e2e8f0; }
.drawer-close {
  width: 28px; height: 28px; border-radius: 6px; color: #64748b; background: transparent;
  border: none; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center;
}
.drawer-close:hover { background: #1e293b; color: #e2e8f0; }

.drawer-body { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }

.section-label {
  font-size: 11px; font-weight: 600; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 1px solid #1e293b; padding-bottom: 6px;
  display: flex; align-items: center; justify-content: space-between;
}

.meta-section { display: flex; flex-direction: column; gap: 10px; }
.meta-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #cbd5e1; }
.meta-label { width: 80px; color: #64748b; font-size: 12px; flex-shrink: 0; }

/* Upload zone */
.upload-zone {
  border: 2px dashed #334155; border-radius: 10px; padding: 28px 20px;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  transition: border-color 0.2s, background 0.2s; cursor: default;
}
.upload-zone.dragover { border-color: #60a5fa; background: rgba(96,165,250,0.05); }
.upload-icon { font-size: 32px; }
.upload-hint { font-size: 13px; color: #64748b; }
.upload-note { font-size: 11px; color: #475569; }

.file-input { display: none; }
.upload-btn {
  display: inline-block; padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
  background: #1d4ed8; color: white; border: none; transition: background 0.12s;
}
.upload-btn:hover { background: #2563eb; }
.upload-btn.sm { padding: 6px 12px; font-size: 12px; }

.selected-file {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: #1e293b; border-radius: 8px; font-size: 13px;
}
.file-icon { font-size: 16px; }
.file-name { flex: 1; color: #e2e8f0; word-break: break-all; }
.file-size { color: #64748b; font-size: 12px; white-space: nowrap; }

.upload-error { font-size: 12px; color: #f87171; }

/* Resume card */
.resume-card {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 8px;
}
.resume-card-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.resume-filename {
  display: flex; align-items: center; gap: 6px; font-size: 13px; color: #e2e8f0;
  word-break: break-all;
}
.resume-card-meta { font-size: 11px; color: #64748b; }
.parse-error { font-size: 12px; color: #f87171; word-break: break-word; }
.parse-progress {
  display: flex; align-items: center; gap: 6px; font-size: 12px; color: #fbbf24;
}
.spinner {
  display: inline-block; width: 12px; height: 12px; border: 2px solid #fbbf24;
  border-top-color: transparent; border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg) } }

/* Replace section */
.replace-section { display: flex; flex-direction: column; gap: 8px; }
.replace-label { font-size: 12px; color: #64748b; }
.replace-row { display: flex; align-items: center; gap: 8px; }
.file-name-sm { font-size: 12px; color: #94a3b8; word-break: break-all; }

/* Action buttons */
.resume-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.action-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px; cursor: pointer;
  border: 1px solid; transition: all 0.12s; white-space: nowrap;
}
.action-btn.sm { padding: 5px 10px; font-size: 12px; }
.action-btn.primary { background: #1d4ed8; color: white; border-color: #1d4ed8; }
.action-btn.primary:hover:not(:disabled) { background: #2563eb; border-color: #2563eb; }
.action-btn.secondary { background: #1e293b; color: #94a3b8; border-color: #334155; }
.action-btn.secondary:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.action-btn.view { background: #1e3a5f; color: #60a5fa; border-color: #1d4ed8; }
.action-btn.view:hover:not(:disabled) { background: #1d4ed8; color: white; }
.action-btn.danger { background: #3b1a1a; color: #f87171; border-color: #7f1d1d; }
.action-btn.danger:hover:not(:disabled) { background: #7f1d1d; color: white; }
.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Confirm box */
.confirm-box {
  padding: 12px 14px; background: #1e293b; border: 1px solid #7f1d1d; border-radius: 8px;
  display: flex; flex-direction: column; gap: 10px; font-size: 13px; color: #f87171;
}
.confirm-btns { display: flex; gap: 8px; }

/* Parsed result panel */
.parsed-panel {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px;
  padding: 16px; display: flex; flex-direction: column; gap: 12px;
}
.parsed-section-title {
  font-size: 11px; font-weight: 600; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px;
}
.parsed-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
}
.parsed-item { display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
.pi-label { color: #64748b; font-size: 11px; }

.tag-list { display: flex; flex-wrap: wrap; gap: 6px; }
.tag {
  padding: 3px 9px; border-radius: 10px; font-size: 12px;
  background: #0f172a; color: #94a3b8; border: 1px solid #334155;
}
.tag.skill { background: #1e3a5f; color: #60a5fa; border-color: #1d4ed8; }

.exp-card {
  background: #0f172a; border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 6px;
}
.exp-header { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.exp-company { font-size: 13px; font-weight: 600; color: #e2e8f0; }
.exp-title { font-size: 12px; color: #94a3b8; }
.exp-dur { font-size: 11px; }
.exp-desc { font-size: 12px; color: #64748b; line-height: 1.5; }

.self-eval {
  font-size: 12px; color: #94a3b8; line-height: 1.6; white-space: pre-wrap;
  background: #0f172a; padding: 10px 12px; border-radius: 8px;
}

/* Preferences */
.chip.pref { background: #2a1a3b; color: #c084fc; border-color: #7c3aed; }

.pref-badge {
  display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
}
.pref-badge.filled { background: #2a1a3b; color: #c084fc; }

.section-action {
  padding: 3px 10px; border-radius: 5px; font-size: 11px; cursor: pointer;
  background: #2a1a3b; color: #c084fc; border: 1px solid #7c3aed;
  transition: all 0.12s;
}
.section-action:hover:not(:disabled) { background: #7c3aed; color: white; }
.section-action:disabled { opacity: 0.5; cursor: not-allowed; }

.prefs-card {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px;
  padding: 14px 16px; display: flex; flex-direction: column; gap: 12px;
}
.prefs-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
}
.pref-item { display: flex; flex-direction: column; gap: 3px; }
.pref-item.full { grid-column: 1 / -1; }
.pref-label { font-size: 11px; color: #64748b; }
.pref-value { font-size: 13px; color: #e2e8f0; }
.prefs-actions { display: flex; gap: 8px; }

.prefs-empty {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; background: #1e293b; border: 1px dashed #334155; border-radius: 8px;
  font-size: 13px;
}

.edit-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
}
.edit-field { display: flex; flex-direction: column; gap: 4px; }
.edit-field.full { grid-column: 1 / -1; }
.edit-label { font-size: 11px; color: #64748b; }
.edit-input {
  padding: 6px 10px; border-radius: 6px; font-size: 13px;
  background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
  outline: none; transition: border-color 0.15s;
}
.edit-input:focus { border-color: #7c3aed; }
.edit-textarea {
  padding: 6px 10px; border-radius: 6px; font-size: 13px;
  background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
  outline: none; resize: vertical; font-family: inherit; line-height: 1.5;
  transition: border-color 0.15s;
}
.edit-textarea:focus { border-color: #7c3aed; }
</style>
