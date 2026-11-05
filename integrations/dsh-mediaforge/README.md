# MediaForge 的 DSH 集成包

让 DeepSeek Harness 能调用 MediaForge 的工具（模型库、图像/音频/视频处理、预检、作业台账、自评价）。

**这个包只是连接器，MediaForge 本体需要单独安装。**

## 安装方式

1. 先安装 MediaForge 本体。
2. 通过 DSH 插件管理器安装这个目录作为 bundle。
3. DSH 会启动 MCP 并执行 `tools/list`，不需要执行 PS1，也不需要重启整个 DSH。

如果工具列表需要刷新，只需重连或重启 MCP 桥接进程。

## 原理

`cordis.patch.yml` 只声明 MCP 启动方式：

```yaml
config:
  command: cmd
  args: [/c, '%LOCALAPPDATA%\Programs\MediaForge\integrations\dsh-mediaforge\mediaforge-mcp.cmd']
```

启动器负责解析安装路径，并设置安装根下的 `data`、`models`、`runtime-envs`。它按 `MEDIAFORGE_HOME → 注册表 InstallDir → 默认安装位置` 查找应用。

所以正常加载只需要 bundle 配置和启动器，不依赖 PowerShell 注册脚本。

## 文件

```
cordis.patch.yml        bundle 配置
launcher.template.cmd   启动器模板
mediaforge-mcp.cmd      实际启动器
icon.svg                DSH 图标（小于 256 KiB）
locale/                 插件显示文本
README.md               本文
NOTES.md                维护说明
```

仓库中的 `setup.ps1`、`register.ps1`、`verify.ps1` 只作为开发维护工具保留，**不随用户 bundle 分发**。
