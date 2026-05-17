import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { i18n } from './i18n'
import Dashboard from './pages/Dashboard.vue'
import CommandsPage from './pages/CommandsPage.vue'
import ErrorLogsPage from './pages/ErrorLogsPage.vue'
import SessionDetailPage from './pages/SessionDetailPage.vue'
import LoginPage from './pages/LoginPage.vue'
import CommandRegistryPage from './pages/CommandRegistryPage.vue'
import DynamicCommandsPage from './pages/DynamicCommandsPage.vue'
import BrowserPoolPage from './pages/BrowserPoolPage.vue'
import CliPoolPage from './pages/CliPoolPage.vue'
import ProxyPoolPage from './pages/ProxyPoolPage.vue'
import JobCachePage from './pages/JobCachePage.vue'
import AgentSessionsPage from './pages/AgentSessionsPage.vue'
import AgentTasksPage from './pages/AgentTasksPage.vue'
import AgentTemplatesPage from './pages/AgentTemplatesPage.vue'
import AgentHistoryPage from './pages/AgentHistoryPage.vue'
import AgentUsersPage from './pages/AgentUsersPage.vue'
import SystemMonitorPage from './pages/SystemMonitorPage.vue'
import ApiCapturePage from './pages/ApiCapturePage.vue'
import CandidateResumesPage from './pages/CandidateResumesPage.vue'
import GrowthMetricsPage from './pages/GrowthMetricsPage.vue'
import McpMetricsPage from './pages/McpMetricsPage.vue'
import ClientErrorsPage from './pages/ClientErrorsPage.vue'
import ChipConfigsPage from './pages/ChipConfigsPage.vue'
import PortalUsersPage from './pages/PortalUsersPage.vue'
import AutofillTemplatesPage from './pages/AutofillTemplatesPage.vue'
import AutofillCapturesPage from './pages/AutofillCapturesPage.vue'
import { useAuthStore } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginPage, meta: { public: true } },
    { path: '/', component: Dashboard },
    { path: '/commands', component: CommandsPage },
    { path: '/error-logs', component: ErrorLogsPage },
    { path: '/sessions/:id', component: SessionDetailPage },
    { path: '/command-registry', component: CommandRegistryPage },
    { path: '/dynamic-commands', component: DynamicCommandsPage },
    { path: '/pool', component: BrowserPoolPage },
    { path: '/cli-pool', component: CliPoolPage },
    { path: '/proxy-pool', component: ProxyPoolPage },
    { path: '/job-cache', component: JobCachePage },
    { path: '/agent-sessions', component: AgentSessionsPage },
    { path: '/agent-tasks', component: AgentTasksPage },
    { path: '/agent-templates', component: AgentTemplatesPage },
    { path: '/agent-history', component: AgentHistoryPage },
    { path: '/agent-users', component: AgentUsersPage },
    { path: '/api-capture', component: ApiCapturePage },
    { path: '/autofill-templates', component: AutofillTemplatesPage },
    { path: '/autofill-captures', component: AutofillCapturesPage },
    { path: '/candidate-resumes', component: CandidateResumesPage },
    { path: '/system-monitor', component: SystemMonitorPage },
    { path: '/growth', component: GrowthMetricsPage },
    { path: '/mcp-metrics', component: McpMetricsPage },
    { path: '/client-errors', component: ClientErrorsPage },
    { path: '/chip-configs',  component: ChipConfigsPage },
    { path: '/portal-users', component: PortalUsersPage },
  ],
})

const pinia = createPinia()
const app = createApp(App)
app.use(pinia)
app.use(router)
app.use(i18n)

// Navigation guard: check auth once, then redirect as needed
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!auth.checked) {
    await auth.check()
  }

  if (!auth.authenticated && !to.meta.public) {
    return '/login'
  }
  if (auth.authenticated && to.path === '/login') {
    return '/'
  }
})

app.mount('#app')
