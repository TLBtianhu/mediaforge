import type { Ref } from 'vue'
import type { ScanMediaPathsResult } from '@/stores/taskTypes'

const AUDIO_EXTENSIONS = ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus']
const VIDEO_EXTENSIONS = ['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv']

function isSupportedInputPath(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  return AUDIO_EXTENSIONS.includes(ext) || VIDEO_EXTENSIONS.includes(ext)
}

type Deps = {
  inputFiles: Ref<string[]>
  invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>
}

/** Input-file selection and filesystem actions for the task store. */
export function makeTaskInputActions({ inputFiles, invoke }: Deps) {
  function addInputFiles(paths: string[]) {
    const valid = paths.filter((p) => p?.trim() && isSupportedInputPath(p))
    if (!valid.length) return 0
    const existing = new Set(inputFiles.value)
    const additions = valid.filter((p) => !existing.has(p))
    if (additions.length) inputFiles.value = [...inputFiles.value, ...additions]
    return additions.length
  }

  function removeInputFile(path: string) {
    inputFiles.value = inputFiles.value.filter((p) => p !== path)
  }

  function clearInputFiles() {
    inputFiles.value = []
  }

  function moveInputFile(fromIndex: number, toIndex: number) {
    const from = Math.trunc(Number(fromIndex))
    const to = Math.trunc(Number(toIndex))
    if (from === to || from < 0 || to < 0 || from >= inputFiles.value.length || to >= inputFiles.value.length) return false
    const next = [...inputFiles.value]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    inputFiles.value = next
    return true
  }

  async function pickFiles() {
    const files = await invoke<string[]>('pick_media_files')
    addInputFiles(files || [])
    return files?.length || 0
  }

  async function scanAndAddPaths(paths: string[]) {
    if (!paths?.length) return { added: 0, warnings: [] as string[] }
    const result = await invoke<ScanMediaPathsResult>('scan_media_paths', { paths })
    return { added: addInputFiles(result.files || []), warnings: result.warnings || [] }
  }

  async function pickInputFolder() {
    const folder = await invoke<string | null>('pick_input_folder')
    if (!folder) return 0
    const result = await scanAndAddPaths([folder])
    return result.added
  }

  async function addPaths(paths: string[]) {
    const result = await scanAndAddPaths(paths)
    return result.added
  }

  async function revealPath(path: string) {
    await invoke('reveal_path', { path })
  }

  async function trashPaths(paths: string[]) {
    const valid = paths.filter((p) => p?.trim())
    if (!valid.length) return { trashed: [] as string[], failed: [] as string[] }
    return invoke<{ trashed: string[]; failed: string[] }>('move_paths_to_trash', { paths: valid, emptyDirs: [] })
  }

  return {
    addInputFiles, removeInputFile, clearInputFiles, moveInputFile,
    pickFiles, scanAndAddPaths, pickInputFolder, addPaths, revealPath, trashPaths,
  }
}
