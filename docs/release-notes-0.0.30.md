# MediaForge 0.0.30

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **DSH 分发清理**：用户 bundle 不再包含 `setup.ps1`、`register.ps1`、`verify.ps1`，只保留插件管理器需要的配置、启动器、图标和文档。
- **安装器不再修复旧运行时**：不再改写旧 `pyvenv.cfg` 或旧后端目录。
- **共享数据根**：`runtime_guard` 使用统一的 `worker_paths.data_root()`，不再回退 `.mediaforge`。
- **旧后端目录退场**：运行时状态不再返回 `legacyDir`，只按资产 ID、哈希和激活指针判断。
- **卸载前选择窗口**：独立运行时、模型和配置历史在真正卸载前选择。
- 保留任务统一、能力对账、工厂依赖和下载诊断能力。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.30-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.30-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.30-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

## 卸载说明

卸载前先显示独立选择窗口。默认只卸载主程序、快捷方式和安装注册项；只有显式勾选时才删除独立运行时、模型和配置历史。DSH 集成由 DSH 插件管理器单独处理。

## 验证范围

- 卸载前选择窗口与静默卸载。
- 真实 Inno 升级、默认数据根和真实首装。
- 完整前端测试与综合 CI。
