<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  SettingsOutline,
  SpeedometerOutline,
  TerminalOutline,
} from '@vicons/ionicons5'

type SelectOption = { label: string; value: string }
type ProxyTestStatus = 'idle' | 'testing' | 'success' | 'error'

const defaultDevice = defineModel<string>('defaultDevice', { required: true })
const downloadSource = defineModel<string>('downloadSource', { required: true })
const downloadMethod = defineModel<string>('downloadMethod', { required: true })
const proxyMode = defineModel<string>('proxyMode', { required: true })
const proxyUrl = defineModel<string>('proxyUrl', { required: true })
const proxyBypass = defineModel<string>('proxyBypass', { required: true })
const maxConcurrentSeparationsInput = defineModel<number | null>('maxConcurrentSeparationsInput', { required: true })
const developerMode = defineModel<boolean>('developerMode', { required: true })

defineProps<{
  deviceOptions: SelectOption[]
  downloadMethodOptions: SelectOption[]
  proxyModeOptions: SelectOption[]
  maxConcurrentSeparations: number
  proxyTestLoading: boolean
  proxyTestStatus: ProxyTestStatus
  proxyTestMessage: string
  proxyTestSuggestion: string
  testProxyConnection: () => void
  resetProxyTest: () => void
}>()

const { t } = useI18n()
</script>

<template>
  <section class="settings-section-panel">
    <n-card class="settings-card" :bordered="true" size="small">
      <template #header>
        <div class="section-title">
          <span class="section-title__icon">
            <n-icon :component="SettingsOutline" size="18" />
          </span>
          <span>{{ t('settings.defaults') }}</span>
        </div>
      </template>

      <div class="settings-merged-layout">
        <section class="settings-group">
          <div class="setting-field" data-guide="settings-device">
            <label class="text-muted text-sm">{{ t('settings.defaultDevice') }}</label>
            <n-select v-model:value="defaultDevice" :options="deviceOptions" />
          </div>
        </section>

        <section class="settings-group" data-guide="settings-downloads">
          <div class="setting-field">
            <label class="text-muted text-sm">{{ t('settings.downloadSource') }}</label>
            <n-select
              v-model:value="downloadSource"
              :options="[
                { label: 'ModelScope', value: 'modelscope' },
                { label: 'Hugging Face', value: 'huggingface' },
                { label: 'HF Mirror', value: 'hf-mirror' },
              ]"
            />
          </div>
          <div class="setting-field">
            <label class="text-muted text-sm">{{ t('settings.downloadMethod') }}</label>
            <n-select v-model:value="downloadMethod" :options="downloadMethodOptions" />
            <p class="text-muted text-sm setting-field__hint">{{ t('settings.downloadMethodHint') }}</p>
          </div>
        </section>

        <section class="settings-group">
          <div class="setting-field">
            <label class="text-muted text-sm">{{ t('settings.proxyMode') }}</label>
            <n-select
              v-model:value="proxyMode"
              :options="proxyModeOptions"
              @update:value="resetProxyTest"
            />
            <p class="text-muted text-sm setting-field__hint">{{ t('settings.proxyModeHint') }}</p>
          </div>
          <div v-if="proxyMode === 'custom'" class="proxy-custom-grid">
            <div class="setting-field">
              <label class="text-muted text-sm">{{ t('settings.proxyUrl') }}</label>
              <n-input
                v-model:value="proxyUrl"
                :placeholder="t('settings.proxyUrlPlaceholder')"
                clearable
                size="small"
                @update:value="resetProxyTest"
              />
            </div>
            <div class="setting-field">
              <label class="text-muted text-sm">{{ t('settings.proxyBypass') }}</label>
              <n-input
                v-model:value="proxyBypass"
                :placeholder="t('settings.proxyBypassPlaceholder')"
                size="small"
              />
            </div>
          </div>
          <div class="setting-field">
            <label class="text-muted text-sm">{{ t('settings.proxyTest') }}</label>
            <div class="proxy-test-row">
              <n-button
                size="small"
                secondary
                :loading="proxyTestLoading"
                :disabled="proxyMode === 'custom' && !proxyUrl.trim()"
                @click="testProxyConnection"
              >
                {{ t('settings.proxyTest') }}
              </n-button>
              <span v-if="proxyTestStatus === 'success'" class="proxy-test-info">
                <n-icon :component="CheckmarkCircleOutline" size="14" color="var(--success)" />
                <span class="proxy-test-info__text">{{ proxyTestMessage }}</span>
              </span>
              <n-tag
                v-else-if="proxyTestStatus === 'error'"
                size="small"
                type="error"
                round
                :bordered="false"
              >{{ t('settings.proxyTestFailed') }}</n-tag>
              <n-tag
                v-else-if="proxyTestStatus === 'testing'"
                size="small"
                :bordered="false"
              >{{ t('settings.proxyTesting') }}</n-tag>
            </div>
            <div v-if="proxyTestStatus === 'error' && proxyTestMessage" class="proxy-test-error">
              <div class="proxy-test-error__head">
                <n-icon :component="AlertCircleOutline" size="14" color="var(--danger)" />
                <span>{{ proxyTestMessage }}</span>
              </div>
              <p v-if="proxyTestSuggestion" class="proxy-test-error__tip">{{ proxyTestSuggestion }}</p>
            </div>
          </div>
        </section>

        <section class="settings-group settings-group--soft">
          <div class="settings-group__head">
            <span class="settings-group__icon">
              <n-icon :component="SpeedometerOutline" size="16" />
            </span>
            <span>{{ t('settings.execution') }}</span>
          </div>
          <div class="setting-field">
            <label class="text-muted text-sm">{{ t('settings.maxConcurrentSeparations') }}</label>
            <n-input-number
              v-model:value="maxConcurrentSeparationsInput"
              :min="1"
              :max="maxConcurrentSeparations"
              :precision="0"
              :step="1"
              clearable
              style="width: 100%;"
            />
            <p class="text-muted text-sm setting-field__hint">
              {{ t('settings.maxConcurrentSeparationsHint') }}
            </p>
          </div>
        </section>

        <section class="settings-group settings-group--soft">
          <div class="settings-group__head">
            <span class="settings-group__icon">
              <n-icon :component="TerminalOutline" size="16" />
            </span>
            <span>{{ t('settings.developerMode') }}</span>
          </div>
          <div class="setting-field setting-field--switch">
            <div class="setting-switch-row">
              <div class="setting-switch-row__copy">
                <label class="text-muted text-sm">{{ t('settings.developerModeTitle') }}</label>
                <p class="text-muted text-sm setting-field__hint">
                  {{ t('settings.developerModeHint') }}
                </p>
              </div>
              <n-switch v-model:value="developerMode" />
            </div>
          </div>
        </section>
      </div>
    </n-card>
  </section>
