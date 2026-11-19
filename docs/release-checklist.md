# 发布检查表

一次跑完，不靠记忆。任何一项没过就不发。

## 1. 代码与版本

```
□ package.json / tauri.conf.json / Cargo.toml / Cargo.lock应用条目版本一致
□ 源EXE与stage EXE的ProductName为MediaForge，ProductVersion等于目标版本
□ git 工作区干净（没有未提交的临时脚本）
□ cargo test 通过（至少 update_ 与 worker 相关用例）
□ 相关Python单测通过；明确记录skip与未覆盖平台，不把环境失败算作通过
```

## 2. 打包（一条命令）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package-release.ps1
# 隔离候选：不读取签名凭据、不另存本机副本，也不执行安装
powershell -ExecutionPolicy Bypass -File scripts\package-release.ps1 -SkipSign -SkipDownloadCopy -OutputDir release\candidates\0.0.33
```

本地工具链若被清理过，先确认 `rustc --version` 与 ISCC 路径可用（默认
`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`，可用 `MEDIAFORGE_ISCC` 覆盖）。
Inno Setup 6.7+ 不再随安装器分发中文翻译：缺 `Languages\ChineseSimplified.isl` 时 ISCC 会报
`Couldn't open include file ...ChineseSimplified.isl`；从 `jrsoftware/issrc` 对应 tag 的
`Files/Languages/Unofficial/` 取同名文件放入 Inno 的 `Languages\` 即可（本机 6.7.4 实测）。

```
□ [1/6] 生产构建走 pnpm tauri build（不是 cargo build，否则前端资源不内嵌）
□ [2/6] staging断言通过：完整python分层、manifest、SDK、integrations、bin与bootstrap
□ bootstrap、worker及推理资产的实际Python均为runtime-manifest指定的3.12；旧ABI在改目录/状态前拒绝
□ runtime描述符为当前schema且含python；自包含资产不含pyvenv.cfg，不覆盖解释器修复旧环境
□ [3/6] 便携 zip 生成
□ [4/6] update.zip 生成并**已签名**（.sig 与 .signature.b64 都在）
□ [5/6] 安装包生成
□ [6/6] 输出路径正确；本机副本只在release/local生成，候选可用-SkipDownloadCopy省略
□ stage EXE身份/版本、资源清单、归档哈希均已核对，不用EXE体积推断资源正确
□ 基础Python版本变化时提供完整安装/便携介质；不含bootstrap的update.zip不能独立承担3.11→3.12升级
□ 默认不含runtime-envs、模型、开发数据、日志和测试截图；数GB资产另行显式打包
□ 正式签名前确认目标版本与ZIP内EXE一致；显式指定私钥路径，本机密码按需从凭据库读取
□ 不把密码放命令参数，不输出signer原始诊断，不在签名环境中执行signer help
```

## 3. 隔离安装与原生界面验证

Windows 无界面候选使用 `scripts/smoke-installer.ps1`，显式传入 `-Stage`、`-Version`、`-Python`（3.12）、`-Compiler` 和 `.tmp` 内的 `-Report`。使用 `-PreviousStage` 与 `-PreviousVersion` 增加同一测试 AppId 下的覆盖升级。`-Installer` 用于记录并复核候选安装器的 SHA-256。脚本重编唯一测试身份，逐文件核对完整 stage，运行安装内的 bootstrap health，并核对静默卸载及样本文件保留。

在干净机器或新用户账户上：

```
□ 安装完成，无报错
□ 启动后窗口有内容（不是白屏）
□ 模型库显示「N 可用 / 0 已下载」，无 [WinError 448] 之类报错
□ 下载一个 64 MB 级模型：有进度、完成后状态变「已下载」
□ 图像页跑一次超分，输出文件存在且尺寸正确
□ 设置 → 运行时显示 cuda（无 N 卡则 cpu）
□ 新安装数据在<安装根>/data，模型/运行时为同根兄弟目录，且没有junction
□ 显式DATA_ROOT正确覆盖默认目录；测试数据与正式用户profile无交叉
□ 独立AppId完成旧版→新版升级和静默卸载，配置/模型/运行时样本SHA保持一致
□ 小资产headless首装、坏SHA拒绝、运行时A→B→A回滚通过
□ 完整3.12资产通过真实下载/哈希/安装/离线复用；打包worker用显式steps完成真实推理
□ 旧/缺失/未来数据schema拒绝且原文件保留；不迁移、不双写、不把读取失败写成空库
□ 完整UI验收另记结果；禁止桌面操作时不启动普通GUI、不操作现有窗口
```

## 4. Harness 集成

```powershell
node scripts/test-python.mjs tests.python.test_dsh_integration_layout
```

在隔离的 Harness profile 中通过插件管理器加载 `link:<安装或解压根>\integrations\dsh-mediaforge`。

```
□ 默认安装、自定义目录和便携版均从各自的实例发现和调用 MCP 工具
□ 连续三次加载和调用后，隔离 profile 的 cordis.patch.yml SHA-256 一致
□ 重启 Harness 后，新会话能调用 mcp__mediaforge__*
□ 首次下模型时 agent 会要求确认（explicit 审批）
```

## 5. 上传与回读

```
□ 魔搭数据集（TLBtianhu/MediaForge-Dist）已上传安装包 + 便携包
□ 每个文件都用**匿名** HEAD 回读，状态 200 且大小一致
□ GitHub Release 已发布，正文含魔搭裸链接
□ update.zip 与其签名已放到更新源（若启用自动更新）
□ 3.12独立资产已单独上传，匿名回读大小/SHA与manifest一致；本地存在不代表公网可安装
□ versions文档schemaVersion=3，签名覆盖的runtime包含platform、arch、backend、python、assetId、size、sha256与urls
```

## 6. 发布后

```
□ 数据集 README 里版本号与文件清单更新
□ 群里/README 的下载链接指向新版本
□ 记录本次发布：版本、产物大小、签名指纹、已知问题
```

2026-11-07本地核对：0.0.33候选通过Windows构建、完整逻辑回归、独立AppId升级/静默卸载、
真实3.12资产headless安装及CPU两步超分。未签名、未上传资产、未运行远端CI或正式发布；
完整原生UI与macOS/Linux原生包尚未验收。精确计数、哈希和证据见
`release/candidates/0.0.33/verification.json`。用户禁止桌面操作，不以CLI检查替代完整UI结论。

2026-11-12 的 `0.0.34 r4` 已通过 Windows 无界面静默首装、`r3 → r4` 同版本覆盖升级和静默卸载。两端完整 stage、bootstrap health 与 8 个保留样本已核对。候选清单见 `release/candidates/0.0.34-20261112-r4/candidate.json`。 [本轮交接](handoff-2026-11-08.md#6-p1-windows-发布候选-r42026-11-12)

同一 r4 候选使用已有 Python 3.12 CUDA 资产完成真实四轨分离和两步图像超分。独立解码和任务账本核对通过。 [推理验收记录](handoff-2026-11-08.md#61-r4-真实-cuda-推理2026-11-12)

`0.0.34 r5` 的本地 ZIP 和 schema 3 待签清单已复核，ModelScope 运行时已通过匿名全量回读。正式签名需要 `TAURI_SIGNING_PRIVATE_KEY_PATH` 与 `VERSIONS_SIGNING_PRIVATE_KEY`，公开渠道暂未提供 r5。 [签名、回读和远端 CI](handoff-2026-11-08.md#63-r5-签名与匿名渠道核对2026-11-12)

`0.0.34 r6` 已使用新密钥完成本地签名和验签，静默覆盖升级通过。发布时使用 r6 的 `versions.json` 与 `.sig`，对应的新信任根随 r6 安装包交付。 [r6 交接记录](handoff-2026-11-08.md#64-r6-更换签名密钥2026-11-18)

---

## 常用命令速查

```powershell
# 打包
powershell -ExecutionPolicy Bypass -File scripts\package-release.ps1

