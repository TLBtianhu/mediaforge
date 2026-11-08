import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { after } from 'node:test'
import { createServer } from 'vite'

const stub = '\0result-removal-codec'
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
  resolve: { alias: { '@tauri-apps/api/core': stub } },
  plugins: [{ name: 'result-removal-codec', resolveId: id => id === stub ? id : null,
    load: id => id === stub ? 'export const calls=[];export let reply;export function setReply(value){reply=value;calls.length=0}export async function invoke(command,args){calls.push({command,args});return reply}' : null }],
})
after(() => vite.close())
const boundary = await vite.ssrLoadModule(stub)
const { removeResultJobs } = await vite.ssrLoadModule('/src/features/tasks/journal.ts')
const fixture = JSON.parse(readFileSync(new URL('./fixtures/jobs/current-contract.json', import.meta.url), 'utf8'))
const agent = fixture.records.find(job => job.source === 'agent')
const response = overrides => ({ schemaVersion: 2, checkedJobIds: ['result'], removedJobIds: ['result'], trashed: [], failed: [], retained: [], ...overrides })
const storage = new Map()
globalThis.localStorage = { get length() { return storage.size }, key: index => [...storage.keys()][index] ?? null,
  getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
function preview(rows) {
  globalThis.window = {}
  storage.clear()
  for (const job of rows) storage.set('mediaforge:job:' + job.jobId, JSON.stringify(job))
}
function terminal(id = 'result', patch = {}) {
  return { ...structuredClone(agent), jobId: id, status: 'completed', owner: null, finishedAt: 1700000002, ...patch }
}

test('desktop removal sends only canonical job identities and explicit choices', async () => {
  globalThis.window = { __TAURI_INTERNALS__: {} }
  boundary.setReply(response({ trashed: ['D:/out/file.wav'] }))
  const result = await removeResultJobs(['result'], { deleteFiles: true })
  assert.deepEqual(result.trashed, ['D:/out/file.wav'])
  assert.deepEqual(boundary.calls, [{ command: 'remove_result_jobs', args: { jobIds: ['result'], deleteFiles: true, checkOnly: false } }])
  boundary.setReply(response({ removedJobIds: [] }))
  await removeResultJobs(['result'], { checkOnly: true })
  assert.equal(boundary.calls[0].args.checkOnly, true)
  assert.equal(boundary.calls[0].args.deleteFiles, false)
})

test('recycling failures and protected files preserve backend explanations', async () => {
  globalThis.window = { __TAURI_INTERNALS__: {} }
  const expected = response({ removedJobIds: [], trashed: ['D:/out/first.wav'], failed: [{ path: 'D:/out/locked.wav', error: 'in use' }], retained: [{ path: 'D:/input/source.wav', reason: 'input' }] })
  boundary.setReply(expected)
  assert.deepEqual(await removeResultJobs(['result'], { deleteFiles: true }), expected)
})

test('malformed or inconsistent removal acknowledgements are rejected', async () => {
  globalThis.window = { __TAURI_INTERNALS__: {} }
  for (const bad of [null, response({ schemaVersion: 1 }), response({ checkedJobIds: ['other'] }),
    response({ removedJobIds: ['other'] }), response({ checkedJobIds: ['result', 'result'] }),
    response({ failed: [{ path: 'x', error: 'busy' }] }), response({ retained: [{ path: 'x' }] }),
    response({ trashed: [''] }), response({ extra: true }), response({ checkedJobIds: [] }), response({ removedJobIds: [] })]) {
    boundary.setReply(bad)
    await assert.rejects(removeResultJobs(['result'], { deleteFiles: true }), /Unsupported task journal schema/)
  }
  boundary.setReply(response({ trashed: ['D:/out/file.wav'], removedJobIds: [] }))
  await assert.rejects(removeResultJobs(['result'], { checkOnly: true }), /Unsupported task journal schema/)
})

test('invalid identities never invoke the desktop command', async () => {
  globalThis.window = { __TAURI_INTERNALS__: {} }
  for (const ids of [['../escape'], ['CON'], ['result', 'result'], ['']]) {
    boundary.setReply(response())
    await assert.rejects(removeResultJobs(ids), /Unsupported task journal schema/)
    assert.equal(boundary.calls.length, 0)
  }
})

test('browser preview removes only selected terminal records and preflight does not write', async () => {
  preview([terminal(), terminal('other')])
  const before = [...storage]
  assert.deepEqual((await removeResultJobs(['result'], { checkOnly: true })).checkedJobIds, ['result'])
  assert.deepEqual([...storage], before)
  assert.deepEqual((await removeResultJobs(['result'])).removedJobIds, ['result'])
  assert.deepEqual([...storage.keys()], ['mediaforge:job:other'])
  assert.deepEqual((await removeResultJobs([])).removedJobIds, [])
  assert.equal(storage.size, 1)
})

test('browser preview cannot pretend to recycle files or erase active, malformed or audio records', async () => {
  preview([terminal()])
  const untouched = [...storage]
  await assert.rejects(removeResultJobs(['result'], { deleteFiles: true }), /requires the desktop app/)
  assert.deepEqual([...storage], untouched)
  for (const row of [terminal('result', { status: 'queued', finishedAt: null }),
    terminal('result', { status: 'running', finishedAt: null, owner: { kind: 'operation-kernel', pid: 123, kernelId: 'fixture' } }),
    terminal('result', { command: 'runtime_install' }),
    terminal('result', { owner: { kind: 'operation-kernel', pid: 123, kernelId: 'fixture' } })]) {
    preview([row, terminal('other')])
    const before = [...storage]
    await assert.rejects(removeResultJobs(['other', 'result']))
    assert.deepEqual([...storage], before)
  }
  preview(fixture.records)
  const audioBefore = [...storage]
  await assert.rejects(removeResultJobs([fixture.records.find(job => job.audio).jobId]))
  assert.deepEqual([...storage], audioBefore)
  preview([terminal()])
  await assert.rejects(removeResultJobs(['missing']))
  assert.equal(storage.size, 1)
  preview([terminal()])
  storage.set('mediaforge:job:broken', '{bad json')
  const before = [...storage]
  await assert.rejects(removeResultJobs(['result']))
  assert.deepEqual([...storage], before)
})
