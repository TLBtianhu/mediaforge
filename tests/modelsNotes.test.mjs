/**
 * 模型库备注/使用统计/收藏切换与备注编辑器契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeModelNotes } from '../src/views/models/notes.ts'

function harness(overrides = {}) {
  const calls = []
  const deps = {
    modelPreferences: { value: { 'a.ckpt': { note: '主力模型' } } },
    modelStore: {
      getModelUseCount: name => (name === 'a.ckpt' ? 7 : 0),
      toggleFavorite: name => calls.push(['fav', name]),
      setModelNote: (name, note) => calls.push(['note', name, note]),
    },
    noteEditorModel: { value: null },
    noteDraft: { value: '' },
    showNoteEditor: { value: false },
    message: { success: value => calls.push(['success', value]) },
    t: key => key,
    ...overrides,
  }
  return { calls, deps, notes: makeModelNotes(deps) }
}

test('modelNote and modelUseCount read from the store and tolerate empty names', () => {
  const { notes } = harness()
  assert.equal(notes.modelNote('a.ckpt'), '主力模型')
  assert.equal(notes.modelNote({ name: 'a.ckpt' }), '主力模型')
  assert.equal(notes.modelNote('missing'), '')
  assert.equal(notes.modelNote(null), '')
  assert.equal(notes.modelUseCount('a.ckpt'), 7)
  assert.equal(notes.modelUseCount('other'), 0)
})

test('modelNameParts splits on underscores and dashes, keeping separators', () => {
  const { notes } = harness()
  assert.deepEqual(notes.modelNameParts('mel_band-roformer'), ['mel_', 'band-', 'roformer'])
})

test('toggleFavorite forwards to the store and stops the click from bubbling', () => {
  const { calls, notes } = harness()
  let stopped = false
  notes.toggleFavorite({ name: 'a.ckpt' }, { stopPropagation: () => { stopped = true } })
  assert.deepEqual(calls, [['fav', 'a.ckpt']])
  assert.equal(stopped, true)
})

test('opening the note editor prefills the existing note', () => {
  const { deps, notes } = harness()
  notes.openModelNoteEditor({ name: 'a.ckpt' })
  assert.equal(deps.noteEditorModel.value.name, 'a.ckpt')
  assert.equal(deps.noteDraft.value, '主力模型')
  assert.equal(deps.showNoteEditor.value, true)
})

test('saving writes the note, closes the editor and confirms', () => {
  const { calls, deps, notes } = harness()
  notes.openModelNoteEditor({ name: 'a.ckpt' })
  deps.noteDraft.value = '改成新备注'
  notes.saveModelNote()
  assert.deepEqual(calls, [['note', 'a.ckpt', '改成新备注'], ['success', 'models.noteSaved']])
  assert.equal(deps.showNoteEditor.value, false)
  assert.equal(deps.noteEditorModel.value, null)
})

test('closing via update clears the editing model', () => {
  const { deps, notes } = harness()
  notes.openModelNoteEditor({ name: 'a.ckpt' })
  notes.handleNoteEditorUpdate(false)
  assert.equal(deps.showNoteEditor.value, false)
  assert.equal(deps.noteEditorModel.value, null)
})
