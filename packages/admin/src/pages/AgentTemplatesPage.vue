<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">{{ t('agentTemplatesPage.title') }}</h1>
        <span class="header-hint">{{ t('agentTemplatesPage.headerHint') }}</span>
      </div>
      <div class="header-right">
        <button class="refresh-btn" :disabled="loading" @click="refresh">
          {{ loading ? t('agentTemplatesPage.loading') : t('agentTemplatesPage.refresh') }}
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div v-if="!loading && templates.length === 0" class="empty">
      {{ t('agentTemplatesPage.noTemplates') }}
    </div>

    <div class="template-grid">
      <div
        v-for="tpl in templates"
        :key="tpl.id"
        class="template-card"
      >
        <div class="template-head">
          <span class="template-emoji">{{ tpl.emoji }}</span>
          <div class="template-title-block">
            <h3 class="template-title">{{ tpl.title }}</h3>
            <span class="template-id">{{ tpl.id }}</span>
          </div>
          <span class="role-badge" :class="'role-' + tpl.role">
            {{ tpl.role === 'jobseeker' ? t('agentTemplatesPage.roleJobseeker') : t('agentTemplatesPage.roleRecruiter') }}
          </span>
        </div>

        <div class="template-desc">{{ tpl.description }}</div>

        <div class="template-meta">
          <span>{{ t('agentTemplatesPage.estimatedTime', { n: tpl.estimated_min }) }}</span>
          <span>·</span>
          <span>{{ t('agentTemplatesPage.supportedPlatforms', { n: tpl.supported_platforms.length }) }}</span>
        </div>

        <!-- 平台 sub-tab 切换 -->
        <div class="platform-tabs">
          <button
            v-for="p in tpl.supported_platforms"
            :key="p"
            class="platform-tab"
            :class="{ active: activePlatform[tpl.id] === p, ['platform-' + p]: true }"
            @click="setActivePlatform(tpl.id, p)"
          >
            {{ platformLabel(p) }}
            <span class="step-count">{{ tpl.steps_by_platform[p].length }}</span>
          </button>
        </div>

        <!-- DAG 渲染:横向 step 列 -->
        <div class="dag-flow">
          <template v-for="(step, idx) in currentSteps(tpl)" :key="step.id">
            <div class="dag-step" :class="{ iter: step.iter_items }">
              <div class="step-num">{{ idx + 1 }}</div>
              <div class="step-content">
                <div class="step-title">{{ step.title }}</div>
                <div class="step-id">{{ step.id }}</div>
                <div class="step-meta">
                  <span v-if="step.iter_items" class="step-tag iter">iter</span>
                  <span v-if="step.op_type !== 'default'" class="step-tag">{{ step.op_type }}</span>
                </div>
                <div class="step-fn" :title="`${step.fn_module}.${step.fn_name}`">
                  {{ stepFnShort(step) }}
                </div>
              </div>
            </div>
            <div v-if="idx < currentSteps(tpl).length - 1" class="dag-arrow">→</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../services/api'
import type { TemplateDag, TemplateStep } from '../types'

const { t } = useI18n()

const templates = ref<TemplateDag[]>([])
const loading = ref(false)
const error = ref('')
const activePlatform = reactive<Record<string, string>>({})

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const r = await api.admin_templateDags()
    templates.value = r.templates
    // 初始化每个 template 的 active platform 为第一个支持的
    for (const t of r.templates) {
      if (!activePlatform[t.id] && t.supported_platforms.length) {
        activePlatform[t.id] = t.supported_platforms[0]
      }
    }
  } catch (e: any) {
    error.value = t('agentTemplatesPage.fetchFailed') + (e.message || e)
  } finally {
    loading.value = false
  }
}

function setActivePlatform(templateId: string, platform: string) {
  activePlatform[templateId] = platform
}

function currentSteps(t: TemplateDag): TemplateStep[] {
  const p = activePlatform[t.id] || t.supported_platforms[0]
  return t.steps_by_platform[p] || []
}

function platformLabel(p: string): string {
  return ({ boss: t('agentTemplatesPage.bossZhipin'), linkedin: 'LinkedIn', indeed: 'Indeed' } as Record<string, string>)[p] || p
}

function stepFnShort(step: TemplateStep): string {
  // 展示 fn 来自哪个 step 文件,e.g. 'common' / 'boss' / 'linkedin' / 'indeed'
  const m = step.fn_module || ''
  const last = m.split('.').pop() || m
  return `${last}.${step.fn_name}`
}

onMounted(refresh)
</script>

