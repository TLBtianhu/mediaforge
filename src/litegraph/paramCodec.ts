/**
 * 参数编解码：让"布局顺序"不再是"线上格式"。
 *
 * pymss 按位置读 `widgets_values`（ComfyUI 的格式就是这样），这是协议，不改。
 * 要解耦的是**我们这一侧**：
 *
 *   - 调用方只认名字（`input_name`、`folder`），不认下标
 *   - 位置化只发生在 `encodeWidgets()`，反序列化只发生在 `decodeWidgets()`
 *
 * 于是 UI 怎么排版都不影响语义：把控件换个顺序，payload 依旧按 spec 顺序写；
 * 新增/删除控件不需要改任何下标。这条规则由 `tests/paramCodec.test.ts` 扫源码守护——
 * 除本文件与格式校验外，出现 `widgets_values[0]` 这样的下标访问就算违规。
 */
import { BUILTIN_SPECS, NODE_SPECS, type NodeSpec, type WidgetSpec } from './nodeSpecs.ts'

const SPECS: Record<string, NodeSpec> = { ...BUILTIN_SPECS, ...NODE_SPECS }

export function specFor(type: string): NodeSpec | undefined {
  return SPECS[type]
}

export function widgetNames(spec: NodeSpec): string[] {
  return (spec.widgets || []).map(widget => widget.name)
}

export function widgetIndex(spec: NodeSpec, name: string): number {
  return (spec.widgets || []).findIndex(widget => widget.name === name)
}

export function widgetSpec(spec: NodeSpec, name: string): WidgetSpec | undefined {
  return (spec.widgets || []).find(widget => widget.name === name)
}

/**
 * 位置数组 → 命名参数。
 *
 * 按当前声明读取；超出声明的布局必须拒绝。省略的可选参数使用当前默认值。
 */
export function decodeWidgets(
  spec: NodeSpec,
  values: unknown[] | null | undefined,
): { params: Record<string, unknown> } {
  const list = Array.isArray(values) ? values : []
  const params: Record<string, unknown> = {}
  const widgets = spec.widgets || []
  if (list.length > widgets.length) throw new Error(`Unsupported widget schema for ${spec.type}`)
  widgets.forEach((widget, index) => {
    params[widget.name] = index < list.length ? list[index] : widget.default
  })
  return { params }
}

export interface EncodeReport {
  values: unknown[]
  ignored: string[]
}

/**
 * 命名参数 → 位置数组（顺序完全由 spec 决定，与 UI 无关）。
 *
 * `strict` 默认打开：多传了 spec 里没有的键就报错，避免"以为调了参数其实没生效"。
 * 需要容错的地方（导入外部图）用 `encodeWidgetsReport` 拿到被忽略的键。
 */
export function encodeWidgetsReport(
  spec: NodeSpec,
  params: Record<string, unknown>,
): EncodeReport {
  const widgets = spec.widgets || []
  const known = new Set(widgets.map(widget => widget.name))
  const ignored = Object.keys(params).filter(key => !known.has(key))
  const values = widgets.map(widget =>
    Object.prototype.hasOwnProperty.call(params, widget.name) ? params[widget.name] : widget.default,
  )
  return { values, ignored }
}

export function encodeWidgets(spec: NodeSpec, params: Record<string, unknown>): unknown[] {
  const report = encodeWidgetsReport(spec, params)
  if (report.ignored.length > 0) {
    throw new Error(
      `unknown widget(s) for ${spec.type}: ${report.ignored.join(', ')} (known: ${widgetNames(spec).join(', ')})`,
    )
  }
  return report.values
}

/** 按名字读一个值；spec 里没有这个控件时返回 `undefined`（调用方自己决定回退）。 */
export function widgetValue(
  spec: NodeSpec | undefined,
  info: { widgets_values?: unknown[] } | null | undefined,
  name: string,
): unknown {
  if (!spec || !widgetSpec(spec, name)) return undefined
  return decodeWidgets(spec, info?.widgets_values).params[name]
}

/**
 * 读"运行时注册节点"的第一个控件值。
 *
 * 分离节点（`*_separate`）由 pymss 在运行时注册，本文件没有它的静态 spec，
 * 所以拿不到控件名——而 pymss 约定第一个控件就是模型名。这个约定属于协议，
 * 集中在这里说明，而不是散落在各处写 `widgets_values[0]`。
 * 等运行时节点也导出 spec 之后，这个函数应该被 `widgetValue(spec, info, 'model')` 取代。
 */
export function firstWidgetValue(info: { widgets_values?: unknown[] } | null | undefined): unknown {
  const list = info?.widgets_values
  return Array.isArray(list) && list.length > 0 ? list[0] : undefined
}

/** 按名字写一个值，返回新的节点信息（不修改入参）。 */export function withWidgetValue<T extends { widgets_values?: unknown[] }>(
  spec: NodeSpec | undefined,
  info: T,
  name: string,
  value: unknown,
): T {
  if (!spec || !widgetSpec(spec, name)) return info
  const { params } = decodeWidgets(spec, info.widgets_values)
  params[name] = value
  return { ...info, widgets_values: encodeWidgets(spec, params) }
}

/** 按名字批量写，一次 encode。 */
export function withWidgetValues<T extends { widgets_values?: unknown[] }>(
  spec: NodeSpec | undefined,
  info: T,
  patch: Record<string, unknown>,
): T {
  if (!spec) return info
  const { params } = decodeWidgets(spec, info.widgets_values)
  let changed = false
  for (const [name, value] of Object.entries(patch)) {
    if (!widgetSpec(spec, name)) continue
    params[name] = value
    changed = true
  }
  if (!changed) return info
  return { ...info, widgets_values: encodeWidgets(spec, params) }
}
