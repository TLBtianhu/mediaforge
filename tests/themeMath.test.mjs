import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { createServer } from 'vite'

// Colour-mixing contract: interpolation must happen in a perceptually uniform
// space. sRGB/HSL lerps rotate hue toward neutral anchors and give different
// perceived steps per hue, which is what the OKLab implementation replaces.
const vite = await createServer({
  configFile: false, appType: 'custom', logLevel: 'error',
  optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
})
after(() => vite.close())

const { mixToward, blendOver, withAlpha, contrastRatio } = await vite.ssrLoadModule('/src/utils/themeMath.ts')

const HEX = /^#[0-9a-f]{6}$/
const channels = (hex) => [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16))

test('the endpoints are returned exactly', () => {
  assert.equal(mixToward('#e58a00', '#172033', 1), '#e58a00')
  assert.equal(mixToward('#e58a00', '#172033', 0), '#172033')
})

test('mixing toward a neutral anchor keeps the hue instead of rotating it', () => {
  // Amber mixed with the light heading colour must stay warm. A polar (OKLCH)
  // interpolation toward a near-gray anchor rotates it toward the anchor's
  // meaningless hue, which previously produced pink text.
  const warm = mixToward('#e58a00', '#e5e7eb', 0.5)
  const [red, green, blue] = channels(warm)
  assert.ok(red > green && green > blue, `expected a warm mix, got ${warm}`)

  const cool = mixToward('#3568ff', '#e5e7eb', 0.5)
  const [coolRed, , coolBlue] = channels(cool)
  assert.ok(coolBlue > coolRed, `expected a cool mix, got ${cool}`)
})

test('one weight means one perceived lightness step across hues', () => {
  const hues = ['#3568ff', '#d56f9f', '#4d8fd8', '#1f7d6f']
  const anchor = '#ffffff'
  // Perceptually uniform interpolation keeps the lightness ordering and lands
  // every hue at a comparable contrast step; sRGB lerps scatter these values.
  const ratios = hues.map((hue) => contrastRatio(hue, anchor))
  const mixed = hues.map((hue) => contrastRatio(mixToward(hue, anchor, 0.5), anchor))
  const spread = (values) => Math.max(...values) - Math.min(...values)
  assert.ok(
    spread(mixed) < spread(ratios),
    `mixing should compress the contrast spread (${spread(ratios).toFixed(2)} -> ${spread(mixed).toFixed(2)})`,
  )
})

test('mixing toward white raises luminance and toward black lowers it', () => {
  const base = '#3f70d4'
  const lighter = mixToward(base, '#ffffff', 0.7)
  const darker = mixToward(base, '#000000', 0.7)
  const luminance = (hex) => {
    const [red, green, blue] = channels(hex)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
  }
  assert.ok(luminance(lighter) > luminance(base))
  assert.ok(luminance(darker) < luminance(base))
})

test('extreme mixes stay inside the gamut', () => {
  for (const [color, anchor, weight] of [
    ['#00ff00', '#ffffff', 0.98],
    ['#ff00ff', '#000000', 0.02],
    ['#0000ff', '#ffff00', 0.5],
  ]) {
    assert.match(mixToward(color, anchor, weight), HEX)
  }
})

test('alpha compositing still happens in sRGB, not OKLab', () => {
  // Compositing is physics; only palette mixing needs perceptual interpolation.
  assert.equal(blendOver(withAlpha('#000000', 0.5), '#ffffff'), '#808080')
  assert.equal(blendOver('#336699', '#ffffff'), '#336699')
})
