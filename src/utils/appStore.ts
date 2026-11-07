import { invoke } from '@tauri-apps/api/core'
import { isTutorialFrame, tutorialAdapter } from '../features/onboarding/demoContext.ts'

export type AppStoreName = 'app-settings' | 'task-history' | 'model-state' | 'editor-ui' | 'audio-tools' | 'workflow-state' | 'media-workflows' | 'separate-state' | 'update-state'

export const isTauriRuntime = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

const saveQueues = new Map<AppStoreName, Promise<void>>()

function localStoreKey(name: AppStoreName) {
  return `pymss-studio:${name}`
}

const strictStores = new Set<AppStoreName>(['task-history', 'separate-state', 'workflow-state', 'media-workflows'])

function readLocalStore<T>(name: AppStoreName): T | null {
  const raw = localStorage.getItem(localStoreKey(name))
  if (raw === null) return null
  try {
    const value = JSON.parse(raw) as T
    if (strictStores.has(name) && (!value || typeof value !== 'object' || Array.isArray(value))) {
      throw new TypeError(`Invalid ${name} store`)
    }
    return value && typeof value === 'object' ? value : null
  } catch (error) {
    if (strictStores.has(name)) throw error
    return null
  }
}

export async function loadAppStore<T>(name: AppStoreName): Promise<T | null> {
  if (isTutorialFrame()) return tutorialAdapter().invoke('load_app_store', { name }) as Promise<T | null>
  if (!isTauriRuntime()) {
    return readLocalStore<T>(name)
  }
  const value = await invoke<T | null>('load_app_store', { name })
  if (strictStores.has(name) && value !== null && (typeof value !== 'object' || Array.isArray(value))) {
    throw new TypeError(`Invalid ${name} store`)
  }
  return value && typeof value === 'object' ? value : null
}

async function writeAppStore(name: AppStoreName, data: unknown, serialized: string) {
  if (isTutorialFrame()) { await tutorialAdapter().invoke('save_app_store', { name, data }); return }
  if (!isTauriRuntime()) {
    localStorage.setItem(localStoreKey(name), serialized)
    return
  }
  await invoke('save_app_store', { name, data })
}

export function saveAppStore(name: AppStoreName, data: unknown) {
  let serialized: string | undefined
  let snapshot: unknown
  try {
    serialized = JSON.stringify(data)
    if (serialized === undefined) {
      throw new TypeError(`App store "${name}" cannot persist an undefined value`)
    }
    snapshot = JSON.parse(serialized) as unknown
  } catch (error) {
    return Promise.reject(error)
  }
  const previous = saveQueues.get(name) ?? Promise.resolve()
  const save = previous
    .catch(() => undefined)
    .then(() => writeAppStore(name, snapshot, serialized))
  saveQueues.set(name, save)
  void save.finally(() => {
    if (saveQueues.get(name) === save) saveQueues.delete(name)
  }).catch(() => undefined)
  return save
}
