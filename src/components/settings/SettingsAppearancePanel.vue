<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ColorPaletteOutline } from '@vicons/ionicons5'

type AccentOption = { value: string; label: string; preview: [string, string] }
type LanguageOption = { label: string; value: string }
type ElementRefSetter = (element: HTMLElement | null) => void

defineProps<{
  themeMode: string
  themeAccent: string
  themeAccentOptions: AccentOption[]
  appearanceThemeAccentLabel: string
  locale: string
  systemLocale: string
  currentResolvedLanguageLabel: string
  languageOptions: LanguageOption[]
  scaleSliderIndex: number
  scaleSliderMarks: Record<number, string>
  scaleFactorPercent: string
  isDefaultScaleFactor: boolean
  animationsEnabled: boolean
  setLanguageSelectWrap: ElementRefSetter
}>()

const emit = defineEmits<{
  selectThemeMode: [mode: string, event: MouseEvent]
  selectThemeAccent: [accent: string, event: MouseEvent]
  selectLocale: [locale: string]
  resetScaleFactor: []
  updateScaleSliderIndex: [index: number]
  updateAnimationsEnabled: [enabled: boolean]
}>()

const { t } = useI18n()
</script>

<template>
  <section class="settings-section-panel">
    <n-card class="settings-card settings-card--compact settings-card--appearance" :bordered="true" size="small">
      <template #header>
        <div class="section-title">
          <span class="section-title__icon">
            <n-icon :component="ColorPaletteOutline" size="18" />
          </span>
          <span>{{ t('settings.appearance') }}</span>
        </div>
      </template>

      <div class="appearance-list">
        <p class="appearance-hint">{{ t('settings.appearanceHint') }}</p>

        <div class="setting-row">
          <div class="setting-row__label">
            <label class="setting-row__title">{{ t('settings.theme') }}</label>
          </div>
          <div class="setting-row__control">
            <div class="theme-switcher">
              <button
                type="button"
                :class="{ active: themeMode === 'system' }"
                @click="emit('selectThemeMode', 'system', $event)"
              >
                {{ t('settings.themeSystem') }}
              </button>
              <button
                type="button"
                :class="{ active: themeMode === 'dark' }"
                @click="emit('selectThemeMode', 'dark', $event)"
              >
                {{ t('settings.themeDark') }}
              </button>
              <button
                type="button"
                :class="{ active: themeMode === 'light' }"
                @click="emit('selectThemeMode', 'light', $event)"
              >
                {{ t('settings.themeLight') }}
              </button>
            </div>
          </div>
        </div>

        <div class="setting-row">
          <div class="setting-row__label">
            <label class="setting-row__title">{{ t('settings.themeColor') }}</label>
          </div>
          <div class="setting-row__control">
            <div class="accent-dots">
              <button
                v-for="accent in themeAccentOptions"
                :key="accent.value"
                type="button"
                class="accent-dot"
                :class="{ active: themeAccent === accent.value }"
                :title="accent.label"
                :aria-label="accent.label"
                @click="emit('selectThemeAccent', accent.value, $event)"
              >
                <span
                  class="accent-dot__fill"
                  :style="{ background: `linear-gradient(135deg, ${accent.preview[0]} 0 50%, ${accent.preview[1]} 50% 100%)` }"
                />
              </button>
              <span class="accent-current">{{ appearanceThemeAccentLabel }}</span>
            </div>
          </div>
        </div>

        <div class="setting-row">
          <div class="setting-row__label">
            <label class="setting-row__title">{{ t('settings.language') }}</label>
            <p v-if="locale === systemLocale" class="setting-row__hint">
              {{ t('settings.languageFollowSystemHint', { locale: currentResolvedLanguageLabel }) }}
            </p>
          </div>
          <div class="setting-row__control">
            <div :ref="(element) => setLanguageSelectWrap(element as HTMLElement | null)" class="language-select-wrap">
              <n-select
                :value="locale"
                :options="languageOptions"
                :consistent-menu-width="false"
                @update:value="emit('selectLocale', $event)"
              />
            </div>
          </div>
        </div>

        <div class="setting-row">
          <div class="setting-row__label">
            <label class="setting-row__title">{{ t('settings.scaleFactor') }}</label>
            <button
              type="button"
              class="scale-reset"
              :disabled="isDefaultScaleFactor"
              @click="emit('resetScaleFactor')"
            >
              {{ t('settings.restoreDefaultScale') }}
            </button>
          </div>
          <div class="setting-row__control">
            <div class="scale-control">
              <n-slider
                :value="scaleSliderIndex"
                :min="0"
                :max="5"
                :step="1"
                :marks="scaleSliderMarks"
                :tooltip="false"
                @update:value="emit('updateScaleSliderIndex', $event)"
              />
              <span class="scale-value">{{ scaleFactorPercent }}</span>
            </div>
          </div>
        </div>

        <div class="setting-row">
          <div class="setting-row__label">
            <label class="setting-row__title">{{ t('settings.animations') }}</label>
            <p class="setting-row__hint">{{ t('settings.animationsHint') }}</p>
          </div>
          <div class="setting-row__control">
            <n-switch
              :value="animationsEnabled"
              @update:value="emit('updateAnimationsEnabled', $event)"
            />
          </div>
        </div>
      </div>
    </n-card>
  </section>
