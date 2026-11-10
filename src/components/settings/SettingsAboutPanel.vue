<script setup lang="ts">
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'
import AppBrandMark from '@/components/AppBrandMark.vue'
import VersionUpdateCard from '@/components/settings/VersionUpdateCard.vue'
import {
  ChevronForwardOutline,
  CompassOutline,
  DocumentTextOutline,
  LinkOutline,
  OpenOutline,
  OptionsOutline,
  RefreshOutline,
} from '@vicons/ionicons5'

type VersionItem = { label: string; value: string; meta: string }
type BuildVerification = { tone: string; label: string; description: string }
type AboutLink = { url: string; label: string; icon: Component }

defineProps<{
  versionItems: VersionItem[]
  buildVerification: BuildVerification
  buildFingerprint: string
  guideActive: boolean
  aboutLinks: AboutLink[]
  desktopLicense: string
  coreLicense: string
}>()

const emit = defineEmits<{
  startGuide: [restart: boolean]
  parameterHelp: []
  openLink: [url: string]
}>()

const { t } = useI18n()
</script>

<template>
  <section class="about-panel">
    <article class="about-hero">
      <div class="about-hero__main">
        <div class="about-logo-wrap">
          <AppBrandMark class="about-logo" :size="80" variant="hero" />
        </div>
        <div class="about-hero__copy">
          <span class="about-eyebrow">{{ t('settings.aboutEyebrow') }}</span>
          <h2 class="about-hero__title">MediaForge</h2>
        </div>
      </div>

      <div class="about-stats" :aria-label="t('settings.about')">
        <div v-for="item in versionItems" :key="item.label" class="about-stat">
          <span class="about-stat__label">{{ item.label }}</span>
          <strong class="about-stat__value">{{ item.value }}</strong>
          <small class="about-stat__meta">{{ item.meta }}</small>
        </div>
      </div>
    </article>

    <article class="about-build-card" :class="`about-build-card--${buildVerification.tone}`">
      <div class="about-build-card__head">
        <span>{{ t('settings.officialVerification') }}</span>
        <strong>{{ buildVerification.label }}</strong>
      </div>
      <p>{{ buildVerification.description }}</p>
      <div class="about-build-fingerprint">
        <span>{{ t('settings.buildFingerprint') }}</span>
        <code>{{ buildFingerprint }}</code>
      </div>
    </article>

    <article class="about-info-card about-guide-card" data-guide="guide-help">
      <div class="section-title section-title--plain">
        <span class="section-title__icon"><n-icon :component="CompassOutline" size="18" /></span>
        <span>{{ t('beginnerGuide.label') }}</span>
      </div>
      <div class="about-guide-card__actions">
        <n-button v-if="guideActive" type="primary" @click="emit('startGuide', false)">
          <template #icon><n-icon :component="ChevronForwardOutline" /></template>
          {{ t('beginnerGuide.resume') }}
        </n-button>
        <n-button type="primary" secondary @click="emit('startGuide', true)">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          {{ t('beginnerGuide.restart') }}
        </n-button>
        <n-button secondary @click="emit('parameterHelp')">
          <template #icon><n-icon :component="OptionsOutline" /></template>
          {{ t('parameterHelp.label') }}
        </n-button>
      </div>
    </article>

    <div class="about-detail-grid">
      <article class="about-info-card about-info-card--license">
        <div class="section-title section-title--plain">
          <span class="section-title__icon">
            <n-icon :component="DocumentTextOutline" size="18" />
          </span>
          <span>{{ t('settings.license') }}</span>
        </div>
        <div class="license-stack">
          <div class="license-row">
            <span>{{ t('settings.desktopLicense') }}</span>
            <strong>{{ desktopLicense }}</strong>
          </div>
          <div class="license-row">
            <span>{{ t('settings.coreLicense') }}</span>
            <strong>{{ coreLicense }}</strong>
          </div>
        </div>
      </article>

      <article class="about-info-card about-info-card--links">
        <div class="section-title section-title--plain">
          <span class="section-title__icon">
            <n-icon :component="LinkOutline" size="18" />
          </span>
          <span>{{ t('settings.relatedLinks') }}</span>
        </div>
        <div class="about-link-list">
          <button
            v-for="link in aboutLinks"
            :key="link.url"
            type="button"
            class="about-link-item"
            @click="emit('openLink', link.url)"
          >
            <span class="about-link-item__icon"><n-icon :component="link.icon" size="18" /></span>
            <span class="about-link-item__label">{{ link.label }}</span>
            <n-icon class="about-link-item__open" :component="OpenOutline" size="16" />
          </button>
        </div>
      </article>
      <VersionUpdateCard />
    </div>
  </section>
</template>

<style scoped>
.about-panel {
  display: grid;
  gap: 16px;
}

