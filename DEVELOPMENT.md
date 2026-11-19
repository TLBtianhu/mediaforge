# MediaForge 开发环境与数据隔离

这份文档是项目的**强制开发规则**，不是建议。新增功能、测试 UI、打包、验证升级时都必须遵守。

## 核心原则

1. **代码仓库不是运行时数据目录。**
   `.gitignore` 只是防止误提交，不能代替运行时隔离。
2. **一套用途，一套 profile。**
   开发、干净包验证、狗粮/长期用户必须使用不同的数据根和 WebView2 profile。
3. **绝不拿正式用户 profile 做开发测试。**
   测试偏好、下载记录、素材库、模型缓存都不能写进用户目录。
4. **绝不自动清理狗粮环境。**
   狗粮环境的价值就是保留长期配置和缓存，用于验证升级；只有明确要重置时才删除。
5. **`src-tauri/target/{debug,release}` 都是开发构建。**
   应用会把它们识别为 development executable；狗粮和干净包验证必须使用**安装后的 exe**。
6. **配置分四类：**
   - 设置：小JSON，带明确schema/version（`settings/app.json`当前`version=1`）；1.0前只接收当前格式，不写结构迁移
   - 模型/索引/缓存：可重建，放用户数据目录
   - 密钥：系统 keyring，不放 JSON
   - 日志/临时文件：可轮转、可清理
7. **模型权重不进仓库、不进安装包。**
   通过 manifest + 下载器准备；模型目录必须和程序本体 `app/` 分离。
8. **不向下兼容不等于清空用户文件。**
   遇到旧、缺失或未来schema必须报错并保留原文件，加载失败后不得把空默认值写回。
   删除历史兼容读取/迁移/双写分支；临时事务文件与UI内存投影不作为永久数据副本。
9. **Python目标唯一且强制一致。**
   `python/runtime-manifest.json`当前声明3.12。开发、bootstrap、独立推理资产和发布验证
   都核对实际major.minor；禁止跟随PATH复制3.11后只把清单写成3.12。
10. **清理优先保留开发便利。**
    用户要求保留能加速后续开发的构建缓存、依赖、模型、运行时和离线回归基线；
    不能仅因文件可重建或版本旧就自动删除。构建日志与一次性验证输出集中放`.tmp/`，不散落根目录。
    用户明确淘汰的Python3.11开发环境和旧3.11运行时包不在保留范围；当前只使用3.12。
    删除被工具策略拒绝的文件记为待删除，不得将其解释为当前开发所需。

## 三套环境

| 环境 | 启动 | 数据 | WebView2 | 是否保留 |
| --- | --- | --- | --- | --- |
| 开发 | `.\scripts\dev.ps1` | `.dev-data/` | `.dev-webview/` | 可随时清 |
| 干净包验证 | `.\scripts\clean-test.ps1 -Exe <安装后的exe>` | `%TEMP%\MediaForge-clean-*` | 同临时目录 | 关闭即删 |
| 狗粮/长期用户 | `.\scripts\dogfood.ps1 -Exe <安装后的exe>` | 安装根下的 `data/`、`models/`、`runtime-envs/` | `%LOCALAPPDATA%\studio.mediaforge.desktop` | 故意保留 |

安装版默认程序位于 `%LOCALAPPDATA%\Programs\MediaForge\app\MediaForge.exe`；
便携版的资产与 exe 同级。旧扁平安装的数据根属于历史布局，不作为新安装的默认位置。
显式指定 `MEDIAFORGE_DATA_ROOT` 后，未单独覆盖的模型/运行时都放在该根的子目录，
不会因为临时目录名为 `data` 就取其父目录。`MEDIAFORGE_MODEL_DIR` 和
`MEDIAFORGE_RUNTIME_ENVS_DIR` 可分别覆盖这两个资产目录。

### 开发

```powershell
.\scripts\dev.ps1
.\scripts\dev.ps1 -Clean
```

环境变量由脚本设置：

```text
MEDIAFORGE_DATA_ROOT
MEDIAFORGE_DATA_DIR
MEDIAFORGE_MODEL_DIR
WEBVIEW2_USER_DATA_FOLDER
MEDIAFORGE_RUNTIME_ENVS_DIR
```

