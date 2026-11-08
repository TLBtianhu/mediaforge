# 当前任务账本契约（schemaVersion 2）

当前任务真源为 `<data-root>/agent-jobs/<jobId>.json`。音频队列、媒体任务与Agent任务共用该目录与
当前格式；不读取或写回settings/tasks.json，不生成index/workspace列表副本，不迁移旧记录。
目录列表与前端显示均从job记录读取。旧、缺失或未来版本、坏JSON及失效引用明确报错，
原文件保留；Rust/Python磁盘读取也拒绝重复JSON键，不能忽略坏记录后报告一个成功的空列表。

## 文件与状态

每个job必需schemaVersion/jobId/command/source/status/fraction/message/error/result、
createdAt/updatedAt/startedAt/finishedAt、owner/runtime/logs。时间为有限Unix秒；createdAt不变，
updatedAt随真实写入更新，startedAt/finishedAt未发生时为null，终态必须有finishedAt。
status仅queued/running/completed/failed/cancelled/interrupted；终态不可倒退，running必须有typed owner。
source仅manual/agent；result为object或null；结构化日志有界保存，不另存nodeLogs分组副本。

owner为null或持有对应owner lease的tauri-worker/operation-kernel身份。PID只作诊断，不能替代lease。
owner=null的queued音频尚未交给worker，必须保留并允许后续调度；不是中断任务。
执行中的owner失去lease才由后端将其恢复为interrupted，前端重载不自行把活任务改成失败。

## 音频批次只保存一份共享请求

每个输入仍有独立job文件与result。批次首条作为primary，只保存一份实际common worker payload，
其中可含model或workflow图、推理参数、设备与批次输出根；不再内嵌一份每个输入的完整任务列表。
primary的audio.memberJobIds只保存有序ID列表，且包含自身。子任务仅有payloadRef指向primary。
每条audio.request只保存自身input/output/inputIndex，以及可选的inputs/workbenchOutputs绑定差异。

primary可有小型presentation：workflowId/sourceUpdatedAt/workbench.nativeGraph，用于恢复界面来源；
不保存另一份workflowDefinition、完整runConfig或完整任务快照。没有独有元数据时可省略presentation。
common payload若显式提供inferenceParamsVersion，必须为当前整数3；缺省才使用当前参数默认值。
子任务不得复制payload、presentation或membership。组内command/source、primary、索引和成员必须一致；
禁止缺父、缺子、跨组引用或循环引用。

空外部输入用null（原生图可从自身输入节点读取文件）；非空输入及输出保留原路径字符。
inputIndex若提供为从1开始的整数；绑定是节点ID到路径字符串的object。

执行请求只在内存中展开：保留common.output批次根，tasks由相应leaf的request加taskId组成。
单条重试也以单leaf batch展开，避免把leaf输出目录误当批次根再次套目录；重试生成新ID，
不得沿用旧taskId/requestId向旧任务发送事件，也不把展开后的payload回写子任务。

## 写入与UI边界

- `enqueue_audio_jobs`校验整组并完成当前job落盘后返回确认，前端才开始调度。
- `start_separation` / `start_workflow_inference`仅传已入队的jobId。后端读盘claim，
  实际参数取自已确认记录，不能再由前端发送一份执行参数覆盖。
- owner lease随执行生命周期持有，普通进度写按单job write锁原子替换。
- 后端观察worker事件，持久化状态、真实结果、详细stage/progress、节点状态/顺序与结构化日志。
  recoverable错误只记录诊断，不提前终态；失败或取消保留已知部分产物。
- worker仅使用临时payload.json传输请求，回收本次自建进程后删除该文件，保留诊断日志和其它工作区产物。
- 前端只维护内存投影：files来自outputs，nodeLogs从结构化日志分组，duration/stageLabel等派生，
  不再通过防抖autosave写整个任务历史，也不能回写status/result。
- 完整列表是权威快照；缺席的旧记录应移出UI，且迟到刷新不能盖掉新事件或复活已删除记录。

## 取消、删除与保留策略

排队单leaf可原子取消，不用等待一个不存在的worker监听.cancel文件。primary取消后仍能作为
共享请求锚点，其他queued成员可从任一leaf发起claim；本次worker使用首个仍待执行leaf作为运行ID。
共享worker执行时取消须结束该worker及其活动成员；workbench整组取消要阻止中间调度。

记录删除按完整组进行，必须全终态并确认没有活动owner，保留所有lock文件inode。
Kernel默认retention只作用于Agent历史；manual音频历史保留到显式删除。任何清理均须保护完整引用组，
不能删掉仍被child引用的primary；不隐式把列表裁成最近100条。
Kernel清理子进程失败时继续持有owner lease与临时请求文件，终态记录也不可被retention删除。
同一Kernel可通过再次cancel有限重试清理，确认wait回收后才释放；没有自动后台重试保证。
结果删除先用`remove_audio_jobs(checkOnly=true)`确认全终态且释放lease，再处理用户明确选择的文件/工程清理，
最后提交元数据删除。删除记录不等于删除媒体文件，不自动清理用户资产。

媒体/Agent通用结果使用`remove_result_jobs(jobIds, deleteFiles=false, checkOnly=false)`，不接收前端文件列表。
仅接受当前结果命令白名单的非audio记录，整次选中记录须全终态且owner lease已释放；audio仍走完整组专用入口。
文件由后端按当前命令的明确结果字段推导，不扫输出目录，不把failed输入、scalar或任意嵌套路径当成产物。
支持infer/media的outputs描述符、工具outputPaths/outputPath以及operation的audio_file描述符；旧MIDI报告
存在猜取旧文件的归属风险，故可删记录但保留其文件。producer已移除目录glob回退，缺明确现存绝对文件报告则失败。

