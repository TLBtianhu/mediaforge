/**
 * 设置页运行时区块的派生状态与动作契约。
 *
 * 这两块逻辑原来长在 SettingsView.vue 里（约 380 行），现在由工厂承接：
 * 测试直接实例化工厂、喂一份运行时快照，断言卡片状态与确认闸门。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeRuntimeState } from '../src/views/settings/runtimeState.ts'
import { makeRuntimeActions } from '../src/views/settings/runtimeActions.ts'

const computed = source => ({ get value() { return source() } })
const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

function stateFor(app, overrides = {}) {
  return makeRuntimeState({
    computed,
    t,
    app: { runtimeEnvSizes: {}, runtimeIncompleteBackends: [], ...app },
    runtimeManualChecking: { value: false },
    runtimeActivating: { value: null },
    runtimeDeleting: { value: null },
    ...overrides,
  })
}

function env(overrides = {}) {
  return {
    backend: 'cuda',
    pythonPath: 'C:/runtimes/cuda/python.exe',
    health: 'ok',
    manifestVersion: '2026.10.2',
    pymssVersion: '2.1.2',
    pymssCoreVersion: '0.1.0',
    ...overrides,
  }
}

test('cards follow the installed environments and mark the active one', () => {
  const state = stateFor({
    runtimeInfo: {
      platform: 'win32', machine: 'AMD64', manifestVersion: '2026.10.2',
      installedBackend: 'cuda', installState: { backend: 'cuda', pythonPath: 'C:/runtimes/cuda/python.exe' },
      installedEnvironments: [env(), env({ backend: 'cpu', pythonPath: 'C:/runtimes/cpu/python.exe' })],
    },
    runtimeEnvSizes: { cuda: 3 * 1024 ** 3, cpu: 900 * 1024 ** 2 },
  })
  const cards = state.runtimeBackendCards.value
  const byBackend = Object.fromEntries(cards.map(card => [card.backend, card]))
  assert.equal(byBackend.cuda.state, 'active')
  assert.equal(byBackend.cpu.state, 'installed')
  assert.equal(byBackend.rocm.state, 'not_installed')
  assert.equal(byBackend.cuda.diskLabel, '3.0 GB')
  assert.equal(byBackend.cuda.pymssVersion, '2.1.2')
  assert.equal(byBackend.cuda.manifestOutdated, false)
  assert.equal(state.runtimeCurrentBackend.value, 'cuda')
  // 两个以上环境才显示合计；单个环境卡片自己就够。
  assert.equal(state.runtimeDiskTotalLabel.value, '3.9 GB')
})

test('an environment outside the platform catalog stays reachable and is flagged', () => {
  const state = stateFor({
    runtimeInfo: {
      platform: 'darwin', machine: 'arm64',
      installedEnvironments: [env({ backend: 'mlx', pythonPath: 'C:/runtimes/mlx/python.exe' }), env({ backend: 'cuda' })],
    },
  })
  const cards = state.runtimeBackendCards.value
  assert.deepEqual(cards.map(card => card.backend), ['mlx', 'cuda'])
  // 不在平台目录里的环境也要能管：保留卡片并标注 offCatalog。
  assert.equal(cards[1].offCatalog, true)
  // offCatalog 卡片自带"非本平台"说明，不再叠一条厂商缺失提示。
  assert.equal(cards[1].vendorMissing, false)
  assert.equal(cards[0].vendorMissing, false)
})

test('a leftover install surfaces its reclaimable size even without an environment', () => {
  const state = stateFor({
    runtimeInfo: { platform: 'win32', machine: 'AMD64', installedEnvironments: [] },
    runtimeIncompleteBackends: ['cuda'],
    runtimeEnvSizes: { cuda: 2 * 1024 ** 3 },
  })
  const cuda = state.runtimeBackendCards.value.find(card => card.backend === 'cuda')
  assert.equal(cuda.leftover, true)
  assert.equal(cuda.leftoverLabel, '2.0 GB')
  assert.equal(cuda.state, 'not_installed')
  assert.equal(cuda.key, 'cuda:none')
})

test('a manifest mismatch marks the card for a core sync', () => {
  const state = stateFor({
    runtimeInfo: {
      platform: 'win32', machine: 'AMD64', manifestVersion: '2026.10.3',
      installedEnvironments: [env()],
    },
  })
  const cuda = state.runtimeBackendCards.value.find(card => card.backend === 'cuda')
  assert.equal(cuda.manifestOutdated, true)
  // 环境比应用清单旧：提示同步核心，而不是"环境更新"。
  assert.equal(cuda.manifestNewer, false)
})

test('the page-file error is translated and keeps the card without a backend', () => {
  const state = stateFor({
    runtimeInfo: { platform: 'win32', machine: 'AMD64', torchBackend: 'error: CUDA driver WinError 1455' },
    envInfo: { torchBackend: 'error: CUDA driver WinError 1455' },
  })
  assert.equal(state.runtimeCurrentError.value, 'settings.runtimePageFileError')
  assert.equal(state.runtimeCurrentLabel.value, 'settings.runtimeLoadFailed')
  assert.equal(state.runtimeCurrentBackend.value, null)
})

test('busy state is the union of detection, install and the switch/delete flags', () => {
  const app = { runtimeInfo: { platform: 'win32', machine: 'AMD64' }, runtimeInfoLoading: false, runtimeInstallStatus: 'idle' }
  assert.equal(stateFor(app).runtimeBusy.value, false)
  assert.equal(stateFor(app, { runtimeManualChecking: { value: true } }).runtimeBusy.value, true)
  assert.equal(stateFor({ ...app, runtimeInstallStatus: 'installing' }).runtimeBusy.value, true)
  assert.equal(stateFor(app, { runtimeActivating: { value: 'cuda' } }).runtimeBusy.value, true)
  assert.equal(stateFor(app, { runtimeDeleting: { value: 'cuda' } }).runtimeBusy.value, true)
})

test('deleting the active environment is refused before any confirmation', async () => {
  const warnings = []
  const dialogs = []
  const activeEnv = env()
  const app = {
    runtimeManagementSupported: true,
    runtimeIncompleteBackends: [],
    runtimeInfo: {
      platform: 'win32', installedBackend: 'cuda',
      installState: { backend: 'cuda', pythonPath: 'C:/runtimes/cuda/python.exe' },
      installedEnvironments: [activeEnv],
    },
    deleteRuntime: async () => { throw new Error('must not be called') },
  }
  const { deleteRuntime } = makeRuntimeActions({
    app, t, runtimeBusy: { value: false }, runtimeCurrentBackend: { value: 'cuda' },
    installedRuntimes: { value: app.runtimeInfo.installedEnvironments },
    runtimeBackendLabel: backend => backend, runtimeDeleting: { value: null },
    message: { warning: value => warnings.push(value), error: value => warnings.push(value), success: () => {} },
    dialog: { warning: options => dialogs.push(options) },
  })
  await deleteRuntime({ backend: 'cuda', env: activeEnv })
  assert.deepEqual(warnings, ['settings.runtimeDeleteActiveError'])
  assert.equal(dialogs.length, 0)
})

test('a leftover cleanup is described as cleanup, not as losing an environment', async () => {
  const dialogs = []
  const deleted = []
  const app = {
    runtimeManagementSupported: true,
    runtimeIncompleteBackends: ['cuda'],
    runtimeInfo: { platform: 'win32', installedEnvironments: [] },
    deleteRuntime: async (backend, options) => { deleted.push([backend, options]) },
    checkRuntimeInfo: async () => {}, checkEnv: async () => {}, loadRuntimeEnvSizes: async () => {},
  }
  const { deleteRuntime } = makeRuntimeActions({
    app, t, runtimeBusy: { value: false }, runtimeCurrentBackend: { value: null },
    installedRuntimes: { value: [] }, runtimeBackendLabel: backend => backend,
    runtimeDeleting: { value: null },
    message: { warning: () => {}, error: () => {}, success: () => {} },
    dialog: { warning: options => { dialogs.push(options); queueMicrotask(() => options.onPositiveClick()) } },
  })
  await deleteRuntime({ backend: 'cuda' })
  assert.equal(dialogs.length, 1)
  assert.equal(dialogs[0].title, 'settings.runtimeCleanLeftoverTitle')
  assert.equal(dialogs[0].positiveText, 'settings.runtimeCleanLeftover')
  assert.deepEqual(deleted, [['cuda', { pythonPath: undefined }]])
})
