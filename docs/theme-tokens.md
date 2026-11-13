# 主题 token 契约与审计（2026-11-08）

主题是横切关注点：它没有"某个模块负责"，只有"所有模块都必须经过同一个出口"。
靠人记必然退化，所以这里把它变成可执行契约：**词表 + 例外登记 + CI 棘轮**。

## 1. 唯一出口

| 位置 | 职责 |
| --- | --- |
| `src/utils/themeTokens.ts` | 真源：品牌色相、tone 色相、surface 明暗；`resolveThemeTokens` / `toneTokens` 在这里派生 ink、wash、on-fill |
| `src/utils/themeMath.ts` | 纯函数：`mixToward` / `contrastRatio` / `inkOn` / `blendOver` / `readableInk`，不碰 DOM |
| `src/utils/theme.ts` | 编排：把 token 写进 `<html>`、产出 Naive `themeOverrides`、切主题与首帧 |
| `src/styles/global.scss` | token 的**默认值**（首帧/无 JS 时使用）+ 少量 Naive 覆盖（`--n-*`） |
| `index.html` 内联脚本 | 首帧预涂色：按 `localStorage` 的 `pymss:theme_mode` 先设 class 与背景色 |

Token 词表（两侧必须同名同集，`check-theme-tokens.py` 对账）：

```
表层   --surface / --surface-1 / --surface-2 / --surface-3
文字   --on-surface-heading / --on-surface / --on-surface-muted / --on-surface-faint
描边   --outline / --outline-strong
强调   --primary（填充） / --primary-hover / --primary-pressed（交互态填充）
       --primary-ink（唯一墨色：文字与图标）
       --primary-soft / --primary-softer / --primary-border / --primary-glow
语义   --success / --warning / --danger / --on-primary（填充上的标签墨色）
阴影   --shadow-panel / --shadow-soft
```

**填充与墨色必须分开**：`--primary` 是填充色，浅色主题下直接当文字用只有 2.9–4.3:1。
文字/图标只允许 `--primary-ink`（由 `readableInk` 从原 strong 种子求解；旧的 `--primary-text` / `--primary-strong` 合并成这一个角色）。
`--on-primary` 按填充色亮度在白色与近黑色之间选（深色主题的填充是粉彩，白字只有 2.3–2.8:1）。

布局与排版 token（`--space-*`、`--radius-*`、`--modal-*`、`--page-*`、`--font-*`）不是颜色，不参与主题对账。

## 2. 对比度由构造保证

颜色不靠事后审计：派生函数直接把这个规则写死。

- `--primary-ink`：`readableInk(...)` 在 **Oklab** 空间逐档求解，四种 accent 两色主题都过 AA；
- `--on-primary`：`inkOn(fill)`，按填充亮度在白色与近黑之间选；
- **交互 ramp 派生**：`primaryHover` / `primaryPressed` 由 fill 朝“远离标签墨色”的方向混 12% / 24%，
  所以粉彩填充的悬停会变亮、饱和填充的悬停会变暗，标签墨色在三种状态下都不翻转；
- **soft 家族派生**：`primarySoft/Softer/Border/Glow` 一律 `rgba(fill, α)`，α 是 accent 调色板里唯一保留的手工数据；
- **tone wash 由感知步长求解**：`TONE_WASH_STEP = 0.05`（Oklab L）。不再是手挑 α——同样 α 下琥珀的实际
  强度是蓝色的 1.7 倍，现在四种 tone 的淡底统一到同一感知步长，α 成了计算结果（蓝 0.118 / 紫 0.126 /
  琥珀 0.172 / 青 0.132，浅色主题）；
- tone 的 `ink`：`readableInk(hue, heading, [surface, surface1, wash×2])`，逐档求解直到同时满足四个背景；
- **混合空间是 Oklab**（`mixToward` 内部）：sRGB/HSL 插值不感知均匀，同一步长在不同色相上亮度不同，
  且往近灰锚点混会旋转色相（实测会把琥珀文字变粉）；Oklab 的 cartesian 形式没有极坐标的 powerless-hue 问题。
- Naive 组件的 Tag 文字：同一套 `mixToward(..., 0.62)`。

