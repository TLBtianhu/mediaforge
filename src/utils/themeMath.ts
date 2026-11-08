export type Rgb = { r: number; g: number; b: number }

/** Parse `#rrggbb`; anything else (including named colours) returns null. */
export function parseHexColor(value: string): Rgb | null {
  const match = /^#([0-9a-f]{6})$/i.exec(value.trim())
  if (!match) return null
  return {
    r: parseInt(match[1].slice(0, 2), 16),
    g: parseInt(match[1].slice(2, 4), 16),
    b: parseInt(match[1].slice(4, 6), 16),
  }
}

export function toHex({ r, g, b }: Rgb): string {
  const channel = (part: number) => Math.max(0, Math.min(255, Math.round(part))).toString(16).padStart(2, '0')
  return `#${channel(r)}${channel(g)}${channel(b)}`
}

type Oklab = { l: number; a: number; b: number }

function srgbToLinear(channel: number): number {
  const value = channel / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

function linearToSrgb(channel: number): number {
  const value = channel <= 0.0031308 ? 12.92 * channel : 1.055 * channel ** (1 / 2.4) - 0.055
  return value * 255
}

function toOklab({ r, g, b }: Rgb): Oklab {
  const red = srgbToLinear(r)
  const green = srgbToLinear(g)
  const blue = srgbToLinear(b)
  const l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
  const m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
  const s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
  const lRoot = Math.cbrt(l)
  const mRoot = Math.cbrt(m)
  const sRoot = Math.cbrt(s)
  return {
    l: 0.2104542553 * lRoot + 0.793617785 * mRoot - 0.0040720468 * sRoot,
    a: 1.9779984951 * lRoot - 2.428592205 * mRoot + 0.4505937099 * sRoot,
    b: 0.0259040371 * lRoot + 0.7827717662 * mRoot - 0.808675766 * sRoot,
  }
}

function fromOklab({ l, a, b }: Oklab): Rgb {
  const lRoot = l + 0.3963377774 * a + 0.2158037573 * b
  const mRoot = l - 0.1055613458 * a - 0.0638541728 * b
  const sRoot = l - 0.0894841775 * a - 1.291485548 * b
  const ll = lRoot ** 3
  const mm = mRoot ** 3
  const ss = sRoot ** 3
  return {
    r: linearToSrgb(4.0767416621 * ll - 3.3077115913 * mm + 0.2309699292 * ss),
    g: linearToSrgb(-1.2684380046 * ll + 2.6097574011 * mm - 0.3413193965 * ss),
    b: linearToSrgb(-0.0041960863 * ll - 0.7034186147 * mm + 1.707614701 * ss),
  }
}

/**
 * Mix `color` toward `anchor` by `weight` of `color`, interpolating in Oklab.
 *
 * sRGB and HSL lerps are not perceptually uniform: equal steps shift lightness
 * (and even hue) differently per hue, which is why shading in those spaces needs
 * hand-tuning. Oklab keeps perceived lightness and chroma independent, so one
 * fixed weight behaves the same for every accent (see docs/theme-research.md).
 *
 * It is deliberately the cartesian form rather than OKLCH: interpolating hue is
 * meaningless when either endpoint is near-neutral, and a gray anchor would
 * otherwise rotate an accent toward the gray's arbitrary hue.
 */
export function mixToward(color: string, anchor: string, weight: number): string {
  const from = parseHexColor(color)
  const to = parseHexColor(anchor)
  if (!from || !to) return color
  const t = Math.max(0, Math.min(1, weight))
  if (t >= 0.999) return toHex(from)
  if (t <= 0.001) return toHex(to)
  const start = toOklab(from)
  const end = toOklab(to)
  return toHex(fromOklab({
    l: start.l * t + end.l * (1 - t),
    a: start.a * t + end.a * (1 - t),
    b: start.b * t + end.b * (1 - t),
  }))
}

/** Oklab lightness (0 = black, 1 = white) — the perceptual step unit. */
export function oklabLightness(color: string): number {
  const rgb = parseHexColor(color)
  return rgb ? toOklab(rgb).l : 0
}

/** `#rrggbb` + alpha to a CSS `rgba()` string. */
export function withAlpha(color: string, alpha: number): string {
  const rgb = parseHexColor(color)
  if (!rgb) return color
  const a = Math.max(0, Math.min(1, alpha))
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${Number(a.toFixed(3))})`
}

function channelLuminance(part: number): number {
  const normalized = part / 255
  return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
}

export function relativeLuminance(color: string): number {
  const rgb = parseHexColor(color)
  if (!rgb) return 0
  return 0.2126 * channelLuminance(rgb.r) + 0.7152 * channelLuminance(rgb.g) + 0.0722 * channelLuminance(rgb.b)
}

export function contrastRatio(first: string, second: string): number {
  const a = relativeLuminance(first)
  const b = relativeLuminance(second)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** White or `darkInk`, whichever is readable on `background`. */
export function inkOn(background: string, darkInk: string): string {
  return contrastRatio(background, '#ffffff') >= contrastRatio(background, darkInk)
    ? '#ffffff'
    : darkInk
}

/** Parse `#rrggbb` or `rgb()/rgba()`; anything else returns null. */
function parseCssColor(value: string): Rgb & { a: number } | null {
  const hex = parseHexColor(value)
  if (hex) return { ...hex, a: 1 }
  const match = /^rgba?\(([^)]+)\)$/i.exec(value.trim())
  if (!match) return null
  const [r, g, b, a = 1] = match[1].split(/[ ,/]+/).map(Number)
  return { r, g, b, a }
}

/** Composite a possibly translucent colour over an opaque one. */
export function blendOver(foreground: string, background: string): string {
  const top = parseCssColor(foreground)
  const bottom = parseCssColor(background)
  if (!top || !bottom) return foreground
  const blend = (a: number, b: number) => a * top.a + b * (1 - top.a)
  return toHex({ r: blend(top.r, bottom.r), g: blend(top.g, bottom.g), b: blend(top.b, bottom.b) })
}

/**
 * Mix `hue` toward `darkInk` just enough that the result clears `target`
 * contrast on every surface it will be painted on. Guarantees ink by
 * construction instead of a hand-tuned weight per hue.
 */
export function readableInk(hue: string, darkInk: string, backgrounds: string[], target = 4.6): string {
  // Lower weight = closer to the anchor (near-black in light, near-white in
  // dark), which is the direction that raises contrast in both themes.
  for (let weight = 0.5; weight >= 0.04; weight -= 0.02) {
    const ink = mixToward(hue, darkInk, weight)
    if (backgrounds.every(background => contrastRatio(ink, background) >= target)) return ink
  }
  return mixToward(hue, darkInk, 0.04)
}
