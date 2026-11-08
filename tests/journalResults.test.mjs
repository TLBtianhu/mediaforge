import assert from 'node:assert/strict'
import test from 'node:test'
import { buildJournalResults, filterJournalResults } from '../src/views/results/journalResults.ts'

const job = (id, overrides = {}) => ({
  jobId: id, command: 'audio_tools', source: 'manual', status: 'completed', createdAt: 100, finishedAt: 200,
  payload: { operation: 'media_image', inputs: ['/input/photo.jpg'], outputDir: '/out' },
  result: { outputs: [{ path: `/out/${id}.png`, size: 1200 }] }, ...overrides,
})

test('persistent media and Agent results project explicit output descriptors without changing their records', () => {
  const jobs = [job('manual'), job('agent', { source: 'agent', command: 'media_tool' })]
  const before = structuredClone(jobs)
  const results = buildJournalResults(jobs)
  assert.deepEqual(results.map(row => row.id), ['manual', 'agent'])
  assert.deepEqual(results.map(row => row.source), ['manual', 'agent'])
  assert.equal(results[0].title, 'photo.jpg')
  assert.equal(results[0].outputs[0].preview, 'image')
  assert.equal(results[0].createdAt, 100000)
  assert.equal(results[0].updatedAt, 200000)
  assert.deepEqual(jobs, before)
})

test('terminal failures, cancellations and interruptions retain produced files while active tasks stay in the queue', () => {
  const results = buildJournalResults(['failed', 'cancelled', 'interrupted', 'running', 'queued'].map(status => job(status, { status })))
  assert.deepEqual(results.map(row => row.status), ['failed', 'cancelled', 'interrupted'])
  assert.deepEqual(buildJournalResults([job('empty', { status: 'failed', result: { outputs: [] } })]), [])
})

test('manual audio journal mirrors never resurrect deleted separation or workflow records', () => {
  for (const command of ['infer', 'infer_workflow']) {
    assert.deepEqual(buildJournalResults([job('deleted-audio', { command })], new Set()), [])
  }
})

test('Agent audio duplicates use task/job identities while independent Agent audio files remain browsable', () => {
  const jobs = [job('known', { source: 'agent', command: 'infer' }),
    job('alias', { source: 'agent', command: 'infer_workflow', cancelTaskId: 'audio-task' }),
    job('payload-alias', { source: 'agent', command: 'infer', payload: { taskId: 'audio-batch' } }),
    job('independent', { source: 'agent', command: 'infer' })]
  const rows = buildJournalResults(jobs, new Set(['known', 'audio-task', 'audio-batch']))
  assert.deepEqual(rows.map(row => row.id), ['independent'])
})

test('probe, runtime and model operations are not displayed as task output files', () => {
  const jobs = [job('download', { command: 'model_download' }), job('runtime', { command: 'runtime_install' }),
    job('probe', { payload: { operation: 'media_probe' } }), job('inspect', { payload: { operation: 'inspect' } })]
  assert.deepEqual(buildJournalResults(jobs), [])
})

test('directory paths, scalar paths and URLs never become produced-file records', () => {
  const outputs = ['/out/raw.png', { path: '/out' }, { path: '/out/' }, { path: '/out/a.wav', kind: 'directory' },
    { path: '/out/value.png', kind: 'scalar' }, { path: 'https://example.com/remote.png' }, { path: '/out/no-extension' }]
  assert.deepEqual(buildJournalResults([job('not-files', { result: { outputs } })]), [])
})

test('named plugin outputs preserve file descriptors while excluding scalar and array outputs', () => {
  const [row] = buildJournalResults([job('operation', { command: 'run_operation', payload: { operation: 'kit.normalize' }, result: {
    outputs: { audio: { kind: 'audio_file', path: '/out/normalized.wav' }, value: { kind: 'scalar', value: '/out/looks-like.wav' },
      notes: { kind: 'array', value: [] } },
  } })])
  assert.equal(row.operation, 'kit.normalize')
  assert.deepEqual(row.outputs.map(output => output.path), ['/out/normalized.wav'])
  assert.equal(row.outputs[0].preview, 'audio')
})

test('output duplicates are removed and the newest persisted snapshot wins for one job', () => {
  const older = job('same')
  const newer = job('same', { finishedAt: 300, result: { outputs: [{ path: '/out/new.mp4' }, { path: '/out/new.mp4' }] } })
  const [row] = buildJournalResults([newer, older])
  assert.equal(row.updatedAt, 300000)
  assert.equal(row.outputs.length, 1)
  assert.equal(row.outputs[0].preview, 'video')
})

test('shared search finds input names, output paths and operation IDs and sorting leaves the source intact', () => {
  const rows = buildJournalResults([job('b', { payload: { operation: 'media_video', inputs: ['/input/B.mp4'] }, finishedAt: 400 }),
    job('a', { payload: { operation: 'media_convert', inputs: ['/input/A.wav'] }, finishedAt: 300 })])
  assert.deepEqual(filterJournalResults(rows, 'media_convert', 'time_desc').map(row => row.id), ['a'])
  assert.deepEqual(filterJournalResults(rows, '/out/b.png', 'time_desc').map(row => row.id), ['b'])
  assert.deepEqual(filterJournalResults(rows, '', 'name_asc').map(row => row.id), ['a', 'b'])
  assert.deepEqual(filterJournalResults(rows, '', 'time_asc').map(row => row.id), ['a', 'b'])
  assert.deepEqual(rows.map(row => row.id), ['b', 'a'])
})

test('millisecond timestamps and explicit extensionless file descriptors remain intact', () => {
  const [row] = buildJournalResults([job('file', { createdAt: 1794000000000, finishedAt: 1794000001000, result: { outputs: [{ kind: 'file', path: '/out/LICENSE' }] } })])
  assert.equal(row.updatedAt, 1794000001000)
  assert.equal(row.outputs[0].name, 'LICENSE')
  assert.equal(row.outputs[0].preview, null)
})

for (const operation of ['convert', 'slicer', 'asr', 'loudness']) {
  test(`current audio_tools ${operation} exposes only outputPaths and preserves partial outputs`, () => {
    const [row] = buildJournalResults([job(operation, { status: 'failed', payload: { operation, inputPath: '/input/source.wav' },
      result: { operation, outputDir: '/out', outputPaths: ['/out/first.wav', '/out/transcript.srt', '/out/first.wav'],
        failed: [{ path: '/input/failed.wav' }], outputPath: '/out/not-the-current-plural-contract.wav' } })])
    assert.deepEqual(row.outputs.map(output => output.path), ['/out/first.wav', '/out/transcript.srt'])
    assert.equal(row.status, 'failed')
  })
}

for (const operation of ['merge', 'midi']) {
  test(`current audio_tools ${operation} exposes its explicit outputPath`, () => {
    const [row] = buildJournalResults([job(operation, { payload: { operation }, result: { operation, outputPath: `/out/${operation}.mid` } })])
    assert.deepEqual(row.outputs.map(output => output.path), [`/out/${operation}.mid`])
  })
}

test('unknown tool operations and other commands do not promote outputPath or failure paths to files', () => {
  for (const [command, operation] of [['audio_tools', 'inspect'], ['audio_tools', 'unknown'], ['run_operation', 'convert'], ['export_editor_mix', 'merge']]) {
    assert.deepEqual(buildJournalResults([job('not-files', { command, payload: { operation },
      result: { outputPaths: ['/out/plural.wav'], outputPath: '/out/single.wav', failed: [{ path: '/input/source.wav' }] } })]), [])
  }
})
