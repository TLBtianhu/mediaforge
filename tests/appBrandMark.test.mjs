import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { Window } from 'happy-dom'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript } from 'vue/compiler-sfc'

const browser = new Window({ url: 'http://localhost' })
for (const key of ['window', 'document', 'navigator', 'Node', 'Element', 'HTMLElement', 'SVGElement']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? browser : browser[key] })
}
let systemDark = false
const scheme = new browser.EventTarget()
Object.defineProperty(scheme, 'matches', { get: () => systemDark })
Object.defineProperty(browser, 'matchMedia', { configurable: true, value: () => scheme })
const { createApp, h, nextTick, reactive } = await import('vue')
const vite = await createServer({
  configFile: false, server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom', optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{ name: 'brand-mark-component', async transform(code, id) {
    if (!id.replaceAll('\\', '/').endsWith('/src/components/AppBrandMark.vue')) return null
    const { descriptor } = parse(code, { filename: id })
    const script = compileScript(descriptor, { id: 'brand-mark-test', inlineTemplate: true })
    return transformWithEsbuild(script.content, `${id}.ts`, { loader: 'ts', tsconfigRaw: {} })
  } }],
})
after(async () => { await vite.close(); await browser.happyDOM.abort() })
const Mark = (await vite.ssrLoadModule('/src/components/AppBrandMark.vue')).default
const theme = await vite.ssrLoadModule('/src/utils/theme.ts')

function mount(t, props = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const state = reactive(props)
  const app = createApp({ render: () => h(Mark, state) })
  app.mount(container)
  t.after(() => { app.unmount(); container.remove() })
  return { container, props: state, mark: () => container.querySelector('.app-brand-mark'), image: () => container.querySelector('img') }
}
async function systemScheme(dark) {
  systemDark = dark
  scheme.dispatchEvent(new browser.Event('change'))
  await nextTick()
}
const imageName = env => new URL(env.image().src).pathname.split('/').at(-1)

test('an already mounted mark switches original image assets with the applied theme', async t => {
  theme.applyTheme('light')
  const marks = ['default', 'compact', 'hero'].map(variant => mount(t, { variant, size: 22 }))
  const originalElements = marks.map(env => env.image())
  assert.deepEqual(marks.map(imageName), Array(3).fill('app-logo-light.png'))
  theme.applyTheme('dark')
  await nextTick()
  assert.deepEqual(marks.map(imageName), Array(3).fill('app-logo-dark.png'))
  assert.deepEqual(marks.map(env => env.image()), originalElements)
  theme.applyTheme('light')
  await nextTick()
  assert.deepEqual(marks.map(imageName), Array(3).fill('app-logo-light.png'))
})

test('the size, variant and optional shadow API remains reactive', async t => {
  const env = mount(t)
  assert.equal(env.mark().style.getPropertyValue('--mark-size'), '32px')
  assert.equal(env.mark().dataset.variant, 'default')
  assert.equal(env.mark().classList.contains('app-brand-mark--shadow'), false)
  Object.assign(env.props, { size: 42, variant: 'hero', shadow: true })
  await nextTick()
  assert.equal(env.mark().style.getPropertyValue('--mark-size'), '42px')
  assert.equal(env.mark().dataset.variant, 'hero')
  assert.equal(env.mark().classList.contains('app-brand-mark--shadow'), true)
  assert.equal(env.mark().getAttribute('aria-hidden'), 'true')
  assert.equal(env.image().alt, '')
})

test('system theme changes update the logo while explicit themes keep their chosen asset', async t => {
  systemDark = false
  theme.initTheme('system')
  const env = mount(t, { size: 22, variant: 'compact' })
  assert.equal(imageName(env), 'app-logo-light.png')
  await systemScheme(true)
  assert.equal(imageName(env), 'app-logo-dark.png')
  await systemScheme(false)
  assert.equal(imageName(env), 'app-logo-light.png')
  theme.applyTheme('light')
  await systemScheme(true)
  assert.equal(imageName(env), 'app-logo-light.png')
  theme.applyTheme('dark')
  await systemScheme(false)
  assert.equal(imageName(env), 'app-logo-dark.png')
})
