<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">{{ t('portalUsersPage.title') }}</h1>
      </div>
      <div class="header-right">
        <div class="stats-chips">
          <span class="chip">{{ t('portalUsersPage.chips.total', { n: total }) }}</span>
          <span class="chip verified">{{ t('portalUsersPage.chips.verified', { n: verifiedCount }) }}</span>
          <span class="chip staff">{{ t('portalUsersPage.chips.staff', { n: staffCount }) }}</span>
          <span class="chip disabled">{{ t('portalUsersPage.chips.disabled', { n: disabledCount }) }}</span>
        </div>
        <input
          v-model="searchQ"
          type="text"
          :placeholder="t('portalUsersPage.searchPh')"
          class="search-input"
          @keyup.enter="load"
        />
        <select v-model="roleFilter" class="filter-select" @change="load">
          <option value="">{{ t('portalUsersPage.allRoles') }}</option>
          <option value="user">User</option>
          <option value="staff">Staff</option>
          <option value="admin">Admin</option>
        </select>
        <button class="refresh-btn" :disabled="loading" @click="load">
          {{ loading ? t('portalUsersPage.loading') : t('portalUsersPage.refresh') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>{{ t('portalUsersPage.cols.email') }}</th>
            <th>{{ t('portalUsersPage.cols.name') }}</th>
            <th>{{ t('portalUsersPage.cols.role') }}</th>
            <th>{{ t('portalUsersPage.cols.status') }}</th>
            <th>{{ t('portalUsersPage.cols.loginMethod') }}</th>
            <th>{{ t('portalUsersPage.cols.lastLogin') }}</th>
            <th>{{ t('portalUsersPage.cols.registeredAt') }}</th>
            <th>{{ t('portalUsersPage.cols.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="users.length === 0 && !loading">
            <td colspan="9" class="empty-row">{{ t('portalUsersPage.emptyUsers') }}</td>
          </tr>
          <tr
            v-for="u in users"
            :key="u.id"
            class="data-row"
          >
            <td class="mono sm">{{ u.id.slice(0, 8) }}…</td>
            <td>
              <span :class="{ dim: !u.email_verified_at }">
                {{ u.email || '—' }}
              </span>
              <span v-if="u.email_verified_at" class="verified-badge">✓</span>
            </td>
            <td>{{ u.name || '—' }}</td>
            <td>
              <span class="role-badge" :class="u.role">{{ u.role }}</span>
            </td>
            <td>
              <span v-if="u.disabled_at" class="status-badge disabled">{{ t('portalUsersPage.statusDisabled') }}</span>
              <span v-else-if="u.deleted_at" class="status-badge deleted">{{ t('portalUsersPage.statusDeleted') }}</span>
              <span v-else class="status-badge ok">{{ t('portalUsersPage.statusOk') }}</span>
            </td>
            <td class="sm">{{ t('portalUsersPage.methodCount', { n: u.identities_count || 0 }) }}</td>
            <td class="sm dim">{{ fmtDt(u.last_login_at) }}</td>
            <td class="sm dim">{{ fmtDt(u.created_at) }}</td>
            <td>
              <button class="action-btn" @click="openDetail(u)">{{ t('portalUsersPage.detail') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > limit" class="pagination">
      <button :disabled="offset === 0" @click="paginate(-1)">{{ t('portalUsersPage.prevPage') }}</button>
      <span>{{ offset / limit + 1 }} / {{ Math.ceil(total / limit) }}</span>
      <button :disabled="offset + limit >= total" @click="paginate(1)">{{ t('portalUsersPage.nextPage') }}</button>
    </div>

    <!-- Toast -->
    <Toast ref="toastRef" />

    <!-- Detail Drawer -->
    <Teleport to="body">
      <div v-if="detail" class="drawer-overlay" @click.self="closeDrawer">
        <div class="drawer drawer-lg">
          <div class="drawer-header">
            <span class="drawer-title">{{ t('portalUsersPage.userDetail') }} — {{ detail.email }}</span>
            <button class="drawer-close" @click="closeDrawer">✕</button>
          </div>
          <div class="drawer-body">

            <!-- Basic Info -->
            <div class="section-label">{{ t('portalUsersPage.basicInfo') }}</div>
            <div class="meta-section">
              <div class="meta-row">
                <span class="meta-label">User ID</span>
                <span class="mono">{{ detail.id }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.email') }}</span>
                <span>
                  {{ detail.email || '—' }}
                  <span v-if="detail.email_verified_at" class="verified-badge">{{ t('portalUsersPage.verified') }}</span>
                  <span v-else class="dim">{{ t('portalUsersPage.unverified') }}</span>
                </span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.name') }}</span>
                <span>{{ detail.name || '—' }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.avatar') }}</span>
                <span v-if="detail.image" class="sm">{{ detail.image.slice(0, 50) }}…</span>
                <span v-else>—</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.language') }}</span>
                <span>{{ detail.locale }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.role') }}</span>
                <select v-model="detail.role" class="edit-select" @change="updateRole">
                  <option value="user">User</option>
                  <option value="staff">Staff</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.status') }}</span>
                <div>
                  <label class="checkbox-label">
                    <input
                      type="checkbox"
                      :checked="!!detail.disabled_at"
                      @change="toggleDisabled"
                    />
                    {{ t('portalUsersPage.disableAccount') }}
                  </label>
                  <span v-if="detail.disabled_at" class="dim sm">{{ t('portalUsersPage.disabledAt', { t: fmtDt(detail.disabled_at) }) }}</span>
                </div>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.registeredAt') }}</span>
                <span class="dim">{{ fmtDt(detail.created_at) }}</span>
              </div>
              <div class="meta-row">
                <span class="meta-label">{{ t('portalUsersPage.cols.lastLogin') }}</span>
                <span class="dim">{{ fmtDt(detail.last_login_at) }}</span>
              </div>
            </div>

            <!-- Identities -->
            <div class="section-label">{{ t('portalUsersPage.cols.loginMethod') }}</div>
            <div v-if="identities.length === 0" class="dim">{{ t('portalUsersPage.noLoginMethod') }}</div>
            <div v-for="id in identities" :key="id.id" class="identity-card">
              <div class="identity-main">
                <span class="identity-provider">{{ id.provider }}</span>
                <span class="identity-kind">{{ id.kind }}</span>
              </div>
              <div class="identity-meta">
                <span class="dim sm">Subject: {{ id.subject.slice(0, 20) }}…</span>
                <span class="dim sm">{{ t('portalUsersPage.linkedAt', { t: fmtDt(id.linked_at) }) }}</span>
              </div>
              <button
                v-if="identities.length > 1"
                class="unlink-btn"
                @click="unlinkIdentity(id.id)"
              >
                {{ t('portalUsersPage.unlink') }}
              </button>
            </div>

            <!-- Tokens -->
            <div class="section-label">{{ t('portalUsersPage.activeDevices', { n: tokens.length }) }}</div>
            <div v-if="tokens.length === 0" class="dim">{{ t('portalUsersPage.noActiveSession') }}</div>
            <div v-for="tk in tokens" :key="tk.id" class="token-card">
              <div class="token-main">
                <span class="token-client">{{ tk.client || 'unknown' }}</span>
                <span class="token-device">{{ tk.device_name || t('portalUsersPage.unnamedDevice') }}</span>
              </div>
              <div class="token-meta">
                <span class="dim sm">UA: {{ (tk.user_agent || '').slice(0, 40) }}…</span>
                <span class="dim sm">{{ t('portalUsersPage.lastActive', { t: fmtDt(tk.last_seen_at) }) }}</span>
                <span class="dim sm">{{ t('portalUsersPage.expires', { t: fmtDt(tk.expires_at) }) }}</span>
              </div>
              <button class="revoke-btn" @click="revokeToken(tk.id)">{{ t('portalUsersPage.revoke') }}</button>
            </div>

            <!-- Actions -->
            <div class="section-label">{{ t('portalUsersPage.actions') }}</div>
            <div class="action-buttons">
              <button class="action-btn danger" @click="revokeAllTokens">
                {{ t('portalUsersPage.revokeAll') }}
              </button>
            </div>

          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PortalUser, AuthIdentity, RefreshToken } from '../types'
import { api } from '../services/api'
import Toast from '../components/Toast.vue'

const { t } = useI18n()

const users = ref<PortalUser[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const searchQ = ref('')
const roleFilter = ref('')
const limit = ref(50)
const offset = ref(0)

const detail = ref<PortalUser | null>(null)
const identities = ref<AuthIdentity[]>([])
const tokens = ref<RefreshToken[]>([])
const toastRef = ref<InstanceType<typeof Toast> | null>(null)

const verifiedCount = computed(() =>
  users.value.filter(u => u.email_verified_at).length
)
const staffCount = computed(() =>
  users.value.filter(u => u.role === 'staff' || u.role === 'admin').length
)
const disabledCount = computed(() =>
  users.value.filter(u => u.disabled_at).length
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api.portal_listUsers({
      q: searchQ.value || undefined,
      role: roleFilter.value as any || undefined,
      limit: limit.value,
      offset: offset.value,
    })
    users.value = r.users
    total.value = r.total
  } catch (e: any) {
    error.value = e.message || t('portalUsersPage.loadFailed')
  } finally {
    loading.value = false
  }
}

function paginate(delta: number) {
  offset.value = Math.max(0, offset.value + delta * limit.value)
  load()
}

function fmtDt(dt: string | null): string {
  if (!dt) return '—'
  const d = new Date(dt)
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function openDetail(user: PortalUser) {
  detail.value = user
  identities.value = []
  tokens.value = []

  try {
    const [idsRes, tokensRes] = await Promise.all([
      api.portal_listUserIdentities(user.id),
      api.portal_listUserTokens(user.id),
    ])
    identities.value = idsRes.identities
    tokens.value = tokensRes.tokens
  } catch (e: any) {
    console.error('加载详情失败', e)
  }
}

function closeDrawer() {
  detail.value = null
  identities.value = []
  tokens.value = []
}

async function updateRole() {
  if (!detail.value) return
  try {
    await api.portal_updateUserRole(detail.value.id, detail.value.role)
    load()
  } catch (e: any) {
    error.value = e.message || t('portalUsersPage.updateRoleFailed')
  }
}

async function toggleDisabled(e: Event) {
  if (!detail.value) return
  const disabled = (e.target as HTMLInputElement).checked
  try {
    await api.portal_setUserDisabled(detail.value.id, disabled)
    load()
  } catch (err: any) {
    error.value = err.message || t('portalUsersPage.updateStatusFailed')
  }
}

async function unlinkIdentity(identityId: string) {
  if (!detail.value) return
  try {
    await api.portal_unlinkIdentity(detail.value.id, identityId)
    toastRef.value?.show(t('portalUsersPage.unlinkSuccess'), 'success')
    openDetail(detail.value)
  } catch (e: any) {
    toastRef.value?.show(e.message || t('portalUsersPage.unlinkFailed'), 'error')
  }
}

async function revokeToken(tokenId: string) {
  try {
    await api.portal_revokeToken(tokenId)
    toastRef.value?.show(t('portalUsersPage.tokenRevoked'), 'success')
    if (detail.value) openDetail(detail.value)
  } catch (e: any) {
    toastRef.value?.show(e.message || t('portalUsersPage.revokeFailed'), 'error')
  }
}

async function revokeAllTokens() {
  if (!detail.value) return
  try {
    await api.portal_revokeAllUserTokens(detail.value.id)
    toastRef.value?.show(t('portalUsersPage.allTokensRevoked'), 'success')
    if (detail.value) openDetail(detail.value)
  } catch (e: any) {
    toastRef.value?.show(e.message || t('portalUsersPage.revokeFailed'), 'error')
  }
}

onMounted(load)
</script>

<style scoped>
.page { padding: 20px; max-width: 1400px; margin: 0 auto; }

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.header-left { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 20px; font-weight: 600; }

.header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.stats-chips { display: flex; gap: 8px; }
.chip {
  padding: 4px 10px;
  background: #1e293b;
  border-radius: 12px;
  font-size: 12px;
  color: #94a3b8;
}
.chip.verified { background: #0f5132; color: #86efac; }
.chip.staff { background: #1e3a8a; color: #93c5fd; }
.chip.disabled { background: #7f1d1d; color: #fca5a5; }

.search-input {
  padding: 7px 12px;
  border: 1px solid #334155;
  border-radius: 6px;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 13px;
  width: 180px;
}
.search-input:focus { outline: none; border-color: #60a5fa; }

.filter-select {
  padding: 7px 10px;
  border: 1px solid #334155;
  border-radius: 6px;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 13px;
}

.refresh-btn {
  padding: 7px 14px;
  background: #1d4ed8;
  border: none;
  border-radius: 6px;
  color: white;
  font-size: 13px;
  cursor: pointer;
}
.refresh-btn:disabled { opacity: 0.6; cursor: not-allowed; }

.error-banner {
  background: #7f1d1d;
  color: #fca5a5;
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 16px;
  font-size: 13px;
}

.table-wrap { overflow-x: auto; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  text-align: left;
  padding: 10px 12px;
  background: #1e293b;
  color: #94a3b8;
  font-weight: 500;
  border-bottom: 1px solid #334155;
  white-space: nowrap;
}
.data-table td { padding: 10px 12px; border-bottom: 1px solid #1e293b; }
.data-row:hover { background: #1e293b; cursor: default; }
.empty-row { text-align: center; color: #64748b; padding: 40px !important; }

.mono { font-family: 'SF Mono', 'Menlo', monospace; }
.sm { font-size: 12px; }
.dim { color: #64748b; }
.verified-badge { color: #22c55e; font-size: 11px; margin-left: 4px; }

.role-badge {
  padding: 3px 8px;
  border-radius: 10px;
  font-size: 11px;
  text-transform: uppercase;
}
.role-badge.user { background: #334155; color: #94a3b8; }
.role-badge.staff { background: #1e3a8a; color: #93c5fd; }
.role-badge.admin { background: #7f1d1d; color: #fca5a5; }

.status-badge {
  padding: 3px 8px;
  border-radius: 10px;
  font-size: 11px;
}
.status-badge.ok { background: #0f5132; color: #86efac; }
.status-badge.disabled { background: #7f1d1d; color: #fca5a5; }
.status-badge.deleted { background: #334155; color: #64748b; }

.action-btn {
  padding: 5px 10px;
  background: #334155;
  border: none;
  border-radius: 4px;
  color: #e2e8f0;
  font-size: 12px;
  cursor: pointer;
}
.action-btn:hover { background: #475569; }
.action-btn.danger { background: #dc2626; }
.action-btn.danger:hover { background: #ef4444; }

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
}
.pagination button {
  padding: 6px 12px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 4px;
  color: #e2e8f0;
  cursor: pointer;
}
.pagination button:disabled { opacity: 0.5; cursor: not-allowed; }

/* Drawer */
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex;
  justify-content: flex-end;
  z-index: 100;
}
.drawer {
  width: 500px;
  max-width: 90vw;
  background: #1e293b;
  border-left: 1px solid #334155;
  display: flex;
  flex-direction: column;
}
.drawer-lg { width: 600px; }
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #334155;
}
.drawer-title { font-size: 16px; font-weight: 600; }
.drawer-close {
  background: none;
  border: none;
  color: #94a3b8;
  font-size: 20px;
  cursor: pointer;
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  margin-bottom: 10px;
  margin-top: 20px;
}
.section-label:first-child { margin-top: 0; }

.meta-section {
  background: #0f172a;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.meta-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #1e293b;
}
.meta-row:last-child { border-bottom: none; }
.meta-label { color: #94a3b8; font-size: 13px; }
.edit-select {
  padding: 4px 8px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 4px;
  color: #e2e8f0;
  font-size: 13px;
}
.checkbox-label { display: flex; align-items: center; gap: 8px; font-size: 13px; }

.identity-card, .token-card {
  background: #0f172a;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 10px;
}
.identity-main, .token-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.identity-provider, .token-client {
  font-weight: 500;
  color: #e2e8f0;
}
.identity-kind, .token-device {
  font-size: 12px;
  color: #94a3b8;
  background: #1e293b;
  padding: 2px 8px;
  border-radius: 10px;
}
.identity-meta, .token-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}
.unlink-btn, .revoke-btn {
  margin-top: 8px;
  padding: 5px 10px;
  background: #7f1d1d;
  border: none;
  border-radius: 4px;
  color: #fca5a5;
  font-size: 12px;
  cursor: pointer;
}
.unlink-btn:hover, .revoke-btn:hover { background: #991b1b; }

.action-buttons { display: flex; gap: 10px; }
</style>
