<script setup lang="ts">
defineOptions({ name: 'WorkbenchSteps' })

defineProps<{
  steps: Array<{ title: string; text: string }>
}>()
</script>

<template>
  <div class="mf-step-strip mf-step-strip--top">
    <div v-for="(step, index) in steps" :key="index" class="mf-step">
      <span class="mf-step__num">{{ String(index + 1).padStart(2, '0') }}</span>
      <div>
        <div class="mf-step__title">{{ step.title }}</div>
        <div class="mf-step__text">{{ step.text }}</div>
      </div>
    </div>
  </div>
</template>
