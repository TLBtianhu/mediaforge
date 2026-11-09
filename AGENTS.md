# AGENTS.md

Guidance for future OpenCode sessions working on pymss-desktop.

## 当前交接（2026-11-08）

- DeepSeek Harness/后续 Agent 先读 [docs/handoff-2026-11-08.md](docs/handoff-2026-11-08.md)，其中记录当前修复、测试证据和下一步验收顺序。
- 2026-11-09 的协议/运行时收敛已提交（`9c7fc32` 起，含 `f86042c`）；工作区应保持干净。接手先检查 `git status`/`git log`，不得用 reset、clean 或旧包覆盖工作区。

## 强制规则：环境与数据隔离

阅读 [DEVELOPMENT.md](DEVELOPMENT.md)。任何 UI 测试、后端测试、打包和升级验证都必须遵守：

- 开发只用 `scripts/dev.ps1`（`.dev-data` / `.dev-webview`），不要拿用户 profile 测试
- 干净包验证用 `scripts/clean-test.ps1`（临时 profile，关闭即删）
- 狗粮/长期用户用 `scripts/dogfood.ps1` 或安装后的应用（用户目录 `.mediaforge`）
- `src-tauri/target/{debug,release}` 是开发构建，不能当安装包或狗粮
- `.gitignore` 只是兜底；真正的隔离靠运行时数据根和 WebView2 profile
- 不自动清理狗粮数据；升级测试必须保留配置和模型
- 模型/日志/索引/测试截图不进仓库，也不进安装包
- 用户优先保留开发便利：不为省空间自动清空Rust构建缓存、node_modules、运行时、模型或仍有回归用途的旧包。
- 上述保留只针对实际需要的资产；用户已明确淘汰旧Python3.11开发环境与旧3.11运行时包，不得再以假设的旧版回归用途保留。当前开发/运行唯一目标为3.12；删除受工具策略限制时如实记录待删除，不称为开发依赖或已清理。
- 构建日志和一次性验证输出统一放`.tmp/`，不要在项目根新增编号日志；整理旧文件先核对实际用途与引用。

## 强制规则：1.0前当前格式与Python版本

- 1.0之前不做向下兼容：结构变了直接换格式，不写历史数据迁移、旧入口回退或双写副本；不为旧格式另做专用备份、重建或兼容引导流程。
- 只接受当前schema；旧、缺失或未来schema明确报错并保留原文件，不能把加载失败当空数据写回。
- 同类持久数据只有一个真源。任务以单job文件为准，列表在读取时聚合；UI内存投影不另存副本。
- 任务当前格式为schemaVersion=2，音频排队/媒体/Agent共用`agent-jobs/<jobId>.json`，禁止恢复`task-history`读写。
- 音频先持久入队确认，再仅按jobId领取执行；共享图/参数仅存primary，child保存引用与输入差异，前端不回写生命周期。
- 目录成员与引用读取使用共享catalog锁；整组删除须全终态且owner lease释放，资产清理前先预检。
- 音频/媒体/Agent结果删除共用后端保护与事务，只传jobId及选择，后端按当前命令结果契约推导文件；音频须展开整组。默认仅删记录并保留关联编辑工程。文件只回收普通文件，不递归删除输出目录；输入、其它任务/编辑器引用、模型与运行时须保护。输入位置或媒体库引用未能完整确认、编辑器打开时保留文件；回收失败保留本次全部任务记录。
- 结果展示从全任务组派生，包含无文件终态与失败/取消部分产物；不能只按成功leaf重组而伪报整组完成。编辑器导出唯一文件字段为result.path，不复制outputs。孤立编辑工程须由后端完整catalog确认，不能按结果页可见ID判断；显式选择清理还须复核当前job/导出/明确绝对路径引用。
- 素材库唯一真源是dataRoot/library.db，当前格式由manifest/library-schema.json声明schema1/MFLB/DELETE。旧0、坏库或额外对象/坏行明确拒绝并保原件，不迁移、修库或建引用副本。所有library入口从扫描/缩略图到commit/close持.asset-references.lock；Windows原生只读全表引用且持lease至回收结束，锁序catalog→owner/write→JSON→model→asset，库writer仅asset→SQLite。无库也持lease防首次导入竞态；WAL/SHM/journal/忙/未知库不当空库，非Windows已有库暂保守拒绝。Bridge/native必须强制显式数据根，不改全局env；缩略图只清确切自有缓存。
- 三种工程删除入口共用完整catalog、全部当前工程、模型registry和素材库引用检查；递归删除前预检整批目录及全部后代，任何引用、未知相对路径或reparse均不猜归属。任一编辑器打开时整批保留；工程自己的引用不阻止显式删除，但其它工程即使同批选中也须保护。工程保存按JSON→asset持锁到atomic replace，不能新添已删除的内部文件引用；已存缺失引用仍可保留重链。保存sources与导出assets分别校验当前v4契约，禁止双写字段迎合检查。
- 录音与波形缓存写入也须持asset lease，获锁后复核当前工程存在/id/schema和路径，不能删除后重建目录。原生波形入口只传内部数据根，不提前mkdir或持asset等待同样拿锁的worker；派生缓存文件本身也须拒reparse。便携版程序根包含data不等于保护整个data：工程删除只排除宽泛程序祖先，模型/运行时根、实际EXE及工程内程序资源始终保护。
- 自定义模型registry唯一位置为dataRoot/settings/user_models.json（pymss当前version1）；四种修改入口从验证/复制至commit/rollback共用asset lease，坏/旧/未来/相对路径registry保原件。Native、Kernel及worker CLI绑定同一根；inline查询用显式请求scope，不临时改线程共享env。第三方pymss CLI未参与本应用锁协议，不宣称覆盖任意外部修改。
- manual历史保留到显式删除，不套用Kernel默认Agent保留策略；仅清当前自建worker回收后的临时请求正文。
- UI/媒体/MCP处理任务按`manifest/execution-quota.json`共享同数据根的worker配额，配置只读`settings/app.json`的现有并发字段；排队不占owner/slot，领取顺序为catalog→quota→owner→write，进程确认回收后才释放。
- 不恢复独立前端并发计数；批次按一个worker计数，查询/下载/运行时管理不占处理配额。当前仅各执行器内部FIFO，不宣称跨进程全局FIFO或物理GPU分区。
- Python版本唯一目标来自`python/runtime-manifest.json`，当前为3.12；开发、bootstrap及独立资产均须实测一致。
- 自包含运行时必须自带匹配解释器、DLL、stdlib和扩展。安装只验证/发布，不覆盖解释器修旧venv。

