# 架构蓝图（1.0.0 之前）

> 这份文件定义"最终状态"，以及到达它的分阶段路径。1.0.0 之前不做向下兼容：
> 数据结构变了就换格式，不写迁移；唯一要保证的是**协议自描述**和**资产可替换**。

## 1. 最终状态

### 1.1 四条真源，一份契约

```
manifest/
  protocol.json      命令表 + 事件表 + payload schema（唯一契约）
  capabilities.json  能力：角色、参数 schema、输出形态
  catalog.json       图像/视频模型：文件、体积、许可、URL、参数覆盖
  runtime.json       运行时：需要哪些能力、支持哪些 backend、依赖清单
```

启动时校验 `schemaVersion`：清单比程序新 → 程序明确报"需要更新应用"；程序比清单新 →
开发态重新生成。**不做隐式兼容**。

### 1.2 一个模型接入 = 改数据，不改代码

- 架构在 spandrel 里：catalog 加一条即可；
- 架构不在：写一个 loader 适配器并登记到加载器表；
- 两种情况都不碰 UI、不碰 MCP、不碰命令表。

### 1.3 三层代码，依赖严格单向

```
media_ops/       纯计算：算子、加载器、管线（不 import core/surfaces/shared 之外的层）
core/            状态与编排：素材索引、模型注册、运行时、作业、插件
surfaces/        适配：worker stdio、MCP、协议查表 dispatch
shared/          跨层原语：协议信封、路径、恢复、安全文件、代理
```

规则：`media_ops` ← `core` ← `surfaces`；`shared` 被所有层依赖但不依赖任何层。
反向 import 由 `scripts/check-architecture.py` 拒绝。
视频不是独立引擎，是 `media_ops` 里调用 ffmpeg 的管线。

### 1.4 事件与命令只有一套

事件统一为 `domain.phase`，总量控制在 ~12 个：

```
task.started / task.progress / task.log / task.result / task.failed / task.cancelled
asset.progress / asset.done / asset.failed
runtime.state / catalog.state / app.state
```

命令统一为 `domain + operation`，由 `protocol.json` 声明；不再有 48 个顶层命令名。
前端、测试、MCP 三边共用同一张表，不各写一套。

### 1.5 两条真实版本轴（catalog 随应用发布）

| 轴 | 标识 | 更新方式 |
|---|---|---|
| App | 语义版本 `x.y.z` | 整包安装 |
| Runtime | `runtime-<backend>-<assetId>`，内容寻址 | 整包下载到**并列目录**，切指针回滚 |
| ~~Catalog~~ | 已删除：catalog 随应用发布，没有独立更新通道 | — |

`versions.json`（签名）只做"比较 + 提示"，永远整包替换，不打补丁、不迁移。
catalog 轴在 0.0.23 之后被删除：它只有声明没有实现，留着就是假的三条轴。
运行时并列存放意味着回滚只是改一个指针，不需要"兼容旧运行时"。

### 1.6 音频是外部引擎

pymss 以外部包形式存在，通过 `core` 里的 audio 适配器接入同一套协议。
它的版本、依赖、许可证都不进入本站的构建主体，媒体侧可以独立重写。

## 2. 分阶段路径

每个阶段都能独立验收，结束时最后一位版本号 +1。

### P1 契约（0.0.17）

- 写 `manifest/protocol.json` 的 media 域（命令表 + 事件表 + schema）
- 旧的 `media_tool` 入口保留为适配层，内部转调新 dispatch
- 加 `scripts/check-architecture.py`：依赖方向 + 事件/命令必须在 protocol.json 里

验收：20 个媒体模型冒烟全过；新增一个模型只改 catalog；协议校验通过。

### P2 事件收敛（0.0.18）

- 媒体事件合并到 `task.*` / `asset.*`
- 前端改为按域订阅，删除字面量事件名
- 旧事件名保留一个版本的转发（P7 删除）

验收：前端订阅代码里只剩域级 listen；媒体任务进度/结果不断流。

### P3 边界（0.0.19）

- `library.py` / `registry.py` / `interface.py` 搬出 `media_ops`
- 引擎只留算子、加载器、管线
- 依赖方向检查纳入 CI

验收：`media_engine` 对 app 层零 import；冒烟与 UI 均正常。

### P4 运行时清单（0.0.20）

- `runtime.json` 声明 backend、Python/torch 版本、支持的能力
- 运行时并列目录 + `active-runtime.json` 指针
- App 在"缺少能力"时给出明确提示，而不是点击后报模块缺失

验收：两份运行时并存，切指针即回滚；缺 cv2 的运行时不会显示图像超分。

### P5 更新检测（0.0.21）

- `versions.json` 发布与签名
- App 比对三轴版本；只做整包下载与切换
- 明确"不做迁移"，数据格式变更直接拒绝并说明

验收：改 runtime id 后 App 提示更新运行时并完成切换。

