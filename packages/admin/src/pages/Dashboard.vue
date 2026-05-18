<template>
  <div class="dashboard">
    <StatsBar :stats="stats" :ws-connected="wsConnected" />

    <div class="main-grid">
      <!-- Chrome 扩展会话 -->
      <div class="panel sessions-panel">
        <div class="panel-title">
          {{ t('dashboard.extSessions') }}
          <span class="count">{{ extSessions.length }}</span>
          <button class="refresh-btn" @click="refresh">{{ t('dashboard.refresh') }}</button>
        </div>
        <div class="session-list">
          <SessionCard
            v-for="s in extSessions"
            :key="s.session_id"
            :session="s"
            class="mb-2"
          />
          <div v-if="extSessions.length === 0" class="empty-hint">
            {{ t('dashboard.emptyExtLine1') }}<br>{{ t('dashboard.emptyExtLine2') }}
          </div>
        </div>
      </div>

      <!-- Agent 列表 -->
      <div class="panel agents-panel">
        <div class="panel-title">
          {{ t('dashboard.agentConnections') }}
          <span class="count">{{ agentsStore.agents.length }}</span>
        </div>
        <div class="agent-list">
          <AgentCard
            v-for="a in agentsStore.agents"
            :key="a.agent_id"
            :agent="a"
            class="mb-2"
          />
          <div v-if="agentsStore.agents.length === 0" class="empty-hint">
            {{ t('dashboard.emptyAgentLine1') }}<br>{{ t('dashboard.emptyAgentLine2') }}
          </div>
        </div>
      </div>

      <!-- 实时事件流 -->
      <div class="panel feed-panel">
        <LiveEventFeed :events="commandsStore.liveEvents" />
      </div>
    </div>

    <!-- Agent Mode 分布 -->
    <div class="panel mode-panel">
      <div class="panel-title">
        {{ t('dashboard.modeDistribution') }}
        <span class="count">{{ t('dashboard.activeCount', { n: modeTotal }) }}</span>
      </div>
      <div v-if="!agentGwOnline" class="empty-hint">{{ t('dashboard.gatewayOffline') }}</div>
      <div v-else class="mode-grid">
        <div
          v-for="m in modeItems"
          :key="m.name"
          class="mode-card"
          :class="'mode-' + m.name"
        >
          <span class="mode-count">{{ m.count }}</span>
          <span class="mode-label">{{ m.display }}</span>
        </div>
      </div>
      <div v-if="modeHistory.length > 0" class="mode-history">
        <div class="mode-history-title">{{ t('dashboard.last7Days') }}</div>
        <div class="mode-history-list">
          <div v-for="h in modeHistory" :key="h.mode" class="mode-history-row">
            <span class="mode-badge-sm" :class="'mode-' + h.mode">{{ modeLabelMap[h.mode] || h.mode }}</span>
            <span class="mode-hist-num">{{ t('dashboard.sessionsCount', { n: h.session_count }) }}</span>
            <span class="mode-hist-num dim">{{ t('dashboard.turnsCount', { n: h.total_turns }) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- CLI 会话列表（bosszp-cli） -->
    <div class="panel cli-sessions-panel">
      <div class="panel-title">
        {{ t('dashboard.cliSessions') }}
        <span class="count">{{ cliSessions.length }}</span>
      </div>
      <div class="cli-session-list">
        <SessionCard
          v-for="s in cliSessions"
          :key="s.session_id"
          :session="s"
        />
        <div v-if="cliSessions.length === 0" class="empty-hint">
          {{ t('dashboard.emptyCliLine1') }}<br>{{ t('dashboard.emptyCliLine2') }}
        </div>
      </div>
    </div>

    <!-- 命令日志 -->
    <div class="panel commands-panel">
      <div class="panel-title">
        {{ t('dashboard.commandLogTitle') }}
      </div>
      <CommandTable :commands="commandsStore.commands.slice(0, 10)" @detail="selectedCmd = $event" />
    </div>

  <CommandDetailModal
    v-if="selectedCmd"
    :cmd="selectedCmd"
    @close="selectedCmd = null"
  />

    <!-- 图表区 -->
    <div class="charts-grid">
      <div class="panel">
        <ResponseTimeChart :data="commandsStore.getResponseTimeSeries()" />
      </div>
      <div class="panel">
        <CommandFreqChart :data="commandsStore.getToolFrequency()" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import StatsBar from '../components/StatsBar.vue'
import SessionCard from '../components/SessionCard.vue'
import AgentCard from '../components/AgentCard.vue'
import CommandDetailModal from '../components/CommandDetailModal.vue'
import LiveEventFeed from '../components/LiveEventFeed.vue'
import CommandTable from '../components/CommandTable.vue'
import ResponseTimeChart from '../components/ResponseTimeChart.vue'
import CommandFreqChart from '../components/CommandFreqChart.vue'
import { useSessionsStore } from '../stores/sessions'
import { useAgentsStore } from '../stores/agents'
import { useCommandsStore } from '../stores/commands'
import { useAdminWS } from '../composables/useAdminWS'
import { api } from '../services/api'
import { EXT_NAME_CLI } from '../constants'
import type { GatewayStats, AdminEvent, CommandLog, AgentGatewayStatus, ModeStats } from '../types'

const { t } = useI18n()

const sessionsStore = useSessionsStore()
const agentsStore = useAgentsStore()
const commandsStore = useCommandsStore()

const selectedCmd = ref<CommandLog | null>(null)

// ── Agent Mode 分布 ──────────────────────────────────────────────────────────
const agentGwOnline = ref(false)
const modeDist = ref<Record<string, number>>({})
const modeHistory = ref<ModeStats[]>([])

const modeLabelMap = computed<Record<string, string>>(() => ({
  search: t('dashboard.modeSearch'), evaluate: t('dashboard.modeEvaluate'), apply: t('dashboard.modeApply'),
  interview: t('dashboard.modeInterview'), compare: t('dashboard.modeCompare'), recruiter: t('dashboard.modeRecruiter'),
}))
const modeTotal = computed(() => Object.values(modeDist.value).reduce((a, b) => a + b, 0))
const modeItems = computed(() => {
  const all = Object.entries(modeDist.value).map(([name, count]) => ({
    name, count, display: modeLabelMap.value[name] || name,
  }))
  // Always show all 6 modes (0 if not active)
  for (const key of Object.keys(modeLabelMap.value)) {
    if (!all.find(m => m.name === key)) {
      all.push({ name: key, count: 0, display: modeLabelMap.value[key] })
    }
  }
  return all.sort((a, b) => b.count - a.count)
})

async function fetchModeData() {
  try {
    const status: AgentGatewayStatus = await api.agent_getStatus()
    agentGwOnline.value = true
    modeDist.value = status.mode_distribution || {}
  } catch {
    agentGwOnline.value = false
  }
  try {
    const res = await api.agent_getModeStats(7)
    modeHistory.value = res.stats || []
  } catch { /* ignore */ }
}

// 区分 Chrome 扩展会话 与 CLI 会话
const extSessions = computed(() => sessionsStore.sessions.filter(s => s.ext_name !== EXT_NAME_CLI))
const cliSessions = computed(() => sessionsStore.sessions.filter(s => s.ext_name === EXT_NAME_CLI))

const stats = reactive<GatewayStats>({
  active_sessions: 0,
  active_agents: 0,
  today_commands: 0,
  avg_duration_ms: 0,
  error_rate_pct: 0,
})

function handleEvent(event: AdminEvent) {
  sessionsStore.handleEvent(event)
  agentsStore.handleEvent(event)
  commandsStore.handleEvent(event)

  // 从权威快照直接更新，避免乱序事件导致计数为负
  if (event.event === 'init_snapshot') {
    stats.active_sessions = event.sessions.filter(s => s.status === 'connected').length
    stats.active_agents = event.agents.filter(a => a.status === 'connected').length
    return
  }
  // 会话/Agent 增减：直接从 store 派生，保证与 store 一致
  if (event.event === 'session_connected' || event.event === 'session_disconnected') {
    stats.active_sessions = sessionsStore.sessions.filter(s => s.status === 'connected').length
  }
  if (event.event === 'agent_connected' || event.event === 'agent_disconnected') {
    stats.active_agents = agentsStore.agents.filter(a => a.status === 'connected').length
  }
  if (event.event === 'command_end') {
    stats.today_commands++
    if (event.duration_ms) {
      stats.avg_duration_ms = stats.avg_duration_ms
        ? (stats.avg_duration_ms * 0.9 + event.duration_ms * 0.1)
        : event.duration_ms
    }
  }
}

const { connected: wsConnected } = useAdminWS(handleEvent)

async function refresh() {
  await Promise.all([
    sessionsStore.fetchSessions(),
    agentsStore.fetchAgents(),
    commandsStore.fetchCommands({ limit: 10 }),
    fetchModeData(),
  ])
  const s = await api.getStats()
  Object.assign(stats, s)
}

let statsTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  refresh()
  // 定时从服务端同步权威统计（每 30s）
  statsTimer = setInterval(() => {
    api.getStats()
      .then(s => Object.assign(stats, s))
      .catch(err => console.warn('[boss-admin] stats 刷新失败:', err))
  }, 30000)
})

