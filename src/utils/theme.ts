import { ref } from 'vue'
import type { GlobalThemeOverrides } from 'naive-ui'
import {
  DEFAULT_THEME_ACCENT,
  DEFAULT_THEME_MODE,
  THEME_ACCENTS,
  THEME_TONES,
  TONE_HUES,
  getThemeAccentPreview,
  normalizeThemeAccent,
  normalizeThemeMode,
  resolveThemeTokens,
  themeOverridesFor,
  toneTokens,
} from './themeTokens'
import type { ResolvedThemeTokens, ThemeAccent, ThemeMode, ThemeTone } from './themeTokens'

export {
  DEFAULT_THEME_ACCENT,
  DEFAULT_THEME_MODE,
  THEME_ACCENTS,
  THEME_TONES,
  TONE_HUES,
  getThemeAccentPreview,
  normalizeThemeAccent,
  normalizeThemeMode,
  resolveThemeTokens,
  themeOverridesFor,
  toneTokens,
}
export type { ResolvedThemeTokens, ThemeAccent, ThemeMode, ThemeTone }
export { contrastRatio, inkOn, mixToward, withAlpha } from './themeMath'

type ViewTransitionLike = {
  ready: Promise<void>
}

type DocumentWithViewTransition = Document & {
  startViewTransition?: (callback: () => void | Promise<void>) => ViewTransitionLike
}

const RIPPLE_TRANSITION_DURATION = 700
const RIPPLE_TRANSITION_EASING = 'cubic-bezier(0.2, 0, 0, 1)'

let currentMode: ThemeMode = DEFAULT_THEME_MODE
let currentAccent: ThemeAccent = DEFAULT_THEME_ACCENT

/**
 * Reactive mirror of the applied dark state. matchMedia is not reactive, so computeds that
 * derive Naive UI theme data read this instead — otherwise an OS scheme flip while following
 * system mode would swap the CSS variables but leave component theming stale.
 */
export const themeIsDark = ref(resolvedIsDark(currentMode))

export function resolvedIsDark(mode: ThemeMode = currentMode): boolean {
  return mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
}

export function getResolvedThemeTokens(
  mode: ThemeMode = currentMode,
  accent: ThemeAccent = currentAccent,
): ResolvedThemeTokens {
  return resolveThemeTokens(resolvedIsDark(mode) ? 'dark' : 'light', accent)
}

function applyThemeClass(mode: ThemeMode) {
  const dark = resolvedIsDark(mode)
  document.documentElement.classList.toggle('dark-theme', dark)
  document.documentElement.classList.toggle('light-theme', !dark)
}

function applyThemeTokens(mode: ThemeMode, accent: ThemeAccent) {
  const tokens = getResolvedThemeTokens(mode, accent)
  const style = document.documentElement.style
  style.setProperty('--surface', tokens.surface)
  style.setProperty('--surface-1', tokens.surface1)
  style.setProperty('--surface-2', tokens.surface2)
  style.setProperty('--surface-3', tokens.surface3)
  style.setProperty('--on-surface-heading', tokens.onSurfaceHeading)
  style.setProperty('--on-surface', tokens.onSurface)
  style.setProperty('--on-surface-muted', tokens.onSurfaceMuted)
  style.setProperty('--on-surface-faint', tokens.onSurfaceFaint)
  style.setProperty('--outline', tokens.outline)
  style.setProperty('--outline-strong', tokens.outlineStrong)
  style.setProperty('--on-primary', tokens.onPrimary)
  style.setProperty('--primary', tokens.primary)
  style.setProperty('--primary-ink', tokens.primaryInk)
  style.setProperty('--primary-hover', tokens.primaryHover)
  style.setProperty('--primary-pressed', tokens.primaryPressed)
  style.setProperty('--primary-soft', tokens.primarySoft)
  style.setProperty('--primary-softer', tokens.primarySofter)
  style.setProperty('--primary-border', tokens.primaryBorder)
  style.setProperty('--primary-glow', tokens.primaryGlow)
  style.setProperty('--success', tokens.success)
  style.setProperty('--warning', tokens.warning)
  style.setProperty('--danger', tokens.danger)
  style.setProperty('--shadow-panel', tokens.shadowPanel)
  style.setProperty('--shadow-soft', tokens.shadowSoft)
  // Page tones are a palette like the accent one: CSS picks which element gets a
  // tone, these explicit assignments derive its hue/wash/ink for the current
  // theme (spelled out so the token audit can see every variable by name).
  const tones = {
    blue: toneTokens('blue', tokens.isDark),
    violet: toneTokens('violet', tokens.isDark),
    amber: toneTokens('amber', tokens.isDark),
    emerald: toneTokens('emerald', tokens.isDark),
  }
  style.setProperty('--tone-hue-blue', tones.blue.hue)
  style.setProperty('--tone-wash-blue', tones.blue.wash)
  style.setProperty('--tone-ink-blue', tones.blue.ink)
  style.setProperty('--tone-hue-violet', tones.violet.hue)
  style.setProperty('--tone-wash-violet', tones.violet.wash)
  style.setProperty('--tone-ink-violet', tones.violet.ink)
  style.setProperty('--tone-hue-amber', tones.amber.hue)
  style.setProperty('--tone-wash-amber', tones.amber.wash)
  style.setProperty('--tone-ink-amber', tones.amber.ink)
  style.setProperty('--tone-hue-emerald', tones.emerald.hue)
  style.setProperty('--tone-wash-emerald', tones.emerald.wash)
  style.setProperty('--tone-ink-emerald', tones.emerald.ink)
}

export function getThemeOverrides(
  mode: ThemeMode = currentMode,
  accent: ThemeAccent = currentAccent,
): GlobalThemeOverrides {
  return themeOverridesFor(getResolvedThemeTokens(mode, accent))
}

export function applyTheme(mode: ThemeMode, accent: ThemeAccent = currentAccent) {
  currentMode = normalizeThemeMode(mode)
  currentAccent = normalizeThemeAccent(accent)
  themeIsDark.value = resolvedIsDark(currentMode)
  applyThemeClass(currentMode)
  applyThemeTokens(currentMode, currentAccent)
}

export function initTheme(
  mode: ThemeMode = DEFAULT_THEME_MODE,
  accent: ThemeAccent = DEFAULT_THEME_ACCENT,
) {
  applyTheme(mode, accent)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (currentMode === 'system') applyTheme(currentMode, currentAccent)
  })
}

export async function runRippleViewTransition(
  update: () => void | Promise<void>,
  origin?: { x: number; y: number },
) {
  const doc = document as DocumentWithViewTransition
  if (!origin || !doc.startViewTransition) {
    await update()
    return
  }

  const { x, y } = origin
  const maxRadius = Math.hypot(
    Math.max(x, window.innerWidth - x),
    Math.max(y, window.innerHeight - y),
  )

  const transition = doc.startViewTransition(async () => {
    await update()
  })

  try {
    await transition.ready
    document.documentElement.animate(
      {
        clipPath: [
          `circle(0px at ${x}px ${y}px)`,
          `circle(${maxRadius}px at ${x}px ${y}px)`,
        ],
        opacity: [0.7, 1],
      },
      {
        duration: RIPPLE_TRANSITION_DURATION,
        easing: RIPPLE_TRANSITION_EASING,
        pseudoElement: '::view-transition-new(root)',
      },
    )
  } catch {
    // ignore interrupted transitions
  }
}
