<template>
  <div class="page">
    <div class="page-header">
      <h1>{{ t('jobCachePage.title') }}</h1>
      <span class="subtitle">{{ t('jobCachePage.subtitle') }}</span>
    </div>

    <!-- Filters -->
    <div class="filters">
      <select v-model="filters.platform" class="filter-input">
        <option value="">{{ t('jobCachePage.allPlatforms') }}</option>
        <option value="boss">{{ t('jobCachePage.bossZhipin') }}</option>
      </select>
      <input
        v-model="filters.keyword"
        class="filter-input"
        :placeholder="t('jobCachePage.keywordPh')"
        @keyup.enter="load"
      />
      <input
        v-model="filters.city_code"
        class="filter-input"
        :placeholder="t('jobCachePage.cityCodePh')"
        @keyup.enter="load"
      />
      <select v-model="filters.has_detail" class="filter-input">
        <option value="">{{ t('jobCachePage.all') }}</option>
        <option value="true">{{ t('jobCachePage.hasDetail') }}</option>
        <option value="false">{{ t('jobCachePage.noDetail') }}</option>
      </select>
      <button class="btn-primary" @click="load">{{ t('jobCachePage.query') }}</button>
      <button class="btn-ghost" @click="resetFilters">{{ t('jobCachePage.reset') }}</button>
    </div>

    <!-- Table -->
    <div class="table-wrap">
      <table v-if="!loading && jobs.length > 0">
        <thead>
          <tr>
            <th>{{ t('jobCachePage.colPlatform') }}</th>
            <th>{{ t('jobCachePage.colTitle') }}</th>
            <th>{{ t('jobCachePage.colCompany') }}</th>
            <th>{{ t('jobCachePage.colCity') }}</th>
            <th>{{ t('jobCachePage.colSalary') }}</th>
            <th>{{ t('jobCachePage.colEducation') }}</th>
            <th>{{ t('jobCachePage.colType') }}</th>
            <th>{{ t('jobCachePage.colDetail') }}</th>
            <th>{{ t('jobCachePage.colFetchedAt') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in jobs" :key="job.id" class="job-row" @click="openDrawer(job)">
            <td><span class="badge">{{ job.platform }}</span></td>
            <td class="job-title">{{ job.title || '-' }}</td>
            <td>{{ job.company || '-' }}</td>
            <td>{{ job.city || '-' }}</td>
            <td class="salary">{{ job.salary || '-' }}</td>
            <td>{{ job.education || '-' }}</td>
            <td>{{ job.job_type || '-' }}</td>
            <td>
              <span :class="['detail-badge', job.has_detail ? 'yes' : 'no']">
                {{ job.has_detail ? t('jobCachePage.yes') : t('jobCachePage.no') }}
              </span>
            </td>
            <td class="time">{{ formatTime(job.fetched_at) }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="loading" class="empty-state">{{ t('jobCachePage.loading') }}</div>
      <div v-if="!loading && jobs.length === 0" class="empty-state">{{ t('jobCachePage.noData') }}</div>
    </div>

    <!-- Pagination -->
    <div v-if="jobs.length > 0" class="pagination">
      <button :disabled="offset === 0" class="btn-ghost" @click="prevPage">{{ t('jobCachePage.prevPage') }}</button>
      <span class="page-info">{{ t('jobCachePage.pageInfo', { page: Math.floor(offset / limit) + 1, count: jobs.length }) }}</span>
      <button :disabled="jobs.length < limit" class="btn-ghost" @click="nextPage">{{ t('jobCachePage.nextPage') }}</button>
    </div>

    <!-- Detail Drawer -->
    <div v-if="drawer.open" class="drawer-overlay" @click.self="closeDrawer">
      <div class="drawer">
        <div class="drawer-header">
          <div>
            <div class="drawer-title">{{ drawer.job?.title || '-' }}</div>
            <div class="drawer-sub">{{ drawer.job?.company }} · {{ drawer.job?.city }}</div>
          </div>
          <button class="close-btn" @click="closeDrawer">✕</button>
        </div>

        <div v-if="drawer.loading" class="drawer-loading">{{ t('jobCachePage.loadingDetail') }}</div>

        <div v-else-if="drawer.detail" class="drawer-body">
          <div v-if="!drawer.detail.has_detail" class="fetch-bar">
            <span class="fetch-hint">{{ t('jobCachePage.noDetailHint') }}</span>
            <button class="btn-fetch" :disabled="drawer.fetching" @click="fetchDetail">
              {{ drawer.fetching ? t('jobCachePage.fetching') : t('jobCachePage.fetchNow') }}
            </button>
            <span v-if="drawer.fetchError" class="fetch-error">{{ drawer.fetchError }}</span>
          </div>
          <section class="info-grid">
            <div v-for="[label, val] in drawerFields" :key="label" class="info-row">
              <span class="info-label">{{ label }}</span>
              <span class="info-val">{{ val || '-' }}</span>
            </div>
          </section>

          <section v-if="drawerTags.length" class="tags-section">
            <div class="section-title">{{ t('jobCachePage.tags') }}</div>
            <div class="tags">
              <span v-for="t in drawerTags" :key="t" class="tag">{{ t }}</span>
            </div>
          </section>

          <section v-if="drawerSkills.length" class="tags-section">
            <div class="section-title">{{ t('jobCachePage.skills') }}</div>
            <div class="tags">
              <span v-for="s in drawerSkills" :key="s" class="tag skill">{{ s }}</span>
            </div>
          </section>

          <section v-if="drawer.detail.description" class="desc-section">
            <div class="section-title">{{ t('jobCachePage.jobDescription') }}</div>
            <pre class="desc-text">{{ drawer.detail.description }}</pre>
          </section>

          <section class="raw-section">
            <div class="section-title" @click="rawExpanded = !rawExpanded" style="cursor:pointer">
              {{ t('jobCachePage.rawJson') }} {{ rawExpanded ? '▲' : '▼' }}
            </div>
            <pre v-if="rawExpanded" class="raw-json">{{ rawJson }}</pre>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { CachedJob } from '../types'

const { t } = useI18n()

const jobs = ref<CachedJob[]>([])
const loading = ref(false)
const limit = 50
const offset = ref(0)

const filters = reactive({
  platform: '',
  keyword: '',
  city_code: '',
  has_detail: '' as '' | 'true' | 'false',
})

const drawer = reactive({
  open: false,
  loading: false,
  fetching: false,
  fetchError: '',
  job: null as CachedJob | null,
  detail: null as CachedJob | null,
})
const rawExpanded = ref(false)

function parseJsonField(val: any): any[] {
  if (Array.isArray(val)) return val
  if (typeof val === 'string') {
    try { const p = JSON.parse(val); return Array.isArray(p) ? p : [] } catch { return [] }
  }
  return []
}

const drawerTags = computed(() => parseJsonField(drawer.detail?.tags))
const drawerSkills = computed(() => parseJsonField(drawer.detail?.skills))

const rawJson = computed(() => {
  const raw = drawer.detail?.raw_list ?? drawer.detail
  if (!raw) return ''
  const parsed = typeof raw === 'string' ? (() => { try { return JSON.parse(raw) } catch { return raw } })() : raw
  return JSON.stringify(parsed, null, 2)
})

const drawerFields = computed(() => {
  const d = drawer.detail
  if (!d) return []
  return [
    [t('jobCachePage.fieldPlatform'), d.platform],
    ['external_id', d.external_id],
    [t('jobCachePage.fieldSalary'), d.salary],
    [t('jobCachePage.fieldJobType'), d.job_type],
    [t('jobCachePage.fieldEducation'), d.education],
    ['HR', d.hr_name ? `${d.hr_name} · ${d.hr_title || ''}` : null],
    [t('jobCachePage.fieldAddress'), d.address],
    [t('jobCachePage.fieldGeo'), d.longitude != null ? `${d.longitude}, ${d.latitude}` : null],
    [t('jobCachePage.fieldFetchedAt'), d.fetched_at],
  ] as [string, string | null | undefined][]
})

async function load() {
  loading.value = true
  try {
    const res = await api.getJobCache({
      platform: filters.platform || undefined,
      keyword: filters.keyword || undefined,
      city_code: filters.city_code || undefined,
      has_detail: filters.has_detail === 'true' ? true : filters.has_detail === 'false' ? false : undefined,
      limit,
      offset: offset.value,
    })
    jobs.value = res.jobs
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.platform = ''
  filters.keyword = ''
  filters.city_code = ''
  filters.has_detail = ''
  offset.value = 0
  load()
}

function prevPage() {
  offset.value = Math.max(0, offset.value - limit)
  load()
}
function nextPage() {
  offset.value += limit
  load()
}

async function openDrawer(job: CachedJob) {
  drawer.open = true
  drawer.job = job
  drawer.detail = job  // show basic info immediately
  drawer.fetchError = ''
  rawExpanded.value = false
  drawer.loading = true
  try {
    drawer.detail = await api.getJobCacheDetail(job.platform, job.external_id)
  } catch {
    drawer.detail = job
  } finally {
    drawer.loading = false
  }
}

async function fetchDetail() {
  if (!drawer.detail) return
  drawer.fetching = true
  drawer.fetchError = ''
  try {
    const res = await api.fetchJobCacheDetail(drawer.detail.platform, drawer.detail.external_id)
    if (res.ok && res.job) {
      drawer.detail = res.job
    } else {
      drawer.fetchError = res.error || t('jobCachePage.fetchFailed')
    }
  } catch (e: any) {
    drawer.fetchError = e.message || t('jobCachePage.fetchFailed')
  } finally {
    drawer.fetching = false
  }
}

function closeDrawer() {
  drawer.open = false
  drawer.job = null
  drawer.detail = null
}

function formatTime(ts: string) {
  if (!ts) return '-'
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<style scoped>
.page {
  padding: 24px;
  min-height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h1 {
  font-size: 20px;
  font-weight: 700;
  color: #f1f5f9;
}
.subtitle {
  font-size: 13px;
  color: #64748b;
}

/* Filters */
.filters {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.filter-input {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #e2e8f0;
  padding: 7px 12px;
  font-size: 13px;
  min-width: 160px;
}
.filter-input:focus { outline: none; border-color: #3b82f6; }
.btn-primary {
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 7px 18px;
  font-size: 13px;
  cursor: pointer;
}
.btn-primary:hover { background: #1d4ed8; }
.btn-ghost {
  background: transparent;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #94a3b8;
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-ghost:hover { background: #1e293b; color: #e2e8f0; }
.btn-ghost:disabled { opacity: 0.4; cursor: not-allowed; }

/* Table */
.table-wrap {
  background: #1e293b;
  border-radius: 8px;
  border: 1px solid #334155;
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th {
  text-align: left;
  padding: 10px 14px;
  color: #64748b;
  font-weight: 600;
  border-bottom: 1px solid #334155;
  white-space: nowrap;
}
td {
  padding: 10px 14px;
  border-bottom: 1px solid #1e293b;
  color: #cbd5e1;
}
.job-row { cursor: pointer; }
.job-row:hover td { background: #263248; }
.job-title { color: #93c5fd; font-weight: 500; }
.salary { color: #4ade80; }
.time { font-size: 12px; color: #475569; white-space: nowrap; }
.badge {
  background: #1d4ed8;
  color: #bfdbfe;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
}
.detail-badge {
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
}
.detail-badge.yes { background: #14532d; color: #4ade80; }
.detail-badge.no  { background: #1e293b; color: #475569; }

.empty-state {
  text-align: center;
  padding: 48px;
  color: #475569;
}

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  justify-content: center;
}
.page-info { font-size: 13px; color: #64748b; }

/* Drawer */
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 100;
  display: flex;
  justify-content: flex-end;
}
.drawer {
  width: min(560px, 90vw);
  background: #1e293b;
  height: 100vh;
  overflow-y: auto;
  border-left: 1px solid #334155;
  display: flex;
  flex-direction: column;
}
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 20px 20px 16px;
  border-bottom: 1px solid #334155;
  position: sticky;
  top: 0;
  background: #1e293b;
  z-index: 1;
}
.drawer-title { font-size: 16px; font-weight: 700; color: #f1f5f9; }
.drawer-sub { font-size: 13px; color: #64748b; margin-top: 4px; }
.close-btn {
  background: transparent;
  border: none;
  color: #64748b;
  font-size: 16px;
  cursor: pointer;
  padding: 4px 8px;
}
.close-btn:hover { color: #f87171; }
.drawer-loading { padding: 32px; text-align: center; color: #475569; }
.drawer-body { padding: 16px 20px; display: flex; flex-direction: column; gap: 20px; }

.info-grid { display: flex; flex-direction: column; gap: 8px; }
.info-row { display: flex; gap: 12px; font-size: 13px; }
.info-label { color: #64748b; min-width: 80px; flex-shrink: 0; }
.info-val { color: #cbd5e1; word-break: break-all; }

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 12px;
  color: #94a3b8;
}
.tag.skill { border-color: #1d4ed8; color: #93c5fd; }

.desc-section {}
.desc-text {
  font-size: 12px;
  color: #94a3b8;
  white-space: pre-wrap;
  word-break: break-word;
  background: #0f172a;
  border-radius: 6px;
  padding: 12px;
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid #334155;
}

.fetch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 10px 14px;
  flex-wrap: wrap;
}
.fetch-hint { font-size: 12px; color: #64748b; flex: 1; }
.btn-fetch {
  background: #1d4ed8;
  color: #fff;
  border: none;
  border-radius: 5px;
  padding: 5px 14px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.btn-fetch:hover { background: #2563eb; }
.btn-fetch:disabled { opacity: 0.5; cursor: not-allowed; }
.fetch-error { font-size: 12px; color: #f87171; width: 100%; }

.raw-section {}
.raw-json {
  font-size: 11px;
  color: #64748b;
  background: #0f172a;
  border-radius: 6px;
  padding: 12px;
  overflow: auto;
  max-height: 300px;
  border: 1px solid #334155;
}
</style>
