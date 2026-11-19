# 安装状态与开发/用户隔离

本文描述当前 Windows 安装、便携和开发布局。原则仍是：**开发态和用户态不共享数据目录，不用 junction/symlink 复用数据，不拿真实用户 profile 做测试。**

详细强制规则见 [DEVELOPMENT.md](../DEVELOPMENT.md)，当前候选与验收边界见 [handoff-2026-11-19.md](handoff-2026-11-19.md)。

## 1. 三种形态与真实数据根

| 形态 | 程序位置 | 数据根 | 模型与独立运行时 |
| --- | --- | --- | --- |
| 开发构建 | `<repo>\src-tauri\target\{debug,release}` | `<repo>\.dev-data` | `<repo>\.dev-data\models`、`<repo>\.dev-data\runtime-envs` |
| 便携版 | 用户选择的解压目录中的 `MediaForge.exe` | `<exe 目录>\data` | `<exe 目录>\models`、`<exe 目录>\runtime-envs` |
| 安装版 | 默认 `%LOCALAPPDATA%\Programs\MediaForge\app\MediaForge.exe` | `<安装根>\data` | `<安装根>\models`、`<安装根>\runtime-envs` |

安装根可由用户选择，不是必须固定在默认路径。`app` 子目录只放可替换程序，数据、模型和运行时与 `app` 平级。当前 Windows 安装版的默认数据根**不是** `%USERPROFILE%\.mediaforge`。

`~/.mediaforge` 只用于非 Windows 或旧布局的兜底读取。当前版本不迁移、不双写旧目录，也不能把旧目录当成安装版的默认位置；不要根据历史文档自动移动或删除它。

便携版通过 exe 同目录的 `mediaforge.portable` 标记识别。标记只应进入便携包，不能进入安装版 `app/`。

## 2. 路径解析与覆盖

`src-tauri/src/storage.rs` 的优先级为：

1. 显式环境变量；
2. 开发构建的 `<repo>\.dev-data`；
3. 便携版或当前安装布局；
4. 非 Windows/旧布局的 `~/.mediaforge` 兜底。

支持 `MEDIAFORGE_DATA_ROOT`、`MEDIAFORGE_DATA_DIR` 和 `PYMSS_STUDIO_DATA_ROOT` 显式指定数据根。此时未单独指定的模型和运行时分别位于该根的 `models`、`runtime-envs` 子目录，不会因为目录名叫 `data` 就改取父目录。

可用 `MEDIAFORGE_MODEL_DIR` / `PYMSS_MODEL_DIR` 和 `MEDIAFORGE_RUNTIME_ENVS_DIR` 分别覆盖模型与运行时根。开发、干净包和长期测试必须使用不同数据根及 WebView2 profile。

## 3. 当前安装布局

默认安装后，关键路径如下：

```text
%LOCALAPPDATA%\Programs\MediaForge\
├─ app\
│  ├─ MediaForge.exe
│  ├─ python\                 worker、Agent 与共享契约代码
│  ├─ python-runtime\         随包 Python 3.12 bootstrap，不是推理环境
│  ├─ bin\                    FFmpeg、FFprobe、aria2c 与 VC++ DLL
│  ├─ manifest\               协议、运行时、配额和素材库契约
│  └─ README.txt
├─ integrations\dsh-mediaforge\
├─ data\                      设置、任务、素材库、输出、日志和编辑器工程
├─ models\                    按需下载的模型
├─ runtime-envs\              独立推理资产及 active-runtime.json
└─ unins000.exe / unins000.dat
```

`HKCU\Software\MediaForge\InstallDir` 指向实际的 `<安装根>\app`，用于识别当前安装实例。当前布局不依赖 `home.txt`。不要把整个安装根当成可随意删除的程序目录，因为其中还包含用户资产。

随包 bootstrap 只负责启动、查询和管理资产。推理使用 `runtime-envs` 中单独安装的自包含 Python 3.12 环境，不能用覆盖解释器的方式修复旧环境。

