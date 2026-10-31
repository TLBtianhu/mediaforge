// SeparateView 的输出命名：token 列表、文件名预览与插入 token。
import { computed, nextTick, ref, type Ref } from 'vue'
import {
  formatDateToken, formatLegacyTimeToken, formatTimeToken, normalizeFilenameFragment, padNumber,
  safeFilenamePart, stripFileExtension,
} from './format'

type Options = {
  t: (key: string, params?: Record<string, unknown>) => string
  inputFiles: Ref<string[]>
  ensembleEnabled: Ref<boolean>
  selectedModelName: Ref<string>
  orderedOutputStems: Ref<string[]>
  outputNamingTemplate: Ref<string>
  outputNamingConfig: Ref<{ enabled: boolean; template: string; stemOrder: string[] }>
  effectiveFormat: Ref<string>
}

export function useOutputNaming({
  t, inputFiles, ensembleEnabled, selectedModelName, orderedOutputStems,
  outputNamingTemplate, outputNamingConfig, effectiveFormat,
}: Options) {
const namingTemplateInputRef = ref<any>(null)

const namingTokens = computed(() => [
  { value: '%index%', label: t('separate.namingTokenIndex') },
  { value: '%input_number%', label: t('separate.namingTokenInputNumber') },
  { value: '%filename%', label: t('separate.namingTokenFilename') },
  { value: '%stem%', label: t('separate.namingTokenStem') },
  { value: '%model%', label: t('separate.namingTokenModel') },
  { value: '%yyyyMMdd%', label: t('separate.namingTokenDate') },
  { value: '%hhmmss%', label: t('separate.namingTokenTime') },
  { value: '%ddmmss%', label: t('separate.namingTokenLegacyTime') },
])

const outputNamingPreviewFiles = computed(() => {
  const stems = orderedOutputStems.value.length ? orderedOutputStems.value : ['vocals']
  return stems.slice(0, 8).map((stem, index) => formatOutputNamingPreviewFile(stem, index))
})

const outputNamingPreviewParts = computed(() => {
  const stems = orderedOutputStems.value.length ? orderedOutputStems.value : ['vocals']
  return stems.slice(0, 8).map((stem, index) => ({
    key: `${stem}-${index}`,
    parts: formatOutputNamingPreviewParts(stem, index),
  }))
})

function formatOutputNamingPreviewFile(stem: string, stemIndex: number) {
  return formatOutputNamingPreviewParts(stem, stemIndex).map(part => part.text).join('')
}

function formatOutputNamingPreviewParts(stem: string, stemIndex: number) {
  const inputPath = inputFiles.value[0] || t('separate.resultFolderPreview')
  const modelName = ensembleEnabled.value ? 'Ensemble' : selectedModelName.value || t('separate.model')
  const now = new Date()
  const values: Record<string, string> = {
    '%index%': padNumber(stemIndex + 1),
    '%input_number%': '01',
    '%filename%': stripFileExtension(inputPath),
    '%stem%': stem,
    '%model%': modelName,
    '%yyyyMMdd%': formatDateToken(now),
    '%hhmmss%': formatTimeToken(now),
    '%ddmmss%': formatLegacyTimeToken(now),
  }
  const template = outputNamingConfig.value.enabled ? outputNamingConfig.value.template : '%filename%_%stem%'
  const parts: Array<{ text: string; token: string }> = []
  const tokenPattern = /%(?:index|input_number|filename|stem|model|yyyyMMdd|hhmmss|ddmmss)%/g
  let cursor = 0
  for (const match of template.matchAll(tokenPattern)) {
    const token = match[0]
    const index = match.index ?? 0
    if (index > cursor) parts.push({ text: template.slice(cursor, index), token: 'literal' })
    parts.push({ text: values[token] || token, token })
    cursor = index + token.length
  }
  if (cursor < template.length) parts.push({ text: template.slice(cursor), token: 'literal' })
  const namedParts = parts.length
    ? parts.map(part => ({ ...part, text: normalizeFilenameFragment(part.text) }))
    : [{ text: 'output', token: 'literal' }]
  const firstPart = namedParts[0]
  const lastPart = namedParts[namedParts.length - 1]
  firstPart.text = firstPart.text.replace(/^[ ._]+/g, '')
  lastPart.text = lastPart.text.replace(/[ ._]+$/g, '')
  const fullName = namedParts.map(part => part.text).join('')
  if (!fullName) namedParts.splice(0, namedParts.length, { text: 'output', token: 'literal' })
  else {
    const safeName = safeFilenamePart(fullName)
    if (safeName.startsWith(fullName)) lastPart.text += safeName.slice(fullName.length)
  }
  return [
    ...namedParts.filter(part => part.text),
    { text: `.${effectiveFormat.value || 'wav'}`, token: 'extension' },
  ]
}

async function insertNamingToken(token: string) {
  const input = namingTemplateInputRef.value?.inputElRef as HTMLInputElement | undefined
  const current = outputNamingTemplate.value
  const withSeparators = (start: number, end: number) => {
    const before = current.slice(0, start)
    const after = current.slice(end)
    const prefix = before && !before.endsWith('_') ? '_' : ''
    const suffix = after && !after.startsWith('_') ? '_' : ''
    return {
      text: `${before}${prefix}${token}${suffix}${after}`,
      cursor: before.length + prefix.length + token.length,
    }
  }
  if (!input) {
    const next = withSeparators(current.length, current.length)
    outputNamingTemplate.value = next.text
    return
  }
  const start = input.selectionStart ?? current.length
  const end = input.selectionEnd ?? start
  const next = withSeparators(start, end)
  outputNamingTemplate.value = next.text
  await nextTick()
  input.focus()
  input.setSelectionRange(next.cursor, next.cursor)
}

  return {
    namingTemplateInputRef, namingTokens, outputNamingPreviewFiles, outputNamingPreviewParts, insertNamingToken,
  }
}
