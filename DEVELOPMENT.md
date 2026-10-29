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
   通过 manifest + 下载器准备；模型目录必须和安装目录分离。

## 三套环境

| 环境 | 启动 | 数据 | WebView2 | 是否保留 |
| --- | --- | --- | --- | --- |
| 开发 | `.\scripts\dev.ps1` | `.dev-data/` | `.dev-webview/` | 可随时清 |
| 干净包验证 | `.\scripts\clean-test.ps1 -Exe <安装后的exe>` | `%TEMP%\MediaForge-clean-*` | 同临时目录 | 关闭即删 |
| 狗粮/长期用户 | `.\scripts\dogfood.ps1 -Exe <安装后的exe>` | 用户目录 `.mediaforge` | `%LOCALAPPDATA%\studio.mediaforge.desktop` | 故意保留 |

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
.\scripts\clean-test.ps1 -Exe "C:\Program Files\MediaForge\MediaForge.exe"
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
.\scripts\dogfood.ps1 -Exe "C:\Program Files\MediaForge\MediaForge.exe"
```

推荐放在**独立 Windows 本地账户或虚拟机**中，避免和开发账户互相影响。

升级测试流程：

1. 安装 v1
2. 修改设置、下载模型、导入素材
3. 直接覆盖安装 v2
4. 验证设置、模型、素材库、最近任务都在

只有明确重置狗粮环境时才删除：

```text
用户目录\.mediaforge
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
mcp__mediaforge__probe
mcp__mediaforge__convert
mcp__mediaforge__pipeline_plan
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
`python/operation_kernel.py`:

```text
agent-jobs/<jobId>.json
agent-jobs/index.json
agent-workspace.json
```

The app's `/agent` page reads the same index through the `agent_jobs` Tauri
command and polls every three seconds. This is the first shared surface:
manual users can observe tasks started by an MCP agent, inspect status and
progress, cancel a running job, and open the workspace. Bidirectional edits
go through the Operation Kernel phase.

The stdio and HTTP bridge modes write the same state, so DSH and the app see
the same task list when they use the same `MEDIAFORGE_DATA_DIR`.

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

## 打包前检查

- [ ] `data/` 不存在或已清理
- [ ] `.dev-data/`、`.dev-webview/` 不进入安装包
- [ ] `models/` 权重不进入安装包
- [ ] 没有测试日志、临时输出、预览截图进入包
- [ ] `pnpm build` 和 `pnpm tauri build` 通过
- [ ] 干净账户首次启动验证通过
- [ ] 狗粮账户覆盖升级验证通过
