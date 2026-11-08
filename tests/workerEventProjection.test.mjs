import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
})
after(async () => { await vite.close() })

test('generated worker events match the protocol manifest', async () => {
  const manifest = JSON.parse(readFileSync(new URL('../manifest/protocol.json', import.meta.url), 'utf8'))
  const module = await vite.ssrLoadModule('/src/types/workerEvents.generated.ts')
  assert.deepEqual([...module.CANONICAL_WORKER_EVENTS], manifest.events.canonical)
})

test('task event helpers only accept canonical names', async () => {
  const { isTaskEvent, isTaskProgress, isTaskResult } = await vite.ssrLoadModule('/src/utils/taskEvents.ts')
  assert.equal(isTaskEvent('task.stage'), true)
  assert.equal(isTaskEvent('task.unknown'), false)
  assert.equal(isTaskProgress('task.progress'), true)
  assert.equal(isTaskResult('task.result'), true)
  assert.equal(isTaskResult('task.done'), false)
})
