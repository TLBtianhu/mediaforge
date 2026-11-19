# MediaForge 的 DSH 集成包

让 DeepSeek Harness 能调用 MediaForge 的工具（模型库、图像/音频/视频处理、预检、作业台账、自评价）。

**这个包只是连接器，MediaForge 本体需要单独安装。**

## 安装方式

1. 安装 MediaForge，或解压便携包。
2. 用 DSH 插件管理器链接该实例的 `integrations\dsh-mediaforge` 目录。
3. 如 DSH 尚未加载新的 bundle，重启对应的 DSH profile。加载后可查询 `mcp__mediaforge__mediaforge_status`。

```text
plugin_manager install_bundle target=link:<安装或解压根>\integrations\dsh-mediaforge
```

## 原理

DSH 从链接的 bundle 目录加载 `cordis.patch.yml`，并从同一目录加载它声明的 `mcp-plugin.mjs`。适配器把该实例的 Python 3.12 bootstrap 和 bridge 交给 DSH 自带的 MCP 客户端，通过 stdio 连接。

安装版的程序位于 `<安装根>\app`。便携版的程序和 `mediaforge.portable` 位于解压根。两种分发形态都使用各自根下的 `data`、`models` 和 `runtime-envs`。源码目录的 bundle 使用源码根的 `.dev-data`，并要求先准备 `python-runtime`。需要换实例时，在 DSH 插件管理器中移除旧链接并链接新目录。

## 文件

```
cordis.patch.yml        bundle 配置
mcp-plugin.mjs          解析所链接的程序实例并创建 MCP 连接
icon.svg                DSH 图标（小于 256 KiB）
locale/                 插件显示文本
README.md               本文
```

仓库中的 `register.ps1`、`verify.ps1` 只作为开发维护工具保留；
安装包与便携包在打包阶段排除这些脚本，用户机器上不会出现它们。

## 从旧版升级（含 DSH 注入区块）

旧版安装器会往 `cordis.patch.yml` 里写一段 `BEGIN/END` 区块。本版本不再生成该区块，
也**不会自动清理**；安装包里同样不带清理脚本。旧版装过 DSH 桥的用户升级后需要手动删除。

**注意：只删 MediaForge 自己的行。** 区块里可能混着 DSH 设置页追加的行
（例如 `- id: agent-preset-registry`、`- id: web`、`- id: web-search-deepseek`），
它们不属于 MediaForge，删掉会丢失你的预设选择和网页搜索配置。

1. 打开 `%USERPROFILE%\.dsh\profiles\<你的 profile>\cordis.patch.yml`（桌面版通常是 `desktop`）；
2. 在 `# === mediaforge MCP BEGIN ===` 与 `# === mediaforge MCP END ===` 之间，
   删掉包含 `id: mediaforge-mcp` 的那一个 `- insert:`（连同它的 `name` / `config` 行）
   以及上面那行 `# 由 MediaForge 安装器维护…` 注释；
   **不要动其它区块（例如 qq-bridge）的 `- insert:`**；
3. 如果还有 `- id: mediaforge-mcp` 的覆盖行（通常带 `disabled: true`），一并删掉；
4. 区块内**其它行一律保留**；
5. **最后**再删掉这两条标记行（先删行、后删标记，避免边界消失后误删）：
   - `# === mediaforge MCP BEGIN ===`
   - `# === mediaforge MCP END ===`
6. 保存后重启 DSH，并**立刻**用插件管理器安装 bundle（中间不要间隔，否则 MediaForge 工具会暂时消失）：

   ```
   plugin_manager install_bundle  target=link:<安装目录>\integrations\dsh-mediaforge
   ```

不删会怎样：profile 里的注入行和新 bundle 的行 id 相同，两个同名 MCP server 会互相冲突，
表现为 MediaForge 工具消失。

> 维护者仍可在源码树里运行仓库版 `register.ps1` 做只读检查或
> `-Profile <name> -RemoveLegacyBlock`——它会自动保留外来行；但它不随安装包分发。
> 不确定时，优先用这个脚本，不要手工删区块。
