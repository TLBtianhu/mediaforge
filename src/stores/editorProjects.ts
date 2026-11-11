import type { Ref } from 'vue'
import { getCurrentLocale } from '@/i18n'
import { blankProjectName } from '@/stores/editorSupport'
import { normalizeSession } from '@/stores/editorNormalize'
import { isTauriRuntime } from '@/utils/appStore'
import type { SeparationTask, StemOutput } from '@/stores/task'
import type {
  EditorProjectSummary, EditorSession,
} from '@/types/editor'
import type {
  EditorProjectCleanupResult, OrphanedEditorProject, PersistedSession,
} from '@/stores/editorSupport'
import type { EditorSourceRole } from '@/types/editor'

type Deps = {
  session: Ref<EditorSession | null>
  loading: Ref<boolean>
  lastError: Ref<string | null>
  projectSummaries: Ref<EditorProjectSummary[]>
  selectedTrackId: Ref<string | null>
  selectedClipId: Ref<string | null>
  invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>
  clearHistory: () => void
  selectTrack: (trackId: string | null) => void
  localizedAutoTrackName: (name: string, role: EditorSourceRole, stemKey?: string | null) => string
  hydratePeaksFromCache: (projectId?: string) => Promise<void>
  hydrateSourceMetadataState: (projectId?: string) => Promise<boolean>
  hydrateSessionSourcesInBackground: (projectId: string) => void
  saveProject: () => Promise<unknown>
  drainPendingSaves: () => Promise<unknown>
  scheduleSave: () => void
  suspendProjectSave: (projectId: string) => void
  resumeProjectSave: (projectId: string) => void
}

