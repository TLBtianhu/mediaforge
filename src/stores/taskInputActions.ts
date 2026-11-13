import type { Ref } from 'vue'
import type { ScanMediaPathsResult } from '@/stores/taskTypes'
import { revealPath as revealNativePath } from '@/api/native'

const AUDIO_EXTENSIONS = ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus']
const VIDEO_EXTENSIONS = ['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv']

function isSupportedInputPath(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  return AUDIO_EXTENSIONS.includes(ext) || VIDEO_EXTENSIONS.includes(ext)
}

/** Native file operations this module depends on; injected so tests stay filesystem-free. */
export type TaskInputNative = {
  pickMediaFiles: () => Promise<string[]>
  scanMediaPaths: (paths: string[]) => Promise<ScanMediaPathsResult>
  pickInputFolder: () => Promise<string | null>
}

type Deps = {
  inputFiles: Ref<string[]>
  native: TaskInputNative
}

/** Input-file selection and filesystem actions for the task store. */
export function makeTaskInputActions({ inputFiles, native }: Deps) {
  function addInputFiles(paths: string[]) {
    const valid = paths.filter((p) => p?.trim() && isSupportedInputPath(p))
    if (!valid.length) return 0
    const existing = new Set(inputFiles.value)
    const additions = [...new Set(valid.filter((p) => !existing.has(p)))]
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
    const files = await native.pickMediaFiles()
    addInputFiles(files || [])
    return files?.length || 0
  }

  async function scanAndAddPaths(paths: string[]) {
    if (!paths?.length) return { added: 0, warnings: [] as string[] }
    const result = await native.scanMediaPaths(paths)
    return { added: addInputFiles(result.files || []), warnings: result.warnings || [] }
  }

  async function pickInputFolder() {
    const folder = await native.pickInputFolder()
    if (!folder) return 0
    const result = await scanAndAddPaths([folder])
    return result.added
  }

  async function addPaths(paths: string[]) {
    const result = await scanAndAddPaths(paths)
    return result.added
  }

  async function revealPath(path: string) {
    await revealNativePath(path)
  }

  return {
    addInputFiles, removeInputFile, clearInputFiles, moveInputFile,
    pickFiles, scanAndAddPaths, pickInputFolder, addPaths, revealPath,
  }
}
