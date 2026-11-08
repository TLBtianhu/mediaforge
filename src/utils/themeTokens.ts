import type { GlobalThemeOverrides } from 'naive-ui'
import { blendOver, contrastRatio, inkOn, mixToward, oklabLightness, readableInk, withAlpha } from './themeMath'


export type ThemeMode = 'system' | 'dark' | 'light'
export type ThemeAccent = 'blue' | 'pink' | 'sky' | 'teal'

type ThemePalette = {
  primary: string
  primaryInk: string
  primarySoft: string
  primarySofter: string
  primaryBorder: string
  primaryGlow: string
  primaryHover: string
  primaryPressed: string
  preview: [string, string]
}

type SurfacePalette = {
  surface: string
  surface1: string
  surface2: string
  surface3: string
  onSurfaceHeading: string
  onSurface: string
  onSurfaceMuted: string
  outline: string
  outlineStrong: string
  success: string
  warning: string
  danger: string
  shadowPanel: string
  shadowSoft: string
}

export type ResolvedThemeTokens = SurfacePalette & ThemePalette & {
  /** Accent ink: the only token allowed to colour text/icons. */
  primaryInk: string
  /** Dimmest text colour that still clears AA on every surface. */
  onSurfaceFaint: string
  /** Legible label colour on a solid `primary` fill. */
  onPrimary: string
  isDark: boolean
}


export const DEFAULT_THEME_MODE: ThemeMode = 'system'
export const DEFAULT_THEME_ACCENT: ThemeAccent = 'blue'
export const THEME_ACCENTS: ThemeAccent[] = ['blue', 'pink', 'sky', 'teal']

/**
 * Page-scoped tone palette. CSS only decides which element gets which tone
 * (`data-tone`); the hue, wash and ink are derived here so the contrast rule has
 * exactly one implementation.
 */
export const TONE_HUES = {
  blue: '#3568ff',
  violet: '#7c5cff',
  amber: '#e58a00',
  emerald: '#0f9f72',
} as const

export type ThemeTone = keyof typeof TONE_HUES
export const THEME_TONES: ThemeTone[] = ['blue', 'violet', 'amber', 'emerald']

/**
 * Perceived lightness step of a page tone wash. Equal α gives unequal tints
 * (amber reads twice as strong as blue), so the tint is solved from the step
 * instead: one number defines "how strong is a tone wash" for every hue.
 */
export const TONE_WASH_STEP = 0.05

function washFor(hue: string, surface: string): string {
  const target = oklabLightness(surface)
  let low = 0
  let high = 0.4
  for (let index = 0; index < 24; index += 1) {
    const mid = (low + high) / 2
    const delta = Math.abs(oklabLightness(blendOver(withAlpha(hue, mid), surface)) - target)
    if (delta < TONE_WASH_STEP) low = mid
    else high = mid
  }
  return withAlpha(hue, (low + high) / 2)
}

const DARK_SURFACE: SurfacePalette = {
  surface: '#111317',
  surface1: '#191c21',
  surface2: '#23272e',
  surface3: '#303640',
  onSurfaceHeading: '#e5e7eb',
  onSurface: '#c9cdd4',
  onSurfaceMuted: '#969da8',
  outline: 'rgba(229, 231, 235, 0.08)',
  outlineStrong: 'rgba(229, 231, 235, 0.14)',
  success: '#7fb59a',
  warning: '#d0ab70',
  danger: '#d7808f',
  shadowPanel: '0 6px 18px rgba(0, 0, 0, 0.12)',
  shadowSoft: '0 14px 40px rgba(0, 0, 0, 0.32)',
}

const LIGHT_SURFACE: SurfacePalette = {
  surface: '#f4f6fa',
  surface1: '#fbfcff',
  surface2: '#eef2f7',
  surface3: '#dfe5ee',
  onSurfaceHeading: '#172033',
  onSurface: '#172033',
  onSurfaceMuted: '#596579',
  outline: 'rgba(31, 42, 68, 0.13)',
  outlineStrong: 'rgba(31, 42, 68, 0.22)',
  success: '#2a7452',
  warning: '#8a5f00',
  danger: '#b4233a',
  shadowPanel: '0 18px 46px rgba(33, 43, 67, 0.07)',
  shadowSoft: '0 20px 70px rgba(33, 43, 67, 0.11)',
}

function accentPalette(
  primary: string,
  primaryInk: string,
  [soft, softer, border, glow]: [number, number, number, number],
  preview: [string, string],
  dark: boolean,
): ThemePalette {
  // Everything except the fill, the ink seed and the tint strengths is derived,
  // so a palette edit cannot leave a stale hover/soft value behind. Hover moves
  // away from the chosen label ink: pastel fills keep their dark label, saturated
  // fills keep their white one.
  const ink = inkOn(primary, dark ? DARK_SURFACE.surface : LIGHT_SURFACE.onSurfaceHeading)
  const away = ink === '#ffffff' ? '#000000' : '#ffffff'
  return {
    primary,
    primaryInk,
    primarySoft: withAlpha(primary, soft),
    primarySofter: withAlpha(primary, softer),
    primaryBorder: withAlpha(primary, border),
    primaryGlow: withAlpha(primary, glow),
    primaryHover: mixToward(primary, away, 0.88),
    primaryPressed: mixToward(primary, away, 0.76),
    preview,
  }
}

