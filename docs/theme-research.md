# 主题与色彩：设计系统专家怎么做（调研笔记）

> 目的：为我们自己的 token/主题架构找外部依据，区分"业界共识""仍有分歧""我们的偏差"。
> 调研时间：2026-11-09。引用见文末。

## 1. 共识一：一个种子色 → 在感知空间里生成整条色阶

- Material 3 的官方实现 `material-color-utilities` 用 **HCT** 色彩空间（hue/chroma/tone），
  提供 `themeFromSourceColor(sourceColor)`：给一个源色，生成整套主题与色调。
- Evil Martians（PostCSS/Autoprefixer 团队）把这条原则说得最直白：
  "OKLCH frees designers from the need to manually choose every color. Designers can define a formula,
  choose a few colors, and an entire design system palette is **automatically generated**."
- 两家都强调：生成发生在**感知空间**，而不是 RGB/HSL。

**对我们的含义**：我们把品牌色、tone、派生统一到 `themeTokens` 是正确方向；但我们的混合
（`mixToward`）是在 **sRGB 通道上线性插值**，这正是专家点名要避免的方式（见 §4 偏差 1）。

## 2. 共识二：色阶按“用途”固定分层，而不是按“强度”命名

- Radix Colors 每条色阶固定 **12 步**，"each step was designed for at least one specific use case"：
  1 应用背景、2 次级背景、3 UI 元素背景、4 悬停背景、5 激活/选中背景、6 细边框/分隔线、
  7 UI 边框与 focus ring、8 更强的边框……（9–12 为实心填充与文字层，见原文表格）。
- 关键点：**步数对应语义**，所以调用方不需要判断"这个多深能不能当文字"——它直接选带语义的那一步。

**对我们的含义**：我们把 `--primary-strong` 拆成 `--primary-ink` / `--primary-line` /
`--primary-hover` 正是同一个方向；我们还可以进一步把 `fill/ink/wash/border` 定义为固定 ramp。

## 3. 共识三：可访问性是约束，不是审美；但“单一 4.5”不是好目标

- APCA 文档（Myndex / Andrew Somers，W3C 受邀专家）明确批评 WCAG 2.x：
  - 数学非感知均匀，"WCAG 4.5:1 can be functionally unreadable when a color is near black"，
    对暗色模式尤其失真；
  - "No single figure (such as 4.5:1 or 3:1) is useful as a blanket target"；
  - 提出按字号/字重分档的 Lc：**Lc 90** 正文首选、**Lc 75** 正文字号 18px/400 下限、
    **Lc 60** 非正文内容文字、**Lc 45** 大标题、**Lc 30** 占位/禁用文字、**Lc 15** 非文字可辨别下限。
- Evil Martians 也补了一刀（重要，避免过度信任 OKLCH）：
  "Contrast was replaced with lightness. Developers should use **APCA** to detect contrast.
  Just OKLCH is not enough."

**对我们的含义**：我们用 WCAG2 4.5 作为硬门槛是"合规且可辩护"，但落后于实践；
至少应按字号/字重分档（我们已有 large 分支），并考虑把 APCA 作为诊断值（不阻塞）。

## 4. 共识四（这条直接打我们的脸）：HSL / sRGB 的"亮度"不可靠

Evil Martians 给的例子：

- "Adding 10% lightness will have different results for blue and purple colors."
- "Hue changes could also change lightness, thus making text unreadable."
- 所以 hover 应该写成 `oklch(from var(--accent) calc(l - 0.1) c h)`——**从同一个源色改一个感知分量**，
  一条规则覆盖所有颜色。

**我们的偏差**：

1. `mixToward(hue, anchor, w)` 是 sRGB 线性插值。它不感知均匀，往深灰混会让蓝紫变灰/变紫；
   这解释了我们为什么需要"求解"（readableInk 逐档试）而不是"一步算对"（固定 L 步长）。
2. tone wash 用固定 α 表达强度，但实测 ΔL* 从 4.74 到 9.20（同一 α，不同色相），
   说明 `rgba(hue, α)` 不是"同等强度"的可控单位；应该用感知空间的 L 步长。
3. 我们的 hover/pressed 是往黑/白混，方向对（远离标签墨色），但混合空间仍然错。

