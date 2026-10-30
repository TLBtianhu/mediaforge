# 架构现状（0.0.16 收尾时）

这份文件回答一个问题：**哪些地方已经只有一条路，哪些地方还有两条。**
判断标准不是"看起来整齐"，而是"同一件事是否只有一个实现"。

## 已经统一

```
模型列表        唯一来源 model_hub（334 条：音频 326 / 图像 5 / 视频 3）
                 GUI 与 agent 都走它，实测 count=334 一致
模型下载        唯一路径 start_model_hub_download → worker model_download
运行时          内容寻址资产（runtime-cuda-2026.10.2，sha256 钉死在二进制里）
                 存放 ~/.mediaforge/runtime-envs/cuda，装完写 .asset-id 标记
安装             per-user，仅两个根：程序 %LOCALAPPDATA%\Programs，状态 ~/.mediaforge
命令面           三层对账：Rust→worker / agent→worker / 前端→Tauri
                 当前 0 悬空、0 孤儿、0 注册后不可达
日志/诊断命令    单独成文件 commands/debug_cmd.rs（13 个命令）
```

## 还有两条的地方

```
1. 事件通道
   audio_tool_*（全应用处理进度：转码/超分/分离/工作流）
   download_*（模型下载）
   模型库下载目前两份都发（兼容期）。收敛方向：统一信封，再删一份。

2. 模型目录的后端实现
   UI 层已统一到 model_hub，但 model_hub 内部仍要合并
   audio_catalog.json + media_catalog.json + pymss registry 三份数据。
   worker_infer 里的"查不到就回落音频目录"就是这层的补丁。

3. 运行时更新的协商
   已刻意不做：没有"检查最新版本"。换运行时 = 发新版应用 + 新 sha256。
   标记不匹配时提示"需要更新"，这是校验，不是协商。
```

## 死代码（明确保留，不删）

```
stores/app.ts 的 runtimeCoreUpdate*      约 57 处
worker_bootstrap.cmd_update_runtime_core
worker.rs 白名单里的 "update_runtime_core" 字符串

为什么留：删 store 里的 ref 会改变 Pinia 的推导类型，
连带 App.vue / main.ts / demoBootstrap.ts 全部失配。那是重构类型契约，
不是清理死代码，风险大于收益。功能本身已不可达（UI、命令、分派都删了）。
```

## 技术债

```
app_cmd.rs 3523 行 / 106 个命令
  四次拆分尝试全部回滚：行首判尾、属性行切块、括号扫描+行号、括号扫描+字节偏移。
  最后一次我的扫描说"深度 0"，rustc 说 unclosed —— 说明文本级工具不可靠。
  下次需要真实语法解析器（rust-analyzer / syn）或 IDE 重构。

SeparateView.vue 4822 行，未拆。
```

## 单点风险

```
运行时包 3.48 GB，超过 GitHub Release 的 2 GiB 单文件上限，
只能托管在 HF 镜像与魔搭。两者都不可用时，新用户无法完成首次准备。
缓解：保留 full 安装包变体（未做）。
```
