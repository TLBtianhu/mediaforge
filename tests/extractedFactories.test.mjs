import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref } from 'vue'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

globalThis.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {} }

const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
})

const { makeStageState } = await vite.ssrLoadModule('/src/views/separate/stageState.ts')
const { makeModelPanel } = await vite.ssrLoadModule('/src/views/separate/modelPanel.ts')
const { makeTaskInputActions } = await vite.ssrLoadModule('/src/stores/taskInputActions.ts')
const { makeEditorTrackActions } = await vite.ssrLoadModule('/src/stores/editorTrackActions.ts')
const { normalizePathKey, hasPeaks, runLimited, trackToExportTrack } = await vite.ssrLoadModule('/src/stores/editorSupport.ts')
const { normalizePersistedModelState } = await vite.ssrLoadModule('/src/stores/taskHelpers.ts')

function stageHarness(tasks = null) {
  const currentJob = ref(tasks ? { id: 'job', output: '/out', tasks, primary: tasks[0] } : null)
  const inputFiles = ref(['/in/one.wav'])
  const selectedModelName = ref('model-a')
  const currentModelInfo = ref({ name: 'model-a' })
  const normalizedOutputDir = ref('/out')
  const modelEntries = ref([])
  const state = makeStageState({
    computed, t: key => key, currentJob, inputFiles, selectedModelName, currentModelInfo,
    normalizedOutputDir, modelEntries,
    categoryLabel: () => 'Vocals', parseModelInstruments: () => [],
    statusLabel: status => status, shortenMiddle: value => value,
  })
  return state
}

function stageTask(id, status, patch = {}) {
  return {
    id, status, input: `/in/${id}.wav`, model: 'model-a', outputs: [], createdAt: 1,
    progress: 0, runConfig: { runMode: 'model', selectedStems: ['vocals'] }, ...patch,
  }
}

test('stage state distinguishes ready, running, done and multi-batch progress', () => {
  assert.equal(stageHarness().taskPanelState.value, 'ready')

  const running = stageHarness([stageTask('a', 'separating', { progress: 40 })])
  assert.equal(running.taskPanelState.value, 'running')
  assert.equal(running.currentTask.value.id, 'a')
  assert.equal(running.currentBatchProgress.value, 40)
  assert.equal(running.currentBatchTitle.value, 'separate.taskRunningTitle')

  const done = stageHarness([stageTask('a', 'done', { outputs: [{ path: '/out/a.wav', stem: 'vocals' }] })])
  assert.equal(done.taskPanelState.value, 'done')
  assert.equal(done.playableOutputs.value.length, 1)
  assert.equal(done.currentBatchIsMulti.value, false)

  const multi = stageHarness([
    stageTask('a', 'done', { outputs: [{ path: '/out/a.wav', stem: 'vocals' }] }),
    stageTask('b', 'separating', { progress: 40 }),
  ])
  assert.equal(multi.taskPanelState.value, 'running')
  assert.equal(multi.currentBatchIsMulti.value, true)
  assert.equal(multi.currentBatchProgress.value, 70)
  assert.equal(multi.currentBatchTitle.value, 'separate.batchRunningTitle')
})

test('model panel maps labels and handles single/ensemble selection', async () => {
  const selectedModelName = ref('model-a')
  const ensembleEnabled = ref(false)
  const ensembleModels = ref([])
  const listed = ref([{ name: 'model-a' }])
  const calls = []
  const panel = makeModelPanel({
    t: key => key, locale: ref('zh-CN'),
    model: { selectModel: async (name) => { calls.push(name) }, getModelUseCount: () => 7 },
    listedDownloadedModels: listed, selectedModelName, ensembleEnabled, ensembleModels,
    shouldPrefetchAdvancedParams: ref(true), detailLoading: ref(false), isLoading: ref(false),
  })
  assert.equal(panel.modelTargetLabel({ targetStem: 'vocals' }), 'vocals')
  assert.equal(panel.modelArchitectureLabel({ modelType: 'bs_roformer' }), 'bs_roformer')
  assert.equal(panel.modelMetaLine({ targetStem: 'vocals', architecture: 'mdx' }), 'vocals · mdx')
  assert.equal(panel.modelUseCount('model-a'), 7)

  panel.handleSelectModel({ name: 'model-a' })
  await Promise.resolve()
  assert.deepEqual(calls, [{ name: 'model-a' }])

  ensembleEnabled.value = true
  panel.handleSelectModel({ name: 'model-a' })
  assert.deepEqual(Array.from(ensembleModels.value), ['model-a'])
  panel.handleSelectModel({ name: 'model-a' })
  assert.deepEqual(Array.from(ensembleModels.value), [])
})

