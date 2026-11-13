import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

describe('worker error presentation', () => {
  it('keeps the raw settings error behind a collapsed technical-details block', () => {
    const view = read('views/SettingsView.vue')
    assert.match(view, /<details class="settings-schema-alert__details">/)
    assert.match(view, /<summary>\{\{ t\('common\.technicalDetails'\) \}\}<\/summary>/)
    assert.match(view, /<pre>\{\{ settings\.loadError \}\}<\/pre>/)
    assert.doesNotMatch(view, /class="settings-schema-alert__detail"/)
  })

  it('shows a localized library error with a retry button and collapsed details', () => {
    const view = read('views/AssetsView.vue')
    assert.match(view, /const libraryError = ref\(''\)/)
    assert.match(view, /libraryErrorIsSchema/)
    assert.match(view, /libraryErrorTitle/)
    assert.match(view, /libraryErrorHint/)
    assert.match(view, /<details class="assets-library-alert__details">/)
    assert.match(view, /<pre>\{\{ libraryError \}\}<\/pre>/)
    assert.match(view, /t\('common\.retry'\)/)
    assert.doesNotMatch(view, /class="assets-library-alert__detail"/)
  })

  it('ships the new strings in both locales', () => {
    for (const locale of ['zh-CN', 'en']) {
      const messages = JSON.parse(read(`i18n/${locale}.json`))
      for (const key of ['libraryTitle', 'librarySchemaTitle', 'librarySchemaHint', 'libraryErrorHint']) {
        assert.equal(typeof messages.assets[key], 'string', `${locale}.assets.${key}`)
        assert.ok(messages.assets[key].length > 0, `${locale}.assets.${key}`)
      }
      assert.equal(typeof messages.common.technicalDetails, 'string', `${locale}.common.technicalDetails`)
    }
  })
})
