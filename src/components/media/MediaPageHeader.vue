<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{
  title: string
  subtitle: string
  icon: Component
  eyebrow?: string
  tone?: 'blue' | 'violet' | 'amber' | 'emerald'
}>(), {
  eyebrow: 'MediaForge',
  tone: 'blue',
})
</script>

<template>
  <header class="mf-page-head" :data-tone="tone">
    <span class="mf-page-head__icon">
      <n-icon :component="icon" :size="25" />
    </span>
    <div class="mf-page-head__text">
      <span class="mf-page-head__eyebrow">{{ eyebrow }}</span>
      <h1>{{ title }}</h1>
      <p>{{ subtitle }}</p>
    </div>
    <div class="mf-page-head__actions">
      <slot />
    </div>
  </header>
</template>
