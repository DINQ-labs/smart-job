<template>
  <div class="overlay" @click.self="$emit('cancel')">
    <div class="modal" :class="variant">
      <div class="modal-icon">
        <span v-if="variant === 'danger'">⚠</span>
        <span v-else>?</span>
      </div>
      <div class="modal-content">
        <p class="modal-title">{{ title }}</p>
        <p v-if="message" class="modal-message">{{ message }}</p>
      </div>
      <div class="modal-actions">
        <button class="btn-cancel" @click="$emit('cancel')">{{ t('confirmModal.cancel') }}</button>
        <button class="btn-confirm" :class="variant" @click="$emit('confirm')">{{ confirmText || t('confirmModal.confirm') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  title: string
  message?: string
  confirmText?: string
  variant?: 'danger' | 'warn'
}>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>

<style scoped>
.overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.65);
  display: flex; align-items: center; justify-content: center; z-index: 200;
}
.modal {
  background: #1e293b; border: 1px solid #334155; border-radius: 10px;
  width: 340px; padding: 24px 20px 16px;
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  box-shadow: 0 20px 60px rgba(0,0,0,.5);
}
.modal.danger { border-color: #7f1d1d; }
.modal.warn   { border-color: #78350f; }

.modal-icon {
  width: 44px; height: 44px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
}
.modal.danger .modal-icon { background: #450a0a; color: #fca5a5; }
.modal.warn   .modal-icon { background: #431407; color: #fcd34d; }

.modal-content { text-align: center; }
.modal-title {
  font-size: 14px; font-weight: 600; color: #e2e8f0; margin: 0 0 6px;
}
.modal-message {
  font-size: 12px; color: #64748b; margin: 0;
  line-height: 1.5;
}

.modal-actions {
  display: flex; gap: 10px; width: 100%; justify-content: center;
}
.btn-cancel {
  flex: 1; padding: 8px; background: #334155; color: #94a3b8;
  border: none; border-radius: 6px; cursor: pointer; font-size: 13px;
}
.btn-cancel:hover { background: #475569; }
.btn-confirm {
  flex: 1; padding: 8px; border: none; border-radius: 6px;
  cursor: pointer; font-size: 13px; font-weight: 600;
}
.btn-confirm.danger { background: #991b1b; color: #fca5a5; }
.btn-confirm.danger:hover { background: #b91c1c; }
.btn-confirm.warn   { background: #b45309; color: #fcd34d; }
.btn-confirm.warn:hover   { background: #d97706; }
</style>