## 强制规则：Windows后台测试不弹命令行窗口

- 非交互测试、worker、FFmpeg、环境探针、下载与打包子进程必须隐藏控制台，不能仅靠重定向输出。
- Python使用`CREATE_NO_WINDOW`，Node使用`windowsHide: true`，Rust复用隐藏命令helper；PowerShell后台`Start-Process`使用`-WindowStyle Hidden`。
- 创建venv使用`HiddenVenvBuilder`：让ensurepip及孙进程继承隐藏控制台，不能只隐藏第一层进程。
- 保留stdout/stderr日志、退出码、超时和取消能力，不用`pythonw.exe`丢弃诊断，也不全局monkeypatch subprocess。
- 用户明确禁止ComputerUse/桌面操控；不为验收弹窗而启动GUI、操作现有窗口或强杀旧测试实例。
- 安装验收用`smoke-installer.ps1`显式stage/版本/Python3.12/编译器，重编唯一测试身份，只跑bootstrap health；正式安装包只读记录哈希，不拿生产AppId覆盖用户安装来冒烟。
- Inno `[Files]`的可变资产排除须从包根锚定，不能以任意层级`models/data`排除合法Python依赖代码；安装验收逐文件比对完整stage，不能只验EXE/health或关键文件抽样。

## 强制规则：DSH 插件集成（2026-11-09 事故后）

MediaForge 曾把 MCP 配置注入 DSH 的 profile `cordis.patch.yml`，而 DSH 设置页会把新行追加到文件末尾注释之前——用户的 web_search 配置因此落进注入区块，下一次安装器整块替换就会删除它。差点发生，已迁移到 bundle。规则：

- **只走 bundle**：`integrations/dsh-mediaforge` 是合法 bundle（`dsh.bundle.patch`）；安装用 `plugin_manager install_bundle target=link:<安装目录>\integrations\dsh-mediaforge`。安装器不得写 profile 的 `cordis.patch.yml`。
- **安装器只拥有自己的行/文件**：任何"从我的标记到我的标记之间都归我"的设计，在用户会持续编辑的共享配置里迟早会吃掉用户的东西。
- **禁止整文件重写**：不做 `yaml.load`→`yaml.dump` 全量回写；禁止把 `BEGIN/END` 区块留在文件末尾。
- **写入必须可逆**：先备份（保留最近 5 份）、锁/原子替换；区块内有外来行时拒绝并报告，不得静默删除。
- **幂等可验证**：连跑安装器/迁移脚本 3 次，`cordis.patch.yml` 的 SHA-256 必须不变。
- 迁移工具与回归测试：`integrations/dsh-mediaforge/register.ps1`（默认只读，`-RemoveLegacyBlock` 才写）与 `tests/python/test_dsh_integration_layout.py`；安全规范总纲见 `C:\Users\30680\Documents\deepseek-harness\default-workspace\DSH-插件接入安全规范-2026-11-09.md`。

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
1. **Prepare**: Run `scripts/prepare-python-runtime.ps1 -Minimal -Python <explicit-3.12-python>` → `python-runtime/`; version mismatch must fail before replacing any output.
2. **Build**: `pnpm tauri build --no-bundle` produces exe
3. **Stage**: Assemble portable directory with exe, worker, embedded Python runtime, and tools (ffmpeg, VC redist)
4. **Package**: Create 7z archive or Inno Setup installer

CI splits jobs: `prepare` → `7z` / `exe` for each variant to parallelize compression.

Inference assets are built separately with `scripts/build-runtime-asset.py`, using an explicit non-venv Python 3.12 host and a fresh asset ID. It stages one self-contained runtime under `.dev-data/runtime-envs/`, installs the locked backend without persistent download caches, and produces one ZIP plus its hash descriptor under `release/runtime/`. Existing runtime assets are never rewritten.

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
- `python/requirements.txt`: Documents runtime deps; inference assets are built separately from the minimal bootstrap
- `src-tauri/tauri.conf.json`: Bundles `python/*` as resources, defines asset protocol scope
- `scripts/prepare-python-runtime.ps1`: Prepares a fresh minimal Python 3.12 bootstrap; `scripts/build-runtime-asset.py` builds the independent inference asset
- `.gitignore`: Excludes `*.md` and `docs/` — this file is an exception

## Testing

Automated tests are split by layer: `pnpm test` covers frontend logic and `pnpm test:python` covers Python Worker modules. Verification also includes manual checks and CI smoke tests (`env_info`, `list_models`).
