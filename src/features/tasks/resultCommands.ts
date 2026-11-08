/** Commands whose terminal journal records can appear in the results page. */
export const RESULT_JOB_COMMANDS: ReadonlySet<string> = new Set([
  'infer', 'infer_workflow', 'audio_tools', 'media_tool',
  'media_image', 'media_video', 'media_workflow', 'media_convert', 'run_operation',
])