test('task input actions filter, dedupe, reorder and scan paths', async () => {
  const inputFiles = ref([])
  const calls = []
  const invoke = async (command, args) => {
    calls.push({ command, args })
    if (command === 'scan_media_paths') return { files: ['/b.wav', '/bad.txt'], warnings: ['skipped'] }
    if (command === 'pick_media_files') return ['/c.mp3']
    return null
  }
  const actions = makeTaskInputActions({ inputFiles, invoke })

  assert.equal(actions.addInputFiles(['/a.wav', '/a.wav', '/x.txt', '/b.mp4']), 2)
  assert.deepEqual(Array.from(inputFiles.value), ['/a.wav', '/b.mp4'])
  assert.equal(actions.moveInputFile(0, 1), true)
  assert.deepEqual(Array.from(inputFiles.value), ['/b.mp4', '/a.wav'])
  assert.equal(actions.moveInputFile(0, 99), false)

  assert.deepEqual(await actions.scanAndAddPaths(['/dir']), { added: 1, warnings: ['skipped'] })
  assert.deepEqual(Array.from(inputFiles.value), ['/b.mp4', '/a.wav', '/b.wav'])
  assert.equal(await actions.pickFiles(), 1)
  assert.deepEqual(Array.from(inputFiles.value), ['/b.mp4', '/a.wav', '/b.wav', '/c.mp3'])
  assert.equal(calls[0].command, 'scan_media_paths')
})

test('editor support keeps path, peak and bounded-work helpers pure', async () => {
  assert.equal(normalizePathKey('C:\\Audio\\Song.WAV'), 'c:/audio/song.wav')
  assert.equal(hasPeaks([1, 2]), true)
  assert.equal(hasPeaks([-1]), false)
  assert.equal(hasPeaks([]), false)

  let active = 0
  let maxActive = 0
  const visited = []
  await runLimited([1, 2, 3, 4], 2, async (item) => {
    active += 1
    maxActive = Math.max(maxActive, active)
    visited.push(item)
    await new Promise(resolve => setTimeout(resolve, 5))
    active -= 1
  })
  assert.deepEqual(visited.sort(), [1, 2, 3, 4])
  assert.equal(maxActive, 2)

  const track = trackToExportTrack({
    id: 'track', sourceId: 'source', name: 'Vocals', role: 'stem', volume: 0.8, pan: -0.2,
    muted: false, solo: true, fadeIn: 0, fadeOut: 0, clips: [],
  }, { duration: 12 })
  assert.equal(track.clips.length, 1)
  assert.equal(track.clips[0].duration, 12)
  assert.equal(track.solo, true)
})

test('editor track actions keep selection, rename and volume clamping in one place', () => {
  const session = ref({
    id: 'project', masterVolume: 1, masterPan: 0, sources: [],
    tracks: [{
      id: 'track-1', sourceId: 'source-1', name: 'Vocals', autoName: true, role: 'stem',
      volume: 1, pan: 0, muted: false, solo: false, fadeIn: 0, fadeOut: 0,
      clips: [{ id: 'clip-1', assetId: 'source-1', start: 0, offset: 0, duration: 4, fadeIn: 0, fadeOut: 0 }],
    }],
  })
  const selectedTrackId = ref(null)
  const selectedClipId = ref(null)
  let history = 0
  let saves = 0
  const actions = makeEditorTrackActions({
    session, selectedTrackId, selectedClipId,
    pushHistory: () => { history += 1 },
    scheduleSave: () => { saves += 1 },
    interactionDepth: ref(0),
    sourceMap: computed(() => new Map()),
  })

  actions.selectTrack('track-1')
  assert.equal(selectedTrackId.value, 'track-1')
  assert.equal(selectedClipId.value, 'clip-1')

  actions.renameTrack('track-1', '  Lead  ')
  assert.equal(session.value.tracks[0].name, 'Lead')
  assert.equal(session.value.tracks[0].autoName, false)
  assert.equal(history, 1)
  assert.equal(saves, 1)

  actions.setTrackVolume('track-1', 5)
  assert.equal(session.value.tracks[0].volume, 2)
  assert.equal(saves, 2)
})

test('persisted model state normalization rejects unknown shapes and trims stems', () => {
  assert.deepEqual(normalizePersistedModelState(null), {})
  assert.deepEqual(normalizePersistedModelState(['bad']), {})
  assert.deepEqual(normalizePersistedModelState({
    batch_size: 2, overlap_size: 'bad', standardize: true,
    selectedStems: [' vocals ', '', 3], selectedStemsCleared: true,
  }), {
    batch_size: 2, standardize: true, selectedStems: ['vocals', '3'], selectedStemsCleared: true,
  })
})