const THEME_PALETTES: Record<ThemeAccent, { light: ThemePalette; dark: ThemePalette }> = {
  blue: {
    dark: accentPalette('#7c9ed9', '#b7ccee', [0.15, 0.075, 0.30, 0.08], ['#7c9ed9', '#b7ccee'], true),
    light: accentPalette('#3f70d4', '#2454ad', [0.13, 0.07, 0.24, 0.14], ['#3f70d4', '#83a7ea'], false),
  },
  pink: {
    dark: accentPalette('#d084ad', '#edbdd2', [0.18, 0.085, 0.36, 0.14], ['#d084ad', '#edbdd2'], true),
    light: accentPalette('#d56f9f', '#b44e7f', [0.18, 0.08, 0.34, 0.20], ['#d56f9f', '#f2b9d3'], false),
  },
  sky: {
    dark: accentPalette('#70b4c8', '#b4d9e2', [0.18, 0.085, 0.36, 0.14], ['#70b4c8', '#b4d9e2'], true),
    light: accentPalette('#4d8fd8', '#2f74c0', [0.18, 0.08, 0.34, 0.20], ['#4d8fd8', '#9bcbf5'], false),
  },
  teal: {
    dark: accentPalette('#70b69d', '#b0d9c9', [0.18, 0.085, 0.36, 0.14], ['#70b69d', '#b0d9c9'], true),
    light: accentPalette('#1f7d6f', '#156f61', [0.14, 0.08, 0.34, 0.20], ['#238c7b', '#84d6ca'], false),
  },
}

function faintInk(surface: SurfacePalette): string {
  // Opacity used to dim labels by 30-40%, which silently dropped them below AA.
  // This is the floor instead: as dim as possible while still passing on the
  // lightest surface it is painted on.
  const backgrounds = [surface.surface2, surface.surface1, surface.surface]
  let ink = surface.onSurfaceMuted
  for (let weight = 0; weight <= 0.9; weight += 0.01) {
    const candidate = mixToward(surface.surface, surface.onSurfaceMuted, weight)
    if (!backgrounds.every(background => contrastRatio(candidate, background) >= 4.6)) break
    ink = candidate
  }
  return ink
}

export function toneTokens(tone: ThemeTone, dark: boolean) {
  const hue = TONE_HUES[tone]
  const surface = dark ? DARK_SURFACE : LIGHT_SURFACE
  const wash = washFor(hue, surface.surface)
  return {
    hue,
    wash,
    // The eyebrow/step ink is also painted on its own wash, so solve for a
    // weight that clears AA there instead of tuning per hue.
    ink: readableInk(hue, surface.onSurfaceHeading, [
      surface.surface,
      surface.surface1,
      blendOver(wash, surface.surface),
      blendOver(wash, surface.surface1),
    ]),
  }
}

export function resolveThemeTokens(mode: 'dark' | 'light', accent: ThemeAccent): ResolvedThemeTokens {
  const dark = mode === 'dark'
  const surface = dark ? DARK_SURFACE : LIGHT_SURFACE
  const palette = THEME_PALETTES[accent][dark ? 'dark' : 'light']
  return {
    ...surface,
    ...palette,
    // `primary` is a fill; as text it fails AA on light surfaces (the light
    // pink reaches only 2.86:1). The ink is solved against every background it
    // is actually painted on, so accent text is legible by construction.
    primaryInk: readableInk(palette.primaryInk, surface.onSurfaceHeading, [
      surface.surface,
      surface.surface1,
      blendOver(palette.primarySoft, surface.surface),
    ]),
    // Both themes need a near-black alternative: dark fills are pastel, so
    // white labels fail there; light fills are saturated, so they often do too.
    onPrimary: inkOn(palette.primary, dark ? surface.surface : surface.onSurfaceHeading),
    onSurfaceFaint: faintInk(surface),
    isDark: dark,
  }
}


export function getThemeAccentPreview(accent: ThemeAccent, dark: boolean) {
  return THEME_PALETTES[accent][dark ? 'dark' : 'light'].preview
}


function isThemeMode(value: string | null | undefined): value is ThemeMode {
  return value === 'system' || value === 'dark' || value === 'light'
}

function isThemeAccent(value: string | null | undefined): value is ThemeAccent {
  return value === 'blue' || value === 'pink' || value === 'sky' || value === 'teal'
}

export function normalizeThemeMode(value: string | null | undefined, fallback: ThemeMode = DEFAULT_THEME_MODE): ThemeMode {
  return isThemeMode(value) ? value : fallback
}

export function normalizeThemeAccent(
  value: string | null | undefined,
  fallback: ThemeAccent = DEFAULT_THEME_ACCENT,
): ThemeAccent {
  return isThemeAccent(value) ? value : fallback
}

