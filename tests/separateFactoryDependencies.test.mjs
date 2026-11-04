import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const view = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
const factory = (name) => readFileSync(new URL(`../src/views/separate/${name}.ts`, import.meta.url), 'utf8')
const call = (name) => {
  const match = view.match(new RegExp(`${name}\\(\\{([\\s\\S]*?)\\}\\)`))
  assert.ok(match, `${name} call not found`)
  return match[1]
}

test('makeQueue receives and declares Vue computed', () => {
  assert.match(call('makeQueue'), /\bcomputed\b/)
  assert.match(factory('queue'), /computed: typeof computed/)
})

test('makeStemSelection receives and declares ref and computed', () => {
  const source = call('makeStemSelection')
  assert.match(source, /\bref\b/)
  assert.match(source, /\bcomputed\b/)
  const types = factory('stemSelection')
  assert.match(types, /ref: typeof ref/)
  assert.match(types, /computed: typeof computed/)
})

test('makePlayback receives and declares Audio and convertFileSrc', () => {
  const source = call('makePlayback')
  assert.match(source, /\bAudio\b/)
  assert.match(source, /\bconvertFileSrc\b/)
  const types = factory('playback')
  assert.match(types, /Audio: new \(\) => HTMLAudioElement/)
  assert.match(types, /convertFileSrc: \(path: string\) => string/)
})
