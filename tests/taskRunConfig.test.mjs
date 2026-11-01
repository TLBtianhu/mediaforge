import assert from 'node:assert/strict'
import test from 'node:test'
import { makeTaskRunConfig } from '../src/stores/taskRunConfig.ts'

function setup() {
  const models = [{ name: 'MDX', aliases: ['mdx alias'], modelType: 'vr' }]
  const factory = makeTaskRunConfig({
    useSettingsStore: () => ({
      modelDir: 'D:/models', downloadSource: 'modelscope', downloadMethod: 'urllib',
      defaultFormat: 'flac', developerMode: true,
      getRuntimeDeviceConfig: () => ({ device: 'cuda:0', deviceIds: [0, 1] }),
      getAudioParams: () => ({ bitrate: '320k' }),
    }),
    useAppStore: () => ({ envInfo: {} }),
    useModelStore: () => ({
      models,
      getModelInferenceOverrides: name => {
        assert.equal(name, 'MDX')
        return {
          batch_size: 2, window_size: 0, aggression: 0.7,
          chunk_size: 3, standardize: true, normalize: false,
        }
      },
    }),
    selectedStems: { value: ['vocals'] },
    useTta: { value: true },
    normalizeOutputNaming: value => value || { enabled: false, template: '', stemOrder: [] },
    CURRENT_INFERENCE_PARAMS_VERSION: 7,
    detectWorkflowFormat: () => 'simple',
    resolveWorkflowRuntimeDefaults: (_definition, defaults) => ({
      ...defaults,
      device: 'cpu',
      outputFormat: 'wav',
    }),
    prepareWorkflowDefinitionForRun: (definition, options) => ({ definition, options }),
  })
  return factory
}

test('model run config snapshots settings, device, stems and inference values', () => {
  const { buildRunConfig } = setup()
  const config = buildRunConfig({ batch_size: 2 }, 'vr', 'flat')
  assert.equal(config.runMode, 'model')
  assert.equal(config.modelDir, 'D:/models')
  assert.equal(config.device, 'cuda:0')
  assert.deepEqual(Array.from(config.deviceIds), [0, 1])
  assert.deepEqual(Array.from(config.selectedStems), ['vocals'])
  assert.equal(config.useTta, true)
  assert.equal(config.inferenceParamsVersion, 7)
  assert.deepEqual(config.inferenceParams, { batch_size: 2 })
})

test('workflow model overrides use aliases and drop incompatible or invalid fields', () => {
  const { workflowModelInferenceParams } = setup()
  const result = workflowModelInferenceParams({ steps: [{ model: 'mdx alias' }] })
  assert.deepEqual(result, {
    'mdx alias': { batch_size: 2, aggression: 0.7, normalize: false },
  })
})

test('workflow run config uses resolved defaults and compatible model overrides', () => {
  const { buildWorkflowRunConfig } = setup()
  const definition = { steps: [{ model: 'mdx alias' }] }
  const config = buildWorkflowRunConfig({ id: 'wf', name: 'Workflow', definition }, 'folders')
  assert.equal(config.runMode, 'workflow')
  assert.equal(config.device, 'cpu')
  assert.deepEqual(Array.from(config.deviceIds), [0])
  assert.equal(config.outputFormat, 'wav')
  assert.deepEqual(config.workflowDefinition.options.modelInferenceParams, {
    'mdx alias': { batch_size: 2, aggression: 0.7, normalize: false },
  })
  assert.equal(config.workflowDefinition.options.device, 'cpu')
})
