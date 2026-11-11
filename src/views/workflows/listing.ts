/**
 * 工作流列表的派生与筛选：类型判定、计数、过滤分组、列表元信息、阻塞状态。
 *
 * 纯派生，不碰 store 响应式状态；workflow utils 从唯一实现处引入。
 */
import type { ComputedRef, Ref } from 'vue'
import type { WorkflowEntry } from '@/stores/workflow'
import type { useWorkflowStore } from '@/stores/workflow'

type WorkflowTypeFilter = 'all' | 'simple' | 'advanced'

type Deps = {
  computed: <T>(source: () => T) => ComputedRef<T>
  t: (key: string, named?: Record<string, unknown>) => string
  workflows: Ref<WorkflowEntry[]>
  query: Ref<string>
  typeFilter: Ref<WorkflowTypeFilter>
  workflow: ReturnType<typeof useWorkflowStore>
  selectedWorkflowId: Ref<string | null>
  isSimpleWorkflowDefinition: (definition: Record<string, unknown>) => boolean
  isGraphWorkflowDefinition: (definition: Record<string, unknown>) => boolean
  analyzeComfyOverview: (definition: Record<string, unknown>) => { nodeCount: number; outputCount: number } | null
  hydrateSimpleWorkflow: (definition: Record<string, unknown>) => { steps: unknown[]; ensembles: unknown[] }
  countWorkflowSaveOutputs: (definition: Record<string, unknown>) => number
  getWorkflowDefinitionIssue: (definition: Record<string, unknown>) => string | null
}

export function makeWorkflowListing(deps: Deps) {
  const { computed, t } = deps

  function normalizeMode(value: boolean | null) {
    return value === null ? 'global' : value ? 'enabled' : 'disabled'
  }

  function workflowKind(item: WorkflowEntry) {
    if (deps.isSimpleWorkflowDefinition(item.definition)) return 'simple'
    if (deps.isGraphWorkflowDefinition(item.definition)) return 'advanced'
    return 'invalid'
  }

  const workflowTypeCounts = computed(() => ({
    all: deps.workflows.value.length,
    simple: deps.workflows.value.filter(item => workflowKind(item) === 'simple').length,
    advanced: deps.workflows.value.filter(item => workflowKind(item) === 'advanced').length,
  }))

  const workflowTypeFilterOptions = computed(() => [
    { value: 'all' as const, label: t('workflows.typeAll'), count: workflowTypeCounts.value.all },
    { value: 'simple' as const, label: t('workflows.typeSimpleShort'), count: workflowTypeCounts.value.simple },
    { value: 'advanced' as const, label: t('workflows.typeAdvancedShort'), count: workflowTypeCounts.value.advanced },
  ])

  const filteredWorkflows = computed(() => {
    const value = deps.query.value.trim().toLowerCase()
    return deps.workflows.value.filter(item => {
      if (deps.typeFilter.value !== 'all' && workflowKind(item) !== deps.typeFilter.value) return false
      if (!value) return true
      return (
        item.name.toLowerCase().includes(value)
        || item.description.toLowerCase().includes(value)
      )
    })
  })

  const workflowGroups = computed(() => [
    {
      kind: 'simple' as const,
      label: t('workflows.typeSimple'),
      items: filteredWorkflows.value.filter(item => workflowKind(item) === 'simple'),
    },
    {
      kind: 'advanced' as const,
      label: t('workflows.typeAdvanced'),
      items: filteredWorkflows.value.filter(item => workflowKind(item) === 'advanced'),
    },
    {
      kind: 'invalid' as const,
      label: t('workflows.typeInvalid'),
      items: filteredWorkflows.value.filter(item => workflowKind(item) === 'invalid'),
    },
  ].filter(group => group.items.length))

  const workflowListMetaMap = computed(() => Object.fromEntries(deps.workflows.value.map(item => {
    if (deps.isGraphWorkflowDefinition(item.definition)) {
      const overview = deps.analyzeComfyOverview(item.definition)
      return [item.id, overview
        ? t('workflows.listMetaAdvanced', { nodes: overview.nodeCount, outputs: overview.outputCount })
        : t('workflows.typeAdvanced')]
    }
    if (deps.isSimpleWorkflowDefinition(item.definition)) {
      const draft = deps.hydrateSimpleWorkflow(item.definition)
      return [item.id, t('workflows.listMetaSimple', {
        steps: draft.steps.length,
        ensembles: draft.ensembles.length,
        outputs: deps.countWorkflowSaveOutputs(item.definition),
      })]
    }
    return [item.id, t('workflows.workflowValidationTitle')]
  })))

  function workflowListMeta(item: WorkflowEntry) {
    return workflowListMetaMap.value[item.id] || ''
  }

  function workflowTypeLabel(item: WorkflowEntry) {
    const kind = workflowKind(item)
    if (kind === 'simple') return t('workflows.typeSimpleShort')
    if (kind === 'advanced') return t('workflows.typeAdvancedShort')
    return t('workflows.typeInvalidShort')
  }

  function workflowDefinitionError(definition: Record<string, unknown>): string {
    const issue = deps.getWorkflowDefinitionIssue(definition)
    if (issue === 'steps-required') return t('workflows.stepsRequired')
    if (issue === 'no-save-outputs') return t('workflows.workflowNoSaveOutputs')
    if (issue === 'invalid-definition') return t('workflows.workflowFormatInvalid')
    if (issue === 'invalid-format') return t('workflows.workflowFormatInvalid')
    return ''
  }

  function workflowRunBlocked(definition: Record<string, unknown>) {
    return Boolean(workflowDefinitionError(definition))
  }

  const workflowStatusMap = computed(() => Object.fromEntries(
    deps.workflows.value.map(item => [item.id, workflowRunBlocked(item.definition)]),
  ))

  function isWorkflowBlocked(item: WorkflowEntry) {
    return Boolean(workflowStatusMap.value[item.id])
  }

  const isNodeEditorOpen = computed(() => deps.selectedWorkflowId.value == null ? false : Boolean(deps.workflow.editorFor(deps.selectedWorkflowId.value)))
  const isAnyEditorOpen = computed(() => deps.workflow.hasAnyEditorOpen)

  function setWorkflowTypeFilter(value: WorkflowTypeFilter) {
    deps.typeFilter.value = value
  }

  return {
    normalizeMode, workflowKind, workflowTypeCounts, workflowTypeFilterOptions,
    filteredWorkflows, workflowGroups, workflowListMetaMap, workflowListMeta,
    workflowTypeLabel, workflowDefinitionError, workflowRunBlocked, workflowStatusMap,
    isWorkflowBlocked, isNodeEditorOpen, isAnyEditorOpen, setWorkflowTypeFilter,
  }
}
