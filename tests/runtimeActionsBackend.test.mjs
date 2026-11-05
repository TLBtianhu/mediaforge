import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('CUDA settings install uses the shared runtime asset path', () => {
  const source = readFileSync(new URL('../src/views/settings/runtimeActions.ts', import.meta.url), 'utf8')
  assert.match(source, /backend === 'cuda'/)
  assert.match(source, /invoke\('runtime_asset_install'\)/)
  assert.match(source, /其它后端暂保留旧路径/)
})
