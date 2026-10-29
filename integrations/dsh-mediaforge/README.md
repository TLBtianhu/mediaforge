# MediaForge 的 DSH 插件包

把 MediaForge 的 agent bridge 接进 DeepSeek Harness：装上之后 agent 就能调用
`mcp__mediaforge__*` 这一整套工具（模型库、媒体能力、计划预检、作业台账、自评价）。

## 它做了什么

只做一件事：往 DSH 的组合里插一行 MCP 客户端配置（`serverName: mediaforge`），
指向一个**固定位置**的启动器：

```
%LOCALAPPDATA%\MediaForge\bin\mediaforge-mcp.cmd
```

包里**不含**任何账号相关的绝对路径；启动器由安装包（或下面的脚本）生成。

## 安装（三步）

1. 让启动器就位（安装版会自动做；便携版手动跑一次）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "D:\我的便携版目录"
   ```

2. 把插件包装进 DSH：在 DSH 会话里让 agent 用 `plugin_manager`
   `action: install_bundle`、`target: <这个目录>`，
   或者用你习惯的方式把它链进 profile。

3. 新开一个 DSH 会话，确认工具出现（例如 `mcp__mediaforge__agent_contract`）。

## 便携版注意

便携版文件夹被移动/改名后，重跑一次 `setup.ps1` 覆盖启动器即可，插件包不用动。

## 验证启动器本身

```powershell
"%LOCALAPPDATA%\MediaForge\bin\mediaforge-mcp.cmd" --stdio
```

它会输出一行 MCP `initialize` 响应后等待输入（Ctrl+C 退出）。若报
`agent_bridge.py not found`，说明 `-AppRoot` 指错了。

## 与内置执行器的关系

DSH 的 agent 通过 MCP 调这里的工具；应用内还有一套**无 LLM 的确定性执行器**
（`python/embedded_loop.py`），两者共用同一个数据根与作业台账。
同时使用时请给它们**不同的输出目录**，避免互相覆盖；GPU 争用也请串行。
