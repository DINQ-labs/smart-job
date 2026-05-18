<template>
  <div class="page">
    <h1>{{ t('operatorsPage.title') }}</h1>
    <p class="subtitle">{{ t('operatorsPage.subtitle') }}</p>

    <!-- 修改我的密码 -->
    <section class="card">
      <h2>{{ t('operatorsPage.pwTitle') }}</h2>
      <form class="row" @submit.prevent="changePassword">
        <input
          v-model="oldPw" type="password" autocomplete="current-password"
          :placeholder="t('operatorsPage.oldPw')"
        />
        <input
          v-model="newPw" type="password" autocomplete="new-password"
          :placeholder="t('operatorsPage.newPw')"
        />
        <button type="submit" :disabled="pwBusy || !oldPw || !newPw">
          {{ t('operatorsPage.pwBtn') }}
        </button>
      </form>
      <p v-if="pwMsg" :class="['msg', pwOk ? 'ok' : 'err']">{{ pwMsg }}</p>
    </section>

    <!-- 账号列表 -->
    <section class="card">
      <h2>{{ t('operatorsPage.accountsTitle') }}</h2>
      <table class="tbl">
        <thead>
          <tr>
            <th>{{ t('operatorsPage.colUsername') }}</th>
            <th>{{ t('operatorsPage.colCreated') }}</th>
            <th>{{ t('operatorsPage.colLastLogin') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>
              {{ u.username }}
              <span v-if="u.username === current" class="tag">{{ t('operatorsPage.currentTag') }}</span>
            </td>
            <td class="dim">{{ fmt(u.created_at) }}</td>
            <td class="dim">{{ u.last_login_at ? fmt(u.last_login_at) : t('operatorsPage.never') }}</td>
            <td>
              <button
                class="del"
                :disabled="u.username === current || users.length <= 1"
                @click="removeUser(u)"
              >{{ t('operatorsPage.delete') }}</button>
            </td>
          </tr>
          <tr v-if="!users.length">
            <td colspan="4" class="dim">—</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 新建账号 -->
    <section class="card">
      <h2>{{ t('operatorsPage.addTitle') }}</h2>
      <form class="row" @submit.prevent="addUser">
        <input v-model="newUsername" autocomplete="off" :placeholder="t('operatorsPage.usernamePh')" />
        <input
          v-model="newPassword" type="password" autocomplete="new-password"
          :placeholder="t('operatorsPage.passwordPh')"
        />
        <button type="submit" :disabled="addBusy || !newUsername || !newPassword">
          {{ t('operatorsPage.addBtn') }}
        </button>
      </form>
      <p v-if="addMsg" :class="['msg', addOk ? 'ok' : 'err']">{{ addMsg }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface AdminUser {
  id: number
  username: string
  created_at: string
  last_login_at: string | null
}

const users = ref<AdminUser[]>([])
const current = ref('')

const oldPw = ref('')
const newPw = ref('')
const pwBusy = ref(false)
const pwMsg = ref('')
const pwOk = ref(false)

const newUsername = ref('')
const newPassword = ref('')
const addBusy = ref(false)
const addMsg = ref('')
const addOk = ref(false)

function fmt(s: string): string {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? s : d.toLocaleString()
}

async function load() {
  try {
    const res = await fetch('/admin/users')
    if (res.status === 401) { window.location.href = '/login'; return }
    const data = await res.json()
    if (data.ok) {
      users.value = data.users || []
      current.value = data.current || ''
    }
  } catch { /* 忽略,保持上次列表 */ }
}

async function addUser() {
  if (!newUsername.value || !newPassword.value) return
  addBusy.value = true
  addMsg.value = ''
  try {
    const res = await fetch('/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: newUsername.value, password: newPassword.value }),
    })
    const data = await res.json()
    addOk.value = !!data.ok
    addMsg.value = data.ok ? t('operatorsPage.addOk') : (data.error || t('operatorsPage.addFail'))
    if (data.ok) {
      newUsername.value = ''
      newPassword.value = ''
      await load()
    }
  } catch {
    addOk.value = false
    addMsg.value = t('operatorsPage.netErr')
  } finally {
    addBusy.value = false
  }
}

async function removeUser(u: AdminUser) {
  if (!confirm(t('operatorsPage.confirmDelete', { name: u.username }))) return
  try {
    const res = await fetch(`/admin/users/${u.id}`, { method: 'DELETE' })
    const data = await res.json().catch(() => ({}))
    if (data.ok) await load()
    else alert(data.error || t('operatorsPage.delFail'))
  } catch {
    alert(t('operatorsPage.netErr'))
  }
}

async function changePassword() {
  if (!oldPw.value || !newPw.value) return
  pwBusy.value = true
  pwMsg.value = ''
  try {
    const res = await fetch('/admin/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPw.value, new_password: newPw.value }),
    })
    const data = await res.json()
    pwOk.value = !!data.ok
    pwMsg.value = data.ok ? t('operatorsPage.pwOk') : (data.error || t('operatorsPage.pwFail'))
    if (data.ok) {
      oldPw.value = ''
      newPw.value = ''
    }
  } catch {
    pwOk.value = false
    pwMsg.value = t('operatorsPage.netErr')
  } finally {
    pwBusy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page { padding: 24px 28px; max-width: 760px; }
h1 { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.subtitle { color: #94a3b8; font-size: 13px; margin: 6px 0 20px; }
.card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.card h2 { font-size: 14px; font-weight: 600; color: #e2e8f0; margin-bottom: 14px; }
.row { display: flex; gap: 10px; flex-wrap: wrap; }
.row input {
  flex: 1;
  min-width: 160px;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 9px 11px;
  color: #e2e8f0;
  font-size: 13px;
  outline: none;
}
.row input:focus { border-color: #60a5fa; }
.row button {
  background: #1d4ed8;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 9px 18px;
  font-size: 13px;
  cursor: pointer;
}
.row button:hover:not(:disabled) { background: #1e40af; }
.row button:disabled { opacity: 0.5; cursor: not-allowed; }
.tbl { width: 100%; border-collapse: collapse; }
.tbl th {
  text-align: left;
  font-size: 11px;
  color: #64748b;
  text-transform: uppercase;
  padding: 6px 8px;
  border-bottom: 1px solid #334155;
}
.tbl td { padding: 9px 8px; font-size: 13px; color: #e2e8f0; border-bottom: 1px solid #2a3a52; }
.tbl td.dim { color: #94a3b8; font-size: 12px; }
.tag {
  font-size: 10px;
  background: #1d4ed8;
  color: #fff;
  border-radius: 4px;
  padding: 1px 6px;
  margin-left: 6px;
}
.del {
  background: transparent;
  border: 1px solid #334155;
  color: #f87171;
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.del:hover:not(:disabled) { background: #334155; }
.del:disabled { opacity: 0.35; cursor: not-allowed; }
.msg { font-size: 12px; margin-top: 10px; }
.msg.ok { color: #4ade80; }
.msg.err { color: #f87171; }
</style>
