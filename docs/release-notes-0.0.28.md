# MediaForge 0.0.28

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **任务模型统一**：`launchTask` 统一音频、工作流和工作台提交；`prepareTaskBatch` / `completeTaskBatch` 统一批次准备与结果计数。
- **路径统一**：安装版、便携版、开发版和 DSH 桥接都使用一致的 `data / models / runtime-envs` 结构，不再默认路由到旧 `.mediaforge`。
- **能力对账**：UI 与 MCP 都要求适配器存在且当前运行时支持。
- **依赖契约**：音频相关工厂的关键依赖显式化，漏传会在编译期暴露。
- **DSH 桥接**：启动器随 integrations 提供，支持安装根路径解析；图标低于 DSH 元数据限制。
- **下载诊断**：保留 aria2c 的真实错误输出，同时保留断点续传文件。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.28-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.28-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.28-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

## 卸载说明

默认只卸载主程序、快捷方式和安装注册项。独立运行时、模型和配置历史只有显式勾选才清理，不勾选则全部保留。DSH 集成由 DSH 插件管理器单独处理。

## 验证范围

- 完整前端测试与生产构建。
- 真实 Inno 升级、默认数据根和静默卸载。
- 真实首装、嵌入式解释器和综合 CI。