.about-hero,
.about-info-card {
  border: 1px solid color-mix(in srgb, var(--outline) 48%, transparent);
  background: color-mix(in srgb, var(--surface-1) 82%, transparent);
  box-shadow: inset 0 1px 0 color-mix(in srgb, white 8%, transparent);
}

.about-hero {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 22px;
  border-radius: 20px;
}

.about-hero__main {
  display: flex;
  align-items: center;
  gap: 18px;
  min-width: 0;
}

.about-logo-wrap {
  flex: 0 0 auto;
  width: 80px;
  height: 80px;
  display: grid;
  place-items: center;
}

.about-logo-wrap > .about-logo {
  width: 100%;
  height: 100%;
}

.about-hero__copy {
  min-width: 0;
  flex: 1 1 auto;
}

.about-eyebrow {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  color: color-mix(in srgb, var(--primary-ink) 88%, var(--on-surface));
  background: color-mix(in srgb, var(--primary-soft) 42%, transparent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.about-hero__title {
  margin: 8px 0 4px;
  color: var(--on-surface);
  font-size: clamp(22px, 2.6vw, 28px);
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.15;
  white-space: nowrap;
}

.about-hero__tagline {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  line-height: 1.6;
}

.about-hero__tech {
  margin: 8px 0 0;
  color: color-mix(in srgb, var(--on-surface-muted) 92%, var(--on-surface));
  font-size: 12px;
  line-height: 1.65;
}

.about-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
  min-width: 0;
}

.about-stat {
  display: grid;
  gap: 1px;
  min-width: 0;
  padding: 10px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-2) 55%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 32%, transparent);
}

.about-stat__label {
  overflow: hidden;
  color: var(--on-surface-muted);
  font-size: clamp(10px, 1.1vw, 11px);
  font-weight: 600;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.about-stat__value {
  overflow: hidden;
  color: var(--on-surface);
  font-size: clamp(14px, 1.6vw, 16px);
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.about-stat__meta {
  overflow: hidden;
  color: color-mix(in srgb, var(--on-surface-muted) 88%, var(--on-surface));
  font-size: clamp(9px, 1vw, 10px);
  font-weight: 600;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.about-build-card {
  display: grid;
  gap: 10px;
  padding: 16px 18px;
  border: 1px solid color-mix(in srgb, var(--outline) 36%, transparent);
  border-radius: 16px;
  background: color-mix(in srgb, var(--surface-2) 38%, transparent);
}

.about-build-card--official {
  border-color: color-mix(in srgb, var(--success) 34%, var(--outline));
  background: color-mix(in srgb, var(--success) 8%, var(--surface-2));
}

.about-build-card--development {
  border-color: color-mix(in srgb, var(--warning) 38%, var(--outline));
  background: color-mix(in srgb, var(--warning) 8%, var(--surface-2));
}

.about-build-card__head,
.about-build-fingerprint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-width: 0;
}

.about-build-card__head span,
.about-build-fingerprint span {
  color: var(--on-surface-muted);
  font-size: 12px;
  font-weight: 700;
}

.about-build-card__head strong {
  color: var(--on-surface);
  font-size: 14px;
  font-weight: 800;
}

.about-build-card p {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  line-height: 1.6;
}

.about-build-fingerprint code {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--on-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 700;
  text-align: right;
}

.about-detail-grid {
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

.about-info-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  padding: 18px 20px;
  border-radius: 18px;
}

.about-info-card__body {
  display: grid;
  gap: 10px;
  flex: 1;
}

.about-guide-card {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}

.about-guide-card__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.about-info-card__body p {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  line-height: 1.75;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}

.section-title--plain {
  justify-content: flex-start;
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

.license-stack {
  display: grid;
  gap: 8px;
  flex: 1;
  align-content: start;
}

.license-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  padding: 11px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-2) 48%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 28%, transparent);
}

.license-row span {
  color: var(--on-surface-muted);
  font-size: 12px;
  font-weight: 600;
}

.license-row strong {
  color: var(--on-surface);
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.about-link-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.about-link-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
  min-height: 52px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--outline) 36%, transparent);
  border-radius: 14px;
  color: var(--on-surface);
  background: color-mix(in srgb, var(--surface-2) 32%, transparent);
  cursor: pointer;
  text-align: left;
  transition: transform 160ms ease, border-color 180ms ease, background 180ms ease;
}

.about-link-item:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--primary-border) 50%, var(--outline));
  background: color-mix(in srgb, var(--primary-soft) 16%, var(--surface-2));
}

.about-link-item__icon {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 36%, transparent);
}

.about-link-item__label {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.about-link-item__open {
  color: var(--on-surface-muted);
  opacity: 0.7;
}
</style>