# 只补签名
powershell -ExecutionPolicy Bypass -File scripts\sign-update.ps1

# 上传（令牌来自环境变量或 .dev-data/dev.env）
python scripts\publish-modelscope.py release\MediaForge-*setup.exe release\MediaForge-*portable.zip

# 集成自检
node scripts/test-python.mjs tests.python.test_dsh_integration_layout
```

## 更新签名的密钥归属（0.0.16 修正）

**当前状态（2026-11-12）**：`src-tauri/tauri.conf.json` 没有 `plugins` 段——第一版有意不启用自动更新
（见 `docs/install-state.md`）。用户靠手动下载新安装包，下面的签名自查只在启用自动更新后才适用。

启用后，发布前必须确认：`tauri.conf.json` 的 `plugins.updater.pubkey` 必须与**签名所用的私钥**
配对，否则更新包在客户端会验签失败。

0.0.16 之前这里是上游的 key（id `A9752DF660245CB0`），而发布用的私钥是本项目的
（id `7F91B4AEE4F5BA7A`）——两者不匹配，即使配了 endpoint 更新也会被拒。现已改为本项目公钥。

自查方法（启用后、不需要私钥）：

```bash
# 解出 .sig 里的 key id，应该与 tauri.conf.json 的 pubkey 文本里那串十六进制一致
python - <<'PY'
import base64, json, pathlib
sig = pathlib.Path("release/MediaForge-<版本>-windows-x64-update.zip.sig").read_text().strip()
blob = base64.b64decode(base64.b64decode(sig).decode().splitlines()[1])
print("sig key id:", blob[2:10][::-1].hex().upper())
conf = json.load(open("src-tauri/tauri.conf.json", encoding="utf-8"))
updater = (conf.get("plugins") or {}).get("updater") or {}
pubkey = updater.get("pubkey")
if not pubkey:
    print("conf pubkey: <not configured — automatic updates are disabled>")
else:
    print("conf pubkey:", base64.b64decode(pubkey).decode().splitlines()[0])
PY
```

启用自动更新需要发布 `latest.json`（Tauri v2 静态清单）并把地址填进 `updater.endpoints`；
在此之前不要把 endpoints 填成不存在的地址，否则客户端每次启动都会请求失败。

