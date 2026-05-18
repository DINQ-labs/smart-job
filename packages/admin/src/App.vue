<template>
  <div class="app-layout">
    <nav v-if="auth.authenticated" class="sidebar">
      <div class="logo">SmartJob</div>
      <router-link to="/" class="nav-item" active-class="active">
        <span class="nav-icon">📊</span> {{ t('app.nav.dashboard') }}
      </router-link>
      <router-link to="/commands" class="nav-item" active-class="active">
        <span class="nav-icon">📋</span> {{ t('app.nav.commands') }}
      </router-link>
      <router-link to="/error-logs" class="nav-item" active-class="active">
        <span class="nav-icon">🚨</span> {{ t('app.nav.errorLogs') }}
      </router-link>
      <router-link to="/command-registry" class="nav-item" active-class="active">
        <span class="nav-icon">⚙️</span> {{ t('app.nav.commandRegistry') }}
      </router-link>
      <router-link to="/dynamic-commands" class="nav-item" active-class="active">
        <span class="nav-icon">☁️</span> {{ t('app.nav.dynamicCommands') }}
      </router-link>
      <router-link to="/pool" class="nav-item" active-class="active">
        <span class="nav-icon">🖥️</span> {{ t('app.nav.pool') }}
      </router-link>
      <router-link to="/cli-pool" class="nav-item" active-class="active">
        <span class="nav-icon">⚡</span> {{ t('app.nav.cliPool') }}
      </router-link>
      <router-link to="/proxy-pool" class="nav-item" active-class="active">
        <span class="nav-icon">🌐</span> {{ t('app.nav.proxyPool') }}
      </router-link>
      <router-link to="/job-cache" class="nav-item" active-class="active">
        <span class="nav-icon">💼</span> {{ t('app.nav.jobCache') }}
      </router-link>
      <div class="nav-divider" />
      <div class="nav-group-label">{{ t('app.group.agent') }}</div>
      <router-link to="/agent-sessions" class="nav-item" active-class="active">
        <span class="nav-icon">🤖</span> {{ t('app.nav.agentSessions') }}
      </router-link>
      <router-link to="/agent-tasks" class="nav-item" active-class="active">
        <span class="nav-icon">⚡</span> {{ t('app.nav.agentTasks') }}
      </router-link>
      <router-link to="/agent-templates" class="nav-item" active-class="active">
        <span class="nav-icon">🧬</span> {{ t('app.nav.agentTemplates') }}
      </router-link>
      <router-link to="/agent-history" class="nav-item" active-class="active">
        <span class="nav-icon">💬</span> {{ t('app.nav.agentHistory') }}
      </router-link>
      <router-link to="/agent-users" class="nav-item" active-class="active">
        <span class="nav-icon">👥</span> {{ t('app.nav.agentUsers') }}
      </router-link>
      <router-link to="/portal-users" class="nav-item" active-class="active">
        <span class="nav-icon">🔐</span> {{ t('app.nav.portalUsers') }}
      </router-link>
      <router-link to="/candidate-resumes" class="nav-item" active-class="active">
        <span class="nav-icon">&#128196;</span> {{ t('app.nav.candidateResumes') }}
      </router-link>
      <div class="nav-divider" />
      <div class="nav-group-label">{{ t('app.group.tools') }}</div>
      <router-link to="/api-capture" class="nav-item" active-class="active">
        <span class="nav-icon">&#128270;</span> {{ t('app.nav.apiCapture') }}
      </router-link>
      <div class="nav-divider" />
      <div class="nav-group-label">{{ t('app.group.autofill') }}</div>
      <router-link to="/autofill-templates" class="nav-item" active-class="active">
        <span class="nav-icon">📝</span> {{ t('app.nav.autofillTemplates') }}
      </router-link>
      <router-link to="/autofill-captures" class="nav-item" active-class="active">
        <span class="nav-icon">🕸️</span> {{ t('app.nav.autofillCaptures') }}
      </router-link>
      <div class="nav-divider" />
      <div class="nav-group-label">{{ t('app.group.system') }}</div>
      <router-link to="/system-monitor" class="nav-item" active-class="active">
        <span class="nav-icon">📡</span> {{ t('app.nav.systemMonitor') }}
      </router-link>
      <router-link to="/mcp-metrics" class="nav-item" active-class="active">
        <span class="nav-icon">📊</span> {{ t('app.nav.mcpMetrics') }}
      </router-link>
      <router-link to="/client-errors" class="nav-item" active-class="active">
        <span class="nav-icon">🪟</span> {{ t('app.nav.clientErrors') }}
      </router-link>
      <router-link to="/chip-configs" class="nav-item" active-class="active">
        <span class="nav-icon">⚡</span> {{ t('app.nav.chipConfigs') }}
      </router-link>
      <router-link to="/growth" class="nav-item" active-class="active">
        <span class="nav-icon">📈</span> {{ t('app.nav.growth') }}
      </router-link>
      <router-link to="/operators" class="nav-item" active-class="active">
        <span class="nav-icon">🛡️</span> {{ t('app.nav.operators') }}
      </router-link>
      <div class="sidebar-spacer" />
      <button class="lang-btn" @click="toggleLocale">
        {{ locale === 'zh' ? 'English' : '中文' }}
      </button>
      <button v-if="auth.authEnabled" class="logout-btn" @click="handleLogout">
        {{ t('app.logout') }}
      </button>
    </nav>
    <main :class="['main-content', { 'no-sidebar': !auth.authenticated }]">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from './stores/auth'
import { setLocale } from './i18n'

const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()

function toggleLocale() {
  setLocale(locale.value === 'zh' ? 'en' : 'zh')
}

async function handleLogout() {
  await auth.logout()
  router.replace('/login')
}
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0f172a;
  color: #e2e8f0;
}
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0f172a; }
::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
</style>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
}
.sidebar {
  width: 180px;
  background: #1e293b;
  border-right: 1px solid #334155;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex-shrink: 0;
}
.logo {
  font-size: 15px;
  font-weight: 700;
  color: #60a5fa;
  padding: 8px 8px 16px;
  border-bottom: 1px solid #334155;
  margin-bottom: 8px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  color: #94a3b8;
  text-decoration: none;
  font-size: 13px;
}
.nav-item:hover { background: #334155; color: #e2e8f0; }
.nav-item.active { background: #1d4ed8; color: white; }
.nav-icon { font-size: 14px; }
.nav-divider {
  height: 1px;
  background: #334155;
  margin: 8px 0;
}
.nav-group-label {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  padding: 4px 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.sidebar-spacer { flex: 1; }
.lang-btn {
  background: transparent;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #94a3b8;
  font-size: 12px;
  padding: 7px 10px;
  cursor: pointer;
  text-align: left;
  margin-top: 8px;
}
.lang-btn:hover { background: #334155; color: #60a5fa; border-color: #60a5fa; }
.logout-btn {
  background: transparent;
  border: 1px solid #334155;
  border-radius: 6px;
  color: #94a3b8;
  font-size: 12px;
  padding: 7px 10px;
  cursor: pointer;
  text-align: left;
  margin-top: 6px;
}
.logout-btn:hover { background: #334155; color: #f87171; border-color: #f87171; }
.main-content { flex: 1; overflow: auto; }
.main-content.no-sidebar { width: 100%; }
</style>