</template>

<style scoped src="../../views/settings/settings-shared.css"></style>
<style scoped>

.setting-field {
  display: grid;
  gap: 10px;
}

.setting-field__hint {
  margin: 0;
  line-height: 1.6;
}

.proxy-test-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.proxy-custom-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 640px) {
  .proxy-custom-grid {
    grid-template-columns: 1fr;
  }
}

.proxy-test-info {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
}

.proxy-test-info__text {
  font-size: 12px;
  color: var(--on-surface);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.proxy-test-error {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--danger) 28%, transparent);
}

.proxy-test-error__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--danger);
  word-break: break-all;
}

.proxy-test-error__tip {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--on-surface-muted);
}

.setting-field--switch {
  gap: 0;
}

.setting-switch-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
}

.setting-switch-row__copy {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.settings-merged-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(320px, 0.92fr);
  gap: 14px;
  align-items: start;
}

.settings-group {
  display: grid;
  gap: 10px;
}

.settings-group:nth-child(1) {
  grid-column: 1;
  grid-row: 1;
}

.settings-group:nth-child(2) {
  grid-column: 1;
  grid-row: 2;
}

.settings-group:nth-child(3) {
  grid-column: 2;
  grid-row: 1 / span 2;
}

.settings-group--soft {
  padding: 14px 0 0;
  border-top: 1px solid color-mix(in srgb, var(--outline) 42%, transparent);
  border-radius: 0;
  background: transparent;
  align-self: stretch;
  align-content: start;
}

.settings-group__head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: color-mix(in srgb, var(--on-surface-muted) 88%, var(--on-surface) 12%);
  font-size: 12px;
  font-weight: 600;
}

.settings-group__icon {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: color-mix(in srgb, var(--primary-ink) 78%, var(--on-surface-muted));
  background: color-mix(in srgb, var(--primary-soft) 36%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 36%, transparent);
}
</style>
