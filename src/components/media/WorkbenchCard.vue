<script setup lang="ts">
import type { Component } from 'vue'

defineOptions({ name: 'WorkbenchCard' })

withDefaults(defineProps<{
  index?: string
  icon?: Component
  title: string
  subtitle?: string
}>(), {
  index: '',
  subtitle: '',
})
</script>

<template>
  <section class="mf-workbench-card">
    <header class="mf-workbench-card__head">
      <span v-if="icon" class="mf-workbench-card__index mf-workbench-card__index--icon">
        <n-icon :component="icon" />
      </span>
      <span v-else class="mf-workbench-card__index">{{ index }}</span>
      <div class="mf-workbench-card__label">
        <strong>{{ title }}</strong>
        <small v-if="subtitle">{{ subtitle }}</small>
      </div>
      <div v-if="$slots.actions" class="mf-workbench-card__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="mf-workbench-card__body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.mf-workbench-card {
  min-width: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 14px;
  padding: 17px 18px;
  border-radius: 18px;
  background: color-mix(in srgb, var(--surface-1) 78%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.03),
    var(--shadow-panel);
}
.mf-workbench-card__head { display: flex; align-items: center; gap: 11px; min-width: 0; }
.mf-workbench-card__index {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 9px;
  color: var(--primary-strong);
  background: var(--primary-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 40%, transparent);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.mf-workbench-card__index--icon svg { width: 15px; height: 15px; }
.mf-workbench-card__label { display: grid; flex: 1; gap: 1px; min-width: 0; }
.mf-workbench-card__label strong { overflow: hidden; color: var(--on-surface-heading); font-size: 13.5px; font-weight: 600; letter-spacing: -.01em; text-overflow: ellipsis; white-space: nowrap; }
.mf-workbench-card__label small { overflow: hidden; color: var(--on-surface-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.mf-workbench-card__actions { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.mf-workbench-card__body { min-width: 0; min-height: 0; }
</style>
