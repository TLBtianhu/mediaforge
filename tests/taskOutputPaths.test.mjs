/**
 * 输出路径与文件命名的纯函数契约（Windows / POSIX）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  normalizeOutputPath, resolveOutputDirectory, joinOutputPath, inputStemSegment,
  outputsFromFiles, cleanupPath, parentDir, pathKey, samePath, cleanupDirChain,
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

test('outputsFromFiles builds absolute and relative outputs and rejects traversal', () => {
  const absolute = outputsFromFiles('D:/out', ['D:/out/vocals.wav'], 'wav', undefined, undefined, true)
  assert.equal(absolute[0].path, 'D:/out/vocals.wav')
  const relative = outputsFromFiles('D:/out', ['vocals'], 'wav', undefined, undefined, true)
  assert.equal(relative[0].path, 'D:/out/vocals.wav')
  const prefixed = outputsFromFiles('D:/out', ['vocals'], 'wav', 'job1', undefined, true)
  assert.equal(prefixed[0].path, 'D:/out/job1_vocals.wav')
  assert.deepEqual(outputsFromFiles('D:/out', ['../evil.wav'], 'wav', undefined, undefined, true), [])
  assert.deepEqual(outputsFromFiles('D:/out', [], 'wav'), [])
})

test('cleanupPath trims trailing slashes and normalizes UNC prefixes', () => {
  assert.equal(cleanupPath('D:\\out\\', true), 'D:/out')
  assert.equal(cleanupPath('/tmp/out/', false), '/tmp/out')
  assert.equal(cleanupPath('//?/D:/out/', true), 'D:/out')
})

test('parentDir and pathKey normalize and compare paths', () => {
  assert.equal(parentDir('D:/out/sub', true), 'D:/out')
  assert.equal(pathKey('D:/Out/Sub', true), 'd:/out/sub')
  assert.equal(pathKey('D:/out/./sub/../x', true), 'd:/out/x')
  assert.equal(samePath('D:/out', 'd:\\OUT\\', true), true)
  assert.equal(samePath('D:/out', 'D:/other', true), false)
})

test('cleanupDirChain walks from a child up to the stop directory only', () => {
  const dirs = cleanupDirChain('D:/out/a/b/c', 'D:/out', true)
  assert.deepEqual(dirs, ['D:/out/a/b/c', 'D:/out/a/b', 'D:/out/a'])
  assert.deepEqual(cleanupDirChain('E:/elsewhere', 'D:/out', true), [])
})
