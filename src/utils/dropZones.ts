/**
 * 拖放落点判定：把"窗口坐标"翻译成"用户想放进的字段"。
 *
 * 为什么需要它：Tauri 的原生拖放事件只给**窗口坐标**，不给 DOM 目标
 * （原生拖放接管后，HTML5 的 drop 事件不会带着真实路径触发）。
 * 所以用坐标去命中各个投放区的矩形，再决定"引用这个目录"还是"加入这些文件"。
 *
 * 坐标是物理像素，元素矩形是 CSS 像素——调用方要传进来 devicePixelRatio。
 */

export interface DropZoneRect {
  id: string
  left: number
  top: number
  right: number
  bottom: number
}

export interface Point {
  x: number
  y: number
}

/** 命中哪个投放区；都不命中返回 null（例如丢在页面空白处）。 */
export function findDropZone(position: Point | null | undefined, zones: DropZoneRect[]): string | null {
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null
  for (const zone of zones) {
    if (
      position.x >= zone.left &&
      position.x <= zone.right &&
      position.y >= zone.top &&
      position.y <= zone.bottom
    ) {
      return zone.id
    }
  }
  return null
}

/** 物理像素 → CSS 像素（devicePixelRatio 非法时按 1 处理）。 */
export function toCssPoint(position: Point | null | undefined, ratio: number): Point | null {
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null
  const scale = Number.isFinite(ratio) && ratio > 0 ? ratio : 1
  return { x: position.x / scale, y: position.y / scale }
}

export interface PathKind {
  path: string
  kind: 'file' | 'directory' | 'missing'
}

/**
 * 从拖入的路径里挑出"要引用的目录"。
 *
 * 规则：只认单个目录；多个路径或文件一律返回 null（由调用方按"加入文件"处理），
 * 免得把一堆素材误设成输出目录。
 */
export function folderToReference(kinds: PathKind[] | null | undefined): string | null {
  if (!kinds || kinds.length !== 1) return null
  const [only] = kinds
  return only.kind === 'directory' ? only.path : null
}

/** 判断这个字段是不是"目录型"（据此决定拖入时是否引用目录）。 */
export const DIRECTORY_ZONES = new Set(['outputDir', 'modelDir'])
