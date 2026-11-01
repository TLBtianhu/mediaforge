/**
 * 源/片段/轨道/会话规范化契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  cloneLegacyTrackEffects, cloneTracks, normalizeSource, normalizeClip,
  normalizeTrack, normalizeSession, toPersistedSession, clamp,
} from '../src/stores/editorNormalize.ts'

const localizedName = (name) => `L:${name}`

test('cloneLegacyTrackEffects and cloneTracks deep-clone effects and chains', () => {
  assert.equal(cloneLegacyTrackEffects(undefined), undefined)
  assert.deepEqual(cloneLegacyTrackEffects({ a: 1 }), { a: 1 })
  assert.equal(cloneLegacyTrackEffects([1, 2]), undefined)
  const tracks = cloneTracks([{ effects: { a: 1 }, pluginChain: [], clips: [{ id: 'c' }] }])
  assert.deepEqual(tracks[0].effects, { a: 1 })
  assert.deepEqual(tracks[0].clips, [{ id: 'c' }])
})

test('normalizeSource fills defaults and computes channels from peaks', () => {
  const source = normalizeSource({ path: 'D:/a/b.wav', channelPeaks: [[1, 2], [3, 4]] })
  assert.equal(source.role, 'stem')
  assert.equal(source.name, 'b.wav')
  assert.equal(source.channels, 2)
  assert.equal(source.missing, false)
  assert.equal(normalizeSource({ role: 'recording' }).role, 'recording')
})

test('normalizeClip clamps offset, duration, volume and fades', () => {
  const source = { id: 's1', duration: 10 }
  const clip = normalizeClip({ offset: 12, duration: 99, volume: 5, fadeIn: 3, fadeOut: 4 }, source)
  assert.equal(clip.offset, 10)
  // availableDuration 为 0 时保留请求时长；volume 夹到 2，fade 夹到该时长。
  assert.equal(clip.duration, 99)
  assert.equal(clip.volume, 2)
  assert.equal(clip.fadeIn, 3)
  assert.equal(clip.fadeOut, 4)
  const normal = normalizeClip({ offset: 2, duration: 4, volume: 1, fadeIn: 1, fadeOut: 1 }, source)
  assert.equal(normal.duration, 4)
  assert.equal(normal.fadeIn, 1)
})

test('normalizeTrack builds clips and respects autoName and canonical fades', () => {
  const sources = new Map([['s1', { id: 's1', role: 'stem', stemKey: 'vocals', duration: 10, name: 'Vocals' }]])
  const track = normalizeTrack({ sourceId: 's1', name: 'vocals', volume: 1 }, sources, true, localizedName)
  assert.equal(track.role, 'stem')
  assert.equal(track.name, 'L:vocals')
  assert.equal(track.autoName, true)
  assert.equal(track.clips.length, 1)
  assert.equal(track.clips[0].duration, 10)
})

test('normalizeSession normalizes sources, tracks and master volume', () => {
  const session = normalizeSession({
    sources: [{ path: 'a.wav', duration: 5 }],
    tracks: [{ sourceId: 'a', name: 'a' }],
    masterVolume: 3,
  }, localizedName)
  assert.equal(session.masterVolume, 2)
  assert.equal(session.sources.length, 1)
  assert.equal(session.tracks.length, 1)
  assert.equal(session.name, 'Untitled Session')
})

test('toPersistedSession clears peaks and stamps a version', () => {
  const persisted = toPersistedSession({
    sources: [{ peaks: [1, 2], channelPeaks: [[3]] }],
    tracks: [],
    pluginChain: [],
  })
  assert.deepEqual(persisted.sources[0].peaks, [])
  assert.deepEqual(persisted.sources[0].channelPeaks, [])
  assert.ok('version' in persisted)
})
