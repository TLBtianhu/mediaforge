import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

const calls = []
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    __TAURI_INTERNALS__: {
      async invoke(command, args) {
        calls.push({ command, args })
        return { taskId: args.payload.requestId, started: true }
      },
    },
  },
})

const { startModelHubDownload } = await import('../src/features/downloads/startModelHubDownload.ts')
const { waitForDeleteTask } = await import('../src/features/downloads/waitForDeleteTask.ts')

test('model hub download uses the Tauri payload envelope with the complete worker request', async () => {
  calls.length = 0
  const request = { requestId: 'model-1', modelId: 'media:lama', modelDir: null, source: 'modelscope' }
  const result = await startModelHubDownload(request)
  assert.deepEqual(calls, [{ command: 'start_model_hub_download', args: { payload: request } }])
  assert.deepEqual(result, { taskId: 'model-1', started: true })
})

test('an already-done delete task resolves during immediate watcher registration', async () => {
  const task = ref({ taskId: 'delete-1', status: 'done', message: 'Deleted' })
  const result = await waitForDeleteTask(() => task.value, 'delete-1', 50)
  assert.equal(result.status, 'done')
})

test('an already-cancelled delete task rejects with its own error instead of a stop-handle TDZ error', async () => {
  const task = ref({ taskId: 'delete-2', status: 'cancelled', message: 'Cancelled by user' })
  await assert.rejects(
    waitForDeleteTask(() => task.value, 'delete-2', 50),
    error => {
      assert.equal(error.message, 'Cancelled by user')
      return true
    },
  )
})