开发数据在项目内，但已被 Git 忽略：

```text
.dev-data/
.dev-webview/
data/          # 历史开发产物，不作为当前默认数据根
```

清理：

```powershell
.\scripts\reset-dev.ps1
.\scripts\reset-dev.ps1 -IncludeLegacyProjectData
```

### 干净包验证

目标：验证用户首次安装时没有任何个性化状态。

1. `pnpm tauri build`
2. 在干净 Windows 账户或 VM 中安装
3. 运行：

```powershell
.\scripts\clean-test.ps1 -Exe "$env:LOCALAPPDATA\Programs\MediaForge\app\MediaForge.exe"
```

临时 profile 在窗口关闭后自动删除。若进程崩溃：

```powershell
Remove-Item "$env:TEMP\MediaForge-clean-*" -Recurse -Force
```

必须检查：

- 首次启动没有引导和旧配置
- 设置、素材库、模型目录为空
- 不读取 `.dev-data`
- 退出后临时目录消失

### 狗粮 / 长期用户

目标：模拟真实用户长期使用与跨版本更新。

```powershell
.\scripts\dogfood.ps1 -Exe "$env:LOCALAPPDATA\Programs\MediaForge\app\MediaForge.exe"
```

推荐放在**独立 Windows 本地账户或虚拟机**中，避免和开发账户互相影响。

升级测试流程：

1. 安装 v1
2. 修改设置、下载模型、导入素材
3. 直接覆盖安装 v2
4. 验证设置、模型、素材库、最近任务都在

只有明确重置狗粮环境时才删除：

```text
<安装根>\data
<安装根>\models
<安装根>\runtime-envs
%LOCALAPPDATA%\studio.mediaforge.desktop
```

## 端口与预览

音频节点工作流的持久格式为 Comfy `version: 0.4` 与六元组 `links`；外层 `workflow-state.version` 和条目 `formatVersion` 都独立为 `1`。LiteGraph 的内存图使用 `version: 1` 与对象链接，只通过适配器投影，不另存一份图。新建、保存、导出和导入必须使用同一持久格式；缺失或其它版本、旧数字键链接及旧控件布局报错并保留原文件，禁止修版号或迁移数据。

- 开发服务器固定 **1420**，`strictPort: true`
- `http://localhost:1420` 只适合看布局；浏览器没有 Tauri IPC/worker
- 实际功能用 Tauri 桌面窗口
- 打包后的应用**不监听 HTTP 端口**

本地媒体通过 `mediaforge-preview` 读取。原生文件选择、拖放后的输入扫描、当前任务账本、编辑工程和素材库只登记具体文件，登记在本次应用会话内有效。波形 JSON 须属于当前工程的派生缓存。Tauri 通用 `asset` 协议已关闭。插件视图使用独立的 `pymss-plugin` 协议，从已声明的视图入口所在目录和随包 SDK 加载资源，视图 CSP 不开放媒体预览协议。

## 插件市场

市场安装、更新和修复只接收 [随包目录](python/plugin-marketplace.json) 中的插件名。目录锁定官方仓库提交、ZIP 字节数与 SHA-256，以及每个插件目录的 SHA-256。文件树摘要是按相对路径排序的文件名与文件 SHA-256 的紧凑 UTF-8 JSON 对象的 SHA-256，路径分隔符为 `/`。更新目录时核对当前 pymss 插件协议，重新计算归档和文件树摘要，并通过隔离的安装与加载回归。

安装先验证来源和内容，再解析依赖或探测 Python 注册。读取已安装市场插件时重新核对随包内容锁与当前安装记录。旧安装记录、篡改文件和目录以外的包会被保留并显示错误。目录内的插件可用“修复”重新获取已批准的包。独立手动安装的 pymss 插件由外部 pymss 的手动插件机制管理。

## UI 测试与截图