/** Editor project listing, creation, loading and deletion. */
export function makeEditorProjects({
  session, loading, lastError, projectSummaries, selectedTrackId, selectedClipId,
  invoke, clearHistory, selectTrack, localizedAutoTrackName,
  hydratePeaksFromCache, hydrateSourceMetadataState, hydrateSessionSourcesInBackground,
  saveProject, drainPendingSaves, scheduleSave, suspendProjectSave, resumeProjectSave,
}: Deps) {
  function clearSession() {
    session.value = null
    selectedTrackId.value = null
    selectedClipId.value = null
    clearHistory()
  }

  async function requestProjectFromTask(task: SeparationTask) {
    return invoke<PersistedSession>('create_editor_project_from_task', {
      payload: {
        taskId: task.id,
        input: task.input,
        outputDir: task.output,
        outputs: task.outputs as StemOutput[],
      },
    })
  }

  async function createFromTask(task: SeparationTask) {
    loading.value = true
    lastError.value = null
    try {
      const result = await requestProjectFromTask(task)
      session.value = normalizeSession(result, localizedAutoTrackName)
      selectTrack(session.value.tracks[0]?.id || null)
      await hydratePeaksFromCache(session.value.id)
      const metadataChanged = await hydrateSourceMetadataState(session.value.id)
      clearHistory()
      const currentProjectId = session.value.id
      if (metadataChanged) await saveProject()
      hydrateSessionSourcesInBackground(currentProjectId)
      return session.value
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function openProjectWindow(projectId: string) {
    return invoke('open_editor_window', { payload: { projectId } })
  }

  async function ensureProjectForTask(task: SeparationTask, options?: { loadIntoSession?: boolean }) {
    if (options?.loadIntoSession !== false) return createFromTask(task)
    const result = await requestProjectFromTask(task)
    const normalized = normalizeSession(result, localizedAutoTrackName)
    return { id: normalized.id }
  }

  async function listProjects() {
    const result = await invoke<EditorProjectSummary[]>('list_editor_projects')
    projectSummaries.value = [...(result || [])].sort((a, b) => b.updatedAt - a.updatedAt)
    return projectSummaries.value
  }

  async function refreshProjects() {
    return listProjects()
  }

  // Keep this interface for the future import-first editor flow. The visible
  // blank-project entry is hidden until external audio import has clear UI.
  async function createBlankProject(name?: string) {
    const locale = getCurrentLocale()
    const result = await invoke<PersistedSession>('create_blank_editor_project', {
      payload: {
        name: name?.trim() || blankProjectName(),
        locale,
      },
    })
    const normalized = normalizeSession(result, localizedAutoTrackName)
    const summary: EditorProjectSummary = {
      id: normalized.id,
      name: normalized.name,
      sourceTaskId: normalized.sourceTaskId,
      sourceResultDir: normalized.sourceResultDir,
      createdAt: normalized.createdAt,
      updatedAt: normalized.updatedAt,
      type: normalized.sourceTaskId ? 'task' : 'blank',
    }
    projectSummaries.value = [summary, ...projectSummaries.value.filter((item) => item.id !== summary.id)]
      .sort((a, b) => b.updatedAt - a.updatedAt)
    return normalized
  }

  async function deleteProject(projectId: string) {
    // Serialize deletion after queued writes so a delayed autosave cannot
    // recreate the project after the backend removes it.
    await drainPendingSaves()
    suspendProjectSave(projectId)
    let result: boolean
    try {
      result = await invoke<boolean>('delete_editor_project', { projectId })
    } catch (error) {
      resumeProjectSave(projectId)
      if (session.value?.id === projectId) scheduleSave()
      throw error
    }
    projectSummaries.value = projectSummaries.value.filter((item) => item.id !== projectId)
    if (session.value?.id === projectId) clearSession()
    resumeProjectSave(projectId)
    return result
  }

  async function deleteProjectsForTasks(taskIds: string[]) {
    const uniqueTaskIds = [...new Set(taskIds.map(id => id.trim()).filter(Boolean))]
    const emptyResult = (): EditorProjectCleanupResult => ({
      deletedProjectIds: [],
      missingProjectIds: [],
      blockedProjectIds: [],
      failedProjectIds: [],
    })
    if (!uniqueTaskIds.length || !isTauriRuntime()) return emptyResult()
    const projectIds = await invoke<string[]>('editor_project_ids_for_tasks', { taskIds: uniqueTaskIds })

    await drainPendingSaves()
    projectIds.forEach(projectId => suspendProjectSave(projectId))
    try {
      const result = await invoke<EditorProjectCleanupResult>('delete_editor_projects_for_tasks', {
        taskIds: uniqueTaskIds,
      })
      const removedProjectIds = new Set([
        ...(result.deletedProjectIds || []),
        ...(result.missingProjectIds || []),
      ])
      projectSummaries.value = projectSummaries.value.filter(item => !removedProjectIds.has(item.id))
      if (session.value && removedProjectIds.has(session.value.id)) clearSession()
      return {
        deletedProjectIds: result.deletedProjectIds || [],
        missingProjectIds: result.missingProjectIds || [],
        blockedProjectIds: result.blockedProjectIds || [],
        failedProjectIds: result.failedProjectIds || [],
      }
    } finally {
      projectIds.forEach(projectId => resumeProjectSave(projectId))
    }
  }

  async function listOpenProjectsForTasks(taskIds: string[]) {
    const uniqueTaskIds = [...new Set(taskIds.map(id => id.trim()).filter(Boolean))]
    if (!uniqueTaskIds.length || !isTauriRuntime()) return []
    const backendOpenProjectIds = await invoke<string[]>('list_open_editor_projects_for_tasks', {
      taskIds: uniqueTaskIds,
    })
    return [...new Set(backendOpenProjectIds || [])]
  }

  async function listOrphanedProjects() {
    if (!isTauriRuntime()) return [] as OrphanedEditorProject[]
    return invoke<OrphanedEditorProject[]>('list_orphaned_editor_projects')
  }

  async function deleteOrphanedProjects() {
    const emptyResult = (): EditorProjectCleanupResult => ({
      deletedProjectIds: [],
      missingProjectIds: [],
      blockedProjectIds: [],
      failedProjectIds: [],
    })
    if (!isTauriRuntime()) return emptyResult()
    const orphanedProjects = await listOrphanedProjects()
    const projectIds = orphanedProjects.map(project => project.projectId)
    if (!projectIds.length) return emptyResult()
    await drainPendingSaves()
    projectIds.forEach(projectId => suspendProjectSave(projectId))
    try {
      const result = await invoke<EditorProjectCleanupResult>('delete_orphaned_editor_projects', { projectIds })
      const removedProjectIds = new Set([
        ...(result.deletedProjectIds || []),
        ...(result.missingProjectIds || []),
      ])
      projectSummaries.value = projectSummaries.value.filter(item => !removedProjectIds.has(item.id))
      if (session.value && removedProjectIds.has(session.value.id)) clearSession()
      return {
        deletedProjectIds: result.deletedProjectIds || [],
        missingProjectIds: result.missingProjectIds || [],
        blockedProjectIds: result.blockedProjectIds || [],
        failedProjectIds: result.failedProjectIds || [],
      }
    } finally {
      projectIds.forEach(projectId => resumeProjectSave(projectId))
    }
  }

  async function loadProject(projectId: string) {
    loading.value = true
    lastError.value = null
    try {
      const result = await invoke<PersistedSession>('load_editor_project', { projectId })
      session.value = normalizeSession(result, localizedAutoTrackName)
      await hydratePeaksFromCache(session.value.id)
      const metadataChanged = await hydrateSourceMetadataState(session.value.id)
      selectTrack(session.value.tracks[0]?.id || null)
      clearHistory()
      const currentProjectId = session.value.id
      if (metadataChanged) await saveProject()
      hydrateSessionSourcesInBackground(currentProjectId)
      return session.value
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  return {
    clearSession, requestProjectFromTask, createFromTask, openProjectWindow,
    ensureProjectForTask, listProjects, refreshProjects, createBlankProject,
    deleteProject, deleteProjectsForTasks, listOpenProjectsForTasks,
    listOrphanedProjects, deleteOrphanedProjects, loadProject,
  }
}
