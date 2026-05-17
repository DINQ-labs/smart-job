<template>
  <Teleport to="body">
    <Transition name="toast">
      <div v-if="visible" class="toast-container" :class="type">
        <span class="toast-icon">{{ icon }}</span>
        <span class="toast-message">{{ message }}</span>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const visible = ref(false)
const message = ref('')
const type = ref<'success' | 'error' | 'info'>('info')

const icon = computed(() => {
  switch (type.value) {
    case 'success': return '✓'
    case 'error': return '✕'
    default: return 'ℹ'
  }
})

let timer: ReturnType<typeof setTimeout> | null = null

function show(msg: string, toastType: 'success' | 'error' | 'info' = 'info', duration = 2000) {
  message.value = msg
  type.value = toastType
  visible.value = true

  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    visible.value = false
  }, duration)
}

defineExpose({ show })
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 14px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
  min-width: 200px;
  justify-content: center;
}

.toast-container.success {
  background: #0f5132;
  border: 1px solid #22c55e;
  color: #86efac;
}

.toast-container.error {
  background: #7f1d1d;
  border: 1px solid #dc2626;
  color: #fca5a5;
}

.toast-container.info {
  background: #1e3a8a;
  border: 1px solid #3b82f6;
  color: #93c5fd;
}

.toast-icon {
  font-size: 18px;
  font-weight: bold;
}

.toast-message {
  color: inherit;
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
}
</style>
