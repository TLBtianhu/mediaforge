/**
 * 词干/轨道命名与颜色契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  stemKeyFromOutput, STEM_NAME_KEYS, LEGACY_STEM_NAMES, EXACT_STEM_KEYS,
  normalizedDefaultName, recordingTrackIndex, isLegacyAutoTrackName,
  trackColor, duplicatedTrackColor,
} from '../src/stores/editorStems.ts'

test('stemKeyFromOutput groups stems into canonical keys', () => {
  assert.equal(stemKeyFromOutput({ stem: 'Vocals' }), 'vocals')
  assert.equal(stemKeyFromOutput({ stem: 'voice' }), 'vocals')
  assert.equal(stemKeyFromOutput({ stem: 'instrumental' }), 'accompaniment')
  assert.equal(stemKeyFromOutput({ stem: 'drums' }), 'drums')
  assert.equal(stemKeyFromOutput({ stem: 'bass' }), 'bass')
  assert.equal(stemKeyFromOutput({ stem: 'other' }), 'other')
  assert.equal(stemKeyFromOutput({ stem: 'piano' }), 'piano')
  assert.equal(stemKeyFromOutput({ stem: '' }), 'other')
})

test('normalizedDefaultName lowercases and unifies separators', () => {
  assert.equal(normalizedDefaultName(' Vocal_Stem-Name '), 'vocal stem name')
})

test('recordingTrackIndex parses recording and 录音 names', () => {
  assert.equal(recordingTrackIndex('recording 3'), 3)
  assert.equal(recordingTrackIndex('录音 2'), 2)
  assert.equal(recordingTrackIndex('recording 0'), 1)
  assert.equal(recordingTrackIndex('vocals'), null)
})

test('isLegacyAutoTrackName matches recording and legacy stem names', () => {
  assert.equal(isLegacyAutoTrackName('recording 2', 'recording'), true)
  assert.equal(isLegacyAutoTrackName('vocals', 'stem', 'vocals'), true)
  assert.equal(isLegacyAutoTrackName('人声', 'stem', 'vocals'), true)
  assert.equal(isLegacyAutoTrackName('random', 'stem', 'vocals'), false)
  assert.equal(isLegacyAutoTrackName('anything', 'reference'), false)
})

test('trackColor and duplicatedTrackColor follow role and stem', () => {
  assert.equal(trackColor('recording'), '#e76f91')
  assert.equal(trackColor('reference'), '#93a1b6')
  assert.equal(trackColor('stem', 'vocals'), '#ff6b7c')
  assert.equal(trackColor('stem', 'drums'), '#f2b45a')
  assert.equal(trackColor('stem', 'unknown'), '#7aa2ff')
  assert.equal(duplicatedTrackColor({ role: 'stem', stemKey: 'bass' }), '#4fc58f')
})

test('stem name tables expose the legacy mappings', () => {
  assert.equal(STEM_NAME_KEYS.vocals, 'editor.stemVocals')
  assert.ok(LEGACY_STEM_NAMES.vocals.includes('人声'))
  assert.equal(EXACT_STEM_KEYS.get('vocals'), 'vocals')
})
