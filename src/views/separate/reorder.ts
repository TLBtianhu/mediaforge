import { ref, type Ref } from 'vue'

export function moveItem<T>(items: T[], fromIndex: number, toIndex: number) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= items.length || toIndex >= items.length) return items
  const next = [...items]
  const [item] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, item)
  return next
}

export function makeReorder(options: {
  isRunModeLocked: Ref<boolean>
  orderedOutputStems: Ref<string[]>
  customStemOrder: Ref<string[]>
  moveInputFile: (fromIndex: number, toIndex: number) => void
}) {
  const { isRunModeLocked, orderedOutputStems, customStemOrder, moveInputFile } = options
  const draggedInputIndex = ref<number | null>(null)
  const inputDragPointerId = ref<number | null>(null)
  const draggedStemIndex = ref<number | null>(null)
  const stemDragPointerId = ref<number | null>(null)
  const stemRowHeight = 43

  function moveStem(fromIndex: number, toIndex: number) {
    customStemOrder.value = moveItem(orderedOutputStems.value, fromIndex, toIndex)
  }

  function onInputPointerDown(event: PointerEvent, index: number) {
    if (isRunModeLocked.value) return
    const target = event.target as HTMLElement | null
    if (target?.closest('.file-chip__remove')) return
    draggedInputIndex.value = index
    inputDragPointerId.value = event.pointerId
    ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  }

  function onInputPointerMove(event: PointerEvent) {
    if (inputDragPointerId.value !== event.pointerId || draggedInputIndex.value === null) return
    const list = (event.currentTarget as HTMLElement).parentElement
    const rows = Array.from(list?.querySelectorAll<HTMLElement>('.file-chip') || [])
    if (!rows.length) return
    const targetIndex = rows.findIndex(row => event.clientY < row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2)
    const nextIndex = targetIndex === -1 ? rows.length - 1 : targetIndex
    if (nextIndex === draggedInputIndex.value) return
    moveInputFile(draggedInputIndex.value, nextIndex)
    draggedInputIndex.value = nextIndex
  }

  function finishInputPointerDrag(event?: PointerEvent) {
    if (event && inputDragPointerId.value === event.pointerId) {
      ;(event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId)
    }
    inputDragPointerId.value = null
    draggedInputIndex.value = null
  }

  function onStemPointerDown(event: PointerEvent, index: number) {
    const target = event.target as HTMLElement | null
    if (target?.closest('.stem-order-row__actions')) return
    draggedStemIndex.value = index
    stemDragPointerId.value = event.pointerId
    ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  }

  function onStemPointerMove(event: PointerEvent) {
    if (stemDragPointerId.value !== event.pointerId || draggedStemIndex.value === null) return
    const list = (event.currentTarget as HTMLElement).parentElement
    const listTop = list?.getBoundingClientRect().top ?? (event.currentTarget as HTMLElement).getBoundingClientRect().top
    const targetIndex = Math.max(0, Math.min(
      orderedOutputStems.value.length - 1,
      Math.trunc((event.clientY - listTop) / stemRowHeight),
    ))
    if (targetIndex === draggedStemIndex.value) return
    moveStem(draggedStemIndex.value, targetIndex)
    draggedStemIndex.value = targetIndex
  }

  function finishStemPointerDrag(event?: PointerEvent) {
    if (event && stemDragPointerId.value === event.pointerId) {
      ;(event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId)
    }
    stemDragPointerId.value = null
    draggedStemIndex.value = null
  }

  return {
    draggedInputIndex,
    inputDragPointerId,
    draggedStemIndex,
    stemDragPointerId,
    moveStem,
    onInputPointerDown,
    onInputPointerMove,
    finishInputPointerDrag,
    onStemPointerDown,
    onStemPointerMove,
    finishStemPointerDrag,
  }
}