onUnmounted(() => {
  if (statsTimer) clearInterval(statsTimer)
})
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  min-height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
}
.main-grid {
  display: grid;
  grid-template-columns: 35% 25% 1fr;
  gap: 16px;
}
.panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px 14px;
}
.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.count {
  background: #334155;
  color: #94a3b8;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
}
.refresh-btn {
  margin-left: auto;
  background: #334155;
  color: #94a3b8;
  border: none;
  padding: 3px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
}
.refresh-btn:hover { background: #475569; }
.session-list, .agent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
}
.commands-panel { overflow-x: auto; }
.cli-sessions-panel { }
.cli-session-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: 260px;
  overflow-y: auto;
}
.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.empty-hint {
  color: #475569;
  font-size: 12px;
  text-align: center;
  padding: 20px;
  line-height: 1.8;
}
.mb-2 { margin-bottom: 0; }

/* ── Mode Panel ────────────────────────────────────────────────────────────── */
.mode-panel { }
.mode-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.mode-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 16px;
  border-radius: 8px;
  min-width: 72px;
}
.mode-count {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
}
.mode-label {
  font-size: 11px;
  opacity: 0.8;
}
.mode-search    { background: #172554; color: #60a5fa; }
.mode-evaluate  { background: #431407; color: #fb923c; }
.mode-apply     { background: #052e16; color: #4ade80; }
.mode-interview { background: #3b0764; color: #c084fc; }
.mode-compare   { background: #4c0519; color: #fb7185; }
.mode-recruiter { background: #042f2e; color: #2dd4bf; }

.mode-history { margin-top: 12px; }
.mode-history-title {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 6px;
  font-weight: 600;
}
.mode-history-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mode-history-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.mode-badge-sm {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 600;
  min-width: 36px;
  text-align: center;
}
.mode-hist-num { color: #94a3b8; font-variant-numeric: tabular-nums; }
.mode-hist-num.dim { color: #475569; }
</style>
