# MediaForge

本地优先的多模态媒体工作站：**音频分离 / 图像修复 / 视频处理 / FFmpeg 格式工厂**，共用模型库、任务队列和工作流。

> 早期版本。当前从 [Pymss Studio](https://github.com/pymss-project/pymss-studio) fork，保留其 Tauri + Vue 3 + Python worker 架构、音频模型库、任务系统和音频编辑器；新增媒体格式工厂与图像/视频处理入口。

## 当前进度

- [x] Fork、独立仓库、MediaForge 品牌壳
- [x] FFmpeg 媒体后端：`probe`、视频/音频/图片转码、提取音频、GIF
- [x] `/factory` 格式工厂页面：文件选择、输出格式、CRF/预设、进度、结果
- [x] 复用 PyMSS worker 事件协议（`audio_tool_progress` / `audio_tool_result`）
- [x] Tauri release 构建通过
- [x] 素材台：SQLite 索引、缩略图、导入文件/文件夹、搜索/筛选、拖拽导入、去格式工厂
- [ ] 模型目录 `modality` 泛化（audio / image / video）
- [ ] 图像超分/去水印适配器（Real-ESRGAN / LaMa）
- [ ] 视频抽帧修复管线（超分/去水印 + 音频回封）

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
├─ ffmpeg.py      # ffprobe / ffmpeg 封装
├─ library.py     # SQLite 素材索引 + 缩略图
├─ worker.py      # worker 命令与事件
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

### 保留的 PyMSS 能力

- 音频分离、模型库、下载器（ModelScope / HuggingFace / hf-mirror）
- 任务队列、工作流、音轨编辑器
- 音频工具：转换、检测、响度、ASR、MIDI、切片、合并

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
