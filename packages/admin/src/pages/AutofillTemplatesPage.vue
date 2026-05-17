<template>
  <div class="af-page">
    <div class="page-header">
      <h2>📝 {{ t('autofillTemplatesPage.title') }}</h2>
      <div class="meta">
        <button class="btn-secondary" :disabled="loading" @click="reload">
          {{ loading ? t('autofillTemplatesPage.loading') : t('autofillTemplatesPage.refresh') }}
        </button>
      </div>
    </div>

    <!-- 概览 -->
    <div v-if="stats" class="stats panel">
      <div class="stat"><div class="sv">{{ stats.templates }}</div><div class="sl">{{ t('autofillTemplatesPage.statTemplates') }}</div></div>
      <div class="stat"><div class="sv">{{ stats.fields_learned }}</div><div class="sl">{{ t('autofillTemplatesPage.statFieldsLearned') }}</div></div>
      <div class="stat"><div class="sv">{{ stats.sites }}</div><div class="sl">{{ t('autofillTemplatesPage.statSites') }}</div></div>
      <div class="stat">
        <div class="sv">{{ stats.captures_pending }}/{{ stats.captures_total }}</div>
        <div class="sl">{{ t('autofillTemplatesPage.statCaptures') }}</div>
      </div>
      <div class="stat">
        <div class="sv">{{ Math.round((stats.match_hit_rate || 0) * 100) }}%</div>
        <div class="sl">{{ t('autofillTemplatesPage.statHitRate') }}</div>
      </div>
      <div class="stat"><div class="sv">{{ stats.match_calls }}</div><div class="sl">{{ t('autofillTemplatesPage.statMatchCalls') }}</div></div>
    </div>

    <div class="filters panel">
      <input v-model="q" class="filter-input" :placeholder="t('autofillTemplatesPage.filterPh')"
             @keyup.enter="reload" />
      <button class="btn-secondary" @click="reload">{{ t('autofillTemplatesPage.search') }}</button>
    </div>

    <div v-if="errorMsg" class="error-banner">⚠ {{ errorMsg }}</div>

    <div class="main">
      <!-- 模板列表 -->
      <div class="list-pane panel">
        <div class="list-header"><span>{{ t('autofillTemplatesPage.totalTemplates', { n: total }) }}</span></div>
        <table v-if="templates.length" class="af-table">
          <thead>
            <tr><th>{{ t('autofillTemplatesPage.thSite') }}</th><th>{{ t('autofillTemplatesPage.thPath') }}</th><th>{{ t('autofillTemplatesPage.thFields') }}</th><th>{{ t('autofillTemplatesPage.thObserved') }}</th><th>{{ t('autofillTemplatesPage.thUsers') }}</th><th>{{ t('autofillTemplatesPage.thUpdated') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="t in templates" :key="t.id"
                :class="{ selected: detail && detail.id === t.id }"
                @click="openDetail(t.id)">
              <td class="site">{{ t.site_origin }}</td>
              <td><code class="path">{{ t.path_template }}</code></td>
              <td>{{ t.field_count }}</td>
              <td>{{ t.observed_count }}</td>
              <td>{{ t.distinct_users }}</td>
              <td class="dim small">{{ fmtTime(t.updated_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="!loading" class="empty">{{ t('autofillTemplatesPage.noTemplates') }}</div>
      </div>

      <!-- 模板详情：字段级人工纠正 -->
      <div v-if="detail" class="edit-pane panel">
        <div class="edit-header">
          <h3>{{ detail.site_origin }}<span class="dim small"> {{ detail.path_template }}</span></h3>
          <div>
            <button class="mini-btn danger" @click="removeTemplate">{{ t('autofillTemplatesPage.deleteTemplate') }}</button>
            <button class="mini-btn" @click="detail = null">✕</button>
          </div>
        </div>
        <div class="fields-wrap">
          <table class="af-table">
            <thead>
              <tr>
                <th>{{ t('autofillTemplatesPage.fhField') }}</th><th>profile_key</th><th>widget</th>
                <th>{{ t('autofillTemplatesPage.fhConfidence') }}</th><th>{{ t('autofillTemplatesPage.fhFill') }}</th><th>{{ t('autofillTemplatesPage.fhSeen') }}</th><th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in detail.fields" :key="f.ident">
                <td>
                  <div class="ident">{{ f.ident }}</div>
                  <div class="dim small">{{ (f.labels && f.labels[0]) || '' }}</div>
                </td>
                <td><input v-model="f.profile_key" class="cell-input" placeholder="—" /></td>
                <td>
                  <select v-model="f.widget" class="cell-input">
                    <option :value="null">—</option>
                    <option v-for="w in WIDGETS" :key="w" :value="w">{{ w }}</option>
                  </select>
                </td>
                <td class="dim small">
                  k {{ pct(f.profile_key_confidence) }}<br />w {{ pct(f.widget_confidence) }}
                </td>
                <td class="dim small">{{ f.fill_ok || 0 }}/{{ f.fill_fail || 0 }}</td>
                <td class="dim small">{{ f.times_seen || 0 }}</td>
                <td><button class="mini-btn" @click="saveField(f)">{{ t('autofillTemplatesPage.saveField') }}</button></td>
              </tr>
            </tbody>
          </table>
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

const WIDGETS = [
  'native-select', 'combobox', 'typeahead', 'aria-radio', 'contenteditable',
  'file', 'date-native', 'date-text', 'checkbox', 'radio', 'text',
]

const loading = ref(false)
const errorMsg = ref('')
const q = ref('')
const templates = ref<any[]>([])
const total = ref(0)
const stats = ref<any>(null)
const detail = ref<any>(null)

function pct(v: number) { return Math.round((v || 0) * 100) + '%' }
function fmtTime(s: string) { return s ? new Date(s).toLocaleString('zh-CN') : '' }

async function reload() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [tpl, st] = await Promise.all([
      api.autofill_listTemplates({ q: q.value, limit: 100 }),
      api.autofill_stats(),
    ])
    templates.value = tpl.items || []
    total.value = tpl.total || 0
    stats.value = st.stats || null
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

async function openDetail(id: number) {
  errorMsg.value = ''
  try {
    const r = await api.autofill_getTemplate(id)
    detail.value = r.template
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

async function saveField(f: any) {
  try {
    await api.autofill_patchTemplateField(detail.value.id, {
      ident: f.ident,
      profile_key: f.profile_key || '',
      widget: f.widget || '',
    })
    errorMsg.value = ''
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

async function removeTemplate() {
  if (!detail.value) return
  if (!confirm(t('autofillTemplatesPage.confirmDelete', { site: detail.value.site_origin, path: detail.value.path_template }))) return
  try {
    await api.autofill_deleteTemplate(detail.value.id)
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

.stats { display: flex; gap: 28px; }
.stat .sv { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.stat .sl { font-size: 11px; color: #64748b; margin-top: 2px; }

.filters { display: flex; gap: 10px; }
.filter-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 10px; border-radius: 5px; font-size: 12px;
}
.filters .filter-input { flex: 1; }

.main { display: grid; grid-template-columns: 1.1fr 1.3fr; gap: 12px; align-items: start; }
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
.site { color: #e2e8f0; word-break: break-all; }
code.path { background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #fbbf24; }

.edit-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px; border-bottom: 1px solid #334155;
}
.edit-header h3 { font-size: 13px; word-break: break-all; }
.fields-wrap { max-height: 70vh; overflow-y: auto; }
.ident { font-weight: 500; word-break: break-all; }
.cell-input {
  width: 100%; background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 4px 6px; border-radius: 4px; font-size: 11px;
}
.mini-btn {
  background: #0f172a; border: 1px solid #334155; color: #94a3b8;
  padding: 3px 8px; border-radius: 3px; font-size: 10px; cursor: pointer; margin-left: 4px;
}
.mini-btn:hover { background: #1e3a5f; color: #e2e8f0; }
.mini-btn.danger { color: #fca5a5; }
.mini-btn.danger:hover { background: #7f1d1d; color: #fee2e2; }

.empty { text-align: center; padding: 32px; color: #64748b; font-size: 12px; }
</style>
