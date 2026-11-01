/**
 * 外观区块（缩放滑杆、主题/语言切换）的契约。
 *
 * normalizeScaleFactor 由视图注入，这里用同样的夹取规则（0.75..1.5，默认 1），
 * 这样测试不依赖 Tauri 的 webview 模块。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeAppearance, SCALE_FACTOR_PRESET_VALUES, dirName } from '../src/views/settings/appearance.ts'

// 涟漪原点需要视口尺寸；测试环境没有浏览器，给一个固定视口。
globalThis.window = { innerWidth: 1280, innerHeight: 720 }

const computed = source => (typeof source === 'function'
  ? { get value() { return source() } }
  : { get value() { return source.get() }, set value(v) { source.set(v) } })
const normalizeScaleFactor = (value, fallback = 1) => {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  return Math.min(1.5, Math.max(0.75, n))
}

function harness(overrides = {}) {
  const applied = []
  const transitions = []
  const deps = {
    computed,
    t: key => key,
    normalizeScaleFactor,
    DEFAULT_SCALE_FACTOR: 1,
    scaleFactor: { value: 1 },
    themeMode: { value: 'dark' },
    themeAccent: { value: 'blue' },
    locale: { value: 'system' },
    currentLocale: { value: 'zh-CN' },
    animationsEnabled: { value: true },
    languageSelectWrap: { value: null },
    applyTheme: (...args) => applied.push(['theme', ...args]),
    setLocale: (...args) => applied.push(['locale', ...args]),
    runRippleViewTransition: async (fn, origin) => { transitions.push(origin); fn() },
    ...overrides,
  }
  return { deps, applied, transitions, appearance: makeAppearance(deps) }
}

test('the slider index snaps to the exact preset, or the nearest one', () => {
  const { deps, appearance } = harness()
  deps.scaleFactor.value = 1.25
  assert.equal(appearance.scaleSliderIndex.value, 4)
  deps.scaleFactor.value = 1.22
  assert.equal(appearance.scaleSliderIndex.value, 4)
  deps.scaleFactor.value = 0.85
  assert.equal(appearance.scaleSliderIndex.value, 1)
  appearance.scaleSliderIndex.value = 0
  assert.equal(deps.scaleFactor.value, 0.75)
})

test('marks and the percent label are rendered from the normalized factor', () => {
  const { deps, appearance } = harness()
  deps.scaleFactor.value = 1.4
  assert.equal(appearance.scaleFactorPercent.value, '140%')
  assert.deepEqual(Object.values(appearance.scaleSliderMarks.value), ['75%', '90%', '100%', '110%', '125%', '150%'])
  assert.equal(appearance.isDefaultScaleFactor.value, false)
  deps.scaleFactor.value = 1
  assert.equal(appearance.isDefaultScaleFactor.value, true)
})

test('scale updates are clamped and reset returns to the default', () => {
  const { deps, appearance } = harness()
  appearance.updateScaleFactor(9)
  assert.equal(deps.scaleFactor.value, 1.5)
  appearance.updateScaleFactor(0.1)
  assert.equal(deps.scaleFactor.value, 0.75)
  appearance.updateScaleFactor(1.3)
  appearance.resetScaleFactorToDefault()
  assert.equal(deps.scaleFactor.value, 1)
})

test('theme switches no-op on the current value and ripple otherwise', async () => {
  const { deps, applied, transitions, appearance } = harness()
  await appearance.selectThemeMode('dark', {})
  assert.equal(applied.length, 0)
  await appearance.selectThemeMode('light', {})
  assert.deepEqual(applied, [['theme', 'light', 'blue']])
  assert.equal(transitions.length, 1)
  await appearance.selectThemeAccent('green', {})
  assert.deepEqual(applied[1], ['theme', 'light', 'green'])
})

test('animations disabled means no ripple origin', async () => {
  const { transitions, appearance } = harness({ animationsEnabled: { value: false } })
  await appearance.selectThemeMode('light', {})
  assert.deepEqual(transitions, [undefined])
})

test('locale switches apply once and skip the current locale', async () => {
  const { applied, appearance } = harness()
  await appearance.selectLocale('system')
  assert.equal(applied.length, 0)
  await appearance.selectLocale('en')
  assert.deepEqual(applied, [['locale', 'en']])
})

test('dirName keeps only the last segment and falls back on empty input', () => {
  assert.equal(dirName('D:/models/sub', 'models'), 'sub')
  assert.equal(dirName('D:\\models\\sub\\', 'models'), 'sub')
  assert.equal(dirName('', 'models'), 'models')
  assert.equal(dirName('///', 'models'), 'models')
})
