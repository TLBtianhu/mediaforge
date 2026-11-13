import assert from 'node:assert/strict'
import { afterEach, beforeEach, describe, it } from 'node:test'
import { loadAppStore, saveAppStore } from '../src/utils/appStore.ts'

const values = new Map<string, string>()

beforeEach(() => {
  values.clear()
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {},
  })
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  })
})

afterEach(() => {
  Reflect.deleteProperty(globalThis, 'window')
  Reflect.deleteProperty(globalThis, 'localStorage')
})

describe('app store persistence', () => {
  it('captures a JSON snapshot when a save is queued', async () => {
    const state = { nested: { value: 1 } }
    const save = saveAppStore('app-settings', state)
    state.nested.value = 2

    await save

    assert.deepEqual(JSON.parse(values.get('pymss-studio:app-settings') || 'null'), {
      nested: { value: 1 },
    })
  })

  it('preserves call order for consecutive writes to the same store', async () => {
    await Promise.all([
      saveAppStore('model-state', { revision: 1 }),
      saveAppStore('model-state', { revision: 2 }),
    ])

    assert.deepEqual(JSON.parse(values.get('pymss-studio:model-state') || 'null'), {
      revision: 2,
    })
  })

  it('surfaces an incompatible backend without reading or overwriting preview preferences', async () => {
    const invoke = async () => {
      throw 'worker error: unknown app store: update-state'
    }
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { __TAURI_INTERNALS__: { invoke } },
    })
    values.set('pymss-studio:update-state', JSON.stringify({ deferredVersion: '0.0.14' }))

    await assert.rejects(loadAppStore('update-state'))
    await assert.rejects(saveAppStore('update-state', { lastAcceptedVersion: '0.0.14' }))
    assert.deepEqual(JSON.parse(values.get('pymss-studio:update-state') || 'null'), {
      deferredVersion: '0.0.14',
    })
  })

  it('rejects corrupt or primitive media workflow preview data instead of treating it as a new empty library', async () => {
    for (const raw of ['{broken', 'null', '42', 'true', '"old-format"', '', '   ']) {
      values.set('pymss-studio:media-workflows', raw)
      await assert.rejects(loadAppStore('media-workflows'), undefined, `stored value ${JSON.stringify(raw)}`)
      assert.equal(values.get('pymss-studio:media-workflows'), raw)
    }
    values.delete('pymss-studio:media-workflows')
    assert.equal(await loadAppStore('media-workflows'), null)
    values.set('pymss-studio:media-workflows', JSON.stringify({ version: 1, presets: [] }))
    assert.deepEqual(await loadAppStore('media-workflows'), { version: 1, presets: [] })
  })

  it('rejects primitive native media workflow responses while allowing an absent library', async () => {
    for (const value of [42, true, 'old-format']) {
      Object.defineProperty(globalThis, 'window', {
        configurable: true,
        value: { __TAURI_INTERNALS__: { invoke: async () => value } },
      })
      await assert.rejects(loadAppStore('media-workflows'))
    }
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { __TAURI_INTERNALS__: { invoke: async () => null } },
    })
    assert.equal(await loadAppStore('media-workflows'), null)
  })

  it('keeps the established lenient browser fallback for unrelated app stores', async () => {
    values.set('pymss-studio:model-state', '{broken')
    assert.equal(await loadAppStore('model-state'), null)
  })
})
