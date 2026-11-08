import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

// The theme contract is arithmetic, not screenshots: every accent/tone pair must
// keep its ink readable on the surfaces it is actually painted on. This runs in
// milliseconds and fails the build before a colour reaches a window.
const vite = await createServer({
  configFile: false, appType: 'custom', logLevel: 'error',
  optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
})
after(() => vite.close())

const tokensModule = await vite.ssrLoadModule('/src/utils/themeTokens.ts')
const math = await vite.ssrLoadModule('/src/utils/themeMath.ts')
const canvas = await vite.ssrLoadModule('/src/litegraph/canvasTheme.ts')
const { resolveThemeTokens, toneTokens, THEME_ACCENTS, THEME_TONES, TONE_WASH_STEP } = tokensModule
const { contrastRatio, parseHexColor, oklabLightness } = math

const AA = 4.5
const AA_LARGE = 3

function parseColor(value) {
  const rgb = /^#([0-9a-f]{6})$/i.exec(String(value).trim())
  if (rgb) {
    return {
      r: parseInt(rgb[1].slice(0, 2), 16),
      g: parseInt(rgb[1].slice(2, 4), 16),
      b: parseInt(rgb[1].slice(4, 6), 16),
      a: 1,
    }
  }
  const rgba = /^rgba?\(([^)]+)\)$/i.exec(String(value).trim())
  if (!rgba) throw new Error(`unsupported colour for the contract test: ${value}`)
  const [r, g, b, a = 1] = rgba[1].split(/[ ,/]+/).map(Number)
  return { r, g, b, a }
}

function hexOf({ r, g, b }) {
  const channel = (part) => Math.round(part).toString(16).padStart(2, '0')
  return `#${channel(r)}${channel(g)}${channel(b)}`
}

/** Composite a possibly translucent colour over an opaque one. */
function over(foreground, background) {
  const top = parseColor(foreground)
  const bottom = parseColor(background)
  const blend = (a, b) => Math.round(a * top.a + b * (1 - top.a))
  return hexOf({ r: blend(top.r, bottom.r), g: blend(top.g, bottom.g), b: blend(top.b, bottom.b) })
}

function writtenTokens(themeTokens) {
  return {
    surface: themeTokens.surface,
    surface1: themeTokens.surface1,
    surface2: themeTokens.surface2,
    primary: themeTokens.primary,
    primaryInk: themeTokens.primaryInk,
    primaryHover: themeTokens.primaryHover,
    primaryPressed: themeTokens.primaryPressed,
    primarySoft: themeTokens.primarySoft,
    onPrimary: themeTokens.onPrimary,
    onSurface: themeTokens.onSurface,
    onSurfaceHeading: themeTokens.onSurfaceHeading,
    onSurfaceMuted: themeTokens.onSurfaceMuted,
    success: themeTokens.success,
    warning: themeTokens.warning,
    danger: themeTokens.danger,
  }
}

for (const mode of ['dark', 'light']) {
  for (const accent of THEME_ACCENTS) {
    test(`${mode}/${accent}: ink stays readable on every surface it is painted on`, () => {
      const tokens = writtenTokens(resolveThemeTokens(mode, accent))
      const tinted = over(tokens.primarySoft, tokens.surface)

      for (const [name, ink] of Object.entries({
        primaryInk: tokens.primaryInk,
        onSurface: tokens.onSurface,
        onSurfaceHeading: tokens.onSurfaceHeading,
        onSurfaceMuted: tokens.onSurfaceMuted,
      })) {
        assert.ok(
          contrastRatio(ink, tokens.surface) >= AA,
          `${name} (${ink}) on surface (${tokens.surface}) is ${contrastRatio(ink, tokens.surface).toFixed(2)}:1`,
        )
        assert.ok(
          contrastRatio(ink, tinted) >= AA,
          `${name} (${ink}) on primarySoft (${tinted}) is ${contrastRatio(ink, tinted).toFixed(2)}:1`,
        )
      }

      for (const [name, ink] of Object.entries({
        onSurface: tokens.onSurface,
        onSurfaceMuted: tokens.onSurfaceMuted,
        success: tokens.success,
        warning: tokens.warning,
        danger: tokens.danger,
      })) {
        for (const surface of [tokens.surface1, tokens.surface2]) {
          assert.ok(
            contrastRatio(ink, surface) >= AA,
            `${name} (${ink}) on ${surface} is ${contrastRatio(ink, surface).toFixed(2)}:1`,
          )
        }
      }

      // The label must stay readable across the derived interaction ramp.
      for (const [state, fill] of Object.entries({
        fill: tokens.primary,
        hover: tokens.primaryHover,
        pressed: tokens.primaryPressed,
      })) {
        assert.ok(
          contrastRatio(tokens.onPrimary, fill) >= AA,
          `onPrimary (${tokens.onPrimary}) on ${state} (${fill}) is ${contrastRatio(tokens.onPrimary, fill).toFixed(2)}:1`,
        )
      }
    })
  }
}

for (const mode of ['dark', 'light']) {
  for (const accent of THEME_ACCENTS) {
    test(`${mode}/${accent}: faint ink is the dimmest legible step`, () => {
      const tokens = resolveThemeTokens(mode, accent)
      for (const background of [tokens.surface, tokens.surface1, tokens.surface2]) {
        assert.ok(
          contrastRatio(tokens.onSurfaceFaint, background) >= AA,
          `onSurfaceFaint (${tokens.onSurfaceFaint}) on ${background} is ${contrastRatio(tokens.onSurfaceFaint, background).toFixed(2)}:1`,
        )
      }
      assert.ok(
        contrastRatio(tokens.onSurfaceFaint, tokens.surface) < contrastRatio(tokens.onSurfaceMuted, tokens.surface),
        'faint must be dimmer than muted; otherwise dimming was not actually expressed',
      )
    })
  }
}