契约测试 `tests/themeContrastMatrix.test.mjs` 在 CI 里毫秒级跑完 `2 主题 × 4 accent × 4 tone`：
ink 在 surface / primarySoft / wash 上 ≥4.5:1，`onPrimary` 在 fill 上 ≥4.5:1，
并断言 token 值必须是具体颜色（不允许再把 `color-mix()` 字符串塞进 token）、
`global.scss` 的 tone 默认值必须与 token 模块一致。

## 3. 三类颜色，三种规则

1. **语义 token**：界面颜色必须用它。CSS 里出现字面颜色即违规。
2. **身份色 / 品牌色**：stem 色、轨道色、品牌标记。固定不随主题变化，但必须**登记理由**。
3. **绘制表面**：LiteGraph 画布、波形、缩略图。CSS 变量管不到，颜色是 JS 常量；同样登记理由，
   并要求切主题时重绘（见 §5 未完成项）。

## 4. 审计器

```powershell
python scripts/check-theme-tokens.py
```

四条检查，失败即非 0：

1. **字面颜色**：每个文件的 hex/rgb/hsl 字面量必须落在 `ALLOWED`（按理由）或 `DEBT`（按预算）里。
   `DEBT` 是棘轮：超过预算失败；**降到预算以下也失败**（要求同时收紧预算）；文件消失或清零同样失败
   （要求删除登记），避免清单腐烂。
2. **token 引用**：`var(--x)` 必须能在项目内找到定义（Naive UI 的 `--n-*` 视为外部）。
   未定义变量不会报错、只会静默失效——这是最难发现的一类主题 bug。
3. **token 对账**：`theme.ts` 的运行时 token 与 `global.scss` 的默认值必须同名同集；
   否则浅色主题缺值，或新增 token 只改了一半。
4. 报告当前基线与例外文件列表。

测试见 `tests/python/test_theme_check.py`（预算棘轮、过期登记、未定义变量、对账、外部变量均有用例）。

## 5. 当前基线（2026-11-08）

- 扫描 282 个文件：字面颜色 **316 处 / 37 个文件**；其中约 150 处是三处真源（`themeTokens.ts` + `themeMath.ts` + `global.scss`）、
  约 70 处是登记的身份色/画布例外，其余在 22 个债务文件里。
- **未定义 token 已清零**：本批把 `media-ui.css`、`FactoryView`、`ModelHubView`、`PluginsView`、
  `CustomModelImportDialog`、`ImageView`、`AssetsView`、`VideoView` 上的一整套幽灵词表
  （`--divider-color`、`--text-color-1/2/3`、`--card-color`、`--body-color`、`--action-color`、
  `--bg-elevated`、`--border-color`、`--primary-color`、`--error-color`、`--text-secondary`、`--fill-2`）
  映射到现有词表；`--on-primary` 补成正式 token（浅/深色均为 `#ffffff`）。
- 这些页面此前 `var()` 无回退值、变量不存在 → 属性在计算期无效 → 颜色静默回退，等于**从未跟随主题**。
- 页面 tone 现在只由 `data-tone` 选择色相，`media-ui.css` 不再有调色板字面量（29 → 5，剩下的是阴影/渐变）。

## 6. 无头视觉验收（2026-11-08 实测）

两个手动工具（不进 CI，需要先 `pnpm dev`，只驱动无头浏览器，不启动桌面应用）：

```powershell
# 深浅两色截图，默认写到 .tmp/theme-shots
python tests/manual/theme_screenshots.py

# WCAG 对比度审计，输出 .tmp/theme-contrast.json
python tests/manual/theme_contrast_audit.py
```

可配 `PYMSS_SMOKE_URL`（默认 `http://localhost:1420/`）与 `PYMSS_CHROMIUM`
（本机 Playwright 浏览器版本与已装包不匹配时用）。

对比度审计的口径：只算带自有文字的元素；按祖先背景逐层合成；**累计 `opacity`**（半透明文字
确实更暗）；跳过禁用控件（WCAG 1.4.3 豁免）；正文 4.5:1、大字 3:1。

### 6.1 本轮发现并修复

| 问题 | 修复前 | 修复后 |
| --- | --- | --- |
| 页面 tone 强调色直接用于小字（eyebrow、步骤序号、统计值） | 44 处低于 AA，最差 **2.44:1**（浅色琥珀） | `--mf-accent-text` = `color-mix(accent 58%, --on-surface-heading)`，双向自动偏移 |
| Naive `n-tag` 状态标签文字 | 浅色 5 处 3.12–3.98:1 | `Tag.textColorSuccess/Warning/Info/Error` 同样按 62% 混向 heading |
| 合计（9 路由 × 深浅两色，18 次加载） | **49** 处 | **0** 处（tone/tag 相关） |

