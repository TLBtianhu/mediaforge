<script setup lang="ts">
import { CloseOutline, FolderOpenOutline, MusicalNotesOutline, ReorderFourOutline } from '@vicons/ionicons5'
import { useI18n } from 'vue-i18n'

withDefaults(defineProps<{
  files: string[]
  busy?: boolean
  reorderable?: boolean
  draggedIndex?: number | null
  kindFor?: (path: string) => string
  shortenFor?: (path: string) => string
  dragHandlers?: {
    down: (event: PointerEvent, index: number) => void
    move: (event: PointerEvent) => void
    up: (event: PointerEvent) => void
    cancel: (event: PointerEvent) => void
  }
}>(), {
  busy: false,
  reorderable: false,
  draggedIndex: -1,
  kindFor: undefined,
  shortenFor: undefined,
  dragHandlers: undefined,
})

const emit = defineEmits<{
  add: []
  folder: []
  remove: [path: string]
  clear: []
}>()
const { t } = useI18n()
</script>

<template>
  <div class="task-input-files">
    <div class="task-input-files__actions">
      <n-button size="small" secondary :disabled="busy" @click="emit('add')">
        <template #icon><n-icon :component="MusicalNotesOutline" /></template>
        {{ t('separate.chooseFiles') }}
      </n-button>
      <n-button size="small" secondary :disabled="busy" @click="emit('folder')">
        <template #icon><n-icon :component="FolderOpenOutline" /></template>
        {{ t('separate.chooseFolder') }}
      </n-button>
      <n-button v-if="files.length" size="tiny" quaternary :disabled="busy" @click="emit('clear')">{{ t('separate.clearAll') }}</n-button>
    </div>
    <span class="task-input-files__count">{{ t('separate.candidateCount', { count: files.length }) }}</span>
    <div v-if="files.length" class="task-input-files__list" role="list">
      <div
        v-for="(path, index) in files"
        :key="path"
        class="task-input-file"
        :class="{ 'task-input-file--dragging': draggedIndex === index }"
        role="listitem"
        @pointerdown="dragHandlers?.down($event, index)"
        @pointermove="dragHandlers?.move($event)"
        @pointerup="dragHandlers?.up($event)"
        @pointercancel="dragHandlers?.cancel($event)"
      >
        <span v-if="reorderable" class="task-input-file__handle" :title="t('separate.dragToReorder')"><n-icon :component="ReorderFourOutline" /></span>
        <n-icon class="task-input-file__glyph" :component="MusicalNotesOutline" />
        <div class="task-input-file__main">
          <strong :title="path">{{ path.split(/[\\/]/).pop() }}</strong>
          <span v-if="kindFor" class="task-input-file__kind">{{ kindFor(path) }}</span>
          <code :title="path">{{ shortenFor ? shortenFor(path) : path }}</code>
        </div>
        <n-button quaternary circle size="tiny" :disabled="busy" :title="t('separate.remove')" :aria-label="t('separate.remove') + ' ' + path.split(/[\\/]/).pop()" @click.stop="emit('remove', path)">
          <template #icon><n-icon :component="CloseOutline" /></template>
        </n-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-input-files { display: grid; gap: var(--space-sm); }
.task-input-files__actions { display: flex; align-items: center; gap: var(--space-sm); flex-wrap: wrap; }
.task-input-files__count { color: var(--on-surface-muted); font-size: 12px; }
.task-input-files__list { display: grid; gap: var(--space-xs); }
.task-input-file { display: flex; align-items: center; gap: var(--space-sm); min-width: 0; padding: var(--space-sm); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--surface-2) 50%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 76%, transparent); }
.task-input-file--dragging { opacity: .55; }
.task-input-file__handle, .task-input-file__glyph { flex: 0 0 auto; color: var(--primary-strong); }
.task-input-file__main { display: grid; flex: 1; gap: 3px; min-width: 0; }
.task-input-file__main strong, .task-input-file__main span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-input-file__main strong { font-size: 12px; font-weight: 600; }
.task-input-file__main span { color: var(--on-surface-muted); font-size: 11px; }
</style>
