# MediaForge 0.0.25

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **单一安装根目录**：程序、配置、模型、运行时和 DSH 集成分别位于固定子目录，不再散落管理。
- **默认用户级安装**：默认不需要管理员权限，也允许选择其他可写安装目录。
- **分类卸载**：卸载程序默认只删除主程序；独立运行时、模型和配置历史必须显式勾选才会清理。
- **DSH 集成解耦**：集成桥随包提供，由 DSH 插件管理器加载，不再由安装器直接修改 DSH 配置。
- **自包含运行时**：`runtime-cuda-2026.11.4` 使用嵌入式 Python，不含 venv 启动器和 `pyvenv.cfg`，不再依赖旧安装路径。
- 保留 0.0.24 的 ProPainter 视频修复、动态 mask、新图标、版本同步和 CI 缓存能力。

## 目录结构

```text
MediaForge/
  app/                 主程序与基础资源
  data/                配置、日志、任务历史
  models/              模型资源
  runtime-envs/        独立运行时
  integrations/        DSH 适配文件
```

旧版扁平安装的数据结构不会自动迁移到新根目录，按新结构重新初始化；旧数据目录由用户自行决定是否保留或清理。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.25-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.25-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.25-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

- GitHub Release：https://github.com/TLBtianhu/mediaforge/releases/tag/v0.0.25
- 魔搭安装包直链：https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.25-windows-x64-cuda-setup.exe

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.25-windows-x64-cuda-setup.exe` | 68053573 | `cd02bbffc807ea1eddd089491090714555723fe1574fdc1b7de51a352473efab` |
| `MediaForge-0.0.25-windows-x64-portable.zip` | 129158140 | `3a4d376e0e7010f0e8d1bb37f165d4100dd9fe764b2df0a4382a9f4c16b1caab` |
| `MediaForge-0.0.25-windows-x64-update.zip` | 105370387 | `0be2ef377a02e814d4c4eae6fa73ae4821723b0093e6f27fc4b48c3093c38d2f` |

更新包签名文件：`MediaForge-0.0.25-windows-x64-update.zip.sig`。

## 环境要求

- Windows 10/11 x64。
- 首次处理媒体任务时需要准备本地运行时；运行时独立于应用版本复用。
- NVIDIA 显卡可使用 CUDA；其他环境按应用支持情况选择 CPU 或对应后端。

## 验证范围

- 安装根目录与分类卸载契约测试。
- 真实 Inno A→B 升级与静默卸载，保留配置、模型和运行时。
- 便携启动、真实首装和综合 CI。
