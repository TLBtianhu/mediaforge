# 架构现状（2026-11-02 核对）

这份文件回答一个问题：**哪些地方已经只有一条路，哪些地方还有两条。**
判断标准不是“看起来整齐”，而是“同一件事是否只有一个实现”。

## 已经统一

```
命令面            manifest/protocol.json：8 个域 / 48 条命令 / handler 为 module:function
                  surfaces/dispatch.py 查表分派；check-command-surface.py --strict 已进 CI
架构不变量        scripts/check-architecture.py 十条全绿：依赖方向 ×3、根目录白名单、
                  事件覆盖、事件命名、handler 存在性、catalog 关联、接口投影、媒体哈希
Python 物理分层   python/ 根只剩 worker.py、agent_bridge.py、audio_catalog.json、
                  runtime-manifest.json；实现分布在 audio_tools / core / third_party /
                  media_ops / shared / surfaces
事件词表          只有 <域>.<动词>：task.started/stage/progress/log/cancelled/result 与
                  asset.started/stage/progress/log/done；终态只有一个 task.result；
                  emit() 里出现 task_* / download_* 这类历史写法会被检查器直接拦下
MCP 工具面        surfaces/agent_contract.py 同时声明 worker 与 bridge 工具，70 个工具；
                  10 个桥接专属工具无 worker 命令，5 个别名与桥接专属 schema 均由契约渲染
模型元数据        领域 catalog 各自唯一：audio_catalog.json（326 个模型、639 个文件哈希）
                  与 media_ops/media_catalog.json（23 个可下载条目全部有 SHA-256）；
                  model_registry.json 的 326 个音频接口条目由 sync_model_registry.py 原位生成
离线下载与校验    model_hub 统一单文件/目录多文件下载；model.verify 可离线检查存在性、
                  大小和 SHA-256，并区分 ok 与 verified。downloaded 仍只是存在性/大小状态，
                  不等于自动完成哈希证明
Rust 命令面       commands/app_cmd.rs 已删除，commands/app/ 下 13 个域模块 + mod.rs；
                  测试专用 helper 与 imports 已用 cfg(test) 移出生产构建
模型列表          core/model_hub.py 是唯一入口，合并 audio_catalog.json（326 条）
                  与 media_ops/media_catalog.json（含目录型 Depth/RMBG 文件清单）
运行时            内容寻址资产 runtime-cuda-2026.10.2，
                  sha256 7a1040eb…784d，3616417066 字节
分发隔离          开发=仓库 .dev-data，便携=<exe 目录>/data，安装=%USERPROFILE%/.mediaforge；
                  开发/便携/首装/升级/A→B→A 回滚均有真实进程级 E2E 与 CI
版本清单          versions.json 的 GitHub 与魔搭端点均返回 200，内容为 app 0.0.23、
                  runtime-cuda-2026.10.2，与 manifest/runtime.json 一致
Vue 拆分          测试直接实例化工厂，不再从 SFC 取声明：
                  makeInferenceDefaults / makePlayback / makeStemSelection / makeQueue /
                  makeOutputDirectory / makeExecutionLog / makeRuntimeDebugFiles
```

## 还有两条的地方

```
1. 已安装应用仍未同步
   当前安装版桥为 61 工具，仓库为 70。隔离打包与产物验证已完成，
   但用户选择不覆盖安装、不同步 DSH；注册表与 Harness 配置未改。
   若以后执行覆盖安装，先备份注册表与 cordis.patch.yml，再验证安装版工具数。

2. 运行时更新的协商
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
测试稳定性       editorParameterSlider.test.mjs 在本地全量/单文件运行中出现过不同断言失败，
                 相关用例单独运行又通过；后续应固定测试同步，不能误判成产品回归。
```

## 单点风险

```
运行时包 3.45 GB，超过 GitHub Release 的 2 GiB 单文件上限，
只能托管在 HF 镜像与魔搭。两者都不可用时，新用户无法完成首次准备。
2026-11-02 已完成真机验证：首次连接重置后通过断点续传下载完成，
大小 3616417066 字节且 SHA-256 与 manifest 一致；真实解包、激活和解释器启动通过，
Python 3.11.9、torch 2.7.1+cu128（CUDA 可用）、cv2、NumPy 与 pymss 均可导入。
因此当前风险是发布源冗余与可用性，而不是完整下载链路未验证。
```
