<template>
  <div class="cr-page">
    <div class="page-header">
      <h2>{{ t('commandRegistryPage.title') }}</h2>
      <span class="subtitle">{{ t('commandRegistryPage.subtitle') }}</span>
      <button class="refresh-btn" @click="load">{{ t('commandRegistryPage.refresh') }}</button>
    </div>

    <div v-if="loading" class="loading">{{ t('commandRegistryPage.loading') }}</div>
    <div v-else-if="error" class="error-msg">{{ error }}</div>

    <template v-else>
      <div v-if="opError" class="op-error-msg">{{ opError }}</div>
      <div v-for="(cmds, groupName) in groups" :key="groupName" class="ext-group">
        <div class="ext-header">
          <span class="ext-badge">{{ cmds[0]?.ext_name }}</span>
          <span class="group-label">{{ groupName }}</span>
          <span class="ext-count">{{ t('commandRegistryPage.commandCount', { n: cmds.length }) }}</span>
        </div>

        <table class="cmd-table">
          <thead>
            <tr>
              <th style="width:120px">{{ t('commandRegistryPage.thPath') }}</th>
              <th style="width:100px">{{ t('commandRegistryPage.thName') }}</th>
              <th style="width:52px">{{ t('commandRegistryPage.thMethod') }}</th>
              <th>{{ t('commandRegistryPage.thDescription') }}</th>
              <th style="width:68px">{{ t('commandRegistryPage.thEnabled') }}</th>
              <th style="min-width:260px">Webhook URL</th>
              <th style="width:52px">{{ t('commandRegistryPage.thSecret') }}</th>
              <th style="width:56px">{{ t('commandRegistryPage.thActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cmd in cmds" :key="cmd.id" :class="{ disabled: !cmd.enabled }">
              <td class="mono">{{ cmd.path }}</td>
              <td>{{ cmd.label }}</td>
              <td><span :class="['method-badge', cmd.method.toLowerCase()]">{{ cmd.method }}</span></td>
              <td class="desc">{{ cmd.description }}</td>
              <td class="center">
                <button
                  :class="['toggle-btn', cmd.enabled ? 'on' : 'off']"
                  :title="cmd.enabled ? t('commandRegistryPage.clickToDisable') : t('commandRegistryPage.clickToEnable')"
                  @click="toggleEnabled(cmd)"
                >{{ cmd.enabled ? t('commandRegistryPage.enabled') : t('commandRegistryPage.disabled') }}</button>
              </td>
              <td>
                <input
                  v-model="cmd._webhookDraft"
                  class="webhook-input"
                  :class="{ 'has-value': cmd._webhookDraft }"
                  :placeholder="t('commandRegistryPage.webhookPlaceholder')"
                  @keydown.enter="saveWebhook(cmd)"
                />
              </td>
              <td>
                <input
                  v-model="cmd._secretDraft"
                  class="webhook-input secret-input"
                  :placeholder="t('commandRegistryPage.optional')"
                  type="password"
                  @keydown.enter="saveWebhook(cmd)"
                />
              </td>
              <td class="center">
                <button
                  class="save-btn"
                  :disabled="saving === cmd.id"
                  @click="saveWebhook(cmd)"
                  :title="saveResult[cmd.id] === 'ok' ? t('commandRegistryPage.saved') : t('commandRegistryPage.saveWebhookSettings')"
                >{{ saving === cmd.id ? '…' : (saveResult[cmd.id] === 'ok' ? '✓' : t('commandRegistryPage.save')) }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'

const { t } = useI18n()

interface CmdRow {
  id: number
  ext_name: string
  path: string
  label: string
  method: string
  description: string
  enabled: number
  webhook_url: string
  webhook_secret: string
  _webhookDraft: string
  _secretDraft: string
}

const loading = ref(true)
const error = ref('')
const opError = ref('')
const groups = reactive<Record<string, CmdRow[]>>({})
const saving = ref<number | null>(null)
const saveResult = reactive<Record<number, 'ok' | 'err'>>({})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.listCommandRegistry()
    // Clear and repopulate groups
    Object.keys(groups).forEach(k => delete groups[k])
    for (const [k, cmds] of Object.entries(data.groups as Record<string, any[]>)) {
      groups[k] = cmds.map(c => ({
        ...c,
        _webhookDraft: c.webhook_url || '',
        _secretDraft: '',  // never prefill secret
      }))
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

let opErrorTimer: ReturnType<typeof setTimeout> | null = null
function showOpError(msg: string) {
  opError.value = msg
  if (opErrorTimer) clearTimeout(opErrorTimer)
  opErrorTimer = setTimeout(() => { opError.value = '' }, 4000)
}

async function toggleEnabled(cmd: CmdRow) {
  const newVal = cmd.enabled ? 0 : 1
  try {
    await api.updateCommandRegistry(cmd.id, { enabled: newVal })
    cmd.enabled = newVal
  } catch (e: any) {
    showOpError(t('commandRegistryPage.operationFailed', { msg: e.message }))
  }
}

async function saveWebhook(cmd: CmdRow) {
  saving.value = cmd.id
  delete saveResult[cmd.id]
  try {
    const fields: Record<string, unknown> = { webhook_url: cmd._webhookDraft }
    if (cmd._secretDraft) fields.webhook_secret = cmd._secretDraft
    await api.updateCommandRegistry(cmd.id, fields)
    cmd.webhook_url = cmd._webhookDraft
    cmd._secretDraft = ''
    saveResult[cmd.id] = 'ok'
    setTimeout(() => delete saveResult[cmd.id], 2000)
  } catch (e: any) {
    saveResult[cmd.id] = 'err'
    showOpError(t('commandRegistryPage.saveFailed', { msg: e.message }))
  } finally {
    saving.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.cr-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.page-header h2 { font-size: 18px; color: #e2e8f0; }
.subtitle { font-size: 12px; color: #475569; flex: 1; }
.refresh-btn {
  background: #334155; color: #94a3b8; border: none;
  padding: 5px 12px; border-radius: 5px; cursor: pointer; font-size: 12px;
}
.refresh-btn:hover { background: #475569; }

.ext-group { margin-bottom: 24px; }
.ext-header {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 8px;
}
.ext-badge {
  background: #1d4ed8; color: white; font-size: 11px; font-weight: 700;
  padding: 2px 10px; border-radius: 10px; font-family: monospace;
}
.ext-count { font-size: 11px; color: #475569; }
.group-label { font-size: 13px; font-weight: 600; color: #cbd5e1; }

.cmd-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.cmd-table th {
  background: #1e293b; color: #64748b; font-weight: 600;
  padding: 7px 10px; text-align: left;
  border-bottom: 1px solid #334155;
}
.cmd-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #1e293b;
  vertical-align: middle;
}
.cmd-table tr:hover td { background: #1e293b40; }
.cmd-table tr.disabled td { opacity: 0.45; }
.cmd-table tr.disabled td.center { opacity: 1; }

.mono { font-family: monospace; font-size: 11px; color: #38bdf8; }
.desc { color: #64748b; }
.center { text-align: center; }

.method-badge {
  font-size: 10px; font-weight: 700; padding: 2px 6px;
  border-radius: 4px; font-family: monospace;
}
.method-badge.get  { background: #065f46; color: #6ee7b7; }
.method-badge.post { background: #1e3a5f; color: #93c5fd; }

.toggle-btn {
  font-size: 11px; font-weight: 600; padding: 3px 10px;
  border: none; border-radius: 10px; cursor: pointer;
}
.toggle-btn.on  { background: #065f46; color: #6ee7b7; }
.toggle-btn.off { background: #3f1515; color: #fca5a5; }
.toggle-btn:hover { filter: brightness(1.2); }

.webhook-input {
  background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
  padding: 5px 8px; border-radius: 4px; font-size: 11px;
  width: 100%; box-sizing: border-box;
}
.webhook-input.has-value { border-color: #3b82f6; }
.webhook-input:focus { outline: none; border-color: #60a5fa; }
.secret-input { width: 80px; }

.save-btn {
  background: #334155; color: #94a3b8; border: none;
  padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
  white-space: nowrap;
}
.save-btn:hover:not(:disabled) { background: #3b82f6; color: white; }
.save-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.loading { color: #475569; padding: 40px; text-align: center; }
.error-msg { color: #f87171; padding: 20px; }
.op-error-msg {
  background: #450a0a; border: 1px solid #7f1d1d; border-radius: 6px;
  color: #fca5a5; font-size: 12px; padding: 8px 14px; margin-bottom: 14px;
}
</style>
