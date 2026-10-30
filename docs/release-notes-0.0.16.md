# 0.0.16 发布说明（草稿）

> 这一版是运行时解耦后的第一个完整版本：安装包从 1850 MB 降到 63 MB，
> 运行时作为独立资产按 sha256 下载，且已在本机完成端到端验证。

## 用户可见的变化

```
安装包          1850 MB → 63 MB
安装权限        需要管理员 → 普通用户即可（装到 %LOCALAPPDATA%\Programs\MediaForge）
首次启动        出现"首次使用需要准备运行环境"卡片，点一次下载 3.18 GB
后续升级        只下 63 MB，运行时复用，不再重复下载
启动器位置      %USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd（DSH 指向这里）
```

## 内部结构变化

**模型系统合并为一套**

```
列表/下载/事件   统一走 model_hub（334 条：音频 326 + 媒体 8）
删除            list_models / start_model_download / download_model 三个旧命令
新增            scripts/compare-model-apis.py（字段对账，gap 326 → 0）
```

**运行时解耦**

```
资产             runtime-cuda-2026.10.1.zip（3.18 GB）+ manifest（sha256/size/urls）
存放             %USERPROFILE%\.mediaforge\runtime-envs\cuda
下载             aria2c 4 连接、断点续传；先校验 sha256 再解压；指针最后写
双源             hf-mirror.com（实测 5.55 MB/s）优先，魔搭（4 连接约 3 MB/s）兜底
```

**命令面清理**

```
删除不可达命令    cancel_runtime_core_update / delete_model(Tauri 层) /
                  export_editor_mix(Tauri 层) / list_audio_files / scan_editor_assets
新增对账脚本      scripts/check-command-surface.py
                  三层：Rust→worker、agent→worker、前端→Tauri
现状            0 悬空 / 0 孤儿 / 1 疑似不可达（worker_health）
```

**性能**

```
任务队列轮询     3 秒 → 10 秒，页面隐藏时完全停止
原因             每条命令都会新起一个 Python 进程，轮询即进程风暴
```

**调试命令分文件**

```
commands/debug_cmd.rs   13 个 debug_* 命令从 app_cmd.rs 拆出（-133 行）
```

## 已验证

```
安装            静默安装退出码 0，无 UAC，落在 %LOCALAPPDATA%\Programs\MediaForge
首启引导        卡片显示 3182 MB（读自编译进二进制的资产大小）
下载            3.18 GB 完成，解压 4.93 GB，active-runtime.json 内容正确
分离            真实歌曲跑通，2 stems，44.1 kHz / f32 / 立体声 / 214 秒
DSH 集成        启动器创建 + cordis 配置指向 ~/.mediaforge/bin
类型/测试        cargo check 0；vue-tsc 0；前端 1425/1430（2 个既有失败）
```

## 已知问题

```
1. 单源风险：运行时包 3.18 GB 超过 GitHub Release 的 2 GB 单文件上限，
   只能放魔搭/HF；魔搭上传多次因网络中断失败，备用源可能缺失
2. worker_health 命令在注册表里但前端无引用，尚未定性
3. app_cmd.rs 仍有 3523 行 / 106 个命令，拆分尝试 4 次失败（见技术债）
4. 安装包未做代码签名，SmartScreen 会提示"未知发布者"
5. 安装器内 VC++ 运行库步骤在 per-user 模式下跳过，依赖系统已有版本
```

## 技术债（已记录，不在本版处理）

```
* app_cmd.rs 拆分：文本级工具（正则/括号扫描）均不可靠，
  需要真实语法解析或 IDE 重构；4 次尝试均回滚，未损坏代码
* SeparateView.vue 4822 行未拆
* worker_bootstrap.cmd_update_runtime_core 与 stores/app.ts 的 runtimeCoreUpdate*
  仍是死代码，删除会破坏 store 的推导类型，需要连类型一起重构
```
