<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DropdownOption } from 'naive-ui'
import { ChevronDownOutline, PlayOutline } from '@vicons/ionicons5'

const props = defineProps<{
  taskPanelState: string
  completedActionOptions: DropdownOption[]
  handleCompletedAction: (key: string) => void
  goToResults: () => void
  beginNextSeparation: () => void
  retryCurrentTask: () => void
}>()
const { t } = useI18n()
</script>

<template>
  <div class="stage-actions">
    <template v-if="props.taskPanelState === 'done'">
      <n-dropdown :options="props.completedActionOptions" placement="top-end" @select="(key: string | number) => props.handleCompletedAction(String(key))">
        <n-button secondary size="large">
          {{ t('separate.moreActions') }}
          <n-icon :component="ChevronDownOutline" />
        </n-button>
      </n-dropdown>
      <n-button secondary size="large" @click="props.goToResults">
        {{ t('separate.viewResults') }}
      </n-button>
      <n-button type="primary" size="large" class="stage-actions__primary" @click="props.beginNextSeparation">
        {{ t('separate.newSeparation') }}
      </n-button>
    </template>
    <template v-else-if="props.taskPanelState === 'failed'">
      <n-button secondary size="large" @click="props.beginNextSeparation">
        {{ t('separate.newSeparation') }}
      </n-button>
      <n-button type="primary" size="large" class="stage-actions__primary" @click="props.retryCurrentTask">
        <template #icon><n-icon :component="PlayOutline" /></template>
        {{ t('common.retry') }}
      </n-button>
    </template>
    <template v-else>
      <n-button secondary size="large" @click="props.retryCurrentTask">
        <template #icon><n-icon :component="PlayOutline" /></template>
        {{ t('separate.runAgain') }}
      </n-button>
      <n-button type="primary" size="large" class="stage-actions__primary" @click="props.beginNextSeparation">
        {{ t('separate.newSeparation') }}
      </n-button>
    </template>
  </div>
</template>

<style scoped>
.stage-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 4px;
}
.stage-actions__primary { min-width: 150px; font-weight: 600; }
</style>