Windows后台测试必须为每层非交互子进程设置不创建控制台窗口；捕获stdout/stderr并不能阻止弹窗。
Python用`CREATE_NO_WINDOW`，Node用`windowsHide: true`，Rust通过共享隐藏命令helper，
PowerShell后台`Start-Process`加`-WindowStyle Hidden`。子进程的子进程也要单独覆盖。
venv通过`shared.worker_subprocess.HiddenVenvBuilder`创建，专门让ensurepip及孙进程继承隐藏控制台；
其余普通子进程仍不创建控制台。
不要改用`pythonw.exe`或丢弃错误输出来隐藏问题。用户禁止桌面操作时只做文件/CLI检查，
不启动GUI、不操作或关闭现有测试窗口；显式GUI用例不能因此被当作已验收。

媒体流式子进程复用`media_ops.stream_process.StreamProcess`。正常结束调用`finish()`，
异常/取消在`finally`调用幂等`close()`，等待回收进程、关闭全部管道并结束stderr排空线程。
不能只kill后直接删除工作目录；stdout进度与stderr必须并行读取，避免日志写满管道后死锁。

- 自动化 UI 测试使用一次性 Playwright 上下文；关闭即丢弃
- 不把测试 localStorage 或测试 profile 写进真实 WebView2
- 每次 UI 改动后保留截图用于对比，截图目录不进 Git
- 测试必须使用隔离 profile 或临时数据根

前端完整测试默认限制为4个文件并发（`pnpm test`），避免多核机器同时
创建大量Vite/Node实例推高内存提交与Windows分页文件。不要为提速盲目恢复
按CPU核心数并发；修改并发策略后仍需核对完整测试数量与失败项。

### 应用图标

- 前端标准原图是 `src/assets/app-logo-dark.png` 与 `app-logo-light.png`，均为1024×1024 RGBA。
- `AppBrandMark` 根据实际生效的 `themeIsDark` 选择对应版本；标题栏、启动页、引导与关于页共用它。完整图标不再叠旧背景、内缩或颜色滤镜。
- 桌面应用、安装器与浏览器favicon固定使用深色版本。`src-tauri/icons/icon.png`保留1024深色标准图，其它PNG/ICO/ICNS由该深色图经项目已有Tauri CLI派生。
- 原始1254×1254用户素材保留在`src-tauri/icons/深色模式.png`与`浅色模式.png`。生成时先机械导出1024，不重绘图案；平台默认导出应先放临时目录，仅同步桌面所需文件，避免将无用移动端产物留在仓库。
- `tauri icon --png 1024`仅生成该尺寸PNG，不能代替默认整套ICO/ICNS生成。默认命令另生成512的`icon.png`，同步时须保留标准1024母版。
- 图标源变更会触发现有build.rs重编译。发布必须重新构建原生EXE与安装器；`-SkipBuild`会保留旧EXE内嵌图标。不要为图标切换改AppId、清用户profile或手动重置系统图标缓存。

## Agent / MCP bridge

`python/agent_bridge.py` is a dependency-free MCP bridge over the existing
worker protocol. It supports both transports:

```text
stdio   : DSH spawns the bridge as an MCP child process
HTTP    : http://127.0.0.1:1421/mcp with a pairing token
```

Start the HTTP bridge:

```powershell
.\scripts\start-mcp.ps1
.\scripts\start-mcp.ps1 -Dev
```

The HTTP bridge writes `mcp-bridge.json` (URL + bearer token) under the data
root. Never expose this port on a public interface.

DSH integration:

```text
integrations/dsh-mediaforge
```

Link the bundle from the selected installation or portable extraction with
`plugin_manager install_bundle target=link:<instance>\integrations\dsh-mediaforge`.
DSH loads its relative plugin module, which registers `@deepseek-ai/dsh-mcp-client`
with `serverName: mediaforge`. Once installed, DSH sees:

```text
mcp__mediaforge__mediaforge_status
mcp__mediaforge__models_list
mcp__mediaforge__models_download
mcp__mediaforge__capabilities_list
mcp__mediaforge__probe
mcp__mediaforge__convert
mcp__mediaforge__pipeline_plan
mcp__mediaforge__pipeline_start
mcp__mediaforge__job_status
mcp__mediaforge__job_cancel
mcp__mediaforge__feedback_submit
```

The linked module derives the bootstrap interpreter, bridge and explicit
data, model and runtime roots from its own bundle location. The installer
ships the bundle, and DSH owns its profile configuration.

### Shared job ledger

UI audio, media workers and MCP use the same current schema-2 files under the data root:

