<template>
  <div class="af-page">
    <div class="page-header">
      <h2>🕸️ {{ t('autofillCapturesPage.title') }}</h2>
      <div class="meta">
        <button class="btn-secondary" :disabled="loading" @click="reload">
          {{ loading ? t('autofillCapturesPage.loading') : t('autofillCapturesPage.refresh') }}
        </button>
      </div>
    </div>

    <div class="filters panel">
      <div class="seg">
        <button v-for="o in PROCESSED_OPTS" :key="o.value"
                :class="{ active: filterProcessed === o.value }"
                @click="setProcessed(o.value)">{{ t(o.label) }}</button>
      </div>
      <input v-model="site" class="filter-input" :placeholder="t('autofillCapturesPage.sitePlaceholder')"
             @keyup.enter="reload" />
      <button class="btn-secondary" @click="reload">{{ t('autofillCapturesPage.search') }}</button>
    </div>

    <div v-if="errorMsg" class="error-banner">⚠ {{ errorMsg }}</div>
    <div v-if="okMsg" class="ok-banner">✓ {{ okMsg }}</div>

    <div class="main">
      <!-- 抓包列表 -->
      <div class="list-pane panel">
        <div class="list-header"><span>{{ t('autofillCapturesPage.totalCaptures', { total }) }}</span></div>
        <table v-if="captures.length" class="af-table">
          <thead>
            <tr><th>{{ t('autofillCapturesPage.colSite') }}</th><th>{{ t('autofillCapturesPage.colRequest') }}</th><th>{{ t('autofillCapturesPage.colStatus') }}</th><th>{{ t('autofillCapturesPage.colTime') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in captures" :key="c.id"
                :class="{ selected: detail && detail.id === c.id }"
                @click="openDetail(c.id)">
              <td>
                <div class="site">{{ c.site_origin }}</div>
                <div class="dim small url">{{ c.page_url }}</div>
              </td>
              <td>{{ c.request_count }}</td>
              <td>
                <span :class="['badge', c.processed ? 'done' : 'pending']">
                  {{ c.processed ? t('autofillCapturesPage.processed') : t('autofillCapturesPage.pending') }}
                </span>
              </td>
              <td class="dim small">{{ fmtTime(c.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="!loading" class="empty">{{ t('autofillCapturesPage.noCaptures') }}</div>
      </div>

      <!-- 抓包详情：隐私审查 -->
      <div v-if="detail" class="edit-pane panel">
        <div class="edit-header">
          <h3>{{ detail.site_origin }}</h3>
          <div>
            <button class="mini-btn" :disabled="enriching" @click="enrich">
              {{ enriching ? t('autofillCapturesPage.enriching') : t('autofillCapturesPage.enrich') }}
            </button>
            <button class="mini-btn danger" @click="removeCapture">{{ t('autofillCapturesPage.delete') }}</button>
            <button class="mini-btn" @click="detail = null">✕</button>
          </div>
        </div>
        <div class="cap-body">
          <div class="dim small cap-url">
            {{ detail.page_url }} · {{ t('autofillCapturesPage.requestCount', { count: (detail.requests || []).length }) }}
          </div>
          <div v-for="(r, i) in detail.requests" :key="i" class="req">
            <div class="req-head">
              <b>{{ r.method }}</b> {{ r.url }}
              <span class="dim"> → {{ r.status }}</span>
            </div>
            <pre v-if="r.req_body" class="req-body">▶ {{ trunc(r.req_body) }}</pre>
            <pre v-if="r.resp_body" class="req-body resp">◀ {{ trunc(r.resp_body) }}</pre>
          </div>
          <div v-if="!(detail.requests || []).length" class="empty">{{ t('autofillCapturesPage.noRequests') }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'

const { t } = useI18n()

const PROCESSED_OPTS = [
  { value: '', label: 'autofillCapturesPage.optAll' },
  { value: 'false', label: 'autofillCapturesPage.optPending' },
  { value: 'true', label: 'autofillCapturesPage.optProcessed' },
] as const

const loading = ref(false)
const enriching = ref(false)
const errorMsg = ref('')
const okMsg = ref('')
const site = ref('')
const filterProcessed = ref('')
const captures = ref<any[]>([])
const total = ref(0)
const detail = ref<any>(null)

function fmtTime(s: string) { return s ? new Date(s).toLocaleString('zh-CN') : '' }
function trunc(s: string) {
  s = String(s || '')
  return s.length > 4000 ? s.slice(0, 4000) + '\n' + t('autofillCapturesPage.truncated') : s
}

async function reload() {
  loading.value = true
  errorMsg.value = ''
  okMsg.value = ''
  try {
    const r = await api.autofill_listCaptures({
      processed: filterProcessed.value || undefined,
      site: site.value || undefined,
      limit: 100,
    })
    captures.value = r.items || []
    total.value = r.total || 0
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

function setProcessed(v: string) {
  filterProcessed.value = v
  reload()
}

async function openDetail(id: number) {
  errorMsg.value = ''
  try {
    const r = await api.autofill_getCapture(id)
    detail.value = r.capture
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

async function enrich() {
  if (!detail.value) return
  enriching.value = true
  errorMsg.value = ''
  okMsg.value = ''
  try {
    const r = await api.autofill_enrichCapture(detail.value.id)
    okMsg.value = t('autofillCapturesPage.enrichDone', { merged: r.merged })
    await reload()
    detail.value = null
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  } finally {
    enriching.value = false
  }
}

async function removeCapture() {
  if (!detail.value) return
  if (!confirm(t('autofillCapturesPage.deleteConfirm', { id: detail.value.id, site: detail.value.site_origin }))) return
  try {
    await api.autofill_deleteCapture(detail.value.id)
    detail.value = null
    await reload()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

onMounted(reload)
</script>

<style scoped>
.af-page { padding: 16px; background: #0f172a; min-height: 100vh; color: #e2e8f0; }
.page-header { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
.page-header h2 { font-size: 18px; }
.meta { margin-left: auto; display: flex; gap: 12px; }
.dim { color: #64748b; }
.small { font-size: 10px; }

.btn-secondary {
  border: none; border-radius: 5px; padding: 6px 14px; font-size: 12px; cursor: pointer;
  background: #334155; color: #94a3b8;
}
.btn-secondary:disabled { opacity: .5; cursor: not-allowed; }

.panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; padding: 12px; margin-bottom: 12px;
}
.error-banner {
  background: #7f1d1d; color: #fee2e2;
  padding: 8px 12px; border-radius: 5px; font-size: 12px; margin-bottom: 12px;
}
.ok-banner {
  background: #14532d; color: #dcfce7;
  padding: 8px 12px; border-radius: 5px; font-size: 12px; margin-bottom: 12px;
}

.filters { display: flex; gap: 10px; align-items: center; }
.filter-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 10px; border-radius: 5px; font-size: 12px; flex: 1;
}
.seg { display: inline-flex; background: #0f172a; border: 1px solid #334155; border-radius: 5px; overflow: hidden; }
.seg button {
  background: transparent; border: none; color: #94a3b8;
  padding: 6px 12px; font-size: 12px; cursor: pointer;
}
.seg button.active { background: #1d4ed8; color: #fff; }

.main { display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; align-items: start; }
.list-pane, .edit-pane { padding: 0; overflow: hidden; }
.list-header {
  display: flex; justify-content: space-between; padding: 8px 12px;
  border-bottom: 1px solid #334155; font-size: 11px; color: #94a3b8;
}
.af-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.af-table th, .af-table td {
  padding: 6px 10px; text-align: left; vertical-align: top;
  border-bottom: 1px solid #1e293b;
}
.af-table th {
  font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: .4px;
}
.list-pane .af-table tr { cursor: pointer; }
.list-pane .af-table tr:hover td { background: #1e293b; }
.list-pane .af-table tr.selected td { background: #1e3a5f; }
.site { color: #e2e8f0; }
.url { word-break: break-all; }

.badge { font-size: 10px; padding: 2px 7px; border-radius: 10px; }
.badge.done { background: #14532d; color: #86efac; }
.badge.pending { background: #78350f; color: #fcd34d; }

.edit-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px; border-bottom: 1px solid #334155;
}
.edit-header h3 { font-size: 13px; word-break: break-all; }
.mini-btn {
  background: #0f172a; border: 1px solid #334155; color: #94a3b8;
  padding: 3px 8px; border-radius: 3px; font-size: 10px; cursor: pointer; margin-left: 4px;
}
.mini-btn:hover { background: #1e3a5f; color: #e2e8f0; }
.mini-btn:disabled { opacity: .5; cursor: not-allowed; }
.mini-btn.danger { color: #fca5a5; }
.mini-btn.danger:hover { background: #7f1d1d; color: #fee2e2; }

.cap-body { max-height: 72vh; overflow-y: auto; padding: 12px; }
.cap-url { word-break: break-all; margin-bottom: 10px; }
.req { margin-bottom: 12px; border-left: 2px solid #334155; padding-left: 8px; }
.req-head { font-size: 11px; word-break: break-all; margin-bottom: 4px; }
.req-body {
  background: #0f172a; border: 1px solid #1e293b; border-radius: 4px;
  padding: 6px 8px; font-size: 10.5px; color: #94a3b8;
  white-space: pre-wrap; word-break: break-all; margin: 2px 0;
}
.req-body.resp { color: #7dd3fc; }

.empty { text-align: center; padding: 32px; color: #64748b; font-size: 12px; }
</style>