<style scoped>
.page { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.page-header {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
}
.header-left { display: flex; align-items: baseline; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; color: #e2e8f0; }
.header-hint { font-size: 12px; color: #64748b; }

.refresh-btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px;
  background: #1e293b; color: #94a3b8; border: 1px solid #334155; cursor: pointer;
}
.refresh-btn:hover:not(:disabled) { background: #334155; color: #e2e8f0; }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error-banner {
  padding: 10px 14px; border-radius: 8px;
  background: #3b1a1a; border: 1px solid #7f1d1d; color: #f87171; font-size: 13px;
}

.empty {
  padding: 64px 16px; text-align: center;
  color: #64748b; font-size: 14px;
}

.template-grid {
  display: flex; flex-direction: column; gap: 16px;
}

.template-card {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 16px;
  display: flex; flex-direction: column; gap: 12px;
}

.template-head {
  display: flex; align-items: center; gap: 12px;
}
.template-emoji {
  font-size: 28px; flex-shrink: 0;
  width: 40px; height: 40px;
  display: flex; align-items: center; justify-content: center;
  background: #1e293b; border-radius: 8px;
}
.template-title-block { flex: 1; min-width: 0; }
.template-title {
  font-size: 16px; font-weight: 600; color: #e2e8f0; margin: 0;
}
.template-id {
  font-size: 11px; color: #64748b; font-family: 'SF Mono', monospace;
}
.role-badge {
  padding: 3px 10px; border-radius: 12px;
  font-size: 11px; font-weight: 600;
}
.role-jobseeker { background: #1e3a5f; color: #60a5fa; }
.role-recruiter { background: #3d2e0a; color: #fbbf24; }

.template-desc {
  font-size: 13px; color: #cbd5e1; line-height: 1.5;
}
.template-meta {
  display: flex; gap: 6px; font-size: 11px; color: #64748b;
}

.platform-tabs {
  display: flex; gap: 6px; flex-wrap: wrap;
  border-bottom: 1px solid #1e293b; padding-bottom: 8px;
}
.platform-tab {
  padding: 5px 12px; border-radius: 6px;
  background: transparent;
  color: #94a3b8; border: 1px solid #334155;
  font-size: 12px; cursor: pointer;
  display: flex; align-items: center; gap: 6px;
}
.platform-tab:hover:not(.active) { background: #1e293b; }
.platform-tab.active.platform-boss     { background: #0a3d2a; color: #4ade80; border-color: #166534; }
.platform-tab.active.platform-linkedin { background: #1e3a5f; color: #60a5fa; border-color: #1e40af; }
.platform-tab.active.platform-indeed   { background: #2d1b3d; color: #c084fc; border-color: #6d28d9; }
.platform-tab .step-count {
  font-size: 10px;
  padding: 1px 5px; border-radius: 8px;
  background: rgba(255,255,255,0.08);
}

.dag-flow {
  display: flex; align-items: stretch; gap: 6px;
  overflow-x: auto;
  padding: 4px 0;
}

.dag-step {
  flex-shrink: 0;
  min-width: 140px; max-width: 180px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex; flex-direction: column; gap: 4px;
  position: relative;
}
.dag-step.iter {
  border-color: #f59e0b;
  border-style: dashed;
  background: rgba(245, 158, 11, 0.04);
}
.step-num {
  position: absolute;
  top: -8px; left: -8px;
  width: 22px; height: 22px;
  background: #475569; color: #f1f5f9;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  border: 2px solid #0f172a;
}
.dag-step.iter .step-num { background: #f59e0b; color: #0f172a; }
.step-content { display: flex; flex-direction: column; gap: 3px; }
.step-title {
  font-size: 12px; font-weight: 600; color: #e2e8f0;
}
.step-id {
  font-size: 10px; color: #94a3b8; font-family: 'SF Mono', monospace;
}
.step-meta {
  display: flex; gap: 4px; flex-wrap: wrap;
}
.step-tag {
  padding: 1px 5px; border-radius: 4px;
  font-size: 9px; letter-spacing: 0.3px;
  background: #334155; color: #cbd5e1;
  text-transform: uppercase;
}
.step-tag.iter { background: #854d0e; color: #fbbf24; }
.step-fn {
  font-size: 10px; color: #64748b;
  font-family: 'SF Mono', monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  margin-top: 2px;
  padding-top: 4px;
  border-top: 1px dashed #334155;
}

.dag-arrow {
  display: flex; align-items: center;
  color: #475569; font-size: 14px;
  flex-shrink: 0;
}
</style>
