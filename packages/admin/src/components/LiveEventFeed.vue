<template>
  <div class="feed">
    <div class="feed-header">{{ t('liveEventFeed.header') }}</div>
    <div class="feed-body" ref="feedEl">
      <div
        v-for="(ev, i) in events"
        :key="i"
        class="feed-item"
        :class="eventClass(ev)"
      >
        <span class="ev-time">{{ fmtTime(ev) }}</span>
        <span class="ev-tag">{{ ev.event }}</span>
        <span class="ev-detail">{{ summarize(ev) }}</span>
      </div>
      <div v-if="events.length === 0" class="empty">{{ t('liveEventFeed.waiting') }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AdminEvent } from '../types'

const { t } = useI18n()

const props = defineProps<{ events: AdminEvent[] }>()
const feedEl = ref<HTMLElement>()

// 新事件到达时滚动到顶部（事件是倒序的）
watch(() => props.events.length, () => {
  nextTick(() => {
    if (feedEl.value) feedEl.value.scrollTop = 0
  })
})

function eventClass(ev: AdminEvent): string {
  if (ev.event.startsWith('session_')) return 'session'
  if (ev.event.startsWith('agent_')) return 'agent'
  if (ev.event.startsWith('command_')) return 'command'
  if (ev.event === 'heartbeat') return 'heartbeat'
  return ''
}

function fmtTime(_ev: AdminEvent): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function summarize(ev: AdminEvent): string {
  switch (ev.event) {
    case 'session_connected': return `sid=${ev.session_id.slice(0, 12)}`
    case 'session_disconnected': return `sid=${ev.session_id.slice(0, 12)}`
    case 'session_login': return `${ev.account_name} (${ev.user_id})`
    case 'session_logout': return `sid=${ev.session_id.slice(0, 12)}`
    case 'agent_connected': return `aid=${ev.agent_id.slice(0, 12)}`
    case 'agent_disconnected': return `aid=${ev.agent_id.slice(0, 12)}`
    case 'agent_bound': return `${ev.agent_id.slice(0, 10)} → ${ev.session_id.slice(0, 10)}`
    case 'command_start': return `${ev.tool_name}`
    case 'command_end': return `${ev.ok ? '✓' : '✗'} ${ev.duration_ms?.toFixed(0)}ms`
    case 'heartbeat': return `sid=${ev.session_id.slice(0, 12)}`
    case 'init_snapshot': return `${(ev.sessions || []).length}sessions`
    default: return ''
  }
}
</script>

<style scoped>
.feed {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 200px;
}
.feed-header {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  padding: 8px 0 6px;
  border-bottom: 1px solid #1e293b;
  margin-bottom: 6px;
}
.feed-body {
  flex: 1;
  overflow-y: auto;
  max-height: 340px;
}
.feed-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  padding: 3px 0;
  border-bottom: 1px solid #0f172a;
  font-size: 11px;
}
.ev-time { color: #475569; flex-shrink: 0; width: 66px; }
.ev-tag { padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 600; flex-shrink: 0; }
.ev-detail { color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session .ev-tag { background: #14532d; color: #4ade80; }
.agent .ev-tag { background: #1e3a5f; color: #60a5fa; }
.command .ev-tag { background: #451a03; color: #fbbf24; }
.heartbeat .ev-tag { background: #1e293b; color: #475569; }
.empty { color: #334155; font-size: 12px; padding: 16px 0; text-align: center; }
</style>