```text
agent-jobs/<jobId>.json
agent-jobs/.catalog.lock
agent-jobs/.owners/<jobId>.lock
agent-jobs/.writes/<jobId>.lock
```

There is no task-history/tasks.json writer, index snapshot or agent-workspace copy.
Readers validate the full current catalog and reference closure before recovery or deletion;
old/future versions, malformed JSON, duplicate keys and incomplete groups raise errors and
preserve existing files. They are never converted into an empty history.

Audio enqueue must be acknowledged durably before scheduling. The primary stores the shared
worker request and ordered member IDs once; each child stores only a payloadRef and its own
input/output/bindings. Starting work passes only jobId, and the backend claims the stored request.
Runtime stage/progress, nodes and structured logs are persisted by the backend observer; the
frontend derives task/result views, file lists and node-log groups in memory.

All directory membership operations and complete reads share .catalog.lock; owner leases and
per-job atomic writes use their separate locks. An ownerless queued job remains queued.
Rust and Python recover a typed owner only after its lease is released, including jobs left by
an exited bridge. PID alone does not establish liveness.

Queued audio cancellation affects the requested leaf. A cancelled primary remains the shared
request anchor for its siblings. Running batch cancellation follows the shared worker ID.
Removing audio records requires the complete group to be terminal and its owner leases free.
Results must call remove_audio_jobs(checkOnly=true) before recycling files or deleting editor
projects; the final metadata transaction follows those explicit user actions.

Full lists are authoritative, without an implicit 100-terminal cap. Kernel retention applies
to its Agent history; manual audio records remain until explicit removal. Reference groups
must not lose an anchor. The CLI transport payload.json is removed after its own worker is
reaped; diagnostics and any other workspace artifacts remain.

MCP stdio/HTTP and the UI share compute-worker admission when configured with the same data root.
`manifest/execution-quota.json` is the one contract for covered commands, the existing settings
field, default 1 and maximum 4 workers. Jobs remain ownerless queued until catalog → quota →
owner → write claim succeeds. A batch occupies one slot; query, download and runtime management
commands are not compute workers. Lowering the limit counts every occupied fixed slot, and
terminal output does not release capacity until the process is reaped.

The Rust dispatcher and Python Kernel still execute their own sources. Each has local FIFO;
this is shared cross-process admission, not global FIFO, a machine-wide GPU budget or a daemon
that takes over all sources. Generic manual queued jobs resume at native startup, audio after
frontend enqueue/start coordination, and Agent jobs when Kernel restarts. Audio/media/Agent result
removal shares the native reference checks and journal transaction. Project deletion also checks
all current jobs, other projects, models and the original library while holding the shared asset
lease; app-owned saves, recordings and waveform publication participate in that lease. See
[the job contract](docs/job-ledger-contract.md).

### Headless installer smoke

`scripts/smoke-installer.ps1 -Stage <stage> -Version <version> -Python <3.12-python> -Compiler <ISCC>`
uses the same installer source and stage to compile a unique test identity. Optional `-Installer`
is read-only hash metadata; the production installer is never executed by this smoke. The test
verifies the `install/app` layout, byte-matched staged files, bootstrap worker health, silent
uninstall and preservation of seeded assets. It does not launch the application GUI. An invalid
identity, timeout or production registry drift retains evidence instead of killing unknown
processes, restoring user registry values or deleting an unverified directory.

Inno 的第二阶段卸载进程在删除测试根前最多等待 30 秒；超时保留存活 PID，不终止进程。已经完成卸载、但因此保留的报告可用
`<Python 3.12> -I -B scripts/installer_smoke.py --cleanup-report <报告.json>` 恢复清理。该入口只复核测试归属、资产和生产注册表后删除对应 owned TEMP root，不重新安装或卸载；编译/安装失败和生产注册表漂移仍需人工检查。

All four Windows CI variants use this common action. Local regression uses fake compiler/
installer/worker processes in disposable profiles; editing this flow is not proof that real
Inno Setup, a production installer or remote CI has been executed successfully.

## Capability and model binding

`python/media_ops/capabilities.py` is the single description of what a media
operation can do:

