# MediaForge 0.0.29

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **卸载前选择窗口**：独立运行时、模型和配置历史在真正卸载前选择，不会还没选择就进入卸载流程。
- **任务与路径统一**：统一任务提交、批次创建和 `data / models / runtime-envs` 路径规则。
- **能力对账**：UI 与 MCP 都要求适配器存在且当前运行时支持。
- **依赖契约**：音频相关工厂的关键依赖显式化。
- **DSH 桥接**：启动器随 integrations 提供，支持安装根路径解析，图标符合元数据限制。
- **下载诊断**：保留 aria2c 的真实错误输出，同时保留断点续传文件。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.29-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.29-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.29-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

- GitHub Release：https://github.com/TLBtianhu/mediaforge/releases/tag/v0.0.29
- 魔搭安装包直链：https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.29-windows-x64-cuda-setup.exe

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.29-windows-x64-cuda-setup.exe` | 67401063 | `48d5743a64a07f565f8bdcd8cefcf154c17be7b31abef627e84e4cc932fd3db4` |
| `MediaForge-0.0.29-windows-x64-portable.zip` | 128567266 | `9a4c6aceee932b914afb4c6c585b695bdf6a98e97b31a13b530c44ea55b8ae9f` |
| `MediaForge-0.0.29-windows-x64-update.zip` | 104608625 | `5f658e9643858022a0a4cbb0fb01830e0961b15676ad868d6130aea3198db3c7` |

更新包签名文件：`MediaForge-0.0.29-windows-x64-update.zip.sig`。

## 卸载说明

卸载前先显示独立选择窗口。默认只卸载主程序、快捷方式和安装注册项；只有显式勾选时才删除独立运行时、模型和配置历史。DSH 集成由 DSH 插件管理器单独处理。

## 验证范围

- 卸载前选择窗口与静默卸载。
- 真实 Inno 升级、默认数据根和真实首装。
- 完整前端测试与综合 CI。
