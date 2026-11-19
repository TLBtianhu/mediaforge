import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { mediaPreviewUrl } from '../src/utils/mediaPreview.ts'

const config = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
const security = config.app.security
const csp = security.csp
const devCsp = security.devCsp

test('production CSP is a real policy, not null', () => {
  assert.equal(typeof csp, 'string')
  for (const directive of ['default-src', 'script-src', 'style-src', 'img-src', 'media-src', 'connect-src', 'frame-src']) {
    assert.match(csp, new RegExp(`(?:^|;)\\s*${directive}\\s`), `missing ${directive}`)
  }
  assert.match(csp, /script-src[^;]*'self'/)
  assert.doesNotMatch(csp, /script-src[^;]*'unsafe-eval'/)
  assert.match(csp, /connect-src[^;]*ipc:/)
  assert.match(csp, /frame-src[^;]*pymss-plugin/)
})

test('dev CSP allows the Vite websocket but keeps eval out of production', () => {
  assert.equal(typeof devCsp, 'string')
  assert.match(devCsp, /connect-src[^;]*ws:\/\/localhost:1420/)
  assert.match(devCsp, /script-src[^;]*'unsafe-eval'/)
})

test('only preview fetches and media elements can use the preview protocol', () => {
  assert.deepEqual(security.assetProtocol, { enable: false, scope: [] })
  const preview = [
    'mediaforge-preview:',
    'http://mediaforge-preview.localhost',
    'https://mediaforge-preview.localhost',
  ]
  for (const policy of [csp, devCsp]) {
    assert.doesNotMatch(policy, /(?:\basset:|\basset\.localhost|\bfile:)/)
    for (const clause of policy.split(';')) {
      const [directive, ...sources] = clause.trim().split(/\s+/)
      for (const source of preview) {
        assert.equal(sources.includes(source), ['img-src', 'media-src', 'connect-src'].includes(directive), directive)
      }
    }
  }
})

test('the native URL converter receives the dedicated preview protocol', t => {
  const path = 'C:\\音频 & media\\sample #1.wav'
  const convertFileSrc = t.mock.fn((value, protocol) => `${protocol}://localhost/${encodeURIComponent(value)}`)
  const window = globalThis.window
  globalThis.window = { __TAURI_INTERNALS__: { convertFileSrc } }
  t.after(() => {
    if (window === undefined) delete globalThis.window
    else globalThis.window = window
  })
  assert.equal(mediaPreviewUrl(''), '')
  assert.equal(mediaPreviewUrl(path), `mediaforge-preview://localhost/${encodeURIComponent(path)}`)
  assert.deepEqual(convertFileSrc.mock.calls.map(call => call.arguments), [[path, 'mediaforge-preview']])
})