export function themeOverridesFor(tokens: ResolvedThemeTokens): GlobalThemeOverrides {
  // Keep component surfaces and solid primary-button labels on the same dark base.
  const darkBase = tokens.isDark ? {
    baseColor: tokens.surface,
    textColorBase: tokens.onSurface,
  } : {}
  return {
    common: {
      ...darkBase,
      bodyColor: tokens.surface,
      cardColor: tokens.surface1,
      modalColor: tokens.surface1,
      popoverColor: tokens.surface1,
      tableColor: tokens.surface1,
      inputColor: tokens.surface2,
      actionColor: tokens.surface2,
      hoverColor: tokens.surface2,
      dividerColor: tokens.outline,
      borderColor: tokens.outline,
      textColor1: tokens.onSurfaceHeading,
      textColor2: tokens.isDark ? tokens.onSurface : tokens.onSurfaceMuted,
      textColor3: tokens.onSurfaceMuted,
      primaryColor: tokens.primary,
      primaryColorHover: tokens.primaryHover,
      primaryColorPressed: tokens.primaryPressed,
      primaryColorSuppl: tokens.primary,
      successColor: tokens.success,
      warningColor: tokens.warning,
      errorColor: tokens.danger,
      placeholderColor: tokens.onSurfaceMuted,
      tableHeaderColor: tokens.surface2,
    },
    Card: {
      paddingMedium: '20px',
      borderRadius: '16px',
      titleTextColor: tokens.onSurface,
      color: tokens.surface1,
      borderColor: tokens.outline,
    },
    Button: {
      borderRadius: '11px',
      textColorPrimary: tokens.onPrimary,
      textColor: tokens.onSurface,
      color: tokens.surface2,
      borderColor: tokens.outline,
      colorHover: tokens.surface3,
      textColorHover: tokens.onSurface,
      ...(tokens.isDark ? {
        textColor: tokens.onSurfaceHeading,
        textColorHover: tokens.onSurfaceHeading,
        textColorPressed: tokens.onSurfaceHeading,
        textColorFocus: tokens.onSurfaceHeading,
        colorSecondary: tokens.surface2,
        colorSecondaryHover: tokens.surface3,
        colorSecondaryPressed: tokens.surface2,
        colorDisabled: tokens.surface2,
        colorDisabledPrimary: tokens.surface2,
        textColorDisabled: tokens.onSurfaceMuted,
        textColorDisabledPrimary: tokens.onSurfaceMuted,
        borderDisabledPrimary: `1px solid ${tokens.outline}`,
        opacityDisabled: '0.55',
      } : {}),
    },
    Input: {
      borderRadius: '11px',
      color: tokens.surface2,
      textColor: tokens.onSurface,
      border: `1px solid ${tokens.outline}`,
      borderHover: `1px solid ${tokens.primary}`,
      borderFocus: `1px solid ${tokens.primary}`,
    },
    Select: {
      borderRadius: '11px',
      menuColor: tokens.surface1,
    },
    Tag: {
      borderRadius: '8px',
      // Soft tag backgrounds need text darker/lighter than the raw status
      // colour alone; 62% toward the heading colour keeps the hue readable in
      // both themes (WCAG AA for the small labels these tags carry).
      textColorSuccess: mixToward(tokens.success, tokens.onSurfaceHeading, 0.62),
      textColorWarning: mixToward(tokens.warning, tokens.onSurfaceHeading, 0.62),
      textColorInfo: mixToward(tokens.primary, tokens.onSurfaceHeading, 0.62),
      textColorError: mixToward(tokens.danger, tokens.onSurfaceHeading, 0.62),
    },
    Progress: {
      railColor: tokens.surface3,
    },
    Menu: {
      itemColorActive: tokens.primarySoft,
      itemTextColorActive: tokens.primaryInk,
      itemTextColor: tokens.onSurfaceMuted,
      itemColorHover: tokens.surface2,
      itemTextColorHover: tokens.onSurface,
      borderRadius: '10px',
    },
    Steps: {
      stepHeaderFontSizeSmall: '14px',
      stepIndicatorTextColorFinished: tokens.primary,
      stepHeaderTextColorFinished: tokens.onSurface,
      stepIndicatorBorderColorFinished: tokens.primary,
      connectorColor: tokens.outline,
    },
    Collapse: {
      titleTextColor: tokens.onSurface,
      titleTextColorDisabled: tokens.onSurfaceMuted,
      dividerColor: tokens.outline,
      borderRadius: '12px',
    },
    DataTable: {
      tdColor: tokens.surface1,
      thColor: tokens.surface2,
      borderColor: tokens.outline,
    },
    Slider: {
      railColor: tokens.surface3,
    },
    Switch: {
      railColor: tokens.surface3,
      railColorActive: tokens.primary,
    },
    InputNumber: {
      color: tokens.surface2,
      border: `1px solid ${tokens.outline}`,
      borderHover: `1px solid ${tokens.primary}`,
      borderFocus: `1px solid ${tokens.primary}`,
    },
  }
}
