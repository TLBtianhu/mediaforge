/**
 * 设置页的代理连通性测试：一次探测 + 把失败翻译成可操作的下一句。
 *
 * 纯判断都在 buildProxyTestSuggestion / formatConnectionInfo，测试直接喂错误文本；
 * testProxyConnection 只负责发命令与落状态。
 */
type Deps = {
  t: (key: string, params?: Record<string, unknown>) => string
  invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>
  proxyMode: { value: string }
  proxyUrl: { value: string }
  proxyBypass: { value: string }
  downloadSource: { value: string }
  proxyTestStatus: { value: 'idle' | 'testing' | 'success' | 'error' }
  proxyTestMessage: { value: string }
  proxyTestSuggestion: { value: string }
  proxyTestElapsed: { value: number }
}

export function makeProxyTest(deps: Deps) {
  const { t, invoke } = deps

  function buildProxyTestSuggestion(error: string, mode: string): string {
    const lower = error.toLowerCase()
    if (lower.includes('invalid_proxy_url')) {
      return t('settings.proxySuggestionBadScheme')
    }
    if (lower.includes('invalid_proxy_port')) {
      return t('settings.proxySuggestionBadScheme')
    }
    if (lower.includes('unsupported_proxy_scheme')) {
      return t('settings.proxySuggestionBadScheme')
    }
    if (lower.includes('unsupported proxy scheme')) {
      return t('settings.proxySuggestionBadScheme')
    }
    if (lower.includes('pysocks')) {
      return t('settings.proxySuggestionPySocks')
    }
    if (lower.includes('getaddrinfo') || lower.includes('name or service not known') || lower.includes('nodename nor servname')) {
      if (mode === 'none') {
        return t('settings.proxySuggestionDnsNone')
      }
      if (mode === 'system') {
        return t('settings.proxySuggestionDnsSystem')
      }
      return t('settings.proxySuggestionDnsCustom')
    }
    if (lower.includes('timed out') || lower.includes('timeout')) {
      return t('settings.proxySuggestionTimeout')
    }
    if (lower.includes('proxyerror') || lower.includes('407') || lower.includes('proxy')) {
      return t('settings.proxySuggestionProxyError')
    }
    if (lower.includes('connection refused') || lower.includes('connection reset')) {
      return mode === 'custom'
        ? t('settings.proxySuggestionRefusedCustom')
        : t('settings.proxySuggestionRefused')
    }
    return ''
  }

  async function testProxyConnection() {
    deps.proxyTestStatus.value = 'testing'
    deps.proxyTestMessage.value = t('settings.proxyTesting')
    deps.proxyTestSuggestion.value = ''
    deps.proxyTestElapsed.value = 0
    try {
      const result = await invoke<{
        ok: boolean
        status?: number
        ip?: string
        filesCount?: number
        error?: string
        elapsedMs: number
        mode: string
        proxy?: string
      }>('test_proxy_connection', {
        payload: {
          mode: deps.proxyMode.value,
          url: deps.proxyUrl.value,
          bypass: deps.proxyBypass.value,
          source: deps.downloadSource.value,
          timeout: 15,
        },
      })
      deps.proxyTestElapsed.value = result.elapsedMs
      if (result.ok) {
        deps.proxyTestStatus.value = 'success'
        deps.proxyTestMessage.value = formatConnectionInfo(result)
        deps.proxyTestSuggestion.value = ''
      } else {
        deps.proxyTestStatus.value = 'error'
        const error = result.error || t('settings.proxyTestUnknownError')
        deps.proxyTestMessage.value = error
        deps.proxyTestSuggestion.value = buildProxyTestSuggestion(error, result.mode || deps.proxyMode.value)
      }
    } catch (err) {
      deps.proxyTestStatus.value = 'error'
      const error = err instanceof Error ? err.message : String(err)
      deps.proxyTestMessage.value = error
      deps.proxyTestSuggestion.value = buildProxyTestSuggestion(error, deps.proxyMode.value)
    }
  }

  function formatConnectionInfo(info: {
    status?: number
    ip?: string
    filesCount?: number
    elapsedMs?: number
  }): string {
    const parts: string[] = []
    if (info.status) parts.push(`HTTP ${info.status}`)
    if (info.ip) parts.push(info.ip)
    if (typeof info.filesCount === 'number' && info.filesCount > 0) {
      parts.push(t('settings.proxyTestFiles', { count: info.filesCount }))
    }
    if (info.elapsedMs) parts.push(`${info.elapsedMs}ms`)
    return parts.join('  ·  ')
  }

  function resetProxyTest() {
    deps.proxyTestStatus.value = 'idle'
    deps.proxyTestMessage.value = ''
    deps.proxyTestSuggestion.value = ''
    deps.proxyTestElapsed.value = 0
  }

  return { buildProxyTestSuggestion, testProxyConnection, formatConnectionInfo, resetProxyTest }
}
