/**
 * 媒体页通用的原生拖放：拖文件加入输入，拖文件夹到目录字段 = 引用该目录。
 *
 * 三个页面（工厂/视频/媒体工作流）本来是同一套需求，所以收成一个 composable：
 * 页面只需要给出"哪些扩展名接受"和"收到文件/目录怎么办"，
 * 位置判定、文件/目录区分、卸载清理都在这里。
 *
 * 注意：Tauri 的原生拖放只给**窗口坐标**，所以用 `[data-drop-zone]` 标记投放区。
 */
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'

import { DIRECTORY_ZONES, findDropZone, folderToReference, toCssPoint, type PathKind } from '../utils/dropZones'

export interface MediaDropHandlers {
  /** 收到文件（来自文件或展开后的文件夹） */
  onFiles: (files: string[]) => void
  /** 拖到目录型字段上的单个文件夹 */
  onDirectory: (zone: string, folder: string) => void
  /** 目录型字段上拖了文件（页面自己提示） */
  onDirectoryRejected?: (zone: string) => void
  /** 只接受这些扩展名（小写、不带点）；不给则不筛 */
  accept?: string[]
  /** 是否把文件夹递归展开（默认 true，图片/媒体扫描命令本身就是递归的） */
  scanImages?: boolean
}

export function useMediaDrop(handlers: MediaDropHandlers): { dragging: Ref<boolean> } {
  const dragging = ref(false)
  let unlisten: (() => void) | undefined

  function accepted(paths: string[]): string[] {
    const accept = handlers.accept
    if (!accept || !accept.length) return paths
    const allowed = new Set(accept.map(ext => ext.toLowerCase()))
    return paths.filter(path => allowed.has(path.split('.').pop()?.toLowerCase() || ''))
  }

  async function handleDrop(rawPaths: string[], position: unknown) {
    const zones = Array.from(document.querySelectorAll<HTMLElement>('[data-drop-zone]')).map(element => {
      const rect = element.getBoundingClientRect()
      return {
        id: element.dataset.dropZone || '',
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
      }
    })
    const point = toCssPoint(position as { x: number; y: number } | null, window.devicePixelRatio)
    const zone = findDropZone(point, zones) || 'input'
    const rawKinds = await invoke<Array<[string, string]>>('path_kinds', { paths: rawPaths })
    const kinds = rawKinds.map(([path, kind]) => ({ path, kind: kind as PathKind['kind'] }))

    if (DIRECTORY_ZONES.has(zone)) {
      const folder = folderToReference(kinds)
      if (folder) {
        handlers.onDirectory(zone, folder)
        return
      }
      handlers.onDirectoryRejected?.(zone)
      return
    }

    const command = handlers.scanImages === false ? 'scan_media_paths' : 'scan_image_paths'
    const result = await invoke<{ files: string[] }>(command, { paths: rawPaths })
    const files = accepted(result.files || [])
    if (files.length) handlers.onFiles(files)
  }

  onMounted(async () => {
    if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
    try {
      unlisten = await getCurrentWebview().onDragDropEvent(async event => {
        const type = event.payload.type
        if (type === 'over' || type === 'enter') {
          dragging.value = true
          return
        }
        dragging.value = false
        if (type !== 'drop') return
        const paths = (event.payload as { paths?: string[] }).paths || []
        if (!paths.length) return
        try {
          await handleDrop(paths, (event.payload as { position?: unknown }).position)
        } catch {
          // 拖放进来的路径处理失败不该让页面报错——用户还能用"浏览"按钮
        }
      })
    } catch {
      // 浏览器预览里没有原生拖放
    }
  })

  onBeforeUnmount(() => {
    unlisten?.()
  })

  return { dragging }
}