for (const mode of ['dark', 'light']) {
  const dark = mode === 'dark'
  for (const tone of THEME_TONES) {
    test(`${mode}/tone ${tone}: derived accent ink is readable`, () => {
      const surface = resolveThemeTokens(mode, 'blue').surface
      const surface1 = resolveThemeTokens(mode, 'blue').surface1
      const toneTokensValue = toneTokens(tone, dark)
      const wash = over(toneTokensValue.wash, surface)
      // Ink is text and must clear AA everywhere, including on its own wash.
      for (const background of [surface, surface1, wash]) {
        assert.ok(
          contrastRatio(toneTokensValue.ink, background) >= AA,
          `tone ${tone} ink (${toneTokensValue.ink}) on ${background} is ${contrastRatio(toneTokensValue.ink, background).toFixed(2)}:1`,
        )
      }
      // One wash step for every hue: equal alpha used to read twice as strong
      // for amber as for blue, so the tint is solved from a perceptual step.
      for (const background of [surface, surface1]) {
        const delta = Math.abs(
          oklabLightness(over(toneTokensValue.wash, background)) - oklabLightness(background),
        )
        assert.ok(
          Math.abs(delta - TONE_WASH_STEP) < 0.012,
          `tone ${tone} wash over ${background} is ΔL ${delta.toFixed(4)}, expected ≈${TONE_WASH_STEP}`,
        )
      }
    })
  }
}

test('token values are concrete colours, not deferred CSS functions', () => {
  for (const mode of ['dark', 'light']) {
    for (const accent of THEME_ACCENTS) {
      const tokens = resolveThemeTokens(mode, accent)
      for (const name of ['primaryInk', 'onPrimary']) {
        assert.ok(parseHexColor(tokens[name]), `${mode}/${accent}.${name} must be a hex colour, got ${tokens[name]}`)
      }
    }
  }
})

test('global.scss defaults match the default theme tokens', () => {
  // global.scss is the first-paint fallback; if it drifts from the palette the
  // pre-paint frame silently disagrees with the app. Its defaults are the dark
  // theme with the default accent.
  const scss = readFileSync(new URL('../src/styles/global.scss', import.meta.url), 'utf8')
  const defaultTokens = resolveThemeTokens('dark', 'blue')
  const hexDefaults = [
    ['--primary', defaultTokens.primary],
    ['--primary-ink', defaultTokens.primaryInk],
    ['--on-primary', defaultTokens.onPrimary],
    ['--on-surface-faint', defaultTokens.onSurfaceFaint],
    ['--primary-hover', defaultTokens.primaryHover],
    ['--primary-pressed', defaultTokens.primaryPressed],
  ]
  for (const [name, expected] of hexDefaults) {
    const found = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6});`).exec(scss)
    assert.ok(found, `global.scss is missing ${name}`)
    assert.equal(found[1].toLowerCase(), expected.toLowerCase(), `${name} default drifts from the token module`)
  }
  const rgbaDefaults = [
    ['--primary-soft', defaultTokens.primarySoft],
    ['--primary-softer', defaultTokens.primarySofter],
    ['--primary-border', defaultTokens.primaryBorder],
    ['--primary-glow', defaultTokens.primaryGlow],
  ]
  const numbers = (value) => value.replaceAll(' ', '').match(/[0-9.]+/g).map(Number)
  for (const [name, expected] of rgbaDefaults) {
    const found = new RegExp(`${name}:\\s*(rgba\\([^)]+\\));`).exec(scss)
    assert.ok(found, `global.scss is missing ${name}`)
    assert.deepEqual(numbers(found[1]), numbers(expected), `${name} default drifts from the token module`)
  }
  for (const tone of THEME_TONES) {
    const darkTokens = toneTokens(tone, true)
    const hue = new RegExp(`--tone-hue-${tone}:\\s*(#[0-9a-fA-F]{6});`).exec(scss)
    assert.ok(hue, `global.scss is missing --tone-hue-${tone}`)
    assert.equal(hue[1].toLowerCase(), darkTokens.hue.toLowerCase())
    const wash = new RegExp(`--tone-wash-${tone}:\\s*(rgba\\([^)]+\\));`).exec(scss)
    assert.ok(wash, `global.scss is missing --tone-wash-${tone}`)
    assert.equal(wash[1].replaceAll(' ', ''), darkTokens.wash.replaceAll(' ', ''))
  }
})

test('canvas palette is derived from the theme tokens and repaintable', () => {
  const { canvasThemeFor, CANVAS_EVENT_LINK_COLOR } = canvas
  for (const mode of ['dark', 'light']) {
    const tokens = resolveThemeTokens(mode, 'blue')
    const theme = canvasThemeFor(tokens)
    assert.equal(theme.background, tokens.surface)
    assert.equal(theme.nodeBackground, tokens.surface1)
    assert.equal(theme.nodeTitle, tokens.surface2)
    assert.equal(theme.link, tokens.primary)
    assert.equal(theme.eventLink, CANVAS_EVENT_LINK_COLOR)
    // Node titles and body text must stay readable on both node surfaces.
    for (const surface of [theme.nodeBackground, theme.nodeTitle]) {
      assert.ok(
        contrastRatio(theme.text, surface) >= AA,
        `canvas text (${theme.text}) on ${surface} is ${contrastRatio(theme.text, surface).toFixed(2)}:1`,
      )
    }
  }
  assert.notEqual(canvasThemeFor(resolveThemeTokens('dark', 'blue')).nodeBackground,
                  canvasThemeFor(resolveThemeTokens('light', 'blue')).nodeBackground)
})
