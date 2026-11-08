import executionQuota from '../../manifest/execution-quota.json' with { type: 'json' }
import { PARAMETER_GUIDES } from '../features/onboarding/parameters.ts'
import { BEGINNER_GUIDE_STEPS } from '../features/onboarding/guide.ts'
import { SCALE_FACTOR_MAX, SCALE_FACTOR_MIN } from '../utils/appZoom.ts'

export const CURRENT_SETTINGS_VERSION = 1

const FIELD_TYPES: Record<string, 'string' | 'boolean' | 'number' | 'object'> = {
  version: 'number',
  themeMode: 'string', themeAccent: 'string', scaleFactor: 'number', locale: 'string',
  animationsEnabled: 'boolean', developerMode: 'boolean', modelDir: 'string', outputDir: 'string',
  defaultDevice: 'string', defaultFormat: 'string', downloadSource: 'string', downloadMethod: 'string',
  maxConcurrentSeparations: 'number', clearInputAfterSubmit: 'boolean', wavBitDepth: 'string',
  flacBitDepth: 'string', mp3BitRate: 'string', m4aBitRate: 'string', m4aCodec: 'string',
  startupOnboardingSeen: 'boolean', beginnerGuideInvitationSeen: 'boolean', beginnerGuideProgress: 'object',
  proxyMode: 'string', proxyUrl: 'string', proxyBypass: 'string', updateChannel: 'string',
  updateSource: 'string', updateEndpointOverride: 'string',
}

const ENUMS: Record<string, readonly unknown[]> = {
  themeMode: ['system', 'dark', 'light'],
  themeAccent: ['blue', 'pink', 'sky', 'teal'],
  locale: ['system', 'zh-CN', 'en'],
  downloadMethod: ['aria2c', 'urllib'],
  wavBitDepth: ['PCM_16', 'PCM_24', 'FLOAT'],
  flacBitDepth: ['PCM_16', 'PCM_24'],
  mp3BitRate: ['128k', '192k', '256k', '320k'],
  m4aCodec: ['aac', 'alac'],
  proxyMode: ['system', 'none', 'custom'],
  updateChannel: ['stable', 'prerelease'],
  updateSource: ['auto', 'github', 'modelscope'],
}

function invalid(): never {
  throw new Error('Unsupported app settings schema; original file was preserved')
}

function validateGuideProgress(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid()
  const progress = value as Record<string, unknown>
  if (Object.keys(progress).some(key => !['status', 'step', 'stepId', 'parameter'].includes(key))) invalid()
  if (!['unseen', 'pending', 'active', 'skipped', 'completed'].includes(String(progress.status))) invalid()
  if (typeof progress.step !== 'number' || !Number.isSafeInteger(progress.step) || progress.step < 0) invalid()
  if ('stepId' in progress && !BEGINNER_GUIDE_STEPS.some(step => step.id === progress.stepId)) invalid()
  if ('parameter' in progress && !PARAMETER_GUIDES.some(parameter => parameter.id === progress.parameter)) invalid()
}

export function validateStoredSettings(value: unknown): void {
  if (value === null) return
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid()
  const settings = value as Record<string, unknown>
  if (settings.version !== CURRENT_SETTINGS_VERSION) invalid()
  for (const [key, item] of Object.entries(settings)) {
    const expected = FIELD_TYPES[key]
    if (!expected || typeof item !== expected) invalid()
    if (expected === 'number' && !Number.isFinite(Number(item))) invalid()
    if (ENUMS[key] && !ENUMS[key].includes(item)) invalid()
  }
  if ('scaleFactor' in settings && (Number(settings.scaleFactor) < SCALE_FACTOR_MIN || Number(settings.scaleFactor) > SCALE_FACTOR_MAX)) invalid()
  if ('maxConcurrentSeparations' in settings && (typeof settings.maxConcurrentSeparations !== 'number'
    || !Number.isSafeInteger(settings.maxConcurrentSeparations) || settings.maxConcurrentSeparations < 1
    || settings.maxConcurrentSeparations > executionQuota.maxSlots)) invalid()
  if ('beginnerGuideProgress' in settings) validateGuideProgress(settings.beginnerGuideProgress)
}