```text
capability: upscale / watermark_removal / face_restoration / frame_interpolation ...
binding:    detector / inpainter / upscaler / interpolator ...
model:      media:lama / media:real-esrgan-x4 / media:watermark-yolo11x ...
```

Pipelines are ordered capability steps with explicit model bindings:

```json
{
  "capability": "watermark_removal",
  "bindings": {
    "detector": "media:watermark-yolo11x",
    "inpainter": "media:lama"
  },
  "params": { "conf": 0.25, "expand": 8 }
}
```

Image and video workers require explicit `steps`; the GUI, MCP and verification
CLI use the same current input shape. Old mode/modelId/factor inputs are rejected.
Image/video UI exposes
the model bindings directly through `ProcessingPlan`. `/media-workflow` composes
ordered media steps. `workflow_contract.py` declares serial support separately
from single-task support and exports it in `media_capabilities.workflow` per
capability and modality. The UI filters choices/params and respects single-step
sequence constraints; the worker validates the complete plan before model load
or output creation. RIFE uses its single-task entry, not the serial workflow.

Named media recipes live in `settings/media-workflows.json` (schema version 1),
separate from audio `workflow-state`. A recipe stores only modality, steps,
bindings and params; task paths and IDs are not persisted with it. Native writes
use `mutate_media_workflows` to serialize read/validate/revision-check/atomic-save
across windows. Existing recipes require their base `updatedAt` revision for
update/delete, and malformed or unknown-version state is preserved with an error.
Browser previews use the same pure mutation rules with an in-context write queue;
they do not claim cross-tab transactional storage.

## Task / Workflow boundary

- A task page runs exactly one capability with one primary model.
- Audio composition belongs to `/workflows`; media serial composition belongs
  to `/media-workflow`. Neither is offered as a task-page mode. Audio graph/simple
  definitions and media recipes must never be sent to each other's executors.
- `/queue` is the global shared task queue; `/results` is the global output/history
  surface. Audio's local queue modal is only a session shortcut and links to `/queue`.
- `/agent` is a compatibility redirect to `/queue`.
- Audio no longer exposes an inline workflow-mode switch; its header links to the
  global workflow page.

### Section hierarchy

- Input and output cards use `01` / `02`.
- The processing plan uses the square cube icon, not `03`, matching the audio
  stage head. The step strip above still communicates the 01/02/03 sequence.
- Cards share the rail-card visual tokens: 18px radius, 17/18px padding,
  `--surface-1`, inset outline, and `--shadow-panel`.

### Reusable workbench UI

- `src/components/media/WorkbenchCard.vue` owns the rail-card surface:
  18px radius, 17/18px padding, numbered/icon header, subtitle, actions slot.
- `src/components/media/WorkbenchSteps.vue` owns the 01/02/03 step strip.
- `src/components/media/ProcessingPlan.vue` owns capability, primary model,
  schema-driven params, and the no-model state.
- `src/features/media-ui/media-ui.css` owns the shared `.media-workbench` grid
  (420px rail, 16px gap, input/output left and plan right).
- Image, video, and format factory must use these pieces instead of adding
  page-local card/step CSS.

### Media workflow execution

- `/media-workflow` builds an ordered list of `ProcessingPlan` steps.
- Each step is one capability plus one primary model.
- `media_ops/workflow_worker.py` runs image steps in memory and video steps
  through intermediate files.
- MCP workflows use one OperationKernel job. The desktop page uses
  `start_audio_tool` and the Rust background supervisor, which writes a durable
  shared journal entry before execution. The resident media store recovers job
  snapshots across page changes; queue/results consume the shared journal.
  Audio/media/Agent use the same current job files and cross-process worker
  admission. Each executor still schedules its own source with local FIFO.
- MCP callers use `workflow_start`; single-task callers keep using
  `pipeline_start`.

## 打包前检查

本地候选默认仅打包bootstrap与程序资源，不重建数GB运行时：

```powershell
$env:CARGO_BUILD_JOBS = '4'
.\scripts\package-release.ps1 -Version 0.0.34 -SkipSign -SkipDownloadCopy -OutputDir release\candidates\0.0.34
```

