import { invoke } from '@tauri-apps/api/core'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type EditorAudioMetadata = WorkerCommandResult<'audio_metadata'>
export type EditorWaveformPeaks = WorkerCommandResult<'waveform_peaks'>
export type EditorMixStartResponse = { taskId: string; started: boolean }

/** Rust `get_audio_metadata` wraps the worker `audio_metadata` command. */
export function getAudioMetadata(payload: WorkerCommandPayload<'audio_metadata'>): Promise<EditorAudioMetadata> {
  return invoke<EditorAudioMetadata>('get_audio_metadata', { payload })
}

/** Rust `generate_waveform_peaks` wraps the worker `waveform_peaks` command. */
export function generateWaveformPeaks(
  payload: WorkerCommandPayload<'waveform_peaks'>,
): Promise<EditorWaveformPeaks> {
  return invoke<EditorWaveformPeaks>('generate_waveform_peaks', { payload })
}

/** Rust `start_editor_mix_export` starts the worker `export_editor_mix` task. */
export function startEditorMixExport(
  payload: WorkerCommandPayload<'export_editor_mix'>,
): Promise<EditorMixStartResponse> {
  return invoke<EditorMixStartResponse>('start_editor_mix_export', { payload })
}
