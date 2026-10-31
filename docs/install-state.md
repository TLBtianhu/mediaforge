# 安装状态与开发/用户隔离

原则：**开发态和用户态不共享任何目录**。没有就创建，不建链接、不复用。
目的是让"开发者的我"能 100% 复刻"普通用户的我"。

## 1. 固定位置（谁都不许改）

| 内容 | 用户态（安装版） | 开发态（仓库） |
|---|---|---|
| 程序本体 | `%LOCALAPPDATA%\Programs\MediaForge`（per-user，不需要管理员） | `C:\MediaForge`（exe 在 `src-tauri\target\debug`） |
| 契约清单 | `<安装目录>\manifest\`（protocol / runtime / catalog） | `<repo>\manifest\` |
| 用户数据根 | `%USERPROFILE%\.mediaforge` | `<repo>\.dev-data` |
| 运行时环境 | `<数据根>\runtime-envs\<assetId>\` + `active-runtime.json` 指针 | `<repo>\.dev-data\runtime-envs\<assetId>\` |
| 模型 | `<数据根>\models` | `<repo>\.dev-data\models` |
| 输出 / 日志 / 设置 | `<数据根>\{outputs,logs,settings}` | `<repo>\.dev-data\{...}` |
| VC++ 运行库 | `<安装目录>\bin\vcrt`、`<安装目录>\python-runtime`，安装运行时后复制到 `runtime-envs\<assetId>\Scripts` | 同一个（随安装包） |
| MCP 启动器 | `%USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd` | 同一个（指向仓库根） |
| 安装指针 | `HKCU\Software\MediaForge\InstallDir` + `%LOCALAPPDATA%\Programs\MediaForge\home.txt` | 同一个（指向仓库根） |

判定依据是 `storage.rs` 的 `resolve_data_root`：
**环境变量 `MEDIAFORGE_DATA_ROOT` → 开发态 `<repo>\.dev-data` → 便携标记 `<exe>\data` → `~/.mediaforge`**。

## 1.1 VC++ 运行库为什么不装 vc_redist

安装是 per-user（`PrivilegesRequired=lowest`），没有管理员权限跑 `VC_redist.x64.exe`。
而缺少 `msvcp140.dll` 时，torch/cv2 这类 C++ 扩展会在 import 阶段直接
`DLL load failed`，用户无从判断。

做法是 **app-local 捆绑**：

```
安装包 bin\vcrt\           8 个可再发行 DLL（msvcp140 系列 / vcruntime140 系列 /
                             concrt140 / vcomp140）+ SOURCE.json 记录来源版本
安装包 python-runtime\     同一组 DLL，保证引导解释器能加载 C++ 扩展
运行时安装后                runtime-envs\<assetId>\Scripts\ 再复制一份，
                             并跑 python -c "ctypes.CDLL('msvcp140.dll')…" 自检
```

自检失败会让运行时安装直接报错，而不是等到用户点任务。许可与来源写在 `NOTICE`。

## 2. 三条硬规则

1. **禁止链接**：开发态与用户态之间不允许 junction / symlink。
   曾经的教训：`~/.mediaforge/models` 被指向 `.dev-data/models`，安装版扫描时命中
   `WinError 448（不受信任的装入点）`，整个模型库页面报错。
2. **用则建**：任何一层目录缺失都自动创建（`mkdir -p` 语义），不依赖安装包预建。
3. **要复刻用户，就换数据根**：想在开发者机器上扮演"全新用户"，不要清空自己的目录，
   而是设 `MEDIAFORGE_DATA_ROOT` 指向一个空目录，或用另一个 Windows 账户。

## 3. 良性安装状态应该长什么样

### 3.1 应用侧

```
C:\Program Files\MediaForge\
├─ MediaForge.exe            ← 程序本体（不是 Pymss Studio.exe）
├─ python\                   ← worker 代码
├─ python-runtime\           ← 引导解释器 + runtime-envs
├─ bin\                      ← ffmpeg / ffprobe / aria2c
├─ integrations\dsh-mediaforge\   ← DSH 集成包（随安装包分发）
└─ README.txt
```

数据根：

```
%USERPROFILE%\.mediaforge\
├─ models\      ← 真目录，不是链接
├─ outputs\  logs\  settings\
```

判定命令：

```powershell
# 1) 没有链接
Get-ChildItem "$env:USERPROFILE\.mediaforge" -Force |
  Where-Object { $_.Attributes -match 'ReparsePoint' }      # 应为空

