# 架构现状（2026-11-08 核对）

这份文件回答一个问题：**哪些地方已经只有一条路，哪些地方还有两条。**
判断标准不是“看起来整齐”，而是“同一件事是否只有一个实现”。

## 已经统一

```
命令面            manifest/protocol.json：8 个域 / 48 条命令 / handler 为 module:function
                  surfaces/dispatch.py 查表分派；check-command-surface.py --strict 已进 CI
命令字段契约      全部 8 个域 / 48 条命令已声明 payload/result schema；dispatch 先校验 payload
前端契约边界      worker 契约调用集中在 src/api/*；scripts/check-frontend-invokes.py 扫描
CSP              tauri.conf.json 设置生产 csp 与 devCsp（Vite websocket/开发脚本单独放行）；
                  禁止生产 script-src 'unsafe-eval'，asset/plugin 协议显式列入
                  src/ 其余直接 invoke，只允许 media_precheck 与 manage_plugin 两个已记录例外
模型库分流        主音频模型库 = list_models（完整 ModelEntry/分类/debug 状态）；
                  媒体目录 = model_hub（id/provider/modality/downloadable 行）
模型库分流        主音频模型库 = list_models（完整 ModelEntry/分类/debug 状态）；
                  媒体目录 = model_hub（id/provider/modality/downloadable 行）
Tauri 命令映射     manifest/tauri-commands.json（137 条，由 Rust #[tauri::command] 生成）；
                  sync-tauri-commands.py --check 与 check-tauri-command-map.py 进 CI，
                  保证注册表、worker 路由、src/api invoke 名三处一致
                  （PAYLOAD_INVALID）；48 条命令都声明 resultSchemaEvent，dispatch 在
                  emit 写管道前校验结果（RESULT_CONTRACT_INVALID）；生成类型并进 CI；
                  check-architecture.py 第 13 条要求每条命令都带 resultSchemaEvent
架构不变量        scripts/check-architecture.py 十三条全绿：依赖方向 ×3、根目录白名单、
                  事件覆盖、事件命名、handler 存在性、catalog 关联、接口投影、媒体哈希、
                  命令字段契约
Python 物理分层   python/ 根只剩 worker.py、agent_bridge.py、audio_catalog.json、
                  runtime-manifest.json；实现分布在 audio_tools / core / third_party /
                  media_ops / shared / surfaces
事件词表          只有 <域>.<动词>：task.started/stage/progress/log/cancelled/result 与
                  asset.started/stage/progress/log/done；终态只有一个 task.result；
                  emit() 里出现 task_* / download_* 这类历史写法会被检查器直接拦下
MCP 工具面        surfaces/agent_contract.py 同时声明 worker 与 bridge 工具，70 个工具；
                  10 个桥接专属工具无 worker 命令，5 个别名与桥接专属 schema 均由契约渲染
模型元数据        领域 catalog 各自唯一：audio_catalog.json（326 个模型、639 个文件哈希）
                  与 media_ops/media_catalog.json（24 个可下载条目全部有 SHA-256）；
                  model_registry.json 的 326 个音频接口条目由 sync_model_registry.py 原位生成
离线下载与校验    model_hub 统一单文件/目录多文件下载；model.verify 可离线检查存在性、
                  大小和 SHA-256，并区分 ok 与 verified。downloaded 仍只是存在性/大小状态，
                  不等于自动完成哈希证明
Rust 命令面       commands/app_cmd.rs 已删除，commands/app/ 下 13 个域模块 + mod.rs；
                  测试专用 helper 与 imports 已用 cfg(test) 移出生产构建
模型列表          core/model_hub.py 是唯一入口，合并 audio_catalog.json（326 条）
                  与 media_ops/media_catalog.json（含目录型 Depth/RMBG 文件清单）
运行时            本地新资产runtime-cuda-py312-2026.11.7：实际Python3.12.13，
                  torch2.7.1+cu128，3794089293字节，sha256 3a644265…ce84；未上传
分发隔离          开发=仓库 .dev-data，便携=<exe 目录>/data，安装=<安装根>/data；
                  安装模型/运行时为同根兄弟目录，显式DATA_ROOT隔离覆盖默认推导
版本检查          每次从发布源刷新并验签；document/signature 单文件原子缓存，
                  失败不把旧缓存报告为最新；仅当前原子cache envelope1与versions文档2
运行时事务        runtime_assets/transaction.rs 持有跨进程锁，隔离解包/自检/标记，
运行时租约        .runtime-envs/.runtime-lease.lock：worker 持共享租约，安装/激活/删除
执行器生命周期    Windows worker 绑定 Job Object；Studio 启的 worker 另有 stdin 管道
                  watchdog，Unix 用独立进程组 + killpg 回收 FFmpeg/tool 子树
                  持独占租约；Rust worker、MCP/CLI 的 worker.py 共用该门禁
                  完成后发布目录、原子切换指针；失败恢复目录与指针，保留续传包
本地打包          只带实际3.12 bootstrap与完整worker/manifest/SDK/tools，资产独立构建；
                  源EXE及Python版本在stage变更前检查，fresh bootstrap不自动删除旧目录
发布契约          Windows/macOS/Linux脚本统一MediaForge产物、manifest与隔离根；
                  updater只写本次成功平台，stable版本通知单独验签且拒绝回退
媒体任务          Rust supervisor在启动前写单job原子记录，owner lease判定中断；
                  Pinia mediaJobs常驻监听，四媒体页切走后继续更新、重进恢复提交快照
持久真源          音频/媒体/Agent统一schema2逐job账本，无tasks.json/index/workspace副本；
                  批次common payload/图只存primary，child引用，files/nodeLogs/耗时仅读时投影
当前格式          jobs2/params3/workbench2/settings1/workflow wrapper1/Comfy0.4/editor4；
                  缺失/旧/未来结构拒绝且保留原文件，无backend回退/隐式迁移/升级备份
ABI门禁           worker与安装器实际核对3.12；资产校验解释器/DLL/扩展/prefix，
                  不从bootstrap覆盖解释器；旧venv在任何恢复、pip或状态写入前拒绝
后台控制台        worker/FFmpeg/探针与测试显式隐藏Windows子进程；Rust共享hidden_command，
                  venv及ensurepip专用隐藏控制台供孙进程继承，日志/退出码/取消保留
流式进程回收      视频逐帧和媒体格式转换共用StreamProcess：并行排空stderr，正常/异常
                  都等待回收并关闭管道/线程；等待超时有界，截断帧拒绝，保留错误尾
队列与产物        音频durable enqueue ACK后调度、start仅jobId，后端持久状态/节点/日志；
                  results增加独立媒体/工具/Agent文件区，保留失败或取消的部分产物
共享执行配额      UI/媒体/MCP共用同数据根OS slot lease与现有并发设置，批次一slot；
                  ownerless queued→原子claim→进程回收归还，前端只交接并常驻同步
安装冒烟          同源码/stage重编唯一测试身份，核对app/全文件与bootstrap health；
                  正式安装包只hash，默认无GUI，超时/身份异常保留现场
媒体方案          media-workflows独立版本库，命名保存/加载/更新/删除只保存方案；
                  Rust读改写事务与revision防止窗口覆盖，损坏库不按空库覆盖
串行支持          workflow_contract按模态声明真实支持/参数/单步约束；UI与worker
                  共用字段，执行前校验全计划，RIFE/未接入能力留在单任务入口
媒体输入          GUI/MCP/CLI统一显式steps；旧mode/modelId/factor转换与旧图像
                  process入口已删，不接受不支持参数的“旧默认值”例外
媒体验证隔离      smoke入口要求显式解释器/模型/数据，合成输入、临时输出与缓存；
                  不自动发现已安装用户运行时，不为测试自动下载资产
Vue 拆分          已拆分簇的测试直接实例化下列工厂，其余部分测试仍提取SFC实际函数：
                  makeInferenceDefaults / makePlayback / makeStemSelection / makeQueue /
                  makeOutputDirectory / makeExecutionLog / makeRuntimeDebugFiles
主题 token        check-theme-tokens.py 四条：字面颜色按理由登记并按文件预算棘轮、var(--x) 必须有定义、
                  theme.ts 与 global.scss 同名同集、过期登记即失败；未定义变量已清零，
                  幽灵词表（--text-color-1/2/3、--divider-color 等）已映射到现有 token
```

