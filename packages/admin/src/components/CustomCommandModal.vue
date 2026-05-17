<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-header">
        <span>{{ t('customCommandModal.title') }}</span>
        <!-- CLI / 扩展 切换 -->
        <label class="cli-toggle" :title="isCli ? t('customCommandModal.cliModeTip') : t('customCommandModal.extModeTip')">
          <input type="checkbox" v-model="cliMode" @change="onModeChange" />
          <span class="cli-toggle-label" :class="{ active: cliMode }">{{ t('customCommandModal.cliMode') }}</span>
        </label>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">
        <!-- CLI 模式：cookie_id 输入 -->
        <div v-if="cliMode" class="field">
          <label>Cookie ID <span class="required">*</span></label>
          <input
            v-model="cookieId"
            class="cookie-id-input"
            :placeholder="t('customCommandModal.cookieIdPh')"
            spellcheck="false"
          />
        </div>

        <!-- 命令选择 -->
        <div class="field">
          <label>{{ t('customCommandModal.selectCommand') }}</label>
          <select v-model="selectedKey" @change="onSelect" :disabled="loadingCmds">
            <option value="">{{ loadingCmds ? t('customCommandModal.loading') : t('customCommandModal.selectPlaceholder') }}</option>
            <optgroup v-for="(cmds, group) in groups" :key="group" :label="group">
              <option
                v-for="cmd in cmds"
                :key="cmd.path"
                :value="cmd.path"
                :disabled="!cmd.enabled"
              >{{ cmd.label }}</option>
            </optgroup>
          </select>
        </div>

        <!-- 命令信息 -->
        <div v-if="current" class="cmd-info">
          <span class="cmd-path">{{ current.path }}</span>
          <span class="cmd-desc">{{ current.description }}</span>
        </div>

        <!-- Body 编辑（仅有参数的命令显示） -->
        <div v-if="current && bodyTemplate" class="field">
          <label>{{ t('customCommandModal.paramsLabel') }}</label>
          <textarea v-model="bodyText" rows="4" spellcheck="false"></textarea>
        </div>
        <div v-else-if="current && !bodyTemplate" class="no-body-hint">
          {{ t('customCommandModal.noParams') }}
        </div>

        <!-- 二维码图片（generate_qrcode / capture_qrcode 命令专用） -->
        <div v-if="qrcodeDataUrl" class="qr-wrap">
          <img :src="qrcodeDataUrl" :alt="t('customCommandModal.qrcodeAlt')" class="qr-img" />
          <p class="qr-hint">{{ t('customCommandModal.qrcodeHint') }}</p>
        </div>

        <!-- 结果 JSON -->
        <div v-if="result !== null" class="result">
          <pre>{{ JSON.stringify(result, null, 2) }}</pre>
        </div>
        <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
      </div>

      <div class="modal-footer">
        <button class="btn-cancel" @click="$emit('close')">{{ t('customCommandModal.close') }}</button>
        <button class="btn-send" :disabled="loading || !current" @click="send">
          {{ loading ? t('customCommandModal.sending') : t('customCommandModal.send') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import { EXT_NAME_CLI } from '../constants'

const { t } = useI18n()

const props = defineProps<{ sessionId: string; extName?: string }>()
defineEmits<{ close: [] }>()

// 部分命令需要 JSON 参数模板（纯 UI 提示，不存 DB）
const BODY_TEMPLATES: Record<string, string> = {
  'boss/search_jobs':        '{"keyword": "Python", "city": 101010100, "page": 1}',
  'boss/get_job_detail':     '{"encrypt_job_id": ""}',
  'boss/start_chat':         '{"encrypt_job_id": ""}',
  'boss/send_message':       '{"encrypt_job_id": "", "content": "你好"}',
  'boss/get_chat_history':   '{"encrypt_job_id": "", "max_msg_id": ""}',
  'boss/search_candidates':  '{"keyword": "Python", "city": 101010100, "page": 1}',
  'boss/get_candidate_detail': '{"encrypt_uid": ""}',
  'boss/contact_candidate':  '{"encrypt_uid": "", "job_id": ""}',
}

interface CmdEntry {
  path: string
  label: string
  method: string
  description: string
  enabled: boolean
  cmd_group: string
}

// CLI 模式状态：初始值由 extName 决定
const cliMode = ref(props.extName === EXT_NAME_CLI)
const isCli = computed(() => cliMode.value)
const cookieId = ref('')

const loadingCmds = ref(true)
const groups = ref<Record<string, CmdEntry[]>>({})

const selectedKey = ref('')
const bodyText = ref('')
const result = ref<unknown>(null)
const errorMsg = ref('')
const loading = ref(false)

const current = computed(() =>
  Object.values(groups.value).flat().find(c => c.path === selectedKey.value) ?? null
)

const bodyTemplate = computed(() =>
  current.value ? (BODY_TEMPLATES[current.value.path] ?? '') : ''
)

// 从结果中提取二维码 data URL
const qrcodeDataUrl = computed<string | null>(() => {
  if (!result.value || typeof result.value !== 'object') return null
  const r = result.value as Record<string, unknown>
  const envelope = r.result as Record<string, unknown> | undefined
  const inner = (envelope?.data ?? envelope ?? r) as Record<string, unknown>
  const url = inner?.qrcode_dataurl
  return typeof url === 'string' && url.startsWith('data:') ? url : null
})

async function loadCommands() {
  loadingCmds.value = true
  groups.value = {}
  selectedKey.value = ''
  result.value = null
  errorMsg.value = ''
  try {
    const extName = isCli.value ? EXT_NAME_CLI : (props.extName ?? undefined)
    const res = await api.listCommandRegistry(extName)
    groups.value = res.groups as Record<string, CmdEntry[]>
  } catch (e: any) {
    errorMsg.value = t('customCommandModal.loadCommandsFailed') + e.message
  } finally {
    loadingCmds.value = false
  }
}

onMounted(loadCommands)

function onModeChange() {
  loadCommands()
}

function onSelect() {
  result.value = null
  errorMsg.value = ''
  bodyText.value = bodyTemplate.value
}

async function send() {
  if (!current.value) return
  if (isCli.value && !cookieId.value.trim()) {
    errorMsg.value = t('customCommandModal.cookieIdRequired')
    return
  }
  errorMsg.value = ''
  result.value = null
  loading.value = true
  try {
    let body: Record<string, unknown> = {}
    if (bodyText.value.trim()) {
      try { body = JSON.parse(bodyText.value) } catch {
        throw new Error(t('customCommandModal.invalidJson'))
      }
    }
    // CLI 模式：将 cookie_id 注入 body
    if (isCli.value) {
      body = { ...body, cookie_id: cookieId.value.trim() }
    }
    const res = await api.sendCustomCommand(
      props.sessionId,
      current.value.method,
      current.value.path,
      Object.keys(body).length ? body : undefined,
    )
    result.value = res
  } catch (e: any) {
    errorMsg.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.modal {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px;
  width: 500px; max-height: 82vh; display: flex; flex-direction: column;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid #334155;
  font-weight: 600; color: #e2e8f0;
}
.close-btn { background: none; border: none; color: #64748b; cursor: pointer; font-size: 16px; }
.modal-body { padding: 16px; overflow-y: auto; flex: 1; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 11px; color: #64748b; margin-bottom: 4px; }
.field select, .field textarea {
  width: 100%; background: #0f172a; border: 1px solid #334155; border-radius: 5px;
  color: #e2e8f0; padding: 7px 9px; font-size: 13px; box-sizing: border-box;
}
.field textarea { font-family: monospace; resize: vertical; }
.cmd-info {
  display: flex; align-items: baseline; gap: 10px;
  background: #0f172a; border: 1px solid #1e3a5f; border-radius: 5px;
  padding: 7px 10px; margin-bottom: 12px;
}
.cmd-path { font-family: monospace; font-size: 12px; color: #38bdf8; white-space: nowrap; }
.cmd-desc { font-size: 11px; color: #64748b; }
.no-body-hint { font-size: 12px; color: #475569; margin-bottom: 12px; }
.result { margin-top: 10px; }
.result pre {
  background: #0f172a; border: 1px solid #334155; border-radius: 5px;
  padding: 10px; font-size: 11px; color: #4ade80; overflow-x: auto; max-height: 220px;
}
.qr-wrap {
  margin: 12px 0;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 16px;
}
.qr-img { width: 220px; height: 220px; display: block; border-radius: 4px; }
.qr-hint { font-size: 12px; color: #64748b; }
.error-msg { color: #f87171; font-size: 12px; margin-top: 8px; }
.modal-footer {
  padding: 12px 16px; border-top: 1px solid #334155;
  display: flex; gap: 8px; justify-content: flex-end;
}
.btn-cancel { background: #334155; color: #94a3b8; border: none; padding: 7px 16px; border-radius: 5px; cursor: pointer; }
.btn-send { background: #3b82f6; color: white; border: none; padding: 7px 16px; border-radius: 5px; cursor: pointer; font-weight: 600; }
.btn-send:disabled { opacity: 0.5; cursor: not-allowed; }
.cli-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; margin-left: auto; margin-right: 8px; }
.cli-toggle input[type="checkbox"] { accent-color: #3b82f6; width: 14px; height: 14px; cursor: pointer; }
.cli-toggle-label { font-size: 11px; font-weight: 500; color: #64748b; transition: color 0.15s; }
.cli-toggle-label.active { color: #38bdf8; }
.cookie-id-input {
  width: 100%; background: #0f172a; border: 1px solid #334155; border-radius: 5px;
  color: #e2e8f0; padding: 7px 9px; font-size: 13px; box-sizing: border-box;
  font-family: monospace;
}
.cookie-id-input:focus { outline: none; border-color: #38bdf8; }
.required { color: #f87171; margin-left: 2px; }
</style>
