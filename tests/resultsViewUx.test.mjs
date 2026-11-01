import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'

const path = new URL('../src/views/ResultsView.vue', import.meta.url)
const { descriptor } = parse(readFileSync(path, 'utf8'))
const template = descriptor.template?.content || ''
const script = descriptor.scriptSetup?.content || ''
const style = descriptor.styles.map(item =>
  item.src ? readFileSync(new URL(item.src, path), 'utf8') : item.content
).join('\n')

test('result selection uses the page header without inserting another toolbar', () => {
  assert.ok(template.includes("'results-page__header-actions--selecting': selecting"))
  assert.ok(template.includes('<template v-if="selecting">'))
  assert.ok(template.includes("t('results.removeSelectedCount', { count: selectedResultIds.length })"))
  assert.ok(template.includes("t('results.batchExit')"))
  assert.ok(!template.includes('class="results-batchbar"'))
  assert.ok(!style.includes('.results-batchbar'))
})

test('normal result actions keep both page actions directly available', () => {
  assert.ok(template.includes("t('results.batchSelect')"))
  assert.ok(template.includes('@click="handleClearResults"'))
  assert.ok(template.includes("t('results.clearAction')"))
  assert.ok(!template.includes(':options="resultActions"'))
})

test('selection mode removes the misleading row expansion affordance', () => {
  assert.ok(template.includes("'result-row__main--selecting': selecting"))
  assert.match(template, /v-if="!selecting"\s+class="result-row__toggle"/)
  assert.ok(style.includes('.result-row__main--selecting'))
})

test('Escape exits result selection while dialogs retain keyboard priority', () => {
  assert.ok(script.includes("event.key !== 'Escape'"))
  assert.ok(script.includes("document.querySelector('[role=\"dialog\"], .n-modal-mask')"))
  assert.ok(script.includes("window.addEventListener('keydown', handleResultsKeydown)"))
  assert.ok(script.includes("window.removeEventListener('keydown', handleResultsKeydown)"))
})

test('mouse hold and drag paints row selection without taking over text selection', () => {
  assert.ok(template.includes('@pointerdown="handleResultIconPointerDown($event, item.id)"'))
  assert.ok(template.includes('@pointerenter="handleResultIconPointerEnter($event, item.id)"'))
  assert.ok(script.includes("event.pointerType !== 'mouse' || event.button !== 0"))
  assert.ok(script.includes("if ((event.buttons & 1) === 0)"))
  assert.ok(script.includes('setTimeout(activateResultDragSelection, RESULT_DRAG_HOLD_MS)'))
  assert.ok(script.includes('if (!selecting.value) selecting.value = true'))
  assert.ok(script.includes('event.preventDefault()'))
  assert.ok(script.includes('if (hasSelectedResultText()) return'))
  assert.ok(template.includes('<strong data-result-text>'))
  assert.ok(template.includes("'result-row__selection-marker--checked': selectedResultSet.has(item.id)"))
  assert.ok(style.includes('user-select: text'))
  assert.ok(style.includes(':global(html.results-drag-selecting *)'))
})

test('drag selection is cancelled on window blur and exposes pressed state', () => {
  assert.ok(script.includes("window.addEventListener('blur', handleResultsWindowBlur)"))
  assert.ok(script.includes("window.removeEventListener('blur', handleResultsWindowBlur)"))
  assert.ok(template.includes(':aria-pressed="selecting ? selectedResultSet.has(item.id) : undefined"'))
})
