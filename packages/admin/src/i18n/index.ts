/*
 * i18n —— vue-i18n 实例。
 *
 * 语言包按命名空间拆分：locales/{zh,en}/<namespace>.ts，每个文件 `export default {}`。
 * 这里用 Vite 的 import.meta.glob 自动收集合并 —— 新增页面只需丢一个分片文件，
 * 不用改本文件（并行迁移时也不会有合并冲突）。
 *
 * 用法：组件内 `const { t } = useI18n()`，模板 `{{ t('app.nav.dashboard') }}`。
 * 非组件的 .ts 文件：`import { i18n } from '@/i18n'` 后用 `i18n.global.t(...)`。
 */
import { createI18n } from 'vue-i18n'

type Dict = Record<string, unknown>

const zhFiles = import.meta.glob('./locales/zh/*.ts', { eager: true }) as Record<string, { default: Dict }>
const enFiles = import.meta.glob('./locales/en/*.ts', { eager: true }) as Record<string, { default: Dict }>

function merge(files: Record<string, { default: Dict }>): Dict {
  const out: Dict = {}
  for (const path in files) {
    const ns = path.slice(path.lastIndexOf('/') + 1).replace(/\.ts$/, '')
    out[ns] = files[path].default
  }
  return out
}

const STORAGE_KEY = 'admin-locale'

function initialLocale(): 'zh' | 'en' {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'zh' || saved === 'en') return saved
  return navigator.language?.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: initialLocale(),
  fallbackLocale: 'zh',
  // messages 由 glob 动态合并，跳过 vue-i18n 的精确 schema 类型推断
  messages: { zh: merge(zhFiles), en: merge(enFiles) } as any,
})

// legacy:false 下 i18n.global.locale 是 WritableComputedRef
const localeRef = i18n.global.locale as unknown as { value: 'zh' | 'en' }

document.documentElement.lang = localeRef.value

export function setLocale(loc: 'zh' | 'en'): void {
  localeRef.value = loc
  localStorage.setItem(STORAGE_KEY, loc)
  document.documentElement.lang = loc
}
