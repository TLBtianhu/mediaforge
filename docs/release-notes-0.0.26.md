# MediaForge 0.0.26

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **修复音频处理页空白**：补齐 `makeOutputDirectory` 的 `computed` 依赖注入，音频 UI 不再在 setup 阶段报错。
- **单一安装根目录**：程序、配置、模型、运行时和 DSH 集成分别位于固定子目录。
- **自包含运行时**：`runtime-cuda-2026.11.4` 使用嵌入式 Python，不含 venv 启动器和 `pyvenv.cfg`。
- **分类卸载**：默认只卸载主程序；配置、模型和运行时只有显式勾选才清理。
- 保留 0.0.25 的 ProPainter、动态 mask、版本同步和 CI 缓存能力。

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
| Windows CUDA 安装包 | `MediaForge-0.0.26-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.26-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.26-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.26-windows-x64-cuda-setup.exe` | 68060234 | `46a9e14fd48c2f99149b1b6a65dc5778c25a4bef5b82587d88c07987e72f39e6` |
| `MediaForge-0.0.26-windows-x64-portable.zip` | 129159066 | `82cc35d82238befc1f74ec4e0ef7481b6297426aff6e0d471335eef2f96100ad` |
| `MediaForge-0.0.26-windows-x64-update.zip` | 105371313 | `ab44db66ba392c22198f326dab1765851944b52b93ac49dda3be123d35943f69` |

更新包签名文件：`MediaForge-0.0.26-windows-x64-update.zip.sig`。

## 环境要求

- Windows 10/11 x64。
- 首次处理媒体任务时需要准备本地运行时；运行时独立于应用版本复用。
- NVIDIA 显卡可使用 CUDA；其他环境按应用支持情况选择 CPU 或对应后端。

## 验证范围

- 音频 UI 依赖注入与完整前端测试。
- 真实 Inno A→B 升级、默认数据根与静默卸载。
- 真实首装、嵌入式解释器与综合 CI。