### P6 MCP 生成（0.0.22）

- 工具列表 = capabilities × operations，从 manifest 生成
- `mcpSchemaVersion` 不匹配时返回可读错误

验收：catalog 新增一条，MCP 工具列表自动变化；`verify.ps1` 仍 PASS。

### P7 1.0 准备

- 删除所有兼容转发层
- 协议 schemaVersion 升到 2：handler 改为 `module:function`，dispatch 由协议查表生成
- 文档与发布清单对齐

## 3. 不变量（用脚本守住）

1. `media_ops` 不 import `core` / `surfaces` / `app_core` / `pymss` / `audio_tools`
2. `core` 不 import `surfaces`
3. `shared` 不 import 任何上层
4. `python/` 根目录只有 `worker.py`、`agent_bridge.py` 和数据文件
5. 引擎发出的每个事件名都在 `protocol.json.events.canonical` 里；旧别名已删除
6. 每个命令的 handler 都能解析到 `module:function`，且模块/函数真实存在
7. capabilities 引用的每个模型都在 catalog 里，且有 `size` / `url` / `licenseSpdx`
8. 发布物里没有机器相关的绝对路径；启动器由脚本生成

## 4. 明确不做

- 向下兼容与数据迁移（1.0 之前）
- delta 更新、自动升级包（只做检测 + 整包替换）
- Authenticode（锦上添花，不是门槛）
- 把音频引擎重写成自己的（保持外部，用适配器隔离）

## 5. 实现状态（2026-11-01，0.0.23）

| 阶段 | 状态 | 落地物 |
|---|---|---|
| P1 契约 | 完成 | `manifest/protocol.json`、`protocol_contract.py`、`media_tool`/`protocol_call` |
| P2 事件 | 完成 | 边界处映射为 `task.*`，前端按域判断 |
| P3 边界 | 完成 | `core/`（library/registry/interface 已并入），引擎零反向依赖，CI 检查 |
| P4 运行时 | 完成 | `manifest/runtime.json` 带能力/sha/urls，并列目录 + `active-runtime.json` |
| P5 更新 | 完成 | 签名 `versions.json`、`versions_check`、`versions_accept_runtime` |
| P6 工具面 | 完成 | 工具由能力生成，`mcpSchemaVersion` 校验，模型清单随 catalog 变 |
| P7 收尾 | 完成 | 规范事件唯一化 |

冻结项：

- `protocol.json` schemaVersion = 2（handler 为 module:function、协议查表 dispatch）；
  `runtime.json` schemaVersion = 1；不兼容变更走主版本号。
- 版本轴只有两条：app 与 runtime；catalog 随应用走，不再声明独立轴。
- 管道上只有 `task.*` / 资产事件；旧 `audio_tool_*` 别名已从代码、协议和测试里删除，`emit()` 不再做名字翻译。

仍然保留、明确不在本轮处理的：

- 音频引擎仍是外部 pymss 包，通过 `audio_tools` 适配器接入。
  - `worker_bootstrap.py` 现在是 232 行门面，实现整体在 `core/bootstrap_parts.py`；
    所有跨符号引用都改写为 `worker_bootstrap.<name>`，因此测试对门面打补丁仍然有效，
    下一步可以把 parts 按依赖层机械拆分而不用改测试。

## 6. 物理分层现状（0.0.23 之后）

```
python/
  worker.py            worker 入口（只解析参数 + 日志上下文）
  agent_bridge.py      MCP 入口
  shared/              跨层原语：协议信封、路径、恢复、安全文件、清单、代理
  core/                状态与编排：模型、运行时、下载、插件、作业、音频、库
  surfaces/            适配：agent_contract、embedded_loop、dispatch（协议查表）
  media_ops/           引擎：算子、加载器、管线
```

检查器守住八条：引擎方向、shared 方向、core 方向、根目录白名单、
事件覆盖、事件命名（拒绝 task_* / download_*）、handler 存在性、catalog 关联。worker 里不再有命令 if 链。

## 7. 巨文件拆分进度（0.0.23 之后）

| 文件 | 起点 | 现在 | 拆出的模块 |
|---|---|---|---|
| `commands/app_cmd.rs` | 3467 | 已删除 | `commands/app/` 下 13 个按域模块 |
| `commands/plugin_host.rs` | 2955 | 142（模块根） | 同目录 18 个子模块：process / session / state / live / request / scan_engine / scan / scan_pipeline / scan_control / render / blacklist / dialogs …，测试拆到 `tests.rs`（1220）与四个 `*_tests.rs` |
| `views/SeparateView.vue` | 4824 | 2479 | `SeparateView.css`、`separate/{format,useOutputNaming,useModelList,inferenceDefaults,playback,stemSelection,queue,outputDirectory}.ts` |
| `views/DebugView.vue` | 3457 | 1736 | `DebugView.css`、`debug_types.ts`、`debug/{useCatalogEditor,workerEvents,useUpdateEndpoint,executionLog,runtimeDebugFiles}.ts` |
| `views/ModelsView.vue` | 2838 | 2687 | `models/{storage,sorting,notes}.ts`，测试见 `tests/models{Storage,Sorting,Notes}.test.mjs` |
| `views/SettingsView.vue` | 3765 | 3189 | `settings/{runtimeState,runtimeActions,proxyTest,appearance,modelDirMigration}.ts`，测试见 `tests/settings{Runtime,Appearance,ModelDir}.test.mjs` |
| `python/core/worker_bootstrap.py` | 2430 | 337（门面） | 实现拆到 `core/bootstrap_parts/{base,primitives,operations,commands}.py`（589/555/509/1059），跨符号调用走门面 |

