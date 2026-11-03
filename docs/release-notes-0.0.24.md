# MediaForge 0.0.24

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **ProPainter 视频修复**：支持固定 mask、逐帧 mask 目录和 `subvideoLength` 分段窗口。
- **媒体工作流 UI**：正确传递模型目录与 mask，修复多文件模型误报未下载的问题。
- **新应用图标**：统一标题栏、安装包、便携资源和集成图标。
- **运行时与版本同步**：使用 `runtime-cuda-2026.11.3`，HF 镜像与魔搭均可下载。
- **开发与 CI 稳定性**：修复编辑器滑块测试竞态，Rust/Pip 缓存缩短验证耗时。

## 签名与更新迁移

本版开始使用 MediaForge 专用 minisign 公钥验证版本清单和更新包。
仍在使用旧公钥的旧安装版，请先手动安装一次 0.0.24；之后的版本清单与更新包才会使用新密钥验证。

这里的 minisign 用于版本清单与更新包校验，**不是 Windows Authenticode 代码签名**。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.24-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.24-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.24-windows-x64-update.zip` |

发布时同步到 GitHub Release 与魔搭 `release/` 目录；运行时包继续由 HF 镜像与魔搭承载。

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.24-windows-x64-cuda-setup.exe` | 68062169 | `a35fe980f784538ba4d7e4da1d5c9162c4257ae0438ad84c44cded24dbf8c3d9` |
| `MediaForge-0.0.24-windows-x64-portable.zip` | 129155236 | `e3edb4b6a5c786ba48122a0b8c9146ec3ddfd1599200e31d1fb15091c046cd5f` |
| `MediaForge-0.0.24-windows-x64-update.zip` | 105367469 | `de269c05a65f33d2e4e8264e35bc579207c0602898a3350e3a671195822a11a9` |

更新包签名文件：`MediaForge-0.0.24-windows-x64-update.zip.sig`。

## 环境要求

- Windows 10/11 x64。
- 首次处理媒体任务时需要准备本地运行时；运行时独立于应用版本复用。
- NVIDIA 显卡可使用 CUDA；其他环境按应用支持情况选择 CPU 或对应后端。

## 验证范围

- 前端、Python、Rust 与综合 CI。
- 隔离首装、便携启动、安装升级和运行时回滚。
- ProPainter 动态 mask 与分段窗口的真实 staging 任务。
