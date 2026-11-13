import assert from 'node:assert/strict'
import test from 'node:test'
import { makeRuntimeActions } from '../src/views/settings/runtimeActions.ts'

for (const [platform, arch, backend, asset, expected] of [
  ['windows', 'x86_64', 'cuda', 'cuda', 'managed'],
  ['windows', 'x86_64', 'cuda', null, 'unsupported'],
  ['windows', 'x86_64', 'cpu', null, 'worker'],
  ['windows', 'x86_64', 'rocm', null, 'worker'],
  ['linux', 'x86_64', 'cuda', null, 'worker'],
  ['linux', 'x86_64', 'rocm', null, 'worker'],
  ['windows', 'aarch64', 'cuda', null, 'unsupported'],
  ['linux', 'aarch64', 'rocm', null, 'unsupported'],
  ['macos', 'aarch64', 'mlx', null, 'unsupported'],
  ['macos', 'x86_64', 'cuda', null, 'unsupported'],
]) {
  test(`${platform}/${arch}/${backend} uses the ${expected} install route`, async () => {
    const calls = []
    const app = {
      runtimeManagementSupported: platform !== 'macos',
      async installRuntime(...args) { calls.push(['worker', ...args]) },
    }
    const actions = makeRuntimeActions({
      app,
      t: key => key,
      message: { error: key => calls.push(['error', key]) },
      dialog: { warning: ({ onPositiveClick }) => {
        calls.push(['confirm'])
        onPositiveClick()
      } },
      currentLocale: { value: 'zh-CN' },
      installedRuntimes: { value: [] },
      runtimeBackendLabel: value => value,
      runtimeBusy: { value: false },
      runtimeCurrentBackend: { value: null },
      runtimeMirror: { value: 'auto' },
      runtimeAssets: {
        async status(selected) {
          calls.push(['status', selected])
          return { platform, arch, available: asset !== null, backend: asset, size: 1024 }
        },
        async install(selected) { calls.push(['managed', selected]) },
      },
    })
    await actions.installBackend(backend)
    const result = expected === 'unsupported'
      ? [['error', 'settings.runtimeInstallTargetUnsupported']]
      : [['confirm'], expected === 'managed' ? ['managed', backend] : ['worker', backend, 'auto', 'zh-CN']]
    assert.deepEqual(calls, [['status', backend], ...result])
  })
}
