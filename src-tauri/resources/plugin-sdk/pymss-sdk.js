/* pymss plugin SDK script.
 * Bridges a sandboxed plugin view page to pymss Studio over JSON-RPC
 * (postMessage), and applies host design tokens to this document so plugin
 * pages visually match the app (dark/light, theme switches) with zero CSS
 * shipping on the plugin side.
 *
 * Usage:
 *   <link rel="stylesheet" href="../../../sdk/pymss-sdk.css">
 *   <script src="../../../sdk/pymss-sdk.js"></script>
 *   const result = await pymss.call('operations.start', {...})
 */
(function () {
  'use strict'

  var pending = new Map() // id -> {resolve, reject, onProgress}
  var nextId = 1
  var readyResolvers = []
  var hostReady = false
  var channel = null
  var outbound = []
  var sessionId = (function () {
    var match = window.location.hash.match(/^#pymss-session=([^&]+)/)
    if (!match) return ''
    try { return decodeURIComponent(match[1]) } catch (e) { return '' }
  })()

  function post(message) {
    if (!channel) {
      outbound.push(message)
      return
    }
    channel.postMessage(Object.assign({}, message, { sessionId: sessionId }))
  }

  function handleMessage(event) {
    var data = event.data
    if (!data || typeof data !== 'object' || data.sessionId !== sessionId) return

    // Host pushes theme tokens; apply them to :root immediately.
    if (data.method === 'host.theme' && data.params) { applyTheme(data.params); return }
    if (data.method === 'host.ready') {
      hostReady = true
      var queue = readyResolvers; readyResolvers = []
      queue.forEach(function (fn) { try { fn() } catch (e) { /* plugin cb */ } })
      return
    }

    // Progress retains the pending call until its terminal RPC response.
    if (data.jsonrpc === '2.0' && data.method === 'task.progress' && data.params) {
      var progressEntry = pending.get(data.params.id)
      if (progressEntry && typeof progressEntry.onProgress === 'function') {
        try { progressEntry.onProgress(data.params) } catch (e) { /* plugin cb */ }
      }
      return
    }

    // RPC responses
    if (data.jsonrpc !== '2.0' || data.id === undefined) return
    var entry = pending.get(data.id)
    if (!entry) return
    pending.delete(data.id)
    if (data.error) entry.reject(new Error(String(data.error.message || 'RPC error')))
    else entry.resolve(data.result)
  }

  function applyTheme(params) {
    var root = document.documentElement
    var vars = params.cssVariables || {}
    Object.keys(vars).forEach(function (key) {
      if (/^--[a-z0-9-]+$/i.test(key)) root.style.setProperty(key, vars[key])
    })
    if (params.colorMode) root.dataset.mode = params.colorMode
    if (params.fontFamily) root.style.setProperty('--font-sans', params.fontFamily)
    document.dispatchEvent(new CustomEvent('pymss:theme', { detail: params }))
  }

  function call(method, params, options) {
    return new Promise(function (resolve, reject) {
      var id = nextId++
      pending.set(id, { resolve: resolve, reject: reject, onProgress: options && options.onProgress })
      post({ jsonrpc: '2.0', id: id, method: method, params: params || {} })
    })
  }

  // Only the URL fragment carries the bootstrap credential. The port itself is
  // created by this document, so a delayed handshake cannot grant it to the next one.
  var pair = new MessageChannel()
  channel = pair.port2
  channel.onmessage = handleMessage
  channel.start()
  var queue = outbound; outbound = []
  queue.forEach(post)
  window.parent.postMessage({ jsonrpc: '2.0', method: 'pymss.ready', sessionId: sessionId }, '*', [pair.port1])

  window.pymss = {
    call: call,
    ready: function () {
      return new Promise(function (resolve) {
        if (hostReady) resolve()
        else readyResolvers.push(resolve)
      })
    },
    onTheme: function (handler) {
      document.addEventListener('pymss:theme', function (event) { handler(event.detail) })
    },
    // Convenience wrappers over the SDK surface
    appInfo: function () { return call('app.info') },
    describeOperation: function (operation) { return call('operations.describe', { operation: operation }) },
    startOperation: function (operation, params, inputs, onProgress) {
      return call('operations.start', { operation: operation, params: params || {}, inputs: inputs || {} }, { onProgress: onProgress })
    },
    pickAudio: function () { return call('files.pickAudio', {}) },
  }
})()