拆分边界由测试契约决定：早期测试按名字从 SFC 里取声明，被取用的声明只能留在顶层；
现在输出目录、队列、播放、词干选择、推理默认值、执行日志、运行时调试文件、设置页的
运行时派生状态与动作都已搬进 `makeX(deps)` 工厂，测试直接实例化工厂并注入桩依赖，
SFC 只剩装配与模板。设置页的五块逻辑已全部搬出（运行时、代理、外观、目录迁移），剩下的是模板与装配。

## 8. 验收清单（每批次都跑）

```powershell
# 前端
node --test --test-reporter=tap "tests/**/*.test.*"
node_modules\.bin\vue-tsc.CMD --noEmit
pnpm build

# Rust
cargo check --manifest-path src-tauri/Cargo.toml
cargo test  --manifest-path src-tauri/Cargo.toml --no-run

# Python
$py = "$env:USERPROFILE\.mediaforge\runtime-envs\cuda\Scripts\python.exe"
& $py -m unittest discover -s tests/python -t .
& $py scripts\check-architecture.py
& $py scripts\smoke-media-models.py
& $py scripts\smoke_mcp.py

# 分发表
& $py python\worker.py health
```

发布前额外跑：

```powershell
powershell -File scripts/smoke-installer.ps1 -Installer release/MediaForge-<ver>-windows-x64-cuda-setup.exe
powershell -File scripts/smoke-portable.ps1  -Zip       release/MediaForge-<ver>-windows-x64-cuda-portable.7z
```

`cargo test` 里与本机环境相关的失败（Windows junction、torch DLL、bash 脚本）在 CI 对应平台上验证；
本地验收以"失败集合与基线一致"为准。

## 9. 四步路线完成情况

| 路线 | 状态 | 证据 |
|---|---|---|
| (1) 物理归位 | 完成 | `python/` 根目录只剩 `worker.py`、`agent_bridge.py` 与数据文件；实现分布在 `shared/`、`core/`、`surfaces/`、`media_ops/`；检查器守着根目录白名单与三条依赖方向 |
| (2) 协议扩域 | 完成 | `protocol.json` 覆盖 system/media/audio/model/runtime/plugin/graph/workflow 共 48 条命令，handler 为 `module:function`；`surfaces/dispatch.py` 按协议查表，worker 的 if 链已删除；旧事件别名全部删除，引擎只发 `task.progress` / `task.result` |
| (3) 版本轴 | 完成 | 只保留 app 与 runtime 两轴；catalog 轴从 `versions.json`、Rust 文档与文档中删除；设置页有检查/切换卡；`runtime.json` 只由 Rust 解析并经 `MEDIAFORGE_RUNTIME_ASSET` 传给 worker，Python 不再读 `active-runtime.json` |
| (4) 巨文件与 CI | 完成 | `app_cmd.rs` 删除；`worker_bootstrap` 337 行门面 + 四层实现；`plugin_host` 142 行模块根 + 18 个子模块 + 独立测试；两个 Vue 文件分别 4824→2479、3457→1736；CI 只在 PR/手动触发；安装版与便携版冒烟接进发布流水线 |

明确保留的残留工作：

- Vue 测试已全部改为直接调用工厂（`makeInferenceDefaults` / `makePlayback` / `makeStemSelection` /
  `makeQueue` / `makeExecutionLog` / `makeOutputDirectory` / `makeRuntimeDebugFiles`），
  不再从 SFC 按名字取声明；这条拆分债清空。
- 分发侧：`versions.json` 仍是占位 URL，运行时 3.45 GB 的首次下载与签名链路还没在真机上验证过；
  便携包/安装包需要按 `mediaforge.portable` 标记重新打包后再发。
- `plugin_host` 的 97/98 本地失败来自 Windows junction 的 `0xC0000142` 环境问题，
  CI 对应平台通过；`worker_bootstrap` 的 40 个组合错误在单文件基线上同样复现（torch DLL）。

验证口径：本地按 §8 清单跑，CI 的 `validate` 与 `Runtime safety` 覆盖全量测试；
Windows 本地环境特有的 junction / torch DLL / bash 用例不计入回归。
