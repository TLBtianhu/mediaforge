import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

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
  assert.match(csp, /img-src[^;]*asset:/)
  assert.match(csp, /frame-src[^;]*pymss-plugin/)
})

test('dev CSP allows the Vite websocket but keeps eval out of production', () => {
  assert.equal(typeof devCsp, 'string')
  assert.match(devCsp, /connect-src[^;]*ws:\/\/localhost:1420/)
  assert.match(devCsp, /script-src[^;]*'unsafe-eval'/)
})
