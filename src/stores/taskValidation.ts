import i18n from '@/i18n'
import { getWorkflowDefinitionIssue } from '@/workflows/runtimeDefinition'
import { getSeparateWorkflowInputIssue } from '@/utils/workflowInputs'
import type { SeparationRunConfig } from './task.ts'

export function workflowDefinitionValidationError(definition: Record<string, unknown>): string {
  const issue = getWorkflowDefinitionIssue(definition)
  if (issue === 'steps-required') return i18n.global.t('workflows.stepsRequired')
  if (issue === 'no-save-outputs') return i18n.global.t('workflows.workflowNoSaveOutputs')
  if (issue === 'invalid-definition' || issue === 'invalid-format') return i18n.global.t('workflows.workflowFormatInvalid')
  return ''
}

export function validateSeparateWorkflowInput(definition: Record<string, unknown>): void {
  const issue = getSeparateWorkflowInputIssue(definition)
  if (issue) {
    throw new Error(i18n.global.t(
      issue.code === 'multiple-inputs' ? 'separate.workflowMultipleInputs' : 'separate.workflowNoInput',
      { count: issue.count },
    ))
  }
}

export function validateWorkflowRunConfig(config: SeparationRunConfig): void {
  const definition = config.workflowDefinition || {}
  const validationError = workflowDefinitionValidationError(definition)
  if (validationError) throw new Error(validationError)
  if (!config.workbench) validateSeparateWorkflowInput(definition)
}