## 5. 手挑仍然合法：专家并不要求"全派生"

Stripe 的公开文章摘要写得很清楚：
"a color system with **hand-picked, vibrant colors** that also met standards for accessibility and contrast."
即：品牌色/色相是**手挑的输入**，可访问性是**校验后的约束**。

Material 的 `sourceColor` 也是人给的；HCT 只负责生成。

**对我们的含义**：`preview` 色卡手挑、品牌色相手挑，都不是"落后"；但手挑值必须被测试约束
（例如可分辨度 ΔE 下限、对比度下限），而不是只靠肉眼。

## 6. 仍有分歧（我们不必选边，但要知道）

| 议题 | 一方 | 另一方 |
| --- | --- | --- |
| 感知空间 | Material HCT（Google 生态） | OKLCH/OkLab（Web 生态，CSS Color 4 采纳） |
| 对比度标准 | WCAG 2.x 4.5（法律/合规基线） | APCA Lc（更贴近感知，WCAG 3 候选） |
| 生成 vs 策展 | M3 dynamic color：全生成 | IBM Carbon / Stripe：分主题策展 + 校验 |
| 文字层级 | 专用 muted token（Radix 11/12 步） | 部分系统用 opacity/vibrancy（Apple HIG） |

## 7. 对照我们当前实现

| 专家实践 | 我们 | 结论 |
| --- | --- | --- |
| 一个种子 → 生成色阶 | `themeTokens` 从 fill 派生 tint/hover/pressed | ✔ 方向一致 |
| 感知空间生成 | sRGB 线性混合 | ✘ 需迁移到 OKLCH |
| 按用途分层 | fill / ink / wash / border / hover / pressed | ✔ 基本一致 |
| wash 用感知步长 | 用 α | ✘ 应改为 ΔL(OKLCH) 求解 |
| 对比度分档 | WCAG 4.5 + large 3.0 | △ 合规但未分档到 APCA |
| 手挑品牌色 | 手挑 primary/ink seed/tone | ✔ 与 Stripe/M3 一致 |
| 手挑值被约束 | 矩阵测试断言对比度 | ✔ 但缺可分辨度（ΔE）测试 |
| 文字不用 opacity | 侧边栏 0.62、计数 0.55 | ✘ 应改为专用 faint token |

## 8. 建议的下一步（按外部依据排序）

> 2026-11-09 更新：1、2、5 已完成；4 的画布部分代码已完成、视觉待 GUI 确认。

1. ~~把 `themeMath` 的混合从 sRGB 换成 OKLCH~~ **已完成（2026-11-09）**：实际采用 **Oklab**（OKLCH 的
   cartesian 形式）。原因：极坐标插值在近灰锚点上会旋转色相，实测把琥珀 ink 变成粉色；Oklab 的 a/b
   线性插值保持色相、只收缩彩度。API 不变，派生色变化 0.13–0.19% 像素。
2. ~~wash 改为固定 OKLCH L 步长求解 α~~ **已完成（2026-11-09）**：用 Oklab L，`TONE_WASH_STEP = 0.05`，
   四种 tone 的 α 变成计算结果（0.089–0.172），视觉差异集中在淡底区域（8–9% 像素）。
3. 对比度门槛分档：正文 / 大字 / 禁用 / 非文字（APCA Lc 60/45/30/15 的对应值）。
4. 给 `preview` 加**可分辨度测试**（OKLab ΔE 两两 ≥ 阈值）。
5. ~~用专色 token 取代文字 opacity~~ **已完成（2026-11-09）**：`--on-surface-faint` 由二分求解，是“在所有表面上都过 4.5 的最暗颜色”；审计从 440 处降到 **0**。

## 参考

- Material color utilities（HCT、`themeFromSourceColor`）：
  https://unpkg.com/@material/material-color-utilities/README.md
- Radix Colors, "Understanding the scale"（12 步与用途表）：
  https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale
- Evil Martians, "OKLCH in CSS: why we moved from RGB and HSL"：
  https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl
- APCA, "The Easy Intro to the APCA Contrast Method"：
  https://git.apcacontrast.com/documentation/APCAeasyIntro
- Stripe, "Designing accessible color systems"：
  https://stripe.com/blog/accessible-color-systems
