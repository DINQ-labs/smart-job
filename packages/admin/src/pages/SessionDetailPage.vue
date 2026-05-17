<template>
  <div class="session-detail-page">
    <div class="page-header">
      <router-link to="/" class="back-link">{{ t('sessionDetailPage.back') }}</router-link>
      <h2>{{ t('sessionDetailPage.title') }}</h2>
    </div>
    <div v-if="loading" class="loading">{{ t('sessionDetailPage.loading') }}</div>
    <div v-else-if="session" class="content">
      <div class="panel info-panel">
        <div class="info-grid">
          <div class="info-item">
            <span class="k">session_id</span>
            <span class="v mono">{{ session.session_id }}</span>
          </div>
          <div class="info-item">
            <span class="k">{{ t('sessionDetailPage.status') }}</span>
            <span class="v" :class="session.status">{{ session.status }}</span>
          </div>
          <div class="info-item">
            <span class="k">{{ t('sessionDetailPage.account') }}</span>
            <span class="v">{{ session.account_name || t('sessionDetailPage.notLoggedIn') }}</span>
          </div>
          <div class="info-item">
            <span class="k">userId</span>
            <span class="v mono">{{ session.user_id || '-' }}</span>
          </div>
          <div class="info-item">
            <span class="k">{{ t('sessionDetailPage.connectedAt') }}</span>
            <span class="v">{{ fmtTime(session.connected_at) }}</span>
          </div>
          <div class="info-item">
            <span class="k">{{ t('sessionDetailPage.disconnectedAt') }}</span>
            <span class="v">{{ session.disconnected_at ? fmtTime(session.disconnected_at) : '-' }}</span>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-title">{{ t('sessionDetailPage.commandHistory') }}</div>
        <CommandTable :commands="commands" />
      </div>
    </div>
    <div v-else class="not-found">{{ t('sessionDetailPage.notFound') }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import CommandTable from '../components/CommandTable.vue'
import { api } from '../services/api'
import type { SessionInfo, CommandLog } from '../types'

const { t } = useI18n()
const route = useRoute()
const session = ref<SessionInfo | null>(null)
const commands = ref<CommandLog[]>([])
const loading = ref(true)

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  const id = route.params.id as string
  try {
    session.value = await api.getSession(id)
    const data = await api.getCommands({ session_id: id, limit: 100 })
    commands.value = data.commands
  } catch {
    session.value = null
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.session-detail-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.back-link { color: #60a5fa; text-decoration: none; font-size: 13px; }
.page-header h2 { font-size: 18px; color: #e2e8f0; }
.panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 14px;
}
.panel-title { font-size: 13px; font-weight: 600; color: #94a3b8; margin-bottom: 10px; }
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.info-item { display: flex; flex-direction: column; gap: 3px; }
.k { font-size: 11px; color: #64748b; }
.v { font-size: 13px; color: #e2e8f0; word-break: break-all; }
.v.mono { font-family: monospace; }
.v.connected { color: #4ade80; }
.v.disconnected { color: #64748b; }
.loading, .not-found { color: #475569; text-align: center; padding: 40px; }
</style>