</template>

<style scoped>
.settings-card {
  border-color: color-mix(in srgb, var(--outline) 58%, transparent) !important;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.025), transparent 42%),
    color-mix(in srgb, var(--surface-1) 72%, transparent) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}

.settings-card :deep(.n-card__header) {
  padding: 18px 20px 12px;
  border-bottom: 1px solid color-mix(in srgb, var(--outline) 42%, transparent);
}

.settings-card :deep(.n-card__content) {
  padding: 4px 20px 18px;
}

.settings-card--compact :deep(.n-card__content) {
  padding-bottom: 20px;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}

.section-title__icon {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: color-mix(in srgb, var(--primary-ink) 74%, var(--on-surface-muted));
  background: color-mix(in srgb, var(--primary-soft) 34%, var(--surface-2));
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 36%, transparent);
}

.appearance-list {
  display: grid;
}

.appearance-hint {
  margin: 0 0 4px;
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.7;
}

.setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 13px 0;
  border-bottom: 1px solid color-mix(in srgb, var(--outline) 36%, transparent);
}

.setting-row + .setting-row {
  border-top: 0;
}

.setting-row__label {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.setting-row__title {
  color: var(--on-surface);
  font-size: 13px;
  font-weight: 600;
}

.setting-row__hint {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.5;
}

.setting-row__control {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.accent-dots {
  display: flex;
  align-items: center;
  gap: 8px;
}

.accent-dot {
  width: 24px;
  height: 24px;
  padding: 3px;
  border: 1px solid color-mix(in srgb, var(--outline) 52%, transparent);
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease;
}

.accent-dot__fill {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: 50%;
}

.accent-dot:hover {
  transform: scale(1.08);
}

.accent-dot.active {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-soft) 70%, transparent);
}

.accent-current {
  margin-left: 4px;
  color: var(--on-surface-muted);
  font-size: 12px;
  white-space: nowrap;
}

.language-select-wrap {
  min-width: 180px;
}

.scale-reset {
  justify-self: start;
  padding: 0;
  border: 0;
  color: var(--primary);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.scale-reset:hover:not(:disabled) {
  text-decoration: underline;
}

.scale-reset:disabled {
  color: var(--on-surface-muted);
  cursor: default;
}

.scale-control {
  width: min(320px, 46vw);
  display: flex;
  align-items: center;
  gap: 12px;
}

.scale-control :deep(.n-slider) {
  flex: 1;
}

.scale-control :deep(.n-slider-mark) {
  font-size: 10px;
}

.scale-value {
  min-width: 44px;
  color: var(--on-surface);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.theme-switcher {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
  margin: 0;
  padding: 4px;
  border: 1px solid color-mix(in srgb, var(--outline) 56%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface) 34%, transparent);
}

.theme-switcher button {
  min-width: 64px;
  padding: 7px 10px;
  border: 0;
  border-radius: 9px;
  color: var(--on-surface-muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.theme-switcher button:hover {
  color: var(--on-surface);
}

.theme-switcher button.active {
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 52%, var(--surface-2));
}
</style>