## 4. 覆盖升级与卸载

- 覆盖升级替换 `app/` 和随包集成文件，保留 `data`、`models`、`runtime-envs`。
- 静默卸载删除程序与测试/安装注册信息，默认保留上述资产。
- 交互卸载会另外提供删除运行时、模型和数据的复选框，默认均不选中；只有用户显式选择后才删除对应目录。因此不能笼统写成“卸载永远不会删除数据”。
- 独立安装验收使用同一安装器源码和 stage 重编唯一测试 AppId，不覆盖生产 AppId，不执行正式安装包。它核对完整文件、bootstrap health、覆盖升级、静默卸载和 8 项资产样本。

当前 r10 的 CPU/CUDA 证据分别为 `.tmp/r10-cpu-installer-upgrade.json` 和 `.tmp/r10-cuda-installer-upgrade.json`。这些检查不启动 GUI，不等于正式安装包的桌面安装验收。

## 5. VC++ 运行库

安装为 per-user（`PrivilegesRequired=lowest`），不能依赖管理员权限安装 VC++ redistributable。随包在以下位置提供同一组 DLL：

- `app\bin\vcrt\`
- `app\python-runtime\`

独立运行时安装后，所需 DLL 也复制到对应 `runtime-envs\<assetId>\Scripts`，并执行加载检查。`SOURCE.json` 记录来源；缺少这些 DLL 会让 torch/cv2 等扩展在导入时失败。

## 6. DSH / MCP 集成

当前唯一支持的方式是加载随包 bundle：

```text
plugin_manager install_bundle target=link:<安装或解压根>\integrations\dsh-mediaforge
```

- 安装器只分发 bundle，不写 DSH profile 的 `cordis.patch.yml`，不注入或整块替换用户配置。
- 不再依赖 `mediaforge-mcp.cmd`、`home.txt` 或旧 `verify.ps1`。
- bundle 从自身位置解析该实例的 bootstrap、Bridge、数据、模型和运行时根。
- DSH 工具名和数量以当前 MCP `tools/list` 为准，不使用历史固定数量。
- 加载、移除及配置归 DSH 管理；三次加载或调用的幂等验证必须保持 DSH 自己配置文件的 SHA-256 不变。

历史遗留的标记区块只能由用户按当前迁移指引审慎处理，不能由安装器自动删除。

## 7. 验证方式与禁止事项

| 入口 | 当前用途 | 边界 |
| --- | --- | --- |
| `scripts/smoke-installer.ps1` | 独立身份的完整 stage、安装升级、bootstrap health、静默卸载与资产保留 | 不执行生产安装器，不启动 GUI |
| `scripts/clean-test.ps1` | 安装后 exe 的首次启动与一次性数据/WebView2 profile | 会启动真实 GUI，必须明确授权并在隔离环境运行 |
| `scripts/smoke-portable.ps1` | 便携数据根及真实进程检查 | 包含 GUI 启动和进程结束逻辑，禁止在未授权环境运行 |
| `scripts/smoke-first-install.ps1` / `smoke-runtime-rollback.ps1` | 隔离运行时安装、激活和回滚 | 使用明确打包输入和测试资产，不操作用户运行时 |

模型和工具数量不写固定值，以当前随包目录、真实查询和运行时状态为准。fixture、bootstrap health、真实推理、真实 WebView 和跨平台结果是不同证据，不能互相替代。

不要清空开发缓存、模型、运行时、旧候选或长期测试数据来腾空间。遇到旧、坏或未来 schema 必须保留原文件并明确报错，不能按空数据写回。

## 8. 更新状态

当前安装升级以手动下载并覆盖安装为主，未启用自动更新。候选可以生成和验证 `update.zip` 及签名，但这不等于更新渠道已发布或启用。发布时必须重新核对目标版本的 ZIP、签名、公钥和远端清单，不能复用其他候选的签名。
