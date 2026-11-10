"""前端主题不变量检查。

主题是横切关注点：正确性只能靠"唯一出口 + 强制"，靠人记必然退化。
本脚本守住四条，退出码非 0 即视为破坏：

1. 字面颜色只允许出现在登记过的位置。登记分两类：
   - `ALLOWED`：按**理由**登记的例外（真源、品牌/身份色、画布、首帧预涂色）；
   - `DEBT`：尚未收敛的债务，按文件的字面颜色**预算**棘轮——只许减少或持平，
     文件消失或清零必须同时删除登记，避免清单腐烂。
2. `var(--x)` 引用的变量必须在项目内有定义；Naive UI 的 `--n-*` 视为外部变量。
   未定义变量同样按文件预算棘轮（当前都是历史遗留，先禁止增长）。
3. `theme.ts` 的运行时 token 与 `global.scss` 的默认值必须同名同集：
   否则浅色主题会缺值、新增 token 会只改一半。
4. 输出人类可读报告；`--strict` 与默认行为一致（违规则非 0）。

风格与 `check-architecture.py` 对齐：无第三方依赖、显式 UTF-8 输出、函数可被测试直接调用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# GitHub 的 Windows runner 控制台默认是 cp1252；直接打印中文/✓ 会 UnicodeEncodeError。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
GLOBAL_STYLE = SRC / "styles" / "global.scss"
THEME = SRC / "utils" / "theme.ts"
INDEX_HTML = ROOT / "index.html"
SOURCE_SUFFIXES = {".vue", ".css", ".scss", ".ts"}

#: 字面颜色：#rgb / #rgba / #rrggbb / #rrggbbaa，以及以数字开头的 rgb()/hsl()。
#: `rgba(var(--x), .2)` 这种由 token 派生的写法不算字面颜色。
HEX_COLOR = re.compile(r"(?<![\w#-])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
FUNC_COLOR = re.compile(r"\b(?:rgba?|hsla?)\(\s*[0-9.]", re.IGNORECASE)
TOKEN_REF = re.compile(r"var\(\s*(--[a-z0-9-]+)", re.IGNORECASE)
# A definition is a property position, not the tail of a selector like
# `.nav-item--debug:not(...)`; require the token not to follow an identifier.
TOKEN_DEF = re.compile(r"""(?<![\w-])['"]?(--[a-z0-9-]+)['"]?\s*:""", re.IGNORECASE)
TOKEN_SET = re.compile(r"""setProperty\(\s*['"](--[a-z0-9-]+)['"]""", re.IGNORECASE)

#: Naive UI 在运行时注入的变量，不属于本项目的 token 词表。
EXTERNAL_TOKEN_PREFIXES = ("--n-",)

#: 布局/排版 token 不是颜色，不参与"运行时必须覆盖默认值"的对账。
NON_COLOR_PREFIXES = (
    "--space-", "--radius-", "--font-", "--page-", "--modal-", "--card-",
    "--mf-", "--n-", "--track-", "--log-", "--dl-", "--surface-4",
)

#: 按理由登记的字面颜色例外。
ALLOWED: dict[str, str] = {
    "src/utils/theme.ts": "主题编排：只把 token 写进 DOM（无颜色字面量）",
    "src/utils/themeTokens.ts": "调色板与 token 派生真源（品牌色相、tone 色相、surface 明暗）",
    "src/utils/themeMath.ts": "对比度/混色数学真源（只允许白色基准）",
    "src/styles/global.scss": "token 默认值与 Naive 覆盖的真源",
    "index.html": "首帧预涂色：JS 加载前按 localStorage 先着色，必须与 token 保持一致",
    "src/stores/editorStems.ts": "轨道身份色：按 stem 语义固定，不随主题变化",
    "src/components/editor/EditorWaveform.vue": "波形绘制色：绘制表面，跟随轨道身份色",
    "src/components/editor/EditorMixer.vue": "推子/轨道绘制色：绘制表面，跟随轨道身份色",
    "src/components/editor/EditorAssetTreeBranch.vue": "素材树身份色",
    "src/components/editor/EditorInspectorPanel.vue": "插件面板绘制色",
    "src/components/editor/EditorTransportBar.vue": "走带绘制色",
    "src/components/AppBrandMark.vue": "品牌标记阴影：品牌资产，不随主题变化",
    "src/features/onboarding/demoData.ts": "教程示例音频的身份色",
    "src/litegraph/registerNodes.ts": "画布身份色：端口/连线类型色固定，不随主题变化",
    "src/litegraph/canvasTheme.ts": "事件连线是数据类型身份色，固定不随主题变化",
    "src/components/workflow/WorkflowNodeEditorLite.vue": "画布调色板与节点底色：LiteGraph 常量",
    "src/views/WorkflowNodeEditorView.vue": "画布宿主容器背景",
}

