/**
 * 工作流列表的派生与筛选：类型判定、计数、过滤分组、列表元信息、阻塞状态。
 *
 * 纯派生，不碰 store 响应式状态；workflow utils 从唯一实现处引入。
 */
import type { ComputedRef } from 'vue'

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export function makeWorkflowListing(deps: Deps) {
  const { computed, t } = deps

  function normalizeMode(value: boolean | null) {
    return value === null ? 'global' : value ? 'enabled' : 'disabled'
  }

  function workflowKind(item: any) {
    if (deps.isSimpleWorkflowDefinition(item.definition)) return 'simple'
    if (deps.isGraphWorkflowDefinition(item.definition)) return 'advanced'
    return 'invalid'
  }

  const workflowTypeCounts = computed(() => ({
    all: deps.workflows.value.length,
    simple: deps.workflows.value.filter((item: any) => workflowKind(item) === 'simple').length,
    advanced: deps.workflows.value.filter((item: any) => workflowKind(item) === 'advanced').length,
  }))

  const workflowTypeFilterOptions = computed(() => [
    { value: 'all' as const, label: t('workflows.typeAll'), count: workflowTypeCounts.value.all },
    { value: 'simple' as const, label: t('workflows.typeSimpleShort'), count: workflowTypeCounts.value.simple },
    { value: 'advanced' as const, label: t('workflows.typeAdvancedShort'), count: workflowTypeCounts.value.advanced },
  ])

  const filteredWorkflows = computed(() => {
    const value = deps.query.value.trim().toLowerCase()
    return deps.workflows.value.filter((item: any) => {
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
      items: filteredWorkflows.value.filter((item: any) => workflowKind(item) === 'simple'),
    },
    {
      kind: 'advanced' as const,
      label: t('workflows.typeAdvanced'),
      items: filteredWorkflows.value.filter((item: any) => workflowKind(item) === 'advanced'),
    },
    {
      kind: 'invalid' as const,
      label: t('workflows.typeInvalid'),
      items: filteredWorkflows.value.filter((item: any) => workflowKind(item) === 'invalid'),
    },
  ].filter(group => group.items.length))

  const workflowListMetaMap = computed(() => Object.fromEntries(deps.workflows.value.map((item: any) => {
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

  function workflowListMeta(item: any) {
    return workflowListMetaMap.value[item.id] || ''
  }

  function workflowTypeLabel(item: any) {
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
    deps.workflows.value.map((item: any) => [item.id, workflowRunBlocked(item.definition)]),
  ))

  function isWorkflowBlocked(item: any) {
    return Boolean(workflowStatusMap.value[item.id])
  }

  const isNodeEditorOpen = computed(() => Boolean(deps.workflow.editorFor(deps.selectedWorkflowId.value)))
  const isAnyEditorOpen = computed(() => deps.workflow.hasAnyEditorOpen)

  function setWorkflowTypeFilter(value: any) {
    deps.typeFilter.value = value
  }

  return {
    normalizeMode, workflowKind, workflowTypeCounts, workflowTypeFilterOptions,
    filteredWorkflows, workflowGroups, workflowListMetaMap, workflowListMeta,
    workflowTypeLabel, workflowDefinitionError, workflowRunBlocked, workflowStatusMap,
    isWorkflowBlocked, isNodeEditorOpen, isAnyEditorOpen, setWorkflowTypeFilter,
  }
}
