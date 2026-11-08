import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { validateStoredSettings } from '../src/stores/settingsSchema.ts'

describe('current app settings schema', () => {
  it('accepts absent and current settings without mutating them', () => {
    validateStoredSettings(null)
    for (const value of [
      { version: 1 },
      { version: 1, themeMode: 'dark', themeAccent: 'teal', locale: 'zh-CN', maxConcurrentSeparations: 4 },
      { version: 1, beginnerGuideProgress: { status: 'active', step: 0, stepId: 'workspace' } },
      { version: 1, beginnerGuideProgress: { status: 'active', step: 0, stepId: 'inference', parameter: 'chunk_size' } },
    ]) {
      const before = structuredClone(value)
      validateStoredSettings(value)
      assert.deepEqual(value, before)
    }
  })

  it('rejects missing, old, future and structurally invalid settings', () => {
    const invalid = [
      {}, [], true, 'current',
      { version: 0 }, { version: 2 }, { version: '1' },
      { version: 1, startupOnboardingSeenVersion: '0.0.21' },
      { version: 1, futureSetting: true },
      { version: 1, themeMode: 'blue' },
      { version: 1, scaleFactor: 4 },
      { version: 1, maxConcurrentSeparations: 0 },
      { version: 1, maxConcurrentSeparations: 1.5 },
      { version: 1, beginnerGuideProgress: { status: 'old', step: 0 } },
      { version: 1, beginnerGuideProgress: { status: 'active', step: 0, stepId: 'removed-step' } },
    ]
    for (const value of invalid) {
      const before = structuredClone(value)
      assert.throws(() => validateStoredSettings(value), /Unsupported app settings schema/)
      assert.deepEqual(value, before)
    }
  })
})
