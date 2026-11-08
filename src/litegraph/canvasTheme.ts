import type { ResolvedThemeTokens } from '../utils/theme'

/** Colours the LiteGraph canvas needs, derived from the app tokens. */
export type CanvasTheme = {
  background: string
  nodeBackground: string
  nodeTitle: string
  text: string
  link: string
  eventLink: string
}

/**
 * Event links are a data-type identity colour (like port colours), not a theme
 * colour: they must stay recognisable regardless of theme or accent.
 */
export const CANVAS_EVENT_LINK_COLOR = '#9b6dff'

export function canvasThemeFor(tokens: ResolvedThemeTokens): CanvasTheme {
  return {
    background: tokens.surface,
    nodeBackground: tokens.surface1,
    nodeTitle: tokens.surface2,
    text: tokens.onSurfaceHeading,
    link: tokens.primary,
    eventLink: CANVAS_EVENT_LINK_COLOR,
  }
}
