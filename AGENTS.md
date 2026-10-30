# AGENTS.md

Guidance for future OpenCode sessions working on pymss-desktop.

## 强制规则：环境与数据隔离

阅读 [DEVELOPMENT.md](DEVELOPMENT.md)。任何 UI 测试、后端测试、打包和升级验证都必须遵守：

- 开发只用 `scripts/dev.ps1`（`.dev-data` / `.dev-webview`），不要拿用户 profile 测试
- 干净包验证用 `scripts/clean-test.ps1`（临时 profile，关闭即删）
- 狗粮/长期用户用 `scripts/dogfood.ps1` 或安装后的应用（用户目录 `.mediaforge`）
- `src-tauri/target/{debug,release}` 是开发构建，不能当安装包或狗粮
- `.gitignore` 只是兜底；真正的隔离靠运行时数据根和 WebView2 profile
- 不自动清理狗粮数据；升级测试必须保留配置和模型
- 模型/日志/索引/测试截图不进仓库，也不进安装包

## Architecture

Tauri desktop app for audio source separation. Three layers:
- **Frontend**: Vue 3 + TypeScript + Vite + Naive UI (component auto-import)
- **Backend**: Tauri (Rust) orchestrates Python worker
- **Worker**: `python/worker.py` wraps the separate [pymss-project/pymss](https://github.com/pymss-project/pymss) library

The desktop app is a wrapper — core separation logic lives in the external pymss package.

## Development Commands

```bash
# Frontend only (port 1420)
pnpm dev

# Full app (Tauri + frontend hot reload)
pnpm tauri dev

# Typecheck + build frontend
pnpm build

# Production Tauri build (runs pnpm build first)
pnpm tauri build
```

Always run `pnpm build` before committing frontend changes to catch type errors.

## Python Worker Layout

`python/worker.py` imports the published `pymss` package from the active Python environment.
Install it for local development with `python -m pip install pymss`.

**Portable/release layout**:
```
<root>/python/worker.py
<root>/python-runtime/python.exe  (embedded interpreter)
```

## Release Process

Windows builds come in two variants: **CUDA** and **CPU** (different PyTorch builds).

Build stages:
1. **Prepare**: Run `scripts/prepare-python-runtime.ps1` to embed Python + deps → `python-runtime/`
2. **Build**: `pnpm tauri build --no-bundle` produces exe
3. **Stage**: Assemble portable directory with exe, worker, embedded Python runtime, and tools (ffmpeg, VC redist)
4. **Package**: Create 7z archive or Inno Setup installer

CI splits jobs: `prepare` → `7z` / `exe` for each variant to parallelize compression.

Key script: `scripts/prepare-python-runtime.ps1 -Variant cuda|default -TorchVersion ... -TorchIndexUrl ...`

Staged build verification runs `python worker.py env_info` and `list_models` to confirm Python environment.

## Release Artifacts

GitHub releases may split files >2GB using `split` (workflow auto-handles). See `SPLIT-ASSETS-README.txt` in releases for merge instructions.

## Component Auto-Import

`unplugin-vue-components` with `NaiveUiResolver` auto-imports Naive UI components. No manual imports needed for NButton, NCard, etc. Typings are disabled (`dts: false`) to avoid conflicts.

## Environment Variables

- `PYMSS_STUDIO_PYTHON`: Python interpreter path (defaults to `python`)
- `PYMSS_STUDIO_DEFAULT_OUTPUT_DIR`: Default separation output directory

## Important Files

- `python/worker.py`: JSON-based worker protocol for model ops, separation, audio processing
- `python/requirements.txt`: Documents runtime deps (actual deps installed by prepare script)
- `src-tauri/tauri.conf.json`: Bundles `python/*` as resources, defines asset protocol scope
- `scripts/prepare-python-runtime.ps1`: Stages portable Python with PyTorch + deps
- `.gitignore`: Excludes `*.md` and `docs/` — this file is an exception

## Testing

Automated tests are split by layer: `pnpm test` covers frontend logic and `pnpm test:python` covers Python Worker modules. Verification also includes manual checks and CI smoke tests (`env_info`, `list_models`).
