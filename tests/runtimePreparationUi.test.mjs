import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('runtime preparation progress is shared by banner and settings', () => {
  const banner = readFileSync(new URL('../src/components/RuntimeAssetBanner.vue', import.meta.url), 'utf8')
  const settings = readFileSync(new URL('../src/views/SettingsView.vue', import.meta.url), 'utf8')
  const component = readFileSync(new URL('../src/components/RuntimePreparationProgress.vue', import.meta.url), 'utf8')
  assert.match(banner, /RuntimePreparationProgress/)
  assert.match(settings, /RuntimePreparationProgress/)
  assert.match(component, /--primary-soft/)
  assert.match(component, /runtime-preparation-progress__actions/)
})
