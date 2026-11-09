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
```

仓库中的 `setup.ps1`、`register.ps1`、`verify.ps1` 只作为开发维护工具保留；
安装包与便携包在打包阶段排除这些脚本，用户机器上不会出现它们。

## 从旧版升级（含 DSH 注入区块）

旧版安装器会往 `cordis.patch.yml` 里写一段 `BEGIN/END` 区块。本版本不再生成该区块，
也**不会自动清理**；安装包里同样不带清理脚本。旧版装过 DSH 桥的用户升级后需要手动删除。

1. 打开 `%USERPROFILE%\.dsh\profiles\<你的 profile>\cordis.patch.yml`（桌面版通常是 `desktop`）；
2. 删除从 `# === mediaforge MCP BEGIN ===` 到 `# === mediaforge MCP END ===` 的整段，
   **包括这两条标记行**；
3. 保存，重启 DSH；
4. 用插件管理器安装 bundle：

   ```
   plugin_manager install_bundle  target=link:<安装目录>\integrations\dsh-mediaforge
   ```

不删会怎样：profile 里的注入行和新 bundle 的行 id 相同，两个同名 MCP server 会互相冲突，
表现为 MediaForge 工具消失。

> 维护者仍可在源码树里运行仓库版 `register.ps1` 做只读检查或 `-Profile <name> -RemoveLegacyBlock`，
> 但它不随安装包分发。
