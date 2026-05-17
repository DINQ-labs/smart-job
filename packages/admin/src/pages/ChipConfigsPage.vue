<template>
  <div class="cc-page">
    <div class="page-header">
      <h2>⚡ {{ t('chipConfigsPage.title') }}</h2>
      <div class="meta">
        <span v-if="version" class="dim">version: {{ version }}</span>
        <button class="btn-secondary" :disabled="loading" @click="reload">
          {{ loading ? t('chipConfigsPage.loading') : t('chipConfigsPage.refresh') }}
        </button>
        <button class="btn-primary" @click="startCreate">{{ t('chipConfigsPage.addChip') }}</button>
      </div>
    </div>

    <!-- 过滤 -->
    <div class="filters panel">
      <div class="filter-group">
        <label>{{ t('chipConfigsPage.role') }}</label>
        <div class="seg">
          <button
            v-for="r in ROLES" :key="r.value"
            :class="{ active: filterRole === r.value }"
            @click="setRole(r.value)"
          >{{ t(r.labelKey) }}</button>
        </div>
      </div>
      <div class="filter-group">
        <label>{{ t('chipConfigsPage.platform') }}</label>
        <div class="seg">
          <button
            v-for="p in PLATFORMS" :key="p.value"
            :class="{ active: filterPlatform === p.value }"
            @click="setPlatform(p.value)"
          >{{ p.label }}</button>
        </div>
      </div>
      <div class="filter-group end">
        <button class="btn-secondary" :disabled="!previewing" @click="togglePreview">
          {{ previewing ? t('chipConfigsPage.hidePreview') : t('chipConfigsPage.showPreview') }}
        </button>
      </div>
    </div>

    <!-- 提示 -->
    <div v-if="errorMsg" class="error-banner">⚠ {{ errorMsg }}</div>

    <!-- 主区域:左列表 + 右编辑面板 -->
    <div class="main">
      <div class="list-pane panel">
        <div class="list-header">
          <span>{{ t('chipConfigsPage.totalCount', { n: filteredChips.length }) }}</span>
          <span class="dim small">{{ t('chipConfigsPage.sortHint') }}</span>
        </div>
        <table v-if="filteredChips.length" class="cc-table">
          <thead>
            <tr>
              <th class="col-order">{{ t('chipConfigsPage.thOrder') }}</th>
              <th class="col-tog">{{ t('chipConfigsPage.thEnabled') }}</th>
              <th class="col-icon"></th>
              <th class="col-label">label_zh</th>
              <th class="col-key">key</th>
              <th class="col-w">{{ t('chipConfigsPage.thWeight') }}</th>
              <th class="col-grp">{{ t('chipConfigsPage.thGroup') }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(chip, idx) in filteredChips" :key="chip.id"
              :class="{ selected: editing?.id === chip.id, disabled: !chip.enabled }"
              @click="select(chip)"
            >
              <td class="col-order">
                <button class="mini-btn" :disabled="idx === 0" :title="t('chipConfigsPage.moveUp')" @click.stop="moveUp(idx)">▲</button>
                <button class="mini-btn" :disabled="idx === filteredChips.length - 1" :title="t('chipConfigsPage.moveDown')" @click.stop="moveDown(idx)">▼</button>
              </td>
              <td class="col-tog">
                <input type="checkbox" :checked="chip.enabled" @click.stop @change="toggleEnabled(chip)" />
              </td>
              <td class="col-icon">{{ chip.icon }}</td>
              <td class="col-label">
                <div class="cell-label">{{ chip.label_zh }}</div>
                <div class="cell-en dim small">{{ chip.label_en }}</div>
              </td>
              <td class="col-key">
                <code class="key">{{ chip.key }}</code>
                <span v-if="ruleRefsForKey(chip.key).length" class="rule-ref"
                      :title="t('chipConfigsPage.referencedByRules', { rules: ruleRefsForKey(chip.key).join(', ') })">⛓ {{ ruleRefsForKey(chip.key).length }}</span>
              </td>
              <td class="col-w">{{ chip.weight }}</td>
              <td class="col-grp">{{ chip.grp }}</td>
              <td class="col-act">
                <button class="mini-btn danger" :disabled="ruleRefsForKey(chip.key).length > 0"
                        :title="ruleRefsForKey(chip.key).length > 0 ? t('chipConfigsPage.deleteBlocked') : t('chipConfigsPage.delete')"
                        @click.stop="onDelete(chip)">✕</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="!loading" class="empty">
          {{ t('chipConfigsPage.emptyList', { role: filterRole, platform: filterPlatform }) }}
        </div>
      </div>

      <!-- 编辑面板 -->
      <div class="edit-pane panel" v-if="editing || creating">
        <div class="edit-header">
          <h3>{{ creating ? t('chipConfigsPage.createTitle') : t('chipConfigsPage.editTitle') }}</h3>
          <button class="mini-btn" @click="cancelEdit">✕</button>
        </div>

        <div class="form">
          <div class="form-row">
            <label>key <span class="dim small">{{ t('chipConfigsPage.keyHint') }}</span></label>
            <input v-model="form.key" :disabled="!creating" placeholder="search_jobs" class="filter-input" />
          </div>
          <div class="form-row two">
            <div>
              <label>role</label>
              <select v-model="form.role" :disabled="!creating" class="filter-input">
                <option value="jobseeker">jobseeker</option>
                <option value="recruiter">recruiter</option>
              </select>
            </div>
            <div>
              <label>platform</label>
              <select v-model="form.platform" :disabled="!creating" class="filter-input">
                <option value="boss">boss</option>
                <option value="linkedin">linkedin</option>
                <option value="indeed">indeed</option>
              </select>
            </div>
          </div>
          <div class="form-row two">
            <div>
              <label>label_zh *</label>
              <input v-model="form.label_zh" :placeholder="t('chipConfigsPage.labelZhPh')" class="filter-input" />
            </div>
            <div>
              <label>label_en *</label>
              <input v-model="form.label_en" placeholder="Search jobs" class="filter-input" />
            </div>
          </div>
          <div class="form-row two">
            <div>
              <label>send_text_zh <span class="dim small">{{ t('chipConfigsPage.sendTextHint') }}</span></label>
              <input v-model="form.send_text_zh" :placeholder="t('chipConfigsPage.leaveEmpty')" class="filter-input" />
            </div>
            <div>
              <label>send_text_en</label>
              <input v-model="form.send_text_en" :placeholder="t('chipConfigsPage.leaveEmpty')" class="filter-input" />
            </div>
          </div>
          <div class="form-row three">
            <div>
              <label>icon</label>
              <input v-model="form.icon" placeholder="🔍" class="filter-input" />
            </div>
            <div>
              <label>weight 0-100</label>
              <input v-model.number="form.weight" type="number" min="0" max="100" class="filter-input" />
            </div>
            <div>
              <label>grp</label>
              <select v-model="form.grp" class="filter-input">
                <option value="default">default</option>
                <option value="onboarding">onboarding</option>
                <option value="shortcut">shortcut</option>
                <option value="task">task</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <label>
              <input type="checkbox" v-model="form.enabled" /> {{ t('chipConfigsPage.enabledLabel') }}
            </label>
          </div>
          <div class="form-row">
            <label>description <span class="dim small">{{ t('chipConfigsPage.descriptionHint') }}</span></label>
            <textarea v-model="form.description" rows="2" class="filter-input" />
          </div>
          <div class="form-actions">
            <button class="btn-secondary" @click="cancelEdit">{{ t('chipConfigsPage.cancel') }}</button>
            <button class="btn-primary" :disabled="saving" @click="save">
              {{ saving ? t('chipConfigsPage.saving') : (creating ? t('chipConfigsPage.create') : t('chipConfigsPage.save')) }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 实时预览(应用规则后的最终 chip 列表) -->
    <div v-if="previewing" class="panel">
      <h3 class="dim small">🔮 {{ t('chipConfigsPage.previewTitle', { role: filterRole, platform: filterPlatform }) }}
         <span v-if="preview?.triggered_rules?.length" class="rule-list">
           {{ t('chipConfigsPage.triggeredRules', { rules: preview.triggered_rules.join(', ') }) }}
         </span>
      </h3>
      <div v-if="preview?.chips" class="preview-chips">
        <span v-for="(c, i) in preview.chips" :key="i" class="preview-chip"
              :class="{ primary: i === 0 }">{{ c.label }}</span>
      </div>
      <div v-else class="empty">…</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { ChipConfig } from '../types'

const { t } = useI18n()

const ROLES = [
  { value: 'jobseeker', labelKey: 'chipConfigsPage.roleJobseeker' },
  { value: 'recruiter', labelKey: 'chipConfigsPage.roleRecruiter' },
] as const
const PLATFORMS = [
  { value: 'boss',     label: 'Boss直聘' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'indeed',   label: 'Indeed' },
] as const

const filterRole     = ref<'jobseeker' | 'recruiter'>('jobseeker')
const filterPlatform = ref('boss')

const chips    = ref<ChipConfig[]>([])
const version  = ref(0)
const loading  = ref(false)
const saving   = ref(false)
const errorMsg = ref('')
const editing  = ref<ChipConfig | null>(null)
const creating = ref(false)
const form     = ref<Partial<ChipConfig>>({})
const previewing = ref(false)
const preview  = ref<{ chips: any[]; triggered_rules: string[] } | null>(null)

// 规则元数据从后端 GET /admin/chip-rules 拉(动态,不再 hardcode)
const ruleRefs = ref<Record<string, string[]>>({})           // key → [rule_id, ...]
const allRules = ref<Array<{ id: string; description: string; references: string[];
                              role: string | null; platform: string | null; enabled: boolean }>>([])

function ruleRefsForKey(key: string): string[] {
  return ruleRefs.value[key] || []
}

async function loadRuleRefs() {
  try {
    const data = await api.agent_listChipRules()
    allRules.value = data.rules
    const refs: Record<string, string[]> = {}
    for (const rule of data.rules) {
      // 只统计跟当前 filter 匹配的规则(role/platform 为 null = 任意)
      if (rule.role && rule.role !== filterRole.value) continue
      if (rule.platform && rule.platform !== filterPlatform.value) continue
      for (const k of rule.references) {
        if (!refs[k]) refs[k] = []
        refs[k].push(rule.id)
      }
    }
    ruleRefs.value = refs
  } catch (_) {
    ruleRefs.value = {}
  }
}

const filteredChips = computed(() => {
  return chips.value
    .filter(c => c.role === filterRole.value && c.platform === filterPlatform.value)
})

async function reload() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [data, _] = await Promise.all([
      api.agent_listChips({ role: filterRole.value, platform: filterPlatform.value }),
      loadRuleRefs(),
    ])
    chips.value = data.chips
    version.value = data.version
    if (previewing.value) await reloadPreview()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

async function reloadPreview() {
  try {
    preview.value = await api.agent_previewChips({
      role: filterRole.value, platform: filterPlatform.value, lang: 'zh',
    })
  } catch (e) {
    preview.value = null
  }
}

function togglePreview() {
  previewing.value = !previewing.value
  if (previewing.value) reloadPreview()
}

function setRole(r: 'jobseeker' | 'recruiter') {
  filterRole.value = r
  cancelEdit()
  reload()
}
function setPlatform(p: string) {
  filterPlatform.value = p
  cancelEdit()
  reload()
}

function select(chip: ChipConfig) {
  creating.value = false
  editing.value = chip
  form.value = { ...chip }
}
function startCreate() {
  editing.value = null
  creating.value = true
  form.value = {
    key: '', role: filterRole.value, platform: filterPlatform.value,
    label_zh: '', label_en: '', send_text_zh: '', send_text_en: '',
    icon: '', weight: 50, grp: 'default', enabled: true,
    sort_order: 0, description: '',
  }
}
function cancelEdit() {
  editing.value = null
  creating.value = false
  form.value = {}
}

async function save() {
  if (!form.value.label_zh || !form.value.label_en) {
    errorMsg.value = t('chipConfigsPage.labelRequired')
    return
  }
  if (creating.value && !form.value.key) {
    errorMsg.value = t('chipConfigsPage.keyRequired')
    return
  }
  saving.value = true
  errorMsg.value = ''
  try {
    if (creating.value) {
      await api.agent_createChip(form.value as any)
    } else if (editing.value) {
      const patch = { ...form.value }
      delete (patch as any).id; delete (patch as any).key
      delete (patch as any).role; delete (patch as any).platform
      delete (patch as any).updated_at; delete (patch as any).updated_by
      await api.agent_patchChip(editing.value.id, patch)
    }
    cancelEdit()
    await reload()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(chip: ChipConfig) {
  try {
    await api.agent_patchChip(chip.id, { enabled: !chip.enabled })
    await reload()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

async function onDelete(chip: ChipConfig) {
  if (!confirm(t('chipConfigsPage.confirmDelete', { label: chip.label_zh, key: chip.key }))) return
  try {
    await api.agent_deleteChip(chip.id)
    if (editing.value?.id === chip.id) cancelEdit()
    await reload()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

async function moveUp(idx: number) {
  if (idx === 0) return
  await swapSortOrder(filteredChips.value[idx], filteredChips.value[idx - 1])
}
async function moveDown(idx: number) {
  if (idx === filteredChips.value.length - 1) return
  await swapSortOrder(filteredChips.value[idx], filteredChips.value[idx + 1])
}
async function swapSortOrder(a: ChipConfig, b: ChipConfig) {
  // 简单交换 sort_order;weight 不动(weight 是粗排,sort_order 是同 weight 下细排)
  try {
    await api.agent_reorderChips([
      { id: a.id, sort_order: b.sort_order },
      { id: b.id, sort_order: a.sort_order },
    ])
    await reload()
  } catch (e: any) {
    errorMsg.value = e?.message || String(e)
  }
}

onMounted(reload)
watch([filterRole, filterPlatform], () => { /* setRole / setPlatform 已 reload */ })
</script>

<style scoped>
.cc-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex; align-items: center; gap: 16px; margin-bottom: 16px;
}
.page-header h2 { font-size: 18px; }
.meta { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.dim { color: #64748b; font-size: 11px; }
.dim.small { font-size: 10px; }

.btn-primary, .btn-secondary {
  border: none; border-radius: 5px; padding: 6px 14px; font-size: 12px; cursor: pointer;
}
.btn-primary { background: #3b82f6; color: white; }
.btn-secondary { background: #334155; color: #94a3b8; }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.panel {
  background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; padding: 12px; margin-bottom: 12px;
}
.error-banner {
  background: #7f1d1d; color: #fee2e2;
  padding: 8px 12px; border-radius: 5px; font-size: 12px; margin-bottom: 12px;
}
.filters { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
.filter-group { display: flex; flex-direction: column; gap: 4px; }
.filter-group.end { margin-left: auto; }
.filter-group label { font-size: 11px; color: #64748b; }
.filter-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 10px; border-radius: 5px; font-size: 12px;
}
.filter-input:disabled { opacity: 0.5; }
.seg { display: inline-flex; background: #0f172a; border: 1px solid #334155; border-radius: 5px; overflow: hidden; }
.seg button {
  background: transparent; border: none; color: #94a3b8;
  padding: 6px 12px; font-size: 12px; cursor: pointer;
}
.seg button.active { background: #1d4ed8; color: white; }

.main {
  display: grid; grid-template-columns: 1.3fr 1fr; gap: 12px;
}
.list-pane { padding: 0; overflow: hidden; }
.list-header {
  display: flex; justify-content: space-between; padding: 8px 12px;
  border-bottom: 1px solid #334155; font-size: 11px; color: #94a3b8;
}
.cc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.cc-table th, .cc-table td { padding: 6px 10px; text-align: left; vertical-align: middle; border-bottom: 1px solid #1e293b; }
.cc-table th { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.4px; }
.cc-table tr { cursor: pointer; transition: background 0.1s; }
.cc-table tr:hover td { background: #1e293b; }
.cc-table tr.selected td { background: #1e3a5f; }
.cc-table tr.disabled td { opacity: 0.45; }
.col-order { width: 60px; white-space: nowrap; }
.col-tog { width: 32px; }
.col-icon { width: 32px; text-align: center; font-size: 14px; }
.col-label .cell-label { font-weight: 500; }
.col-label .cell-en { font-size: 10.5px; }
.col-key { width: 200px; }
.col-key .key { background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #fbbf24; font-size: 10.5px; }
.col-key .rule-ref { margin-left: 6px; font-size: 10px; color: #60a5fa; cursor: help; }
.col-w, .col-grp { width: 50px; }
.col-grp { color: #94a3b8; }
.col-act { width: 36px; text-align: right; }

.mini-btn {
  background: #0f172a; border: 1px solid #334155; color: #94a3b8;
  padding: 2px 6px; border-radius: 3px; font-size: 10px; cursor: pointer;
  margin: 0 1px;
}
.mini-btn:hover:not(:disabled) { background: #1e3a5f; color: #e2e8f0; }
.mini-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.mini-btn.danger { color: #fca5a5; }
.mini-btn.danger:hover:not(:disabled) { background: #7f1d1d; color: #fee2e2; }

.edit-pane { padding: 0; }
.edit-header { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid #334155; }
.edit-header h3 { font-size: 13px; }
.form { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.form-row { display: flex; flex-direction: column; gap: 4px; }
.form-row.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-row.three { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
.form-row label { font-size: 11px; color: #64748b; }
.form-row label input[type="checkbox"] { margin-right: 4px; vertical-align: middle; }
.form-actions { display: flex; gap: 8px; justify-content: flex-end; padding-top: 8px; border-top: 1px solid #334155; }

.empty { text-align: center; padding: 32px; color: #64748b; font-size: 12px; }

.preview-chips { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 8px; }
.preview-chip {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 6px 12px; border-radius: 14px; font-size: 12px;
}
.preview-chip.primary { background: #1d4ed8; color: white; border-color: #1d4ed8; }
.rule-list { color: #60a5fa; margin-left: 8px; font-weight: 400; }
</style>
