import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { detectRuntimePlatform } from '../src/utils/runtime.ts'

function componentFunction(file, name, scope) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name)
  assert.ok(declaration, `${name} must exist`)
  const code = ts.transpileModule(declaration.getText(parsed), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return vm.runInNewContext(`${code}\n${name}`, scope)
}

function componentComputed(file, name, scope) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const declaration = parsed.statements
    .filter(ts.isVariableStatement)
    .flatMap(node => [...node.declarationList.declarations])
    .find(node => node.name.getText(parsed) === name)
  assert.ok(declaration?.initializer, `${name} must exist`)
  const code = ts.transpileModule(`const ${name} = ${declaration.initializer.getText(parsed)}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return vm.runInNewContext(`${code}\n${name}`, { computed: fn => fn(), ...scope })
}

test('deferred update preflight keeps busy feedback visible', async () => {
  const scope = {
    deferredUpdateInstalling: { value: false }, deferredUpdateError: { value: '' }, deferredUpdateModalVisible: { value: true },
    updates: { installationBlockedReason: 'busy', downloadAndInstall() { assert.fail('busy update must not start') } },
  }
  await componentFunction('../src/App.vue', 'installDeferredUpdate', scope)()
  assert.equal(scope.deferredUpdateModalVisible.value, true)
  assert.equal(scope.deferredUpdateError.value, 'busy')
})

test('deferred early errors reopen the prompt when no install overlay owns the error', async () => {
  for (const overlay of [false, true]) {
    const scope = {
      deferredUpdateInstalling: { value: false }, deferredUpdateError: { value: '' }, deferredUpdateModalVisible: { value: true },
      updates: { installationBlockedReason: '', isInstallOverlayVisible: overlay, downloadAndInstall: async () => { throw new Error('update unavailable') } },
    }
    await componentFunction('../src/App.vue', 'installDeferredUpdate', scope)()
    assert.equal(scope.deferredUpdateModalVisible.value, !overlay)
    assert.match(scope.deferredUpdateError.value, /update unavailable/)
  }
})

test('the ASR page only offers removal for removable components', () => {
  for (const [status, expected] of [
    [null, false],
    [{ installed: false, present: false, canUninstall: false }, false],
    [{ installed: true, present: true, source: 'environment', canUninstall: false }, false],
    [{ installed: true, present: true, source: 'environment', canUninstall: true }, true],
    [{ installed: true, present: true, source: 'managed', canUninstall: true }, true],
    [{ installed: true }, true],
  ]) {
    assert.equal(componentComputed('../src/features/audio-tools/tools/asr/AsrTool.vue', 'funAsrCanUninstall', {
      funAsrStatus: { value: status },
    }), expected)
  }
})

for (const [platform, machine, expected] of [
  ['darwin', 'arm64', ['mlx']],
  ['darwin', 'aarch64', ['mlx']],
  ['darwin', 'x86_64', ['mlx']],
  ['darwin', '', ['mlx']],
  ['win32', 'AMD64', ['cpu', 'cuda', 'rocm']],
  ['linux', 'x86_64', ['cpu', 'cuda', 'rocm']],
]) {
  test(`${platform}/${machine} settings and onboarding offer the intended runtime environments`, () => {
    const scope = {
      app: { runtimeInfo: { platform, machine } },
      runtimePlatform: { value: detectRuntimePlatform({ platform, machine }) },
      runtimeBackendName: backend => backend,
      runtimeBackendDescription: backend => backend,
      t: key => key,
      backendInstalled: () => true,
      runtimeRecommendedBackend: { value: expected[0] },
      runtimeSizeHint: () => '',
    }
    const catalog = componentComputed('../src/views/SettingsView.vue', 'runtimeBackendCatalog', scope)
    const choices = componentComputed('../src/components/StartupOnboarding.vue', 'runtimeBackendChoices', scope)
    assert.deepEqual(Array.from(catalog, item => item.backend), expected)
    assert.deepEqual(Array.from(choices, item => item.value), expected)
  })
}

test('onboarding offers only MLX on Intel and unidentified Macs', () => {
  for (const machine of ['x86_64', '']) {
    const choices = componentComputed('../src/components/StartupOnboarding.vue', 'runtimeBackendChoices', {
      runtimePlatform: { value: detectRuntimePlatform({ platform: 'darwin', machine }) },
      runtimeBackendName: backend => backend, t: key => key, backendInstalled: () => false,
      runtimeRecommendedBackend: { value: 'mlx' }, runtimeSizeHint: () => '',
    })
    assert.deepEqual(Array.from(choices, item => item.value), ['mlx'])
  }
})

for (const platform of ['darwin', 'linux', 'win32']) {
  for (const installed of [false, true]) {
    test(`${platform} onboarding hands ${installed ? 'an installed' : 'a missing'} runtime to management`, async () => {
      const actions = []
      const app = {
        runtimeManagementSupported: platform === 'win32' || platform === 'linux',
        runtimeReadyForBackend: () => false,
        runtimeInfo: { installedEnvironments: [{ backend: 'cpu', pythonPath: '/runtime/cpu/bin/python' }] },
        activateRuntime: async () => actions.push('activate'),
        installRuntime: async () => actions.push('install'),
      }
      const primary = componentFunction('../src/components/StartupOnboarding.vue', 'primaryAction', {
        app,
        currentStep: { value: 'runtime' }, canGoNext: { value: true },
        selectedBackendReady: { value: false },
        finishOnboarding: async (_event, skip, manage) => actions.push({ skip, manage }),
      })
      await primary({})
      assert.deepEqual(actions, [{ skip: true, manage: true }])
    })
  }
}

test('unsupported platforms skip core update prompts', () => {
  const actions = []
  const scope = { app: { runtimeCoreUpdateSupported: false, updateRuntimeCore: async () => actions.push('update') }, runtimeCorePromptVisible: { value: false } }
  componentFunction('../src/App.vue', 'showRuntimeCorePrompt', scope)()
  assert.deepEqual(actions, [])
})

for (const backend of ['mlx', 'cpu']) {
  test(`${backend} startup requests confirmation and never starts a core synchronization`, () => {
    const actions = []
    const scope = {
      app: { runtimeManagementSupported: false, runtimeCoreUpdateSupported: true, updateRuntimeCore: async (...args) => actions.push(args) },
      runtimeCorePromptShown: { value: false },
      bootReady: { value: true },
      showStartupOnboarding: { value: false },
      isStandaloneRoute: { value: false },
      runtimeManifestSyncRequired: { value: true },
      runtimeCoreUpdateAvailable: { value: true },
      runtimeCorePromptBlocked: { value: false },
      activeRuntime: { value: { backend, pythonPath: '/runtime/bin/python3' } },
      runtimeCorePromptVisible: { value: false },
      beginnerGuide: { visible: false },
      settings: { beginnerGuideInvitationSeen: true },
    }
    componentFunction('../src/App.vue', 'showRuntimeCorePrompt', scope)()
    assert.equal(actions.length, 0)
    assert.equal(scope.runtimeCorePromptVisible.value, true)
  })
}


test('runtime notices wait for updates and reads, and close once the installed core is current', () => {
  for (const status of ['preparing', 'prepared', 'updating']) {
    const blocked = componentComputed('../src/App.vue', 'runtimeCorePromptBlocked', {
      app: { runtimeCoreUpdateStatus: status, runtimeInfoLoading: false, runtimeCoreVersionsLoading: false },
    })
    assert.equal(blocked, true)
  }
  for (const loading of ['runtimeInfoLoading', 'runtimeCoreVersionsLoading']) {
    assert.equal(componentComputed('../src/App.vue', 'runtimeCorePromptBlocked', {
      app: { runtimeCoreUpdateStatus: 'success', [loading]: true },
    }), true)
  }
  for (const [blocked, available] of [[true, true], [false, false]]) {
    const scope = {
      app: { runtimeCoreUpdateSupported: true },
      runtimeCorePromptShown: { value: true }, runtimeCorePromptVisible: { value: true },
      runtimeCorePromptBlocked: { value: blocked }, runtimeCoreUpdateAvailable: { value: available },
      bootReady: { value: true }, showStartupOnboarding: { value: false }, isStandaloneRoute: { value: false },
    }
    componentFunction('../src/App.vue', 'showRuntimeCorePrompt', scope)()
    assert.equal(scope.runtimeCorePromptVisible.value, false)
  }
})


test('every confirmation dismissal settles the pending runtime operation', async () => {
  for (const event of ['onMaskClick', 'onEsc', 'onClose', 'onNegativeClick', 'onPositiveClick']) {
    let options
    const confirm = componentFunction('../src/views/SettingsView.vue', 'confirmRuntimeAction', {
      dialog: { warning: value => { options = value } }, t: key => key,
    })
    const result = confirm('Update core', 'Version plan', 'Confirm')
    assert.equal(typeof options[event], 'function', `${event} must settle the confirmation`)
    options[event]()
    assert.equal(await result, event === 'onPositiveClick')
  }
})

test('non-Windows developer controls cannot open an editor or mutate runtime files', async () => {
  const scope = { app: { runtimeManagementSupported: true, runtimeDebugSupported: false } }
  for (const name of ['openRuntimeDebugEditor', 'saveRuntimeDebugEditor', 'restoreRuntimeDebugFile', 'overrideActiveRuntimePointer']) {
    await componentFunction('../src/views/DebugView.vue', name, scope)({ editable: true, backupExists: true })
  }
})
