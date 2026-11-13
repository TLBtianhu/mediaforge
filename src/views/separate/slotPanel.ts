import type { ComputedRef, Ref } from 'vue'
import { buildRenderPlan, type ModelInterface } from '@/litegraph/renderPlan'
import { layoutFor } from '@/litegraph/layouts'
import type { ModelEntry } from '@/stores/model'

type Deps = {
  computed: {
    <T>(source: () => T): ComputedRef<T>
  }
  currentModelInfo: Ref<ModelEntry | null>
  currentChunkConstraint: Ref<{ step: number; offset: number; min: number } | null | undefined>
  slotRefs: Record<string, Ref<unknown>>
}

/**
 * The advanced panel is driven by the model's slot contract plus a front-end
 * layout. Keep the interface/plan/values derivation out of the view; the view
 * only supplies the native refs the slots bind to.
 */
export function makeSlotPanel({ computed, currentModelInfo, currentChunkConstraint, slotRefs }: Deps) {
  const slotPanelInterface = computed<ModelInterface | null>(() => {
    const info = currentModelInfo.value
    const slots = (info as { slots?: ModelInterface['slots'] } | null)?.slots
    if (!info?.name || !slots || Object.keys(slots).length === 0) return null
    const task = (info as { slotTask?: string }).slotTask || 'separation'
    const interface_: ModelInterface = { modelId: info.name, task, slots: { ...slots } }
    // Constraints (chunk alignment) come from the backend; only pass them through.
    const constraint = currentChunkConstraint.value
    const chunk = interface_.slots.chunk_size
    if (chunk && constraint) {
      interface_.slots.chunk_size = {
        ...chunk,
        align: { step: constraint.step, offset: constraint.offset, min: constraint.min },
      }
    }
    return interface_
  })

  const slotPanelPlan = computed(() => {
    const current = slotPanelInterface.value
    return current ? buildRenderPlan(current, layoutFor(current.task)) : null
  })

  const slotPanelValues = computed<Record<string, unknown>>(() => {
    const current = slotPanelInterface.value
    if (!current) return {}
    const values: Record<string, unknown> = {}
    for (const [slot, binding] of Object.entries(current.slots)) {
      const target = slotRefs[binding.bind]
      if (target && target.value !== undefined) values[slot] = target.value
    }
    return values
  })

  function updateSlotPanel(next: Record<string, unknown>) {
    const current = slotPanelInterface.value
    if (!current) return
    for (const [slot, value] of Object.entries(next)) {
      const binding = current.slots[slot]
      const target = binding ? slotRefs[binding.bind] : undefined
      if (target) target.value = value
    }
  }

  return { slotPanelInterface, slotPanelPlan, slotPanelValues, updateSlotPanel }
}
