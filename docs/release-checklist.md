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
powershell -ExecutionPolicy Bypass -File scripts\package-release.ps1 -SkipSign -SkipDownloadCopy -OutputDir release\candidates\0.0.32
```

```
□ [1/6] 生产构建走 pnpm tauri build（不是 cargo build，否则前端资源不内嵌）
□ [2/6] staging断言通过：完整python分层、manifest、SDK、integrations、bin与bootstrap
□ [3/6] 便携 zip 生成
□ [4/6] update.zip 生成并**已签名**（.sig 与 .signature.b64 都在）
□ [5/6] 安装包生成
□ [6/6] 输出路径正确；本机副本只在release/local生成，候选可用-SkipDownloadCopy省略
□ stage EXE身份/版本、资源清单、归档哈希均已核对，不用EXE体积推断资源正确
□ 默认不含runtime-envs、模型、开发数据、日志和测试截图；数GB资产另行显式打包
□ 正式签名前确认目标版本与ZIP内EXE一致；仅使用显式密钥路径和进程环境
□ 不把密码放命令参数，不输出signer原始诊断，不在签名环境中执行signer help
```

## 3. 隔离安装与原生界面验证

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
□ 完整UI验收另记结果；禁止桌面操作时不启动普通GUI、不操作现有窗口
```

## 4. Harness 集成

```powershell
powershell -ExecutionPolicy Bypass -File "<安装根>\app\integrations\dsh-mediaforge\verify.ps1"
```

```
□ PASS，且 tools/list 有 50+ 条
□ 重启 Harness 后，新会话能调用 mcp__mediaforge__*
□ 首次下模型时 agent 会要求确认（explicit 审批）
```

## 5. 上传与回读

```
□ 魔搭数据集（TLBtianhu/MediaForge-Dist）已上传安装包 + 便携包
□ 每个文件都用**匿名** HEAD 回读，状态 200 且大小一致
□ GitHub Release 已发布，正文含魔搭裸链接
□ update.zip 与其签名已放到更新源（若启用自动更新）
```

## 6. 发布后

```
□ 数据集 README 里版本号与文件清单更新
□ 群里/README 的下载链接指向新版本
□ 记录本次发布：版本、产物大小、签名指纹、已知问题
```

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
powershell -ExecutionPolicy Bypass -File "C:\Program Files\MediaForge\integrations\dsh-mediaforge\verify.ps1"
```

## 更新签名的密钥归属（0.0.16 修正）

发布前必须确认：`src-tauri/tauri.conf.json` 里 `plugins.updater.pubkey` 必须与**签名所用的私钥**
配对，否则更新包在客户端会验签失败。

0.0.16 之前这里是上游的 key（id `A9752DF660245CB0`），而发布用的私钥是本项目的
（id `7F91B4AEE4F5BA7A`）——两者不匹配，即使配了 endpoint 更新也会被拒。现已改为本项目公钥。

自查方法（不需要私钥）：

```bash
# 解出 .sig 里的 key id，应该与 tauri.conf.json 的 pubkey 文本里那串十六进制一致
python - <<'PY'
import base64, json, pathlib
sig = pathlib.Path("release/MediaForge-<版本>-windows-x64-update.zip.sig").read_text().strip()
blob = base64.b64decode(base64.b64decode(sig).decode().splitlines()[1])
print("sig key id:", blob[2:10][::-1].hex().upper())
conf = json.load(open("src-tauri/tauri.conf.json", encoding="utf-8"))
pub = base64.b64decode(conf["plugins"]["updater"]["pubkey"]).decode()
print("conf pubkey:", pub.splitlines()[0])
PY
```

另外 `plugins.updater.endpoints` 目前是空数组（`[]`）——**自动更新没有启用**，
用户靠手动下载新安装包。要启用需要发布 `latest.json`（Tauri v2 静态清单）
并把地址填进 endpoints；在此之前不要把 endpoints 填成不存在的地址，否则
客户端每次启动都会请求失败。

