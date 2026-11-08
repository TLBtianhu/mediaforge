import assert from 'node:assert/strict'
import test, { after, beforeEach } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
})
after(() => vite.close())

const callbacks = new Map()
const listeners = new Map()
let callbackId = 0
const starts = []
let startFailure

Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    __TAURI_INTERNALS__: {
      transformCallback(handler) { callbacks.set(++callbackId, handler); return callbackId },
      async invoke(command, args) {
        if (command === 'plugin:event|listen') {
          listeners.set(args.event, callbacks.get(args.handler))
          return args.handler
        }
        if (command === 'plugin:event|unlisten') {
          listeners.delete(args.event)
          return null
        }
        if (command === 'start_operation_task') {
          if (startFailure) {
            const failure = startFailure
            startFailure = undefined
            throw failure
          }
          starts.push(args.payload)
          return { taskId: args.payload.requestId, queued: true }
        }
        throw new Error(`Unexpected IPC command: ${command}`)
      },
    },
    __TAURI_EVENT_PLUGIN_INTERNALS__: { unregisterListener() {} },
  },
})

const { usePluginOps, stopPluginOps } = await vite.ssrLoadModule('/src/features/plugin-ops/composable.ts')

function emit(index, type, payload) {
  const request = starts[index]
  assert.ok(request, `operation ${index} has not started`)
  const listener = listeners.get('pymss://worker-event')
  assert.ok(listener, 'the plugin-ops listener must be registered')
  listener({ payload: { type, requestId: request.requestId, payload } })
}

async function waitForStart(count) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (starts.length >= count) return
    await new Promise(resolve => setTimeout(resolve, 1))
  }
  throw new Error(`operation ${count} never started`)
}

function pendingRun(options = {}) {
  return usePluginOps().run({
    operation: options.operation || 'fixture.score',
    params: {},
    inputs: {},
    onProgress: options.onProgress,
  })
}

beforeEach(() => {
  // Waits from a previous test must not leak into the next one.
  stopPluginOps('test-reset')
  starts.length = 0
  startFailure = undefined
})

test('plugin operations consume the canonical progress and result events emitted by the worker', async () => {
  const progress = []
  const running = pendingRun({ onProgress: value => progress.push(value) })
  await waitForStart(1)
  emit(0, 'task.progress', { fraction: 0.5, message: 'half' })
  emit(0, 'task.result', { operation: starts[0].operation, outputs: { score: { kind: 'scalar', value: 42 } }, outputDir: 'D:/out' })
  const result = await running
  assert.deepEqual(progress, [{ fraction: 0.5, message: 'half' }])
  assert.equal(result.outputs.score.value, 42)
  assert.equal(starts[0].operation, 'fixture.score')
})

test('concurrent operations keep independent waits and results', async () => {
  const firstProgress = []
  const secondProgress = []
  const first = pendingRun({ operation: 'fixture.first', onProgress: value => firstProgress.push(value.fraction) })
  const second = pendingRun({ operation: 'fixture.second', onProgress: value => secondProgress.push(value.fraction) })
  await waitForStart(2)
  assert.notEqual(starts[0].requestId, starts[1].requestId)
  emit(1, 'task.progress', { fraction: 0.75, message: '' })
  emit(0, 'task.progress', { fraction: 0.25, message: '' })
  emit(1, 'task.result', { outputs: { value: { kind: 'scalar', value: 2 } } })
  emit(0, 'task.result', { outputs: { value: { kind: 'scalar', value: 1 } } })
  assert.deepEqual(firstProgress, [0.25])
  assert.deepEqual(secondProgress, [0.75])
  assert.equal((await first).outputs.value.value, 1)
  assert.equal((await second).outputs.value.value, 2)
})

test('a result carrying failed entries rejects instead of reporting success', async () => {
  const running = pendingRun()
  await waitForStart(1)
  emit(0, 'task.result', { failed: [{ input: 'a.wav', error: 'decode failed' }], outputs: {} })
  await assert.rejects(running, /decode failed/)
})

test('a recoverable error keeps waiting and is reported as progress', async () => {
  const progress = []
  const running = pendingRun({ onProgress: value => progress.push(value) })
  await waitForStart(1)
  emit(0, 'task.progress', { fraction: 0.4, message: 'working' })
  emit(0, 'error', { recoverable: true, message: 'retrying subprocess' })
  emit(0, 'task.result', { outputs: { value: { kind: 'scalar', value: 7 } } })
  assert.equal((await running).outputs.value.value, 7)
  assert.deepEqual(progress, [
    { fraction: 0.4, message: 'working' },
    { fraction: 0.4, message: 'retrying subprocess' },
  ])
})

test('a fatal error and a worker cancellation both end the wait', async () => {
  const failing = pendingRun()
  await waitForStart(1)
  emit(0, 'error', { message: 'worker exploded' })
  await assert.rejects(failing, /worker exploded/)

  const cancelled = pendingRun()
  await waitForStart(2)
  emit(1, 'task.cancelled', { message: 'Cancelled' })
  await assert.rejects(cancelled, /cancelled/)
})

test('closing the page rejects every pending operation', async () => {
  const first = pendingRun({ operation: 'fixture.closed' })
  await waitForStart(1)
  stopPluginOps()
  await assert.rejects(first, /PLUGIN_OPS_CLOSED/)
  // A later page can still use the bridge.
  const later = pendingRun({ operation: 'fixture.reopened' })
  await waitForStart(2)
  emit(1, 'task.result', { outputs: { value: { kind: 'scalar', value: 9 } } })
  assert.equal((await later).outputs.value.value, 9)
})

test('a rejected submission fails the wait instead of leaving it pending', async () => {
  startFailure = new Error('worker unavailable')
  await assert.rejects(pendingRun(), /worker unavailable/)
  assert.equal(starts.length, 0)
})
