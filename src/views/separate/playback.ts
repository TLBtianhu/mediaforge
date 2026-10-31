// SeparateView 的播放与预览缓存：播放状态、LRU 缓存、预加载同步。
// 依赖通过 deps 注入（Audio 构造器与 convertFileSrc 也由调用方提供，方便 VM 测试）。
import type { StemOutput } from '@/stores/task'

type Deps = Record<string, any>

export function makePlayback(deps: Deps) {
  function getOutputPlayback(path: string) {
    return deps.outputPlayback.value[path] || { currentTime: 0, duration: 0 }
  }

  function setOutputPlayback(path: string, patch: Partial<{ currentTime: number; duration: number }>) {
    const previous = getOutputPlayback(path)
    deps.outputPlayback.value = {
      ...deps.outputPlayback.value,
      [path]: { ...previous, ...patch },
    }
  }

  function touchPreviewAudio(path: string) {
    const index = deps.audioAccessOrder.indexOf(path)
    if (index >= 0) deps.audioAccessOrder.splice(index, 1)
    deps.audioAccessOrder.push(path)
  }

  function releasePreviewAudio(path: string) {
    const audio = deps.audioElements.get(path)
    if (audio) {
      deps.audioElements.delete(path)
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
    const accessIndex = deps.audioAccessOrder.indexOf(path)
    if (accessIndex >= 0) deps.audioAccessOrder.splice(accessIndex, 1)
    if (deps.playingOutputPath.value === path) deps.playingOutputPath.value = ''
    if (deps.outputPlayback.value[path]) {
      const { [path]: _, ...rest } = deps.outputPlayback.value
      deps.outputPlayback.value = rest
    }
  }

  function trimPreviewAudioCache(protectedPath = '') {
    while (deps.audioElements.size > deps.PREVIEW_AUDIO_CACHE_LIMIT) {
      const candidate = deps.audioAccessOrder.find((path: string) => path !== protectedPath && path !== deps.playingOutputPath.value)
        || deps.audioAccessOrder[0]
      if (!candidate) return
      releasePreviewAudio(candidate)
    }
  }

  function getAudio(path: string) {
    const cached = deps.audioElements.get(path)
    if (cached) {
      touchPreviewAudio(path)
      return cached
    }

    const audio = new deps.Audio()
    audio.preload = 'metadata'
    audio.src = deps.convertFileSrc(path)
    audio.addEventListener('loadedmetadata', () => {
      if (deps.audioElements.get(path) !== audio) return
      setOutputPlayback(path, { duration: audio.duration || 0 })
    })
    audio.addEventListener('timeupdate', () => {
      if (deps.audioElements.get(path) !== audio) return
      setOutputPlayback(path, { currentTime: audio.currentTime || 0, duration: audio.duration || 0 })
    })
    audio.addEventListener('ended', () => {
      if (deps.audioElements.get(path) !== audio) return
      if (deps.playingOutputPath.value === path) deps.playingOutputPath.value = ''
      setOutputPlayback(path, { currentTime: 0, duration: audio.duration || 0 })
    })
    audio.addEventListener('error', () => {
      if (deps.audioElements.get(path) !== audio) return
      if (deps.playingOutputPath.value === path) deps.playingOutputPath.value = ''
    })
    deps.audioElements.set(path, audio)
    touchPreviewAudio(path)
    trimPreviewAudioCache(path)
    audio.load()
    return audio
  }

  function syncOutputPreviewAudio(outputs: StemOutput[]) {
    const activePaths = new Set(outputs.map(output => output.path).filter(Boolean))
    for (const path of [...deps.audioElements.keys()]) {
      if (!activePaths.has(path)) releasePreviewAudio(path)
    }
    outputs.slice(0, deps.PREVIEW_AUDIO_CACHE_LIMIT).forEach((output) => {
      if (output.path) getAudio(output.path)
    })
  }

  return {
    getOutputPlayback, setOutputPlayback, touchPreviewAudio, releasePreviewAudio,
    trimPreviewAudioCache, getAudio, syncOutputPreviewAudio,
  }
}
