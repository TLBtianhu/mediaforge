# MediaForge 0.0.27

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **卸载选项修复**：独立运行时、模型和配置历史改用三个独立复选框，避免卸载列表越界与“只能全卸”的问题。
- **音频处理页**：补齐 `makeOutputDirectory` 的 `computed` 依赖注入，音频 UI 正常显示。
- **自包含运行时**：`runtime-cuda-2026.11.4` 使用嵌入式 Python，不含 venv 启动器和 `pyvenv.cfg`。
- **后台准备**：运行时下载和解释器自检不再弹出控制台窗口。
- 保留单一安装根目录、分类卸载、ProPainter 和动态 mask 能力。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.27-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.27-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.27-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

- GitHub Release：https://github.com/TLBtianhu/mediaforge/releases/tag/v0.0.27
- 魔搭安装包直链：https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.27-windows-x64-cuda-setup.exe

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.27-windows-x64-cuda-setup.exe` | 68053883 | `3b7e8fdc33d3001acccff7f74e9d903e61a4fe5f44342e5431ba49861e9fa002` |
| `MediaForge-0.0.27-windows-x64-portable.zip` | 129158941 | `9b02048238c6cf6fc87331d9fb3f888c90bfdef1bb2c72a4966bdc33f0787c32` |
| `MediaForge-0.0.27-windows-x64-update.zip` | 105371188 | `4cb35837a610445138aaaab5b456e97e200163c48a9f7e3b3856192359c2fa0c` |

更新包签名文件：`MediaForge-0.0.27-windows-x64-update.zip.sig`。

## 卸载说明

卸载程序默认只删除主程序、快捷方式和安装注册项。独立运行时、模型和配置历史只有显式勾选才清理；不勾选则全部保留。DSH 集成由 DSH 插件管理器单独处理。

## 验证范围

- 卸载独立复选框与静默卸载。
- 音频 UI 依赖注入与完整前端测试。
- 真实首装、升级和综合 CI。