## 还有两条的地方

```
1. 调度与持久存储
   持久存储已统一逐job/schema2，排队/运行/结果无音频history镜像；共享锁及引用组删除已落地。
   列表读取已有增量：Rust 与 Python 都按文件指纹缓存已校验记录，未变化的记录不重解析、不再逐个抢写锁；
   `agent_jobs` 的 `schemaVersion 3` 信封支持 `known` 增量请求与 `removed` 删除语义，并保留权威全量对账。
   仍存在的两条：各执行器运行本来源、内部FIFO（跨进程全局FIFO/物理GPU分区未做）；
   增量只省传输与解析，目录成员与全部记录仍每次按当前格式校验；Peak/Python 共享记录为只读约定。
   UI/媒体/MCP共享并发配额已落地，各执行器仍运行本来源、内部FIFO；跨进程全局FIFO/物理GPU分区未做。
   媒体/Agent结果支持单条/批量移除记录及显式选择回收文件；同catalog内检查终态与owner释放，
   后端从当前job结果识别文件，保护输入/其它任务/编辑工程/程序与模型运行时，不递归清目录。
   Windows已从library.db原库只读全量引用，跨进程asset lease覆盖库writer与回收；正常库仅保护真实path/thumbnail命中。
   schema1/MFLB/DELETE由单manifest声明，旧0/坏库/WAL/忙/未知格式保留文件、不修复；非Windows已有库仍保守拒绝。
   打开的编辑器、旧MIDI归属不明产物也保留。Bridge/native数据根已显式绑定，缩略图只清自有缓存。
   音频失败/取消/中断、无文件终态与完整混合批次已展示，active批次不可整组删除；清空只处理全终态组。
   export_editor_mix以唯一result.path进入统一展示/回收，Kernel与UI识别真实完成/取消事件。
   遗留工程清理由原生完整catalog复核任务/导出/路径引用，不信任前端可见ID，清理不扩大显式选择。
   三种工程删除已统一当前任务/独立工程/模型/素材库快照；相对文件引用拒猜，目录祖先相交也保护，
   任一编辑器打开即阻止删除，整批及嵌套reparse预检先于删除。保存与自定义模型writer共享asset lease，
   不能新添已删除的内部文件引用；既有缺失源保留重链，外部第三方writer不宣称参与锁协议。
   保存sources与真实导出assets分开校验，默认exports归属检查使用真实worker投影，无额外持久副本。
   录音/波形发布也持lease并重验工程存在，派生缓存拒reparse；便携data允许清理，实际程序/资产仍保护。
   MCP自定义模型导入与peaks契约已对齐真实参数，导入经Kernel执行；两种模型查询根一致，坏registry不吞空。
   未知插件输出形状仍不猜文件；Windows真实回收与完整GUI仍未验收。
   音频回收已接入同一native资产引用gate与事务，primary/child入口均严格扩整组；前端重复路径planner已删。
   结果删除保留关联编辑工程，工程引用文件亦保留；无清理引用工程再绕过回收保护的自动步骤。
   manual历史不受Kernel默认Agent保留策略清理；旧schema与遗留任务文件保留、不迁移。
   当前完整原生UI、崩溃中途多文件事务恢复与跨平台后代监督仍需独立验收。

2. 本地候选与正式分发
   当前Windows候选版本0.0.34，从当前干净源码重建，复用既有3.12 bootstrap；
   具体源码提交、产物哈希及独立身份无GUI验收以release/candidates/0.0.34/verification.json为准。
   旧候选保留各自原始证据，不把0.0.33安装包当作包含最近源码变更。
   0.0.33历史本地原生及安装/便携/更新包已生成；独立AppId的31→33升级、
   静默卸载保留4项资产、真实3.12资产headless首装/拒旧ABI/坏哈希/离线复用、
   小资产A→B→A回滚与CPU两步真实超分均通过。安装器保留文件不等于结构迁移。
   未覆盖用户安装版或同步DSH，未推送/打tag/发布；真实签名与远端CI仍未执行。
   macOS/Linux仅发布契约与隔离fixture验证，原生包和完整桌面UI仍未验收。
   用户禁止ComputerUse；后续保持文件/CLI操作，不触碰已打开的旧测试窗口。
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
巨文件（行数）   SettingsView.vue 791 / ModelsView.vue 984 / SeparateView.vue 2014 /
                 DebugView.vue 1395 / stores/task.ts 1155 / stores/editor.ts 2001
                 SeparateView 与 DebugView 已按工厂拆过一轮，剩下的先把有测试契约的簇外移。
门面间接         已移除：worker_bootstrap.py 合并为普通模块，bootstrap_parts 分包已删除。
测试稳定性       editorParameterSlider.test.mjs 已统一焦点、active 状态和生命周期拖拽准备；
                 plugin dialog fixtures 消除关闭时的二次 panic，以 pending 语义替代墙钟断言；
                 定向连续 20 轮、完整前端与综合 CI 均通过
```

## 单点风险

```
新3.12资产3794089293B（3.53GiB），仍需独立资产源托管，不能塞进程序安装包。
本轮只本地生成并验证，未上传HF/魔搭、未签名或正式发布；完整UI与Mac/Linux原生未验。
用户明确退役的两个项目3.11开发环境、旧运行时ZIP/描述符及retained生成源已完成精确清理；
约17.3GiB旧文件已删除，当前3.12启动/torch导入与六项保护哈希复核通过，用户/狗粮、模型及构建缓存未动。
```
