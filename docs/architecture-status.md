# 架构现状（2026-11-01 核对）

这份文件回答一个问题：**哪些地方已经只有一条路，哪些地方还有两条。**
判断标准不是“看起来整齐”，而是“同一件事是否只有一个实现”。

## 已经统一

```
命令面            manifest/protocol.json：8 个域 / 48 条命令 / handler 为 module:function
                  scripts/check-architecture.py 八条全绿（依赖方向 ×3、根目录白名单、
                  事件覆盖、事件命名、handler 存在性、catalog 关联）
Python 物理分层   python/ 根只剩 worker.py、agent_bridge.py、audio_catalog.json、
                  runtime-manifest.json；实现分布在 audio_tools / core / game /
                  media_ops / shared / surfaces
事件词表          只有 <域>.<动词>：task.started/stage/progress/log/done/cancelled/result 与
                  asset.started/stage/progress/log/done；emit() 里出现 task_* / download_*
                  这类历史写法会被检查器直接拦下（三端与测试已同步改完）
Rust 命令面       commands/app_cmd.rs 已删除，commands/app/ 下 13 个域模块 + mod.rs
模型列表          core/model_hub.py 是唯一入口，合并 audio_catalog.json（326 条）
                  与 media_ops/media_catalog.json（30 条）
运行时            内容寻址资产 runtime-cuda-2026.10.2，
                  sha256 7a1040eb…784d，3616417066 字节
安装              per-user，只有两个根：程序 %LOCALAPPDATA%\Programs，
                  状态 %USERPROFILE%\.mediaforge
Vue 拆分          测试直接实例化工厂，不再从 SFC 取声明：
                  makeInferenceDefaults / makePlayback / makeStemSelection / makeQueue /
                  makeOutputDirectory / makeExecutionLog / makeRuntimeDebugFiles
```

## 还有两条的地方

```
1. task.done 与 task.result 两个终态名
   两者都是"任务成功结束"，但载荷用途不同：
     task.done    推理/工作流的完成事件，带 files / outputs / outputDir
     task.result  工具类任务（audio_tools、media_tool、原生分析）的结果回包
   前端按通道分别处理，Rust 的终态表也分开列。合并需要先定"统一载荷"的形态，
   暂时保留，且只在 protocol.json 的 events 里声明一次。

2. 模型下载有两份实现
   活的命令是 model.download → core/model_hub_worker.cmd_model_download（走 model_hub 库）。
   但 core/worker_download.cmd_download_model 是孤儿：不在 protocol.json、dispatch 够不到，
   只有它的库函数 download_studio_model 被 worker_infer 的自动补下载调用。
   即"model_hub 下载"与"worker_download 下载"两套并存；test_worker_download.py 直接测的是孤儿命令。
   收敛方向：infer 的自动补下载也走 model_hub，删掉孤儿命令并把测试迁到库函数。

3. versions.json 还没发布
   version_check.rs 里的两个 URL（GitHub release 与魔搭）实测都是 404；
   release/ 里那份是 0.0.20 时代、还带已删除 catalog 轴的旧产物，已清掉。
   要发布的清单由 scripts/make-versions.py 生成并签名（私钥只在环境变量里）。

4. 运行时更新的协商
   已刻意不做：没有“检查最新版本”，换运行时 = 发新版应用 + 新 sha256。
   标记不匹配时提示“需要更新”，这是校验，不是协商。
```

## 死代码（明确保留，不删）

```
stores/app.ts 的 runtimeCoreUpdate*        约 63 处
worker_bootstrap.cmd_update_runtime_core
worker.rs 白名单里的 "update_runtime_core" 字符串

为什么留：删 store 里的 ref 会改变 Pinia 的推导类型，
连带 App.vue / main.ts / demoBootstrap.ts 全部失配。那是重构类型契约，
不是清理死代码，风险大于收益。功能本身已不可达（UI、命令、分派都删了）。
```

## 技术债

```
巨文件（行数）   SettingsView.vue 3765 / ModelsView.vue 2838 / SeparateView.vue 2479 /
                 DebugView.vue 1736 / stores/task.ts 2433 / stores/editor.ts 2273
                 SeparateView 与 DebugView 已按工厂拆过一轮，剩下的先把有测试契约的簇外移。
门面间接         worker_bootstrap.py 337 行门面 + bootstrap_parts 四层；
                 测试仍按门面名字打补丁，所以 wb.<name> 这层间接还在。
```

## 单点风险

```
运行时包 3.45 GB，超过 GitHub Release 的 2 GiB 单文件上限，
只能托管在 HF 镜像与魔搭。两者都不可用时，新用户无法完成首次准备。
同一原因：3.45 GB 的首次下载链路还没在真机上跑通过。
```
