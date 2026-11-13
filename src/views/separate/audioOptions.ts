import type { ComputedRef } from 'vue'

type Deps = {
  t: (key: string, params?: Record<string, unknown>) => string
  computed: <T>(source: () => T) => ComputedRef<T>
}

/** Audio format choices and the input-kind label used by the rail. */
export function makeAudioOptions({ t, computed }: Deps) {
  const formatOptions = [
    { label: 'WAV', value: 'wav' },
    { label: 'FLAC', value: 'flac' },
    { label: 'MP3', value: 'mp3' },
    { label: 'M4A', value: 'm4a' },
  ]

  function getFileKindLabel(path: string) {
    const ext = path.split('.').pop()?.toLowerCase() || ''
    if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv'].includes(ext)) return t('separate.videoFile')
    return t('separate.audioFile')
  }

  const wavBitDepthOptions = computed(() => [
    { label: t('audio.pcm16'), value: 'PCM_16' },
    { label: t('audio.pcm24'), value: 'PCM_24' },
    { label: t('audio.float'), value: 'FLOAT' },
  ])
  const flacBitDepthOptions = computed(() => [
    { label: t('audio.pcm16'), value: 'PCM_16' },
    { label: t('audio.pcm24'), value: 'PCM_24' },
  ])
  const bitRateOptions = computed(() => [
    { label: t('audio.bitrate128'), value: '128k' },
    { label: t('audio.bitrate192'), value: '192k' },
    { label: t('audio.bitrate256'), value: '256k' },
    { label: t('audio.bitrate320'), value: '320k' },
    { label: t('audio.bitrate512'), value: '512k' },
  ])
  const mp3BitRateOptions = computed(() => bitRateOptions.value.filter(option => parseInt(option.value, 10) <= 320))
  const m4aCodecOptions = computed(() => [
    { label: t('audio.codecAac'), value: 'aac' },
  ])
  const audioSteps = computed(() => [
    { title: t('separate.input'), text: t('separate.stepInputHint') },
    { title: t('separate.output'), text: t('separate.stepOutputHint') },
    { title: t('separate.model'), text: t('separate.stepRunHint') },
  ])

  return {
    formatOptions, getFileKindLabel, wavBitDepthOptions, flacBitDepthOptions,
    bitRateOptions, mp3BitRateOptions, m4aCodecOptions, audioSteps,
  }
}
