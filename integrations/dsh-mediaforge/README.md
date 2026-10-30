# MediaForge 的 DSH 集成包

让 DeepSeek Harness 能调用 MediaForge 的工具（模型库、图像/音频/视频处理、预检、
作业台账、自评价）。

**这个包只是"连接器"，本体需要单独安装。**

## 两条来路

### 先有 MediaForge，后想用 agent

安装包会自动登记（检测到 Harness 时）。如果你是"先装应用、后装 Harness"，补跑一次：

```powershell
powershell -ExecutionPolicy Bypass -File register.ps1
```

然后重启 Harness。

### 先有 Harness，想接 MediaForge

1. 先装 MediaForge 本体（否则工具会出现但调用失败）：
   <https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist>
2. 再跑 `register.ps1`，或把这个目录作为 bundle 装进 profile
3. 重启 Harness

## 原理（为什么包很小）

包里**没有任何绝对路径**。它只写一行配置：

```yaml
config:
  command: cmd
  args: [/c, '%USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd']
```

启动器在用户级固定位置，负责找到应用：

```
MEDIAFORGE_HOME → 注册表 HKCU\Software\MediaForge\InstallDir → %LOCALAPPDATA%\Programs\MediaForge → %ProgramFiles%\MediaForge
```

所以同一份包能服务开发树、安装版、便携版；应用换了位置，只需重跑 `setup.ps1`。

## 自检

```powershell
powershell -ExecutionPolicy Bypass -File verify.ps1
```

期望：

```
== 1. 启动器 ==     OK
== 2. 路径解析 ==   指向安装目录；Program Files 兜底 = True
== 3. MCP 握手 ==   serverInfo.name = mediaforge；50+ 条工具
== 结果 ==          PASS
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| Harness 里没有 `mcp__mediaforge__*` | 没重启 | 重启 Harness（MCP 连接在启动时建立） |
| 工具在，调用报 `cannot find a MediaForge install` | 应用没装/指针丢失 | 装本体，或跑 `setup.ps1 -AppRoot <目录>` |
| 工具在，调用报 `No active runtime environment` | 运行时没装好 | 应用内「设置 → 运行时」安装/切换 |
| 出现两行 `serverName: mediaforge` | 手工配过又装了一次 | 删掉旧的那行；`register.ps1` 只维护自己标记的区块 |
| 模型库整页报 `WinError 448` | 数据目录里有 junction | 删掉链接，让 `~\.mediaforge\models` 成为真实目录 |

## 文件

```
cordis.patch.yml        配置（bundle 安装时读它）
register.ps1            把一行 MCP 配置写进 profile（幂等）
setup.ps1               写启动器 + 注册表指针（便携版/开发树用）
verify.ps1              自检：启动器 → 路径 → 握手
launcher.template.cmd   启动器模板（无绝对路径）
DESIGN.md               设计取舍（为什么 stdio、为什么不用 pnpm）
```