### 6.2 四种 accent 全跑

`--primary` 家族只在默认蓝下测过，换 accent 立刻暴露：

| 问题 | 实测 | 修复 |
| --- | --- | --- |
| `--primary-strong` 当激活态文字（导航、tab、卡片序号、比例值） | 粉 3.75、天蓝 3.7–4.43 | 浅色下按 55% 混向 heading |
| 白字压在 `--primary` 填充上 | 粉 **3.18**、天蓝 3.37、青 4.12 | `--on-primary` 按填充亮度自适应（粉/天蓝改近黑字）；青把浅色 `primary` 从 `#238c7b` 调到 `#1f7d6f` |
| `--primary` 当链接文字 | 蓝 4.33、粉 2.86、天蓝 3.03、青 3.7 | 新增 `--primary-text`，11 处文字改用 |
| 剩余 | **440** 处（14 路由 × 2 主题 × 4 accent = 112 次加载） | 416 处压暗标题 + 24 处计数，其余 **0** |

> 表中 `--primary-text` / `--primary-strong` 是当时的名字，第 2 步已合并为 `--primary-ink`；
> 混合空间在第 4 步换成了 Oklab，数值与当时不同，结论（全部达标）不变。

### 6.3 压暗标题与计数：已用 token 收口

审计曾经剩下的 416 处侧边栏分组标题（`opacity: .62`，深色 3.34:1）与 20 处计数（`opacity: .55–.72`）
全部来自“用 opacity 表达层级”。现在换成 `--on-surface-faint`：

- 它是“在 surface / surface1 / surface2 上都 ≥4.5”的**最暗合法颜色**，由二分求解得出；
- 深色 `#898f99`（surface 5.71:1、surface2 4.61:1），浅色 `#616d80`（4.84–5.11:1）；
- 矩阵测试断言它既过 AA、又确实比 `--on-surface-muted` 更暗（否则“压暗”没有被表达出来）；
- 112 次页面加载的对比度审计现在 **0 处**（此前 440 处）。

代价是浅色主题的 faint 只比 muted 暗约 12%——因为 AA 已经是地板，想更暗只能降低标准或调大字重。
这是“可访问性优先”的必然结果，不是实现缺陷。

## 7. 未完成项（需要决定或需要 GUI）

1. **页面 tone**：`media-ui.css` 的 `data-tone`（blue/violet/amber/emerald）是每页强调色，
   与用户的 `themeAccent` 是两套。要决定"tone 固定"还是"从 accent 派生"；目前按固定调色板登记为债务。
2. **日志控制台**：`TaskLogConsole` / `DebugView` 的深色日志面板是终端语义（固定深色）还是应跟随主题，
   尚未声明。
3. **画布视觉确认**：画布调色板已改为从 token 派生（`litegraph/canvasTheme.ts`），工作流编辑器也加了
   `watch(themeMode/themeAccent/themeIsDark)` 并 `setDirty(true, true)`；单测断言节点文字在两种主题下
   都过 AA。**但真实重绘只在 WebView2 窗口里能看见**，无头浏览器拿不到 LiteGraph 的窗口环境，
   需要开一次窗口切主题确认。端口/事件连线的身份色仍是固定值（有意为之）。
4. **双主题验收基线**：现有 117 个手动 Playwright 脚本几乎只在默认主题跑，没有一个断言 light/dark
   的可见结果。这是"测试全绿但主题坏掉"的根因。
5. **插件的颜色来源**：`PluginViewHost` 只推送 `THEME_TOKENS` 白名单里的变量；插件若用别的名字会静默缺失。

## 8. 新增例外怎么登记

1. 先问：这个颜色是语义色、身份色，还是绘制表面？
2. 语义色 → 换 token（缺语义就补 token，并按 §1 两侧同时加）。
3. 身份色/绘制表面 → 在 `scripts/check-theme-tokens.py` 的 `ALLOWED` 里加一行**理由**，不要写"历史原因"。
4. 暂时修不了 → 加进 `DEBT` 并给预算；下次收敛时必须同步收紧预算，否则 CI 会提示。
