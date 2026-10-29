# MediaForge

本地优先的多模态媒体工作站：**音频处理 / 图像处理 / 视频处理 / FFmpeg 格式工厂**，共用模型库、任务队列和工作流。

> 早期版本。当前从 [Pymss Studio](https://github.com/pymss-project/pymss-studio) fork，保留其 Tauri + Vue 3 + Python worker 架构、音频模型库、任务系统和音频编辑器；新增媒体格式工厂与图像/视频处理入口。

## 当前进度

- [x] Fork、独立仓库、MediaForge 品牌壳
- [x] FFmpeg 媒体后端：`probe`、视频/音频/图片转码、提取音频、GIF
- [x] `/factory` 格式工厂页面：文件选择、输出格式、CRF/预设、进度、结果
- [x] 复用 PyMSS worker 事件协议（`audio_tool_progress` / `audio_tool_result`）
- [x] Tauri release 构建通过
- [x] 素材台：SQLite 索引、缩略图、导入文件/文件夹、搜索/筛选、拖拽导入、去格式工厂
- [x] 统一模型库：音频 326 + 图像/视频 8，统一检索、状态与下载
- [x] 音频模型与图像/视频模型合并为同一目录
- [x] 侧栏常驻运行状态：任务进度、队列、设备、最近失败
- [x] 图像超分/去水印：Real-ESRGAN + YOLO + LaMa，支持目标分辨率与前后对比
- [x] 视频抽帧修复管线（超分/去水印 + 音频回封）

## 已实现能力

### 格式工厂

页面：`#/factory`

- 输入：MP4 / MOV / MKV / AVI / WebM / MP3 / WAV / FLAC / M4A / AAC / OGG / JPG / PNG / WEBP / GIF
- 输出：MP4 / MKV / WebM / MP3 / WAV / FLAC / M4A / OGG / JPG / PNG / WEBP / GIF
- 视频：H.264 / VP9，CRF 与 preset
- 音频：MP3 / AAC / FLAC / WAV / Vorbis
- 图片：首帧导出、图片转视频（默认 3 秒）
- 进度、失败隔离、输出文件列表

后端：

```text
python/media_ops/
├─ ffmpeg.py        # ffprobe / ffmpeg 封装
├─ library.py       # SQLite 素材索引 + 缩略图
├─ image_ops.py     # Real-ESRGAN / YOLO / LaMa
├─ image_worker.py  # 图像任务命令
├─ video_ops.py     # 抽帧 / 逐帧修复 / 合帧
├─ video_worker.py  # 视频任务命令
├─ media_catalog.json  # 图像/视频模型清单
├─ audio_catalog.json  # 音频模型清单（pinned）
├─ model_hub.py      # 统一模型目录 / 状态 / 下载
├─ operation_kernel.py # 共享任务、取消、工作区
├─ agent_bridge.py   # MCP stdio / HTTP bridge
├─ model_hub_worker.py # 模型库命令
├─ worker.py        # worker 命令与事件
└─ __init__.py
```

### 素材台

页面：`#/assets`

- SQLite 索引（默认 `%APPDATA%/MediaForge/library.db`）
- 缩略图缓存（`%APPDATA%/MediaForge/thumbnails`）
- 导入文件/文件夹，支持拖拽
- 搜索名称/路径/标签，按图片/视频/音频筛选
- 一键「去格式工厂」带着素材跳转
- 移除只删索引，不删原文件
- 后端命令：`media_library`（`list` / `import` / `remove` / `tags` / `stats`）

### 图像处理

页面：`#/image`

- 三种模式：仅超分、仅去水印、去水印 + 超分
- Real-ESRGAN 2× / 4×，或 1080p / 2K / 4K / 自定义目标分辨率
- 适配方式：等比、留黑边、拉伸、裁剪填满
- YOLO11x 水印检测 + LaMa 修复，阈值/遮罩扩展可调
- 前后对比滑块、处理信息（输出尺寸、检测框、耗时、设备）
- 支持从素材台带图进入
- 模型目录可单独指定，留空使用 `models/` 或 `MEDIAFORGE_MODEL_DIR`

需要的权重：

```text
watermark_yolo11x.pt
big-lama.pt
RealESRGAN_x2plus.pth
RealESRGAN_x4plus.pth
```

### 视频处理

页面：`#/video`

- FFmpeg 抽帧 → 逐帧去水印/超分 → H.264 合帧 + 原音频回封
- 三种模式：仅超分、仅去水印、去水印 + 超分
- 固定遮罩（快）/ 逐帧检测（移动水印）
- 1080p / 2K / 4K / 自定义目标分辨率
- 支持只处理前 N 秒预览
- 输出 MP4，AAC 音频，保留原始帧率
- 支持从素材台带视频进入

### 模型库

页面：`#/models`

一个页面、一个目录、一个下载通道：

- 音频 326 个模型 + 图像/视频 8 个模型（共 334）
- 图像/视频：4 个已接入（Real-ESRGAN x2/x4、LaMa、YOLO）+ 4 个目录待接入（RIFE、ProPainter、BasicVSR++、GFPGAN）
- 视频 Tab 同时包含 4 个逐帧复用的图像模型和 3 个原生视频模型
- 按模态筛选：全部 / 音频 / 图像 / 视频
- 搜索名称、分类、架构；仅看已下载
- 统一显示下载状态、大小、许可证、运行依赖
- 音频走 pymss 源，图像/视频走各自官方源
- 浏览/状态/下载不需要 pymss 推理环境
- 运行依赖不满足时只看不跑；安装 runtime 后才能推理
- 旧音频高级管理页保留在 `/models/audio-legacy`

### Agent / MCP

本地 MCP bridge：`python/agent_bridge.py`

```powershell
.\scripts\start-mcp.ps1        # HTTP，127.0.0.1:1421
.\scripts\start-mcp.ps1 -Dev   # 使用 .dev-data
```

DSH 集成 bundle：`integrations/dsh-mediaforge`

安装后 DSH 可直接调用：

```text
mcp__mediaforge__probe
mcp__mediaforge__convert
mcp__mediaforge__pipeline_plan
mcp__mediaforge__job_status
mcp__mediaforge__models_list
mcp__mediaforge__models_download
```

MCP 只提供能力，不传递 DSH 模型或 API Key。模型网关和内部 Agent 是下一阶段。

协议冒烟测试（临时 profile，不碰用户数据）：

```powershell
.\scripts\smoke-mcp.ps1
```

页面：`#/agent`

- 读取 `agent-jobs/index.json` 和 `agent-workspace.json`
- 手动图像/视频/格式工厂任务也会写入同一索引
- 音频分离批次同样写入同一索引，可在 Agent 页观察和取消
- 手动用户可以看到 Agent 通过 MCP 启动的任务，也可以取消
- Agent 先查 `models_list`，计划缺模型时用 `models_download` 下载；下载前应由用户确认
- 手动任务从 Agent 页取消时会走 `cancel_task`，Agent 任务走 `cancel_agent_job`

### 运行状态

侧栏底部常驻状态胶囊：

- 空闲：绿点
- 运行中：蓝点 + 百分比 + 底部细进度线
- 排队：显示队列数量
- 失败：红色状态
- 点击展开：当前任务、阶段、设备、运行/队列/失败计数、最近错误

### 保留的 PyMSS 能力

- 音频分离、模型库、下载器（ModelScope / HuggingFace / hf-mirror）
- 任务队列、工作流、音轨编辑器
- 音频工具：转换、检测、响度、ASR、MIDI、切片、合并

## 模型下载

图像处理需要约 430MB 权重：

```bash
python download_models.py
# 国内网络
python download_models.py --proxy http://127.0.0.1:7897
```

下载到 `models/`。也可以在图像页选择其他模型目录。权重不随仓库分发，请遵守各自许可证。

## 三套运行环境

> 强制规则见 [DEVELOPMENT.md](DEVELOPMENT.md)。

| 环境 | 启动方式 | 数据 / 缓存 | 用途 |
| --- | --- | --- | --- |
| 开发 | `.\scripts\dev.ps1` | `.dev-data/`、`.dev-webview/` | 日常改代码、看实际效果 |
| 干净包验证 | `.\scripts\clean-test.ps1 -Exe "C:\Program Files\MediaForge\MediaForge.exe"` | 系统临时目录，关闭即删 | 验证“首次启动、无个性化配置”的发布包 |
| 狗粮 / 长期用户 | `.\scripts\dogfood.ps1 -Exe "C:\Program Files\MediaForge\MediaForge.exe"` | 用户目录 `.mediaforge` + 默认 WebView2 | 模拟真实用户持续使用与跨版本更新 |

> `src-tauri/target/release` 会被应用识别为开发构建。干净包验证和狗粮必须指向**安装后的 exe**，不能用 target 下的产物。

### 清理

```powershell
# 清开发 profile（推荐）
.\scripts\reset-dev.ps1

# 连旧的 data/ 一起清
.\scripts\reset-dev.ps1 -IncludeLegacyProjectData
```

- `clean-test.ps1` 的临时 profile 会在窗口关闭后自动删除
- `dogfood` 的数据**故意不清理**，否则无法测试升级/迁移
- 要重置狗粮环境：卸载应用后删除用户目录 `.mediaforge` 和 `%LOCALAPPDATA%\studio.mediaforge.desktop`
- 做跨版本升级测试时，建议用**独立 Windows 本地账户**或虚拟机跑狗粮，避免和开发账户互相影响

## 开发

前置：

- Node.js 24+、pnpm 10+
- Rust stable（Tauri 2）
- Windows：Visual Studio C++ Build Tools
- FFmpeg / ffprobe 在 `PATH`，或设置 `MEDIAFORGE_FFMPEG` / `MEDIAFORGE_FFPROBE`

```bash
pnpm install
pnpm dev          # 仅前端，端口 1420
pnpm tauri dev    # 完整桌面应用
pnpm build        # vue-tsc + vite build
pnpm tauri build --no-bundle
```

### 开发 profile 与用户数据隔离

开发时不要使用正式用户目录：

```powershell
# 隔离 profile 启动（数据在 .dev-data，WebView2 在 .dev-webview）
.\scripts\dev.ps1

# 清空开发 profile 后重新启动
.\scripts\dev.ps1 -Clean
```

隔离脚本会设置：

```text
MEDIAFORGE_DATA_ROOT
MEDIAFORGE_DATA_DIR
MEDIAFORGE_MODEL_DIR
WEBVIEW2_USER_DATA_FOLDER
```

规则：

- `data/`：旧开发数据，已 ignore
- `.dev-data/`：隔离开发数据，已 ignore
- `.dev-webview/`：隔离 WebView2 缓存，已 ignore
- 正式版数据：用户目录下的 `.mediaforge`
- 模型、日志、索引、下载记录都不进 Git
- `scripts/reset-dev.ps1` 只清理项目内开发目录，不碰正式用户数据

首次启动引导已移除；启动直接进入工作区。

测试时我使用隔离 profile 或临时 `MEDIAFORGE_DATA_ROOT`，不会把测试偏好写进正式配置。

后端单测方式：

```bash
cd python
python worker.py audio_tools --payload payload.json
```

## 目录

```text
src/                     Vue 3 前端
src/views/FactoryView.vue
src/views/AssetsView.vue
src/views/ImageView.vue
src/views/VideoView.vue
src/views/ModelHubView.vue
src/router/routes.ts
src/components/SideNav.vue
src/i18n/                zh-CN / en
python/                  Python worker
python/media_ops/        FFmpeg 媒体后端
python/worker_tools.py   media_* 命令路由
src-tauri/               Tauri 2 / Rust
```

## 许可证与上游

- 本项目沿用 **AGPL-3.0**。
- 基于 [pymss-project/pymss-studio](https://github.com/pymss-project/pymss-studio) fork，保留上游版权与许可证。
- 音频推理核心来自 [pymss-project/pymss](https://github.com/pymss-project/pymss)。
- 上游 README 与完整音频功能文档见上游仓库。