# 2) 指针指向安装目录
Get-Content "$env:LOCALAPPDATA\MediaForge\home.txt"
Get-ItemProperty HKCU:\Software\MediaForge | Select InstallDir
```

### 3.2 集成侧

```
%USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd   ← 启动器（不含绝对路径）
DSH profile package.json:  "@local/mediaforge-mcp": "link:<安装目录>\integrations\dsh-mediaforge"
DSH profile cordis.patch.yml: 一行 serverName: mediaforge
```

判定命令（一条就够）：

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\MediaForge\integrations\dsh-mediaforge\verify.ps1"
```

期望输出：

```
== 1. 启动器 ==     OK
== 2. 路径解析 ==   home.txt / registry 都指向安装目录；Program Files 兜底 = True
== 3. MCP 握手 ==   serverInfo.name = mediaforge；tools/list 有 58 条
== 结果 ==          PASS
```

### 3.3 DSH 侧

- 重启后新会话里工具名形如 `mcp__mediaforge__agent_contract`
- Plugins 页面能看到 `@local/mediaforge-mcp`，状态 enabled
- **只应有一行** `serverName: mediaforge`；重复会导致后加载的那行失败
- `failOnStartupError: false`（应用没装时 DSH 仍能启动，只是没有这些工具）

## 4. 出问题时的定位顺序

| 现象 | 先查 |
|---|---|
| DSH 里没有 `mcp__mediaforge__*` | `verify.ps1`：启动器 → 指针 → 握手 |
| `cannot find a MediaForge install` | 指针文件与注册表；`MEDIAFORGE_HOME` 是否指错 |
| 工具在但调用报错 | 运行时：应用内设置页看 cuda/cpu；`active-runtime.json` |
| 模型库整页报错 | 数据根里是否有 reparse point（见 3.1） |
| 模型列表为空但已下载 | `MEDIAFORGE_MODEL_DIR` 是否指向别处 |

## 5. 待办（按此文档执行）

1. 安装器：目标目录固定为 `{autopf}\MediaForge`，不继承旧路径（已做，新 AppId）
2. 启动器/指针：安装结束自动写（已做）
3. 数据根：缺失时自动创建（需在代码里确认每层都 `mkdir`）
4. 开发态防呆：开发构建若数据根落到 `~/.mediaforge` 就警告并退出
5. 去掉一切 junction 用法（已清除现场的链接）

## 6. 升级策略：只发安装包

第一版**不做自动更新**，只发布安装包。升级路径是"下载新安装包再装一遍"：

```
数据在 %USERPROFILE%\.mediaforge，安装器从不碰它
安装位置固定，重装即覆盖，不会出现两份
旧版本的快捷方式/注册表项由同一个 AppId 接管，不会残留
```

代价：用户不会收到"有新版本"的提示。可以接受——比"自动更新验签失败把用户卡住"好得多。

`update.zip` 仍会在打包时产出并签名（[4/6] 步骤），因为：
- 它是将来启用自动更新的唯一载体，提前验证格式有好处
- 签名链路现在就跑通，比将来补更便宜

要开启自动更新时，需要三件事同时成立：

1. `update.zip` 与 `.sig` 上传到更新源
2. 更新清单（`<channel>-<generation>-<platform>-update.json`）里的 `url` 与 `signature` 正确
3. 客户端二进制内嵌的公钥与签名私钥配对（0.0.14 起已内嵌本项目自己的公钥）