catalog及所选owner/write锁覆盖引用检查、文件动作和记录删除；已存编辑工程读取至文件动作复用JSON写锁，
防本进程保存穿过检查。输入、其它任务的输入/已知产物、活动任务声明输出范围、编辑工程引用、模型与运行时、
程序及非outputs应用数据受保护；链接/reparse祖先、非普通文件和无法可靠检查的路径保留。
程序与resource目录采用整根保护；便携输出若嵌在程序目录下也保守保留，不为回收输出放宽程序资产边界。
任意打开的编辑器可能有未保存引用，故保留文件；library.db存在时尚无完整只读引用查询及统一资产锁，
候选文件均保留，不根据有条数上限的UI列表猜测。native媒体库调用与删除共用JSONguard，覆盖首次建库窗口；
模型登记四条写命令持进程内mutation lease至实际reap，删除持同gate读快照至回收，busy或退出未确认即保留文件。
此保护不宣称任意外部进程修改的事务一致性。

默认仅删记录；显式勾选文件回收才执行，无永久删除回退，不删除输出目录。响应固定schemaVersion2及
checkedJobIds/removedJobIds/trashed/failed[{path,error}]/retained[{path,reason}]。
任一文件回收失败则本次全部任务记录保留，可能已有部分文件移入回收站；UI须再次明确确认才能只删记录。
受保护文件的retained不是回收失败，UI必须告知保留数量，不能报告全部文件已回收。预检无文件/记录删除动作，
最终提交重新检查；前端仅按后端removedJobIds安装内存删除标记，不另存删除清单或回写job生命周期。
Windows使用专用STA线程与recycle-only/no-UI操作；自动化以临时文件和注入回收器验证失败/引用边界，
不触发系统回收站或GUI。export_editor_mix及未知输出形状尚未纳入结果页，不承诺任意插件文件的回收归属。
音频结果仍使用前述既有独立文件清理流程，尚未迁入本轮通用结果的native资产保护入口；
其跨媒体/Agent、素材库及模型登记引用互斥需要后续统一，不能称所有结果文件回收已采用同一保护协议。

目录成员增删与引用闭包读取共用`.catalog.lock`，单job写共用`.writes/<id>.lock`，
执行归属共用`.owners/<id>.lock`，Rust与Python使用同一锁协议。
catalog读列表时也按对应write锁读取单文件，避免Windows原子替换与打开文件相撞；已持write锁的内部读取不重复加锁。
多文件操作失败时只回滚本次明确拥有的路径；遗留不完整事务或损坏引用报错，不能静默重建。

## 共享处理任务配额

`manifest/execution-quota.json`是覆盖命令、默认1/最大4和配置字段名的唯一来源。
配额读取同data-root的`settings/app.json`中现有`maxConcurrentSeparations`，不另存调度配置或队列索引。
Rust/Python共用`.quota/global-0.lock`至`global-3.lock`的无正文OS lease；锁文件保留inode，释放不删除。
领取时在catalog内检查全部固定slot，包括当前上限之外仍在运行的高位slot，因此4降到1不提前接纳新worker。

音频批次占一个slot。通用手工任务与Agent处理任务先落ownerless queued，成功领取后才写running/startedAt、
持owner及slot并启动worker。资源不足是正常排队；排队取消不创建payload/进程，取消音频primary仍可执行其余leaf。
结果先终态也不能提前释slot；正常退出、取消和启动失败均确认reap后才归还。Python清理失败保留两项lease，
Rust线程接管失败/unwind也由owned child守卫先回收再释锁。

Rust以每应用一个内存pending表/dispatcher接回任务，重启仅恢复通用manual queued；音频仍由前端确认后交接。
Kernel以一个可关闭、弱引用pump恢复Agent queued；双方不代跑另一来源。前端常驻每2秒串行同步账本，
不依赖Queue路由；ACK丢失经权威刷新后有界重发，同组登记幂等，等待不会伪造failed。

本阶段只共享同数据根的处理worker上限；各执行器内部FIFO，不承诺全局FIFO、跨数据根配额或物理GPU分区。
查询、下载、运行时管理不占处理slot；原有运行时/应用互斥机制仍独立。执行器自身崩溃后的孤儿子树监督，
仍须按跨平台进程生命周期债务继续完善。

## 验证入口

`tests/fixtures/jobs/current-contract.json`是三层共同的格式样例，含合法音频primary/child、
无外部输入的原生图、无presentation的模型任务、独立Agent记录和紧凑的拒绝案例。
它仅验证存储契约，不代表其中占位模型或空图可以执行。
Python conformance、Rust schema/CRUD/owner/ref测试和前端codec/实际store测试共同验证读取及写入边界。
`tests/fixtures/jobs/quota-contract.json`补充两后端共用的21个配额配置样例；真实Rust/Python进程验证双向互斥与退出释锁。
所有验证须遵守DEVELOPMENT隔离与Windows隐藏子进程规则，不启动GUI或使用用户profile。

已删除三个依赖旧task-history、旧事件入口或重复工作流快照的手工UI脚本；其旧实现可从Git历史查看。
记录删除的自动化覆盖已转入当前store及Rust账本测试。真实工作流画布颜色、重载等视觉验收仍待使用
当前契约重建隔离脚本，并在用户允许GUI验证后执行；不能把旧视觉脚本历史通过当作本轮验收。
