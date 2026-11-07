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
   - 设置：小 JSON，带 schema/version，支持迁移
   - 模型/索引/缓存：可重建，放用户数据目录
   - 密钥：系统 keyring，不放 JSON
   - 日志/临时文件：可轮转、可清理
7. **模型权重不进仓库、不进安装包。**
   通过 manifest + 下载器准备；模型目录必须和程序本体 `app/` 分离。

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
data/          # 旧开发数据，仅兼容
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

- 开发服务器固定 **1420**，`strictPort: true`
- `http://localhost:1420` 只适合看布局；浏览器没有 Tauri IPC/worker
- 实际功能用 Tauri 桌面窗口
- 打包后的应用**不监听 HTTP 端口**

## UI 测试与截图

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

It is a configuration-only bundle that registers `@deepseek-ai/dsh-mcp-client`
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

The bundle uses the stdio launcher:

```text
scripts/mediaforge-mcp.cmd
```

Adjust `command`, `cwd`, and `MEDIAFORGE_DATA_DIR` in
`integrations/dsh-mediaforge/cordis.patch.yml` when the project is not at
`C:\MediaForge`.

### Shared agent workspace

The bridge writes durable task state under the data root through
`python/core/operation_kernel.py`:

```text
agent-jobs/<jobId>.json
agent-jobs/index.json
agent-workspace.json
```

Individual `agent-jobs/<jobId>.json` files are authoritative. `index.json` is
a compatibility snapshot from the Python bridge; `agent_jobs` reads the job
files and recovers interrupted Rust-owned work only after its owner lease is
released. Rust records manual worker tasks before spawning, saves progress
and terminal results atomically, and retains partial outputs on failure or
cancellation. Batch children retain their own input and shared parameters;
only the parent duplicates the complete batch input list.

`stores/mediaJobs.ts` owns the media event subscription for the app lifetime.
Image, video, format conversion and media workflow pages restore their latest
submitted snapshot when mounted again. `/queue` merges the journal with audio
store tasks, including audio work still waiting for the scheduler, and refreshes
external jobs every ten seconds while visible. Audio cancellation goes through
the audio store scheduler; other manual tasks use `cancel_task` and agent jobs
use `cancel_agent_job`.

Old ownerless manual `running` records have unknown liveness. They remain in
the queue for diagnosis but do not lock a media page or block a new submission.
Only a current-session submission or a backend-owned lease record restores
an active media page. Terminal legacy results can still be restored.

Results include a separate read-only media/tool/Agent file area alongside the
existing audio area. Manual audio journal mirrors are excluded so clearing audio
history cannot resurrect it. The physical audio history and journal remain
separate; media result deletion and a shared cross-process scheduler are not
implemented. `journal_job` remains a guarded compatibility path for older tool
components; the four media pages no longer write their own lifecycle records.

The stdio and HTTP bridge modes write the same state, so DSH and the app see
the same task list when they use the same `MEDIAFORGE_DATA_DIR`.

Smoke test (temporary profile, no user data):

```powershell
.\scripts\smoke-mcp.ps1
```

Current limitations, intentionally explicit:

- The stdio bridge is a separate headless worker process. It shares the data
  root and models, but not the live Tauri job queue.
- `pipeline_plan` is read-only; image/video execution still goes through the
  existing app commands until the Operation Kernel refactor lands.
- No DSH model bridge yet. MCP is used for capabilities only, not for
  inheriting DSH login/model selection.

Next phases:

1. Extract the shared Operation Kernel so UI, MCP, and the internal agent use
   one implementation.
2. Add a DSH Host plugin model gateway that streams from `ctx.llm` using
   `agentDefaultModel`; no second API key.
3. Add the internal agent loop and the structured harness feedback workflow.

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

The legacy `mode` names remain as compatibility presets, but new callers should
use steps. Image and video workers both accept `steps`; image/video UI exposes
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
  Audio history remains physically separate and cross-process scheduling is
  not yet unified.
- MCP callers use `workflow_start`; single-task callers keep using
  `pipeline_start`.

## 打包前检查

本地候选默认仅打包bootstrap与程序资源，不重建数GB运行时：

```powershell
$env:CARGO_BUILD_JOBS = '4'
pnpm tauri build --no-bundle --ci
.\scripts\package-release.ps1 -Version 0.0.32 -SkipBuild -SkipSign -SkipDownloadCopy -OutputDir release\candidates\0.0.32
```

`-SkipBuild`仍校验源EXE的ProductName/ProductVersion；三个源码版本与Cargo.lock须由
`scripts/sync-build-version.ps1`一起同步。运行时资产只有显式`-BuildRuntimeAsset`才打包。
签名仅使用显式路径/进程环境，`-SkipSign`不读取签名凭据；不要在有签名环境变量时
运行signer的help，某些CLI版本可能把环境默认值打印到终端。禁止记录私钥或密码。

`scripts/smoke-release-candidate.py`以独立AppId和临时安装目录验证安装、升级与静默
卸载，保留配置/模型/运行时样本并校验哈希。它不会启动应用，拒绝清理仍有进程占用的
测试目录。不要拿正式AppId、用户profile或当前运行的测试窗口代替该流程。

运行时CLI冒烟使用`make-fixture-runtime.ps1`的小型假解释器资产及本地HTTP，证明下载、
校验、解包、指针激活与回滚，不证明真实PyTorch或模型兼容。当前Tauri 2.11.2的三个
`--smoke-*`分支在`app.run()`之前退出，不创建配置WebView。普通exe启动会进入GUI；
用户禁止桌面操作时只允许已确认的headless分支，不调用ComputerUse或操控已有窗口。

- [ ] `data/` 不存在或已清理
- [ ] `.dev-data/`、`.dev-webview/` 不进入安装包
- [ ] `models/` 权重不进入安装包
- [ ] 没有测试日志、临时输出、预览截图进入包
- [ ] `pnpm build` 和 `pnpm tauri build` 通过
- [ ] 干净账户首次启动验证通过
- [ ] 狗粮账户覆盖升级验证通过
