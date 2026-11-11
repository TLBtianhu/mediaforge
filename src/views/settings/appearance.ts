/**
 * 设置页「外观」区块：缩放滑杆、主题/强调色/语言切换。
 *
 * 缩放是纯换算（normalizeScaleFactor 由视图注入，避免把 Tauri 的 webview 依赖带进测试）；
 * 主题与语言切换共用一次涟漪过渡，过渡函数与 DOM 应用也全部注入。
 */
import type { ComputedRef, Ref, WritableComputedRef } from 'vue'
import type { ThemeAccent, ThemeMode } from '@/utils/theme'
import type { LocaleSetting } from '@/i18n'

type Deps = {
  computed: {
    <T>(source: () => T): ComputedRef<T>
    <T>(source: { get: () => T; set: (value: T) => void }): WritableComputedRef<T>
  }
  t: (key: string, named?: Record<string, unknown>) => string
  normalizeScaleFactor: (value: unknown) => number
  DEFAULT_SCALE_FACTOR: number
  scaleFactor: Ref<number>
  themeMode: Ref<ThemeMode>
  themeAccent: Ref<ThemeAccent>
  locale: Ref<LocaleSetting>
  currentLocale: Ref<string>
  animationsEnabled: Ref<boolean>
  languageSelectWrap: Ref<HTMLElement | null>
  applyTheme: (mode: ThemeMode, accent?: ThemeAccent) => void
  setLocale: (locale: LocaleSetting) => void
  runRippleViewTransition: (update: () => void | Promise<void>, origin?: { x: number; y: number }) => Promise<void>
}

export const SCALE_FACTOR_PRESET_VALUES = [0.75, 0.9, 1, 1.1, 1.25, 1.5] as const

export function makeAppearance(deps: Deps) {
  const { computed, t, normalizeScaleFactor, DEFAULT_SCALE_FACTOR } = deps

  function formatScaleFactorLabel(value: number) {
    return `${Math.round(normalizeScaleFactor(value) * 100)}%`
  }

  function isSameScaleFactor(left: unknown, right: unknown) {
    return Math.abs(normalizeScaleFactor(left) - normalizeScaleFactor(right)) < 0.001
  }

  function updateScaleFactor(value: number) {
    deps.scaleFactor.value = normalizeScaleFactor(value)
  }

  function resetScaleFactorToDefault() {
    updateScaleFactor(DEFAULT_SCALE_FACTOR)
  }

  const scaleFactorPercent = computed(() => formatScaleFactorLabel(deps.scaleFactor.value))
  const scaleSliderIndex = computed({
    get: () => {
      const current = normalizeScaleFactor(deps.scaleFactor.value)
      const exact = SCALE_FACTOR_PRESET_VALUES.findIndex((value) => isSameScaleFactor(value, current))
      if (exact !== -1) return exact
      let nearest = 0
      let minDiff = Number.POSITIVE_INFINITY
      SCALE_FACTOR_PRESET_VALUES.forEach((value, index) => {
        const diff = Math.abs(value - current)
        if (diff < minDiff) {
          minDiff = diff
          nearest = index
        }
      })
      return nearest
    },
    set: (index: number) => {
      const value = SCALE_FACTOR_PRESET_VALUES[index]
      if (value !== undefined) updateScaleFactor(value)
    },
  })
  const scaleSliderMarks = computed<Record<number, string>>(() =>
    SCALE_FACTOR_PRESET_VALUES.reduce<Record<number, string>>((marks, value, index) => {
      marks[index] = formatScaleFactorLabel(value)
      return marks
    }, {}),
  )
  const isDefaultScaleFactor = computed(() => isSameScaleFactor(deps.scaleFactor.value, DEFAULT_SCALE_FACTOR))
  const currentResolvedLanguageLabel = computed(() =>
    deps.currentLocale.value === 'en'
      ? t('settings.languageEnglish')
      : t('settings.languageSimplifiedChinese'),
  )
  const appearanceThemeAccentLabel = computed(() =>
    t(`settings.themeAccent${deps.themeAccent.value[0].toUpperCase()}${deps.themeAccent.value.slice(1)}`),
  )

  function getEventOrigin(event: MouseEvent) {
    const target = event.currentTarget as HTMLElement | null
    if (!target) return { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    const rect = target.getBoundingClientRect()
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    }
  }

  function getElementOrigin(element: HTMLElement | null) {
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    }
  }

  async function selectThemeMode(mode: string, event: MouseEvent) {
    if (deps.themeMode.value === mode) return
    const nextMode = mode as ThemeMode
    const origin = deps.animationsEnabled.value ? getEventOrigin(event) : undefined
    await deps.runRippleViewTransition(() => {
      deps.themeMode.value = nextMode
      deps.applyTheme(nextMode, deps.themeAccent.value)
    }, origin)
  }

  async function selectThemeAccent(accent: string, event: MouseEvent) {
    if (deps.themeAccent.value === accent) return
    const nextAccent = accent as ThemeAccent
    const origin = deps.animationsEnabled.value ? getEventOrigin(event) : undefined
    await deps.runRippleViewTransition(() => {
      deps.themeAccent.value = nextAccent
      deps.applyTheme(deps.themeMode.value, nextAccent)
    }, origin)
  }

  async function selectLocale(code: string) {
    if (deps.locale.value === code) return
    const nextLocale = code as LocaleSetting
    const origin = deps.animationsEnabled.value
      ? (getElementOrigin(deps.languageSelectWrap.value) || {
          x: window.innerWidth / 2,
          y: window.innerHeight / 2,
        })
      : undefined
    await deps.runRippleViewTransition(() => {
      deps.locale.value = nextLocale
      deps.setLocale(nextLocale)
    }, origin)
  }

  return {
    scaleFactorPercent, scaleSliderIndex, scaleSliderMarks, isDefaultScaleFactor,
    currentResolvedLanguageLabel, appearanceThemeAccentLabel,
    formatScaleFactorLabel, isSameScaleFactor, updateScaleFactor, resetScaleFactorToDefault,
    getEventOrigin, getElementOrigin, selectThemeMode, selectThemeAccent, selectLocale,
  }
}

/** 取路径最后一段；空路径回落到默认目录名。 */
export function dirName(path: string, fallback: string) {
  const normalized = (path || '').trim().replace(/[\\/]+$/, '')
  if (!normalized) return fallback
  const segments = normalized.split(/[\\/]+/).filter(Boolean)
  return segments.at(-1) || fallback
}
