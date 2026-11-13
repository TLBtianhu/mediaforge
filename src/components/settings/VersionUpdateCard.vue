<script setup lang="ts">
// 版本与更新：只做"检测 + 接受"，替换永远整包进行。
// 检测读的是签名过的 versions.json；验签失败/没有发布时只提示，不自动装。
import { runtimeAssetInstall, versionsAcceptRuntime, versionsCheck } from '@/api/native'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAppStore, type RuntimeBackend } from '@/stores/app'
import { runtimeAssetOfferBackend } from '@/utils/runtime'

const { t } = useI18n()
const app = useAppStore()

type Axes = { app?: string; runtime?: string }
type CheckResult = {
  local?: Axes
  remote?: Axes
  available?: { app?: boolean; runtime?: boolean }
  source?: string
}

const checking = ref(false)
const switching = ref(false)
const checked = ref(false)
const error = ref('')
const result = ref<CheckResult | null>(null)
const checkedBackend = ref<RuntimeBackend | null>(null)
const runtimeBackend = computed(() => runtimeAssetOfferBackend(app.runtimeInfo))

const appNewer = computed(() => Boolean(result.value?.available?.app))
const runtimeDifferent = computed(() => checkedBackend.value !== null
  && checkedBackend.value === runtimeBackend.value && Boolean(result.value?.available?.runtime))
const localApp = computed(() => result.value?.local?.app || '')
const remoteApp = computed(() => result.value?.remote?.app || '')
const localRuntime = computed(() => result.value?.local?.runtime || '')
const remoteRuntime = computed(() => result.value?.remote?.runtime || '')

async function check() {
  checking.value = true
  error.value = ''
  try {
    const backend = runtimeBackend.value
    result.value = await versionsCheck<CheckResult>(backend ?? undefined)
    checkedBackend.value = backend
    checked.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    result.value = null
  } finally {
    checking.value = false
  }
}

async function switchRuntime() {
  const backend = checkedBackend.value
  if (!backend || !runtimeDifferent.value || switching.value) return
  switching.value = true
  error.value = ''
  try {
    await versionsAcceptRuntime(backend)
    await runtimeAssetInstall(backend)
    // 进度由全局 RuntimeAssetBanner 显示；这里只确认"已开始"
    result.value = null
    checked.value = false
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    switching.value = false
  }
}
</script>

<template>
  <n-card class="version-update-card" :bordered="true" size="small">
    <template #header>
      <div class="version-update-card__header">
        <span>{{ t('updates.title') }}</span>
        <n-button size="small" secondary :loading="checking" @click="check">
          {{ checking ? t('updates.checking') : t('updates.check') }}
        </n-button>
      </div>
    </template>

    <p v-if="!checked && !error" class="version-update-card__hint">{{ t('updates.hint') }}</p>

    <template v-if="checked && result">
      <div class="version-update-card__row">
        <span>{{ t('updates.app') }}</span>
        <code>{{ localApp }}</code>
        <n-tag v-if="appNewer" type="warning" size="small">{{ t('updates.newApp', { version: remoteApp }) }}</n-tag>
        <n-tag v-else type="success" size="small">{{ t('updates.upToDate') }}</n-tag>
      </div>
      <div v-if="localRuntime && checkedBackend === runtimeBackend" class="version-update-card__row">
        <span>{{ t('updates.runtime') }}</span>
        <code>{{ localRuntime }}</code>
        <template v-if="runtimeDifferent">
          <n-tag type="info" size="small">{{ t('updates.runtimeLatest', { asset: remoteRuntime }) }}</n-tag>
          <n-button size="tiny" type="primary" :loading="switching" @click="switchRuntime">
            {{ t('updates.switchRuntime') }}
          </n-button>
        </template>
        <n-tag v-else type="success" size="small">{{ t('updates.upToDate') }}</n-tag>
      </div>
    </template>

    <p v-if="error" class="version-update-card__error">{{ t('updates.failed', { error }) }}</p>
  </n-card>
</template>

<style scoped>
.version-update-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.version-update-card__hint {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
}
.version-update-card__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 8px 0;
  font-size: 13px;
}
.version-update-card__row code {
  font-size: 12px;
  opacity: 0.85;
}
.version-update-card__error {
  margin: 6px 0 0;
  color: var(--danger);
  font-size: 12px;
  word-break: break-word;
}
</style>
