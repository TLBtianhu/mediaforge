import { convertFileSrc } from '@tauri-apps/api/core'

export function mediaPreviewUrl(path: string): string {
  return path ? convertFileSrc(path, 'mediaforge-preview') : ''
}
