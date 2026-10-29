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
- [x] 图像/视频模型目录：图像/视频模型清单、依赖检查、下载状态
- [ ] 音频模型与图像/视频模型合并为统一检索
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
├─ media_models.py  # 模型状态与下载
├─ model_worker.py  # 模型命令
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

### 图像/视频模型

页面：`#/media-models`

- 统一管理 Real-ESRGAN / LaMa / YOLO11x 四个图像/视频模型
- 检查 torch / spandrel / ultralytics / opencv / pillow / ffmpeg 依赖
- 显示已下载 / 未下载 / 文件不完整
- 应用内下载，进度实时显示
- 可切换模型目录，默认 `models/` 或 `MEDIAFORGE_MODEL_DIR`
- 图像/视频处理页头部可直接进入模型页

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
src/views/ImageView.vue
src/views/VideoView.vue
src/views/MediaModelsView.vue
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
