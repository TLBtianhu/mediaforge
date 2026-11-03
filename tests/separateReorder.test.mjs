import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { makeReorder, moveItem } from '../src/views/separate/reorder.ts'

function setup() {
  const inputMoves = []
  const captured = []
  const released = []
  const isRunModeLocked = ref(false)
  const orderedOutputStems = ref(['vocals', 'drums', 'bass'])
  const customStemOrder = ref([])
  const view = makeReorder({
    isRunModeLocked,
    orderedOutputStems,
    customStemOrder,
    moveInputFile: (from, to) => inputMoves.push([from, to]),
  })
  const control = {
    setPointerCapture: id => captured.push(id),
    releasePointerCapture: id => released.push(id),
  }
  return { view, inputMoves, captured, released, control, isRunModeLocked, customStemOrder }
}

const plainTarget = { closest: () => null }

test('moveItem is immutable and ignores invalid or unchanged positions', () => {
  const items = ['a', 'b', 'c']
  assert.deepEqual(moveItem(items, 0, 2), ['b', 'c', 'a'])
  assert.deepEqual(items, ['a', 'b', 'c'])
  for (const [from, to] of [[0, 0], [-1, 1], [0, -1], [3, 1], [0, 3]]) {
    assert.equal(moveItem(items, from, to), items)
  }
})

test('input dragging uses row midpoints and releases pointer capture', () => {
  const { view, inputMoves, captured, released, control } = setup()
  view.onInputPointerDown({ pointerId: 7, target: plainTarget, currentTarget: control }, 0)
  assert.equal(view.draggedInputIndex.value, 0)
  assert.deepEqual(captured, [7])
  const rows = [0, 20, 40].map(top => ({ getBoundingClientRect: () => ({ top, height: 20 }) }))
  const list = { querySelectorAll: () => rows }
  const target = { parentElement: list }
  view.onInputPointerMove({ pointerId: 7, clientY: 45, currentTarget: target })
  assert.deepEqual(inputMoves, [[0, 2]])
  assert.equal(view.draggedInputIndex.value, 2)
  view.onInputPointerMove({ pointerId: 8, clientY: 0, currentTarget: target })
  assert.deepEqual(inputMoves, [[0, 2]])
  view.finishInputPointerDrag({ pointerId: 7, currentTarget: control })
  assert.deepEqual(released, [7])
  assert.equal(view.draggedInputIndex.value, null)
  assert.equal(view.inputDragPointerId.value, null)
})

test('input dragging ignores remove actions and locked run state', () => {
  const { view, captured, control, isRunModeLocked } = setup()
  view.onInputPointerDown({ pointerId: 1, target: { closest: () => ({}) }, currentTarget: control }, 0)
  assert.equal(view.draggedInputIndex.value, null)
  isRunModeLocked.value = true
  view.onInputPointerDown({ pointerId: 2, target: plainTarget, currentTarget: control }, 0)
  assert.equal(view.draggedInputIndex.value, null)
  assert.deepEqual(captured, [])
})

test('stem dragging reorders by row height and preserves the latest position', () => {
  const { view, captured, released, control, customStemOrder } = setup()
  view.onStemPointerDown({ pointerId: 9, target: plainTarget, currentTarget: control }, 0)
  assert.deepEqual(captured, [9])
  const list = { getBoundingClientRect: () => ({ top: 100 }) }
  view.onStemPointerMove({ pointerId: 9, clientY: 190, currentTarget: { parentElement: list } })
  assert.deepEqual(customStemOrder.value, ['drums', 'bass', 'vocals'])
  assert.equal(view.draggedStemIndex.value, 2)
  view.finishStemPointerDrag({ pointerId: 9, currentTarget: control })
  assert.deepEqual(released, [9])
  assert.equal(view.draggedStemIndex.value, null)
})

test('stem buttons use the same immutable reorder operation', () => {
  const { view, customStemOrder } = setup()
  view.moveStem(0, 1)
  assert.deepEqual(customStemOrder.value, ['drums', 'vocals', 'bass'])
})

test('stem action areas do not start dragging', () => {
  const { view, captured, control } = setup()
  view.onStemPointerDown({ pointerId: 4, target: { closest: () => ({}) }, currentTarget: control }, 0)
  assert.equal(view.draggedStemIndex.value, null)
  assert.deepEqual(captured, [])
})