`-SkipBuild`仍校验源EXE的ProductName/ProductVersion；三个源码版本与Cargo.lock须由
`scripts/sync-build-version.ps1`一起同步。程序打包不再兼做推理资产构建。
默认打包从当前源码运行官方Tauri构建，再检查EXE版本；各外部调用隐藏控制台并保留双流诊断与退出码。
前端构建入口统一为`scripts/build-frontend.mjs`，直接运行已安装的vue-tsc/Vite；
构建不隐式执行pnpm install，不清空或重建node_modules，缺依赖则报错要求显式准备。
本地候选的verification.json还须绑定干净源码提交、构建前后源码SHA与EXE/stage/归档全量哈希；
版本号一致本身不能证明-SkipBuild使用了最新源码。
`BootstrapDir`明确选择工作区内已准备好的3.12最小解释器，源或旧stage的实际版本不符就拒绝。
bootstrap准备仅接受全新目录，不自动删除旧内容。工作区当前`python-runtime`已是实际3.12.13；
若在`release/bootstrap-*`准备另一份新源，使用显式`BootstrapDir`选择，不能默默回退旧源。
更新包签名需要显式私钥路径。本机签名可用进程变量 `TAURI_SIGNING_PASSWORD_CREDENTIAL_TARGET` 指定
Windows 凭据管理器条目（按用途与公钥 ID 命名）。已有默认条目为 `MediaForge/TauriUpdateSigningPassword`。
签名进程的 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 可显式覆盖。签名脚本只把解析出的密码传给
签名子进程，`-SkipSign` 不读取签名凭据。禁止记录私钥或密码，避免在有签名环境变量时运行 signer help。

当前单stage候选用`scripts/smoke-installer.ps1`显式stage/版本/Python3.12/compiler重编独立AppId，
核对安装布局、全文件与bootstrap health后静默卸载，校验配置/模型/运行时样本保留。
正式setup只记录哈希，不执行；不启动应用GUI，拒绝清理仍有进程占用的测试目录。
不为旧格式开发迁移、备份/重建或兼容引导。不要拿正式AppId、用户profile或旧测试窗口代替该流程。

运行时CLI冒烟使用`make-fixture-runtime.ps1`的小型假解释器资产及本地HTTP，证明下载、
校验、解包、指针激活与回滚，不证明真实PyTorch或模型兼容。当前Tauri 2.11.2的三个
`--smoke-*`分支在`app.run()`之前退出，不创建配置WebView。普通exe启动会进入GUI；
用户禁止桌面操作时只允许已确认的headless分支，不调用ComputerUse或操控已有窗口。

独立Windows资产由`scripts/build-runtime-asset.py`构建：显式选择非venv的3.12宿主，
在`.dev-data/runtime-envs/<新assetId>`只创建一份自包含环境，再生成单个ZIP及hash描述符。
解释器、python312.dll、stdlib与cp312扩展必须同源；安装原样验证和发布，不修补旧venv。
Windows布局按[Python模块搜索路径规则](https://docs.python.org/3.12/library/sys_path_init.html)验证，
解释器实际prefix必须位于资产内，不能借用源安装或用户site-packages。

托管下载清单与签名版本文档使用 schema 3。运行时描述显式包含 `platform`、`arch`、`backend` 和 Python ABI。
当前托管 ZIP 对应 Windows x86_64 CUDA。Linux x86_64 的 CUDA 和 Windows/Linux x86_64 的 CPU、ROCm 使用各自的 worker 安装路径，macOS arm64 使用随包 MLX。发布签名版本文档时需用当前生成脚本生成 schema 3。

`pnpm test:python`从当前资产或显式`MEDIAFORGE_RUNTIME_PYTHON`选择解释器，先强制核对3.12，
再以一次性profile/cache运行测试；不下载依赖。签名测试依赖PyNaCl，仅安装在测试工具目录或CI，
不进入推理资产。旧格式拒绝与资产不删是两条规则；升级保留文件不代表应用会迁移其结构。

- [ ] `data/` 不存在或已清理
- [ ] `.dev-data/`、`.dev-webview/` 不进入安装包
- [ ] `models/` 权重不进入安装包
- [ ] 没有测试日志、临时输出、预览截图进入包
- [ ] `pnpm build` 和 `pnpm tauri build` 通过
- [ ] 干净账户首次启动验证通过
- [ ] 狗粮账户覆盖升级验证通过