#: 已知债务：文件 -> (字面颜色预算, 理由)。只许减少或持平。
DEBT: dict[str, tuple[int, str]] = {
    "src/App.vue": (1, "var() 回退值仍为字面量"),
    "src/components/DownloadDetailModal.vue": (10, "下载日志块沿用深色字面量"),
    "src/components/media/WorkbenchCard.vue": (1, "内阴影仍为字面量"),
    "src/components/ModelSlotField.vue": (1, "var() 回退值仍为字面量"),
    "src/components/RuntimeAssetBanner.vue": (3, "状态色回退值仍为字面量"),
    "src/components/TaskLogConsole.vue": (17, "日志控制台固定深色，未声明为设计决定"),
    "src/components/ViewportRecoveryOverlay.vue": (1, "遮罩色仍为字面量"),
    "src/components/workflow/simple-node-editor.css": (5, "紫色强调色为裸 hex"),
    "src/components/workflow/WorkflowNodeLog.vue": (1, "日志色仍为字面量"),
    "src/features/media-ui/media-ui.css": (5, "页面 tone 阴影/渐变仍有字面量"),
    "src/views/DebugView.css": (38, "调试页深色日志块与页面阴影混用，待决定是否跟随主题"),
    "src/views/ImageView.vue": (3, "计数色与遮罩仍为字面量"),
    "src/views/ModelHubView.vue": (4, "var() 回退值仍为字面量"),
    "src/views/models/models-view.css": (4, "var() 回退值仍为字面量"),
    "src/views/PluginsView.vue": (3, "状态色回退值仍为字面量"),
    "src/views/QueueView.vue": (2, "状态色回退值仍为字面量"),
    "src/views/results/results-view.css": (3, "var() 回退值与内阴影仍为字面量"),
    "src/views/SeparateView.css": (17, "阴影与强调色仍为字面量"),
    "src/views/settings/settings-view.css": (3, "危险色回退值与内阴影仍为字面量"),
    "src/views/VideoView.vue": (1, "var() 回退值仍为字面量"),
    "src/views/workflows/workflows-view.css": (1, "var() 回退值仍为字面量"),
    "src/views/WorkflowSimpleEditorView.vue": (1, "var() 回退值仍为字面量"),
}

#: 由主题派生、因此没有静态默认值的 token：`--tone-ink-*` 依赖当前主题的
#: heading 色，静态默认值只可能对一种主题正确；CSS 侧一律写成
#: `var(--tone-ink-x, currentColor)`，JS 未运行时可读但不强调。
DERIVED_RUNTIME_TOKENS: dict[str, str] = {
    "--tone-ink-blue": "按主题从 tone 色相与 heading 派生，CSS 有 currentColor 兜底",
    "--tone-ink-violet": "同上",
    "--tone-ink-amber": "同上",
    "--tone-ink-emerald": "同上",
}

#: 未定义 token 引用的预算：同样只许减少或持平。
#: 当前已清零——任何新出现的 `var(--x)` 缺少定义都会直接失败。
UNDEFINED_TOKEN_DEBT: dict[str, tuple[int, str]] = {}


def source_files(root: Path) -> list[Path]:
    files = [
        path for path in (root / "src").rglob("*")
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and "node_modules" not in path.parts
    ]
    index = root / "index.html"
    if index.is_file():
        files.append(index)
    return sorted(files)


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def count_literals(text: str) -> int:
    return len(HEX_COLOR.findall(text)) + len(FUNC_COLOR.findall(text))


def collect_tokens(files: list[Path]) -> tuple[set[str], set[str], set[str]]:
    """Return (defined, referenced, runtime). `runtime` comes from setProperty calls."""
    defined: set[str] = set()
    referenced: set[str] = set()
    runtime: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        defined.update(TOKEN_DEF.findall(text))
        runtime.update(TOKEN_SET.findall(text))
        referenced.update(TOKEN_REF.findall(text))
    return defined, referenced, runtime


def external_token(name: str) -> bool:
    return name.lower().startswith(EXTERNAL_TOKEN_PREFIXES)


def check_literal_colors(files: list[Path], root: Path) -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    counts: dict[str, int] = {}
    for path in files:
        name = relative(path, root)
        count = count_literals(path.read_text(encoding="utf-8"))
        if not count:
            continue
        counts[name] = count
        if name in ALLOWED:
            continue
        if name in DEBT:
            budget, reason = DEBT[name]
            if count > budget:
                problems.append(f"{name} 字面颜色 {count} 处，超过预算 {budget}（{reason}）")
            continue
        problems.append(f"{name} 出现 {count} 处字面颜色；要么改用 token，要么在 ALLOWED/DEBT 登记理由")
    for name, (budget, _reason) in DEBT.items():
        if name not in counts:
            problems.append(f"{name} 的债务登记已过期：文件不存在或已没有字面颜色，请删除该登记")
        elif counts[name] < budget:
            # 收敛是好事，提示更新预算，便于棘轮继续收紧。
            problems.append(f"{name} 字面颜色降到 {counts[name]} 处，请把预算从 {budget} 收紧")
    return problems, counts


