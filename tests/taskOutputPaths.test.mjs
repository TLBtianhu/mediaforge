/**
 * 输出路径与文件命名的纯函数契约（Windows / POSIX）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  normalizeOutputPath, resolveOutputDirectory, joinOutputPath, inputStemSegment,
} from '../src/stores/taskOutputPaths.ts'

test('normalizeOutputPath and resolveOutputDirectory fall back to outputs', () => {
  assert.equal(normalizeOutputPath(''), 'outputs')
  assert.equal(normalizeOutputPath('D:/out'), 'D:/out')
  assert.equal(resolveOutputDirectory('', 'D:/fallback'), 'D:/fallback')
  assert.equal(resolveOutputDirectory('D:/out', 'D:/fallback'), 'D:/out')
  assert.equal(resolveOutputDirectory('', ''), 'outputs')
})

test('joinOutputPath picks the separator from the platform and the base', () => {
  assert.equal(joinOutputPath('D:/out', 'a.wav', true), 'D:/out/a.wav')
  assert.equal(joinOutputPath('D:\\out\\', 'a.wav', true), 'D:\\out\\a.wav')
  assert.equal(joinOutputPath('/tmp/out', 'a.wav', false), '/tmp/out/a.wav')
})

test('inputStemSegment keeps the basename without its extension', () => {
  assert.equal(inputStemSegment('D:/audio/song.wav', 'x', true), 'song')
  assert.equal(inputStemSegment('/home/u/mix.flac', 'x', false), 'mix')
  assert.equal(inputStemSegment('', 'fallback', true), 'fallback')
})
