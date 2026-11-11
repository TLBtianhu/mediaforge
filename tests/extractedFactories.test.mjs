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
const { makeTaskInferenceParams } = await vite.ssrLoadModule('/src/stores/taskInferenceActions.ts')
const { buildInferenceParams: buildInferenceParamsFromValues, modelInferenceParamsFromState: modelInferenceParamsFromValues } = await vite.ssrLoadModule('/src/stores/taskInferenceParams.ts')
const { makeSeparateActions } = await vite.ssrLoadModule('/src/views/separate/separateActions.ts')
const { makeAudioOptions } = await vite.ssrLoadModule('/src/views/separate/audioOptions.ts')

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

test('task inference actions wire current refs into the shared builders', () => {
  const inference = makeTaskInferenceParams({
    selectedModelDefaults: ref({ batch_size: 4 }),
    selectedModelOverrides: ref({}),
    inferenceParamsDirty: ref(true),
    batch_size: ref(2), overlap_size: ref(1024), num_overlap: ref(2), chunk_size: ref(8192),
    window_size: ref(512), aggression: ref(5), enable_post_process: ref(true),
    post_process_threshold: ref(0.5), high_end_process: ref(false),
    standardize: ref(true), normalize: ref(true),
    getSavedModelState: () => ({ overlap_size: 3 }),
    useModelStore: () => ({
      selectedModel: 'other-model', models: [], hasKnownModelInferenceBase: () => false,
      getModelBaseInferenceDefaults: () => ({}), getModelInferenceOverrides: () => ({}),
    }),
    buildInferenceParamsFromValues, modelInferenceParamsFromValues,
  })
  const normal = inference.buildInferenceParams(null)
  assert.equal(normal.batch_size, 2)
  assert.equal(normal.overlap_size, 1024)
  assert.equal(normal.chunk_size, 8192)
  assert.equal(normal.window_size, undefined)

  const vr = inference.buildInferenceParams('vr')
  assert.equal(vr.window_size, 512)
  assert.equal(vr.aggression, 5)
  assert.equal(vr.enable_post_process, true)
  assert.equal(vr.overlap_size, undefined)

  const saved = inference.modelInferenceParamsFromState('saved-model')
  assert.equal(saved.overlap_size, 3)
})

test('separate actions own queue/log/cancel transitions', async () => {
  const currentTask = ref({ id: 'task-1', status: 'done', logs: ['line'] })
  const currentBatchTasks = ref([{ id: 'task-1', status: 'done' }])
  const cancellingTaskId = ref(null)
  const removingRunRecords = ref(false)
  const showLogModal = ref(false)
  const focusedSeparationJobId = ref('job-1')
  const composingNewJob = ref(false)
  let stopped = 0
  const warnings = []
  const cancelled = []
  const removed = []
  let retried = 0
  const job = {
    id: 'job-1', model: 'model-a', inputCount: 2,
    primary: { input: '/in/a.wav', runConfig: { runMode: 'model' } },
  }
  const actions = makeSeparateActions({
    t: key => key,
    message: { success() {}, error() {} },
    dialog: { warning: options => warnings.push(options) },
    task: {
      removeSeparationRuns: async ids => { removed.push(...ids); return ids.length },
      cancelTask: async id => { cancelled.push(id); return true },
      retryTask: async () => { retried += 1; return { jobId: 'job-2' } },
    },
    router: { push() {} },
    currentTask, currentBatchTasks, cancellingTaskId, removingRunRecords,
    showLogModal, focusedSeparationJobId, composingNewJob,
    stopAllPreviewAudio: () => { stopped += 1 },
    focusQueueJob: target => target.id === 'job-1',
    cancelQueueJob: target => cancelled.push(target.id),
    isNoResultSeparationJob: () => true,
    isTerminalTaskStatus: status => ['done', 'failed', 'cancelled'].includes(status),
    getFileName: path => path.split('/').pop(),
  })

  assert.equal(actions.queueJobInputSummary(job), 'separate.queueBatchInputs')
  actions.beginNextSeparation()
  assert.equal(stopped, 1)
  assert.equal(showLogModal.value, false)
  assert.equal(focusedSeparationJobId.value, null)
  assert.equal(composingNewJob.value, true)

  actions.viewQueueJobLogs(job)
  await Promise.resolve()
  assert.equal(showLogModal.value, true)

  actions.requestCancelQueueJob(job)
  assert.equal(warnings.length, 1)
  warnings[0].onPositiveClick()
  assert.deepEqual(cancelled, ['job-1'])

  actions.removeSeparationRunRecords([job])
  assert.equal(warnings.length, 2)
  await warnings[1].onPositiveClick()
  assert.deepEqual(removed, ['job-1'])
  assert.equal(removingRunRecords.value, false)

  actions.handleCompletedAction('retry')
  assert.equal(warnings.length, 3)
  await warnings[2].onPositiveClick()
  assert.equal(retried, 1)
  assert.equal(focusedSeparationJobId.value, 'job-2')
})

test('audio options stay aligned with the format contract', () => {
  const options = makeAudioOptions({ t: key => key, computed })
  assert.deepEqual(options.formatOptions.map(item => item.value), ['wav', 'flac', 'mp3', 'm4a'])
  assert.equal(options.wavBitDepthOptions.value.length, 3)
  assert.deepEqual(options.mp3BitRateOptions.value.map(item => item.value), ['128k', '192k', '256k', '320k'])
  assert.equal(options.audioSteps.value.length, 3)
  assert.equal(options.getFileKindLabel('/tmp/movie.mp4'), 'separate.videoFile')
  assert.equal(options.getFileKindLabel('/tmp/song.wav'), 'separate.audioFile')
})