def check_token_references(files: list[Path], root: Path) -> tuple[list[str], dict[str, list[str]]]:
    defined, referenced, runtime = collect_tokens(files)
    # Tokens assigned with setProperty on <html> are defined at runtime; several
    # of them (--tone-ink-*) deliberately have no static default.
    defined |= runtime
    undefined: dict[str, list[str]] = {}
    for path in files:
        name = relative(path, root)
        for token in sorted(set(TOKEN_REF.findall(path.read_text(encoding="utf-8")))):
            if token in defined or external_token(token):
                continue
            undefined.setdefault(name, []).append(token)
    problems: list[str] = []
    for name, tokens in sorted(undefined.items()):
        budget = UNDEFINED_TOKEN_DEBT.get(name, (0, ""))[0]
        if len(tokens) > budget:
            problems.append(f"{name} 引用未定义 token {tokens}，预算 {budget}")
    for name, (budget, _reason) in UNDEFINED_TOKEN_DEBT.items():
        actual = len(undefined.get(name, []))
        if actual == 0:
            problems.append(f"{name} 的未定义 token 债务已清零，请删除该登记")
        elif actual < budget:
            problems.append(f"{name} 未定义 token 降到 {actual} 个，请把预算从 {budget} 收紧")
    return problems, undefined


def check_token_parity(files: list[Path], root: Path) -> list[str]:
    """theme.ts 的运行时 token 与 global.scss 的颜色默认值必须同名同集。"""
    theme_path = root / "src" / "utils" / "theme.ts"
    style_path = root / "src" / "styles" / "global.scss"
    if not theme_path.is_file() or not style_path.is_file():
        return [f"缺少主题真源：{relative(theme_path, root)} / {relative(style_path, root)}"]
    runtime = set(TOKEN_SET.findall(theme_path.read_text(encoding="utf-8")))
    defaults = {
        token for token in TOKEN_DEF.findall(style_path.read_text(encoding="utf-8"))
        if not token.lower().startswith(NON_COLOR_PREFIXES)
    }
    derived = set(DERIVED_RUNTIME_TOKENS)
    problems: list[str] = []
    sources = [path.read_text(encoding="utf-8") for path in files]
    for token in sorted(derived):
        reference = f"var({token}"
        fallback = f"var({token},"
        if any(reference in source for source in sources) and not any(fallback in source for source in sources):
            problems.append(f"{token} 是派生 token，但没有任何 CSS 用 var({token}, …) 提供兜底")
    runtime -= derived
    defaults -= derived
    for token in sorted(runtime - defaults):
        problems.append(f"{token} 在 theme.ts 运行时赋值，但 global.scss 没有默认值（浅色/首帧会缺值）")
    for token in sorted(defaults - runtime):
        problems.append(f"{token} 在 global.scss 有默认值，但 theme.ts 从不按主题重新赋值")
    return problems


def run(root: Path = ROOT) -> int:
    files = source_files(root)
    literal_problems, counts = check_literal_colors(files, root)
    reference_problems, undefined = check_token_references(files, root)
    parity_problems = check_token_parity(files, root)
    allowed_files = sorted(name for name in ALLOWED if name in counts or (root / name).is_file())
    print(f"  扫描 {len(files)} 个文件；字面颜色 {sum(counts.values())} 处，分布在 {len(counts)} 个文件")
    print(f"  例外登记 {len(ALLOWED)} 个（真源/身份色/画布/首帧），债务登记 {len(DEBT)} 个文件")
    failed = False
    for label, problems in (
        ("字面颜色", literal_problems),
        ("token 引用", reference_problems),
        ("token 对账", parity_problems),
    ):
        if problems:
            failed = True
            print(f"  [FAIL] {label}（{len(problems)}）")
            for problem in problems[:12]:
                print(f"      {problem}")
            if len(problems) > 12:
                print(f"      …… 还有 {len(problems) - 12} 条")
        else:
            print(f"  [ok] {label}")
    if undefined and not reference_problems:
        print("  未定义 token：" + ", ".join(f"{name}({len(tokens)})" for name, tokens in sorted(undefined.items())))
    print(f"  例外文件：{', '.join(allowed_files)}")
    return 1 if failed else 0


if __name__ == "__main__":
    # 默认即严格；接受 --strict 只是为了与其它检查器调用风格一致。
    unknown = set(sys.argv[1:]) - {"--strict"}
    if unknown:
        print(f"  [FAIL] 未知参数：{sorted(unknown)}（只接受 --strict）")
        raise SystemExit(2)
    raise SystemExit(run())
