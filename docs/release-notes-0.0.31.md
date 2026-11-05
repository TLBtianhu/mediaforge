# MediaForge 0.0.31

本地媒体工作站：音频分离、图像处理、视频修复与格式转换。

## 这一版

- **输入来源统一**：`TaskInputFiles` 共享文件添加、文件夹选择、移除和清空。
- **输出设置统一**：`TaskOutputSettings` 共享输出目录、保存方式和预览。
- **模型控件统一**：`ModelParameterField` 和 `ModelPanelState` 共享参数输入、加载态和空态。
- **运行时准备统一**：`RuntimePreparationProgress` 共享进度条、日志与取消操作。
- **布局与主题统一**：弹窗宽度、间距、圆角和状态色开始使用共享 token。
- 保留任务统一、能力对账、DSH 分发清理和下载诊断能力。

## 下载

| 产物 | 文件名 |
|---|---|
| Windows CUDA 安装包 | `MediaForge-0.0.31-windows-x64-cuda-setup.exe` |
| Windows 便携版 | `MediaForge-0.0.31-windows-x64-portable.zip` |
| 更新包 | `MediaForge-0.0.31-windows-x64-update.zip` |

安装包本机副本继续放在 `release\local`，不写 Downloads。

- GitHub Release：https://github.com/TLBtianhu/mediaforge/releases/tag/v0.0.31
- 魔搭安装包直链：https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.31-windows-x64-cuda-setup.exe

## 产物校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| `MediaForge-0.0.31-windows-x64-cuda-setup.exe` | 67405288 | `c3235f4d1e92b6e2b578824703075d101d41e5bdc0eb9d74a35234e219c74ad6` |
| `MediaForge-0.0.31-windows-x64-portable.zip` | 128564507 | `09e897fc615d8821b48b115c95cdefa6f09ae0d71de49b8317757024376f302c` |
| `MediaForge-0.0.31-windows-x64-update.zip` | 104605866 | `0a5255c7588102dcd85c9381d777ea10d7977db257f7f35c73061a268aabf831` |

更新包签名文件：`MediaForge-0.0.31-windows-x64-update.zip.sig`。

## 卸载说明

卸载前先显示独立选择窗口。默认只卸载主程序、快捷方式和安装注册项；只有显式勾选时才删除独立运行时、模型和配置历史。DSH 集成由 DSH 插件管理器单独处理。

## 验证范围

- 输入/输出/模型组件定向测试与生产构建。
- 真实 Inno 升级、默认数据根和真实首装。
- 综合 CI。
