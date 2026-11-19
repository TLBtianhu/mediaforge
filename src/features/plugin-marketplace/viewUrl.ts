type ProtocolUrl = (path: string, protocol: string) => string

export function pluginViewUrl(plugin: string, view: string, entry: string, protocolUrl: ProtocolUrl, sessionId?: string): string {
  const filename = entry.split(/[\\/]/).filter(Boolean).at(-1)
  if (!filename || filename === '.' || filename === '..') throw new Error('Plugin view entry is missing')
  const base = protocolUrl('view', 'pymss-plugin').replace(/\/$/, '')
  const url = `${base}/${encodeURIComponent(plugin)}/${encodeURIComponent(view)}/${encodeURIComponent(filename)}`
  return sessionId ? `${url}#pymss-session=${encodeURIComponent(sessionId)}` : url
}
