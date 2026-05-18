<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="logo">SmartJob</div>
      <p class="subtitle">{{ t('loginPage.subtitle') }}</p>
      <form @submit.prevent="handleLogin">
        <div class="field">
          <label>{{ t('loginPage.username') }}</label>
          <input
            v-model="username"
            type="text"
            :placeholder="t('loginPage.usernamePh')"
            autocomplete="username"
            :disabled="loading"
          />
        </div>
        <div class="field">
          <label>{{ t('loginPage.password') }}</label>
          <input
            v-model="password"
            type="password"
            :placeholder="t('loginPage.passwordPh')"
            autocomplete="current-password"
            :disabled="loading"
          />
        </div>
        <p v-if="errorMsg" class="error">{{ errorMsg }}</p>
        <button type="submit" :disabled="loading || !username || !password">
          {{ loading ? t('loginPage.loggingIn') : t('loginPage.login') }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

async function handleLogin() {
  if (!username.value || !password.value) return
  loading.value = true
  errorMsg.value = ''
  const result = await auth.login(username.value, password.value)
  loading.value = false
  if (result.ok) {
    router.replace('/')
  } else {
    errorMsg.value = result.error || t('loginPage.loginFailed')
    password.value = ''
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  background: #0f172a;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 36px 40px;
  width: 360px;
}
.logo {
  font-size: 20px;
  font-weight: 700;
  color: #60a5fa;
  margin-bottom: 6px;
}
.subtitle {
  color: #94a3b8;
  font-size: 13px;
  margin-bottom: 28px;
}
.field {
  margin-bottom: 16px;
}
label {
  display: block;
  font-size: 12px;
  color: #94a3b8;
  margin-bottom: 6px;
}
input {
  width: 100%;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 10px 12px;
  color: #e2e8f0;
  font-size: 14px;
  outline: none;
}
input:focus { border-color: #60a5fa; }
input:disabled { opacity: 0.5; cursor: not-allowed; }
.error {
  color: #f87171;
  font-size: 13px;
  margin-bottom: 12px;
}
button {
  width: 100%;
  background: #1d4ed8;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 11px;
  font-size: 14px;
  cursor: pointer;
  margin-top: 4px;
}
button:hover:not(:disabled) { background: #1e40af; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
