<template>
  <div class="commands-page">
    <div class="page-header">
      <h2>{{ t('commandsPage.title') }}</h2>
      <div class="filters">
        <input v-model="filterSession" :placeholder="t('commandsPage.sessionFilterPh')" class="filter-input" />
        <input v-model="filterAgent" :placeholder="t('commandsPage.agentFilterPh')" class="filter-input" />
        <button @click="search">{{ t('commandsPage.search') }}</button>
      </div>
    </div>
    <div class="panel">
      <CommandTable
        :commands="commandsStore.commands"
        :offset="offset"
        :limit="limit"
        :show-pagination="true"
        @prev="prevPage"
        @next="nextPage"
        @detail="selectedCmd = $event"
      />
    </div>
  </div>

  <CommandDetailModal
    v-if="selectedCmd"
    :cmd="selectedCmd"
    @close="selectedCmd = null"
  />
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import CommandTable from '../components/CommandTable.vue'
import CommandDetailModal from '../components/CommandDetailModal.vue'
import { useCommandsStore } from '../stores/commands'
import type { CommandLog } from '../types'

const { t } = useI18n()

const selectedCmd = ref<CommandLog | null>(null)

const commandsStore = useCommandsStore()
const filterSession = ref('')
const filterAgent = ref('')
const offset = ref(0)
const limit = 100

async function search() {
  offset.value = 0
  await commandsStore.fetchCommands({
    session_id: filterSession.value || undefined,
    agent_id: filterAgent.value || undefined,
    limit,
    offset: 0,
  })
}

async function prevPage() {
  offset.value = Math.max(0, offset.value - limit)
  await load()
}

async function nextPage() {
  offset.value += limit
  await load()
}

async function load() {
  await commandsStore.fetchCommands({
    session_id: filterSession.value || undefined,
    agent_id: filterAgent.value || undefined,
    limit,
    offset: offset.value,
  })
}

onMounted(load)
</script>

<style scoped>
.commands-page {
  padding: 16px;
  background: #0f172a;
  min-height: 100vh;
  color: #e2e8f0;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
.page-header h2 { font-size: 18px; color: #e2e8f0; }
.filters { display: flex; gap: 8px; margin-left: auto; }
.filter-input {
  background: #1e293b;
  border: 1px solid #334155;
  color: #e2e8f0;
  padding: 6px 10px;
  border-radius: 5px;
  font-size: 12px;
  width: 180px;
}
.filters button {
  background: #3b82f6;
  color: white;
  border: none;
  padding: 6px 14px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 12px;
}
.panel {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px;
}
</style>
