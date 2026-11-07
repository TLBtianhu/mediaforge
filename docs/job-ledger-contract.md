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
最后提交元数据删除。删除记录不等于删除媒体文件，本轮不新增媒体产物统一删除或自动清理用户资产。

目录成员增删与引用闭包读取共用`.catalog.lock`，单job写共用`.writes/<id>.lock`，
执行归属共用`.owners/<id>.lock`，Rust与Python使用同一锁协议。
多文件操作失败时只回滚本次明确拥有的路径；遗留不完整事务或损坏引用报错，不能静默重建。

## 验证入口

`tests/fixtures/jobs/current-contract.json`是三层共同的格式样例，含合法音频primary/child、
无外部输入的原生图、无presentation的模型任务、独立Agent记录和紧凑的拒绝案例。
它仅验证存储契约，不代表其中占位模型或空图可以执行。
Python conformance、Rust schema/CRUD/owner/ref测试和前端codec/实际store测试共同验证读取及写入边界。
所有验证须遵守DEVELOPMENT隔离与Windows隐藏子进程规则，不启动GUI或使用用户profile。

已删除三个依赖旧task-history、旧事件入口或重复工作流快照的手工UI脚本；其旧实现可从Git历史查看。
记录删除的自动化覆盖已转入当前store及Rust账本测试。真实工作流画布颜色、重载等视觉验收仍待使用
当前契约重建隔离脚本，并在用户允许GUI验证后执行；不能把旧视觉脚本历史通过当作本轮验收。
