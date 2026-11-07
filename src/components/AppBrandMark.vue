<script setup lang="ts">
import { computed } from 'vue'
import appLogoDark from '@/assets/app-logo-dark.png'
import appLogoLight from '@/assets/app-logo-light.png'
import { themeIsDark } from '@/utils/theme'

const props = withDefaults(defineProps<{
  size?: number
  shadow?: boolean
  variant?: 'default' | 'hero' | 'compact'
}>(), {
  size: 32,
  shadow: false,
  variant: 'default',
})

const styleVars = computed(() => ({
  '--mark-size': `${props.size}px`,
}))
const appLogo = computed(() => themeIsDark.value ? appLogoDark : appLogoLight)
</script>

<template>
  <div
    class="app-brand-mark"
    :class="{ 'app-brand-mark--shadow': shadow }"
    :style="styleVars"
    :data-variant="variant"
    aria-hidden="true"
  >
    <img class="app-brand-mark__image" :src="appLogo" alt="" />
  </div>
</template>

<style scoped>
.app-brand-mark {
  width: var(--mark-size);
  height: var(--mark-size);
  border-radius: calc(var(--mark-size) * 0.28);
  overflow: visible;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  position: relative;
  isolation: isolate;
}

.app-brand-mark--shadow {
  box-shadow:
    0 10px 28px rgba(17, 24, 39, 0.10),
    0 2px 8px rgba(17, 24, 39, 0.06);
}

.app-brand-mark__image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
}

.app-brand-mark[data-variant='compact'] {
  border-radius: calc(var(--mark-size) * 0.26);
}

:global(.light-theme) .app-brand-mark[data-variant='hero'].app-brand-mark--shadow {
  box-shadow:
    0 8px 18px rgba(31, 41, 55, 0.08),
    0 2px 6px rgba(31, 41, 55, 0.05);
}

:global(.dark-theme) .app-brand-mark[data-variant='hero'].app-brand-mark--shadow {
  box-shadow:
    0 10px 22px rgba(0, 0, 0, 0.22),
    0 2px 8px rgba(0, 0, 0, 0.12);
}

</style>
