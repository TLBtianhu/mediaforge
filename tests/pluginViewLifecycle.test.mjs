import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'
import { isTaskProgress, isTaskResult } from '../src/utils/taskEvents.ts'

const descriptor = parse(readFileSync(new URL('../src/features/plugin-marketplace/PluginViewHost.vue', import.meta.url), 'utf8')).descriptor
const source = ts.createSourceFile('host.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const names = new Set([
  'allowed', 'rejectRunRequests', 'closeChannel', 'handleCall', 'postToFrame',
  'onWindowMessage', 'onChannelMessage', 'onFrameLoad',
])
const snippets = source.statements.flatMap(node => {
  if (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) return [node.getText(source)]
  if (ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)) {
    const call = node.expression
    if (['onMounted', 'onBeforeUnmount'].includes(call.expression.getText(source))) {
      return [`const ${call.expression.getText(source)}Callback = ${call.arguments[0].getText(source)}`]
    }
  }
  return []
})
const code = ts.transpileModule(snippets.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function host(overrides = {}) {
  const messages = []
  const sessions = ['session-a', 'session-b', 'session-c']
  let nextSession = 0
  let controller
  const state = {
    disposed: false, runRequests: new Map(), unlistenWorker: undefined, themeObserver: undefined,
    channel: undefined, sessionId: '', loadedOnce: false, recovering: false, closed: 0,
    props: { pluginName: 'gain', pluginsRoot: '/plugins', view: { id: 'editor', entry: 'ui/index.html' } },
    permissions: () => ['operations'],
    frame: { value: { contentWindow: { postMessage: value => messages.push(value) } } },
    src: { value: '' }, status: { value: 'loading' }, errorText: { value: '' },
    window: { addEventListener() {}, removeEventListener() {} }, document: { documentElement: {} },
    MutationObserver: class { observe() {} disconnect() {} },
    crypto: { randomUUID: () => sessions[Math.min(nextSession++, sessions.length - 1)] },
    onWindowMessage() {}, pushTheme() {}, isTaskProgress, isTaskResult,
    pluginViewUrl: (_plugin, _view, _entry, _convert, session) => `http://pymss-plugin.localhost/view/gain/editor/index.html#pymss-session=${session}`,
    convertFileSrc() {}, listen: async () => () => {}, invoke: async () => {},
    ...overrides,
  }
  state.MessageChannel = class {
    constructor() {
      this.port1 = {
        postMessage: message => {
          messages.push(message)
          state.onPort1Message?.(message)
        },
        close: () => { state.closed += 1 },
        start: () => {},
      }
      this.port2 = {
        postMessage: message => {
          state.onPort2Message?.(message)
          queueMicrotask(() => controller.onChannelMessage({ data: message }))
        },
        close: () => {},
        start: () => {},
      }
      state.lastPair = this
    }
  }
  // The host imports typed wrappers; route them through the invoke mock above.
  state.startOperationTask = payload => state.invoke('start_operation_task', { payload })
  state.setPluginViewRoot = root => state.invoke('set_plugin_view_root', { root })
  controller = vm.runInNewContext(`${code}\n({
    handleCall, postToFrame, onWindowMessage, onChannelMessage, onFrameLoad,
    mount: onMountedCallback, unmount: onBeforeUnmountCallback,
  })`, state)
  return { state, controller, messages }
}

function bootstrap(state, controller, sessionId = 'session-a') {
  const pair = new state.MessageChannel()
  controller.onWindowMessage({
    source: state.frame.value.contentWindow,
    ports: [pair.port1],
    data: { jsonrpc: '2.0', method: 'pymss.ready', sessionId },
  })
  return pair
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('native operation start failure removes its pending SDK request', async () => {
  const failure = new Error('worker unavailable')
  const { state, controller } = host({ invoke: async () => { throw failure } })
  await assert.rejects(controller.handleCall(1, 'operations.start', {}), error => error === failure)
  assert.equal(state.runRequests.size, 0)
})

test('unmount releases a listener that resolves after the view is closed', async () => {
  const listener = deferred()
  const started = deferred()
  let unlistened = 0
  const { state, controller } = host({ listen: () => { started.resolve(); return listener.promise } })
  const mounted = controller.mount()
  await started.promise
  assert.equal(state.src.value, '')
  controller.unmount()
  listener.resolve(() => { unlistened++ })
  await mounted
  assert.equal(unlistened, 1)
  assert.equal(state.src.value, '')
})

test('listener setup failure becomes a view error', async () => {
  const { state, controller } = host({ listen: async () => { throw new Error('listen unavailable') } })
  await controller.mount()
  assert.equal(state.status.value, 'error')
  assert.match(state.errorText.value, /listen unavailable/)
  assert.equal(state.src.value, '')
})

test('the frame waits for the native plugin root and event listener', async () => {
  const root = deferred()
  const commands = []
  const { state, controller } = host({ invoke: (command, args) => {
    commands.push([command, args.root])
    return root.promise
  } })
  const mounted = controller.mount()
  assert.equal(state.src.value, '')
  root.resolve()
  await mounted
  assert.deepEqual(commands, [['set_plugin_view_root', '/plugins']])
  assert.match(state.src.value, /^http:.*#pymss-session=session-a$/)
})

test('unmount rejects an operation while native startup is pending', async () => {
  const startup = deferred()
  const { state, controller, messages } = host({ invoke: () => startup.promise })
  const running = controller.handleCall(1, 'operations.start', {})
  assert.equal(state.runRequests.size, 1)
  controller.unmount()
  assert.equal(state.runRequests.size, 0)
  controller.postToFrame({ result: 'late' })
  assert.deepEqual(messages, [])
  startup.resolve()
  await assert.rejects(running, /PLUGIN_VIEW_CLOSED/)
})

test('an unknown session cannot open a channel or invoke RPC', async () => {
  const calls = []
  const { state, controller, messages } = host({ invoke: (...args) => calls.push(args) })
  await controller.mount()
  calls.length = 0
  controller.onWindowMessage({
    source: state.frame.value.contentWindow,
    data: { jsonrpc: '2.0', method: 'pymss.ready', sessionId: 'session-a' },
  })
  assert.equal(state.channel, undefined)
  bootstrap(state, controller, 'attacker-session')
  assert.equal(state.channel, undefined)
  bootstrap(state, controller, 'session-a')
  assert.ok(state.channel)
  calls.length = 0
  messages.length = 0
  controller.onChannelMessage({
    data: { jsonrpc: '2.0', id: 9, method: 'operations.describe', sessionId: 'attacker-session', params: {} },
  })
  await new Promise(setImmediate)
  assert.deepEqual(calls, [])
  assert.deepEqual(messages, [])
})

test('bootstrap uses the document-created port without granting a replacement to the window', async () => {
  const direct = []
  const { state, controller } = host()
  await controller.mount()
  state.frame.value.contentWindow.postMessage = (...args) => direct.push(args)
  const pair = bootstrap(state, controller)
  assert.equal(state.channel, pair.port1)
  assert.deepEqual(direct, [])
})

test('navigation rejects pending work, closes the old port and requires a fresh session', async () => {
  const { state, controller, messages } = host({ invoke: async () => undefined })
  await controller.mount()
  bootstrap(state, controller)
  const running = controller.handleCall(7, 'operations.start', {})
  assert.equal(state.runRequests.size, 1)
  controller.onFrameLoad()
  controller.onFrameLoad()
  await assert.rejects(running, /PLUGIN_VIEW_NAVIGATED/)
  assert.equal(state.closed, 1)
  assert.equal(state.sessionId, 'session-b')
  assert.match(state.src.value, /session-b$/)
  const sent = messages.length
  controller.postToFrame({ jsonrpc: '2.0', id: 7, result: { path: '/private/result.wav' } })
  assert.equal(messages.length, sent)
  controller.onFrameLoad()
  assert.equal(state.recovering, false)
  bootstrap(state, controller, 'session-a')
  assert.equal(messages.length, sent)
  bootstrap(state, controller, 'session-b')
  assert.equal(messages.at(-1).method, 'host.ready')
  assert.equal(messages.at(-1).sessionId, 'session-b')
})

test('the actual SDK correlates concurrent progress and waits for terminal responses', { timeout: 5000 }, async () => {
  let workerListener
  const starts = []
  const { state, controller } = host({
    listen: async (_event, callback) => { workerListener = callback; return () => {} },
    invoke: async (command, args) => { if (command === 'start_operation_task') starts.push(args.payload) },
  })
  await controller.mount()
  const sdkWindow = {
    location: { hash: '#pymss-session=session-a' },
    parent: { postMessage: (message, _origin, ports) => {
      queueMicrotask(() => controller.onWindowMessage({ source: state.frame.value.contentWindow, data: message, ports }))
    } },
  }
  vm.runInNewContext(readFileSync(new URL('../src-tauri/resources/plugin-sdk/pymss-sdk.js', import.meta.url), 'utf8'), {
    window: sdkWindow, MessageChannel: state.MessageChannel,
  })
  const sdkChannel = state.lastPair.port2
  state.onPort1Message = message => queueMicrotask(() => sdkChannel.onmessage({ data: message }))
  await sdkWindow.pymss.ready()
  const progress = [[], []]
  let finished = false
  const first = sdkWindow.pymss.startOperation('test.first', {}, {}, value => { progress[0].push(value.fraction); throw new Error('plugin callback') })
    .then(value => { finished = true; return value })
  const second = sdkWindow.pymss.startOperation('test.second', {}, {}, value => progress[1].push(value.fraction))
  await new Promise(setImmediate)
  assert.equal(starts.length, 2)
  const emit = (index, type, payload) => workerListener({ payload: { type, requestId: starts[index].requestId, payload } })
  emit(1, 'task.progress', { fraction: 0.75 })
  emit(0, 'task.progress', { fraction: 0.25 })
  await new Promise(setImmediate)
  assert.deepEqual(progress, [[0.25], [0.75]])
  assert.equal(finished, false)
  emit(0, 'task.result', { outputs: { value: 1 } })
  emit(1, 'task.result', { outputs: { value: 2 } })
  assert.equal((await first).outputs.value, 1)
  assert.equal((await second).outputs.value, 2)
  emit(0, 'task.progress', { fraction: 1 })
  await new Promise(setImmediate)
  assert.deepEqual(progress, [[0.25], [0.75]])
  assert.equal(state.runRequests.size, 0)
  controller.unmount()
})
