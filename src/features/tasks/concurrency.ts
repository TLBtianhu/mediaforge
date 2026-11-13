import executionQuota from '../../../manifest/execution-quota.json' with { type: 'json' }

export const DEFAULT_CONCURRENT_SEPARATIONS = executionQuota.defaultLimit
export const MAX_CONCURRENT_SEPARATIONS = executionQuota.maxSlots

export function normalizeConcurrentSeparations(value: unknown): number {
  const count = Number(value)
  return Number.isFinite(count)
    ? Math.min(MAX_CONCURRENT_SEPARATIONS, Math.max(1, Math.trunc(count)))
    : DEFAULT_CONCURRENT_SEPARATIONS
}
