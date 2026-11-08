import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { Window } from 'happy-dom'
import { createServer, transformWithEsbuild } from 'vite'
import { compileScript, parse } from 'vue/compiler-sfc'

const browser = new Window({ url: 'http://localhost' })
for (const key of ['window', 'document', 'navigator', 'Node', 'Element', 'HTMLElement', 'SVGElement', 'MutationObserver', 'HTMLCanvasElement']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: key === 'window' ? browser : browser[key] })
}

let themeColor = '#ff0000'
globalThis.getComputedStyle = () => ({ getPropertyValue: (name) => (name === '--primary' ? themeColor : '') })

const frames = []
globalThis.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length }
globalThis.cancelAnimationFrame = () => {}
function flushFrames() {
  const pending = frames.splice(0)
  pending.forEach((callback) => callback(0))
}

const contexts = new Map()
globalThis.HTMLCanvasElement.prototype.getContext = function getContext() {
  if (!contexts.has(this)) {
    contexts.set(this, { fillStyle: '', setTransform() {}, clearRect() {}, fillRect() {} })
  }
  return contexts.get(this)
}

const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'waveform-component',
    async transform(code, id) {
      if (!id.replaceAll('\\', '/').endsWith('/src/components/editor/EditorWaveform.vue')) return null
      const { descriptor } = parse(code, { filename: id })
      const script = compileScript(descriptor, { id: 'waveform-test', inlineTemplate: true })
      return transformWithEsbuild(script.content, `${id}.ts`, { loader: 'ts', tsconfigRaw: {} })
    },
  }],
})
after(async () => {
  await vite.close()
  await browser.happyDOM.abort()
})

const { createApp, h } = await import('vue')
const Waveform = (await vite.ssrLoadModule('/src/components/editor/EditorWaveform.vue')).default

function repaintColors() {
  flushFrames()
  return [...contexts.values()].map((context) => context.fillStyle).filter(Boolean)
}

test('waveform repaints when the theme tokens change', async () => {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({
    render: () => h(Waveform, {
      peaks: [0.2, 0.5, 0.9, 0.4, 0.7], assetDuration: 10, duration: 10, width: 64, height: 16,
    }),
  })
  app.mount(container)
  try {
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.ok(repaintColors().includes('#ff0000'), 'the first draw must use the current token')

    themeColor = '#00ff00'
    document.documentElement.setAttribute('data-theme', 'dark')
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.ok(repaintColors().includes('#00ff00'), 'a theme change must repaint with the new token')
  } finally {
    app.unmount()
    container.remove()
  }
})
