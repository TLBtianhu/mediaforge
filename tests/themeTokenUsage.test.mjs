import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')

test('runtime banner and settings states use theme tokens', () => {
  const banner = read('src/components/RuntimeAssetBanner.vue')
  const version = read('src/components/settings/VersionUpdateCard.vue')
  const plugins = read('src/views/PluginsView.vue')
  const settings = read('src/views/settings/settings-view.css')
  for (const source of [banner, version, plugins, settings]) {
    assert.match(source, /--on-surface|--success|--warning|--danger/)
  }
  assert.doesNotMatch(banner, /#e8e8ea|#9a9aa2|#b6b6bd|#f07178/)
  assert.doesNotMatch(version, /--text-color-3|#f07178/)
  assert.doesNotMatch(plugins, /#d86262|#449f77/)
  assert.doesNotMatch(settings, /#22c55e|#f2c94c/)
})
