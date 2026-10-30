# 模型系统统一（未完成，设计已定）

现状：**两套模型系统并存**，症状是"模型库里已下载，音频处理页却显示没有可用模型"。
0.0.15 只补了一根线（下载完成后刷新共享 store），根因还在。

## 1. 两套系统对照

| | A 套（模型库页面） | B 套（音频/图像页面） |
|---|---|---|
| 列表命令 | `model_hub` → `python/model_hub.py` | `list_models` → `worker_models.cmd_list_models` |
| 数据来源 | `audio_catalog.json`（326）+ `media_catalog.json`（8） | pymss registry（656 索引）+ 音频目录 |
| 下载命令 | `start_model_hub_download` → `model_hub_worker.cmd_model_download` | `start_model_download` → `worker_download.cmd_download_model` |
| 事件通道 | `audio_tool_progress` / `audio_tool_result` | `download_progress` / `download_done` |
| 前端列表 | `ModelHubView` 自己的局部状态 | 共享 `stores/model.ts` |
| 完整性校验 | 有 sha256 + 大小 | 只比大小 |
| 卡死保护 | 有下载看门狗 | 无 |

**A 套明显更完整**（sha256、watchdog、模型库 UI 都走它），所以统一方向是 **A 为唯一真相**。

## 2. 目标状态

```
一条列表命令     model_hub            （音频 326 + 媒体 8 = 334，带 sha256/downloaded/localState）
一条下载命令     start_model_hub_download
一条事件通道     download_progress / download_done / download_log
一个前端 store   stores/model.ts      （ModelHubView 不再自己 load）
```

## 2.5 进度（2026-10-30）

```
[x] 3.1 model_hub_worker 同时发 download_started/progress/done（旧 audio_tool_* 保留）
[x] store 在 download_done 后重拉一次列表
[ ] 3.2 model_hub 条目字段对齐 store 的形状
[ ] 3.3 store 列表数据源改为 model_hub
[ ] 3.4 ModelHubView 去掉局部列表
[ ] 3.5 删除 B 套（worker_download 分支、Rust 命令、单测）
```

真实下载验证（HTDemucs4）：`download_started ×1 / download_progress ×64 /
download_done ×1`，且旧的 `audio_tool_*` 同时照发——两条通道并行期间前端可随时切换。

## 2.6 对账基线（2026-10-30 实测）

`python scripts/compare-model-apis.py`：

```
A(model_hub)     334 条
B(list_models)   326 条
只存在于 B 的模型  0          ← 模型集合已对齐
缺必需字段的条目  326 / 326    ← 字段尚未合并
缺少的字段        supported modelPath configPath sizeBytes slots slotTask modelType
```

结论：统一不是"合流两个数据源"，而是**把 B 的解析结果并进 A 的条目**。
B 的判定逻辑（`worker_models.model_path_for` / `config_path_for` / slots 解析 /
supported 判定）已经存在，直接复用，不要重写。

验收命令：

```powershell
python scripts/compare-model-apis.py --strict   # 退出码 0 才算完成
```

## 2.7 字段合并结果（2026-10-30）

`_audio_record` 补齐了选择器要读的字段（值全部来自 catalog 与已解析路径，
没有新增判定逻辑）：

```
supported / sizeBytes / modelPath / configPath / source / modelType / targetStem
```

对账（对账规则也修正为"只在 B 有值而 A 缺失时算缺口"）：

```
A 334 条 / B 326 条
只存在于 B 的模型  0
缺必需字段的条目   0 / 326
```

注意 `slots` / `slotTask` 在两边**都为空**：音频页的槽位面板走的是
`model_interface` 命令，不从列表里取。所以它们不构成切换阻塞，但切换后要单独验
一次槽位面板。

下一步：`src/stores/model.ts` 换数据源 → 三页联调 → 删 B 套。

## 2.8 列表已统一，下载还没（下一步的真正难点）

现状（2026-10-30）：

```
stores/model.ts:803   invoke('model_hub')                 ← 列表已统一
stores/model.ts:1164  invoke('start_model_download')      ← 下载仍走 B 套
ModelHubView.vue:173  invoke('start_model_hub_download')  ← 下载仍走 A 套
```

也就是说**两个页面看同一份列表，但下载还是两条路**。

切下载时要对齐的是"身份"：

* B 套按 `taskId`（`download_<...>`）关联事件，store 的处理器用
  `taskId?.startsWith('download_')` 判断归属；
* A 套按 `requestId` 关联，事件里没有 `taskId`。

所以直接改 invoke 会让进度条**静默不动**——事件到了，但 store 认不出是自己的。

两种收敛方式（择一，别混）：

1. **A 套补 taskId**：`start_model_hub_download` 接受并透传 `taskId`，
   事件里带上同一个 id，store 不用改判定逻辑。
2. **store 认 requestId**：把 `downloadTaskIndex` 改成 requestId 键，
   同时保留 taskId 兼容。

推荐 1：身份是后端产生的，前端只消费；改动集中在一处。

验收：下载中进度条连续更新、完成后两个页面同时显示"已下载"、中途取消能回到未下载。

## 3. 改动清单（按依赖顺序）

### 3.1 后端：让 A 套发出统一事件

`python/model_hub_worker.py` 现在发 `audio_tool_*`。改成同时发标准下载事件：

```python
emit("download_started", {"model": name, "taskId": task_id}, task_id=task_id)
emit("download_progress", {
    "model": name, "progress": fraction,
    "completedFiles": done, "totalFiles": total,
    "aggregateDownloadedBytes": ..., "aggregateTotalBytes": ...,
    "speedBytesPerSecond": ...,
}, task_id=task_id)
emit("download_done", {
    "model": name, "downloaded": [...], "skipped": [...],
    "modelInfo": {...},      # 必须带，前端靠它 upsert
    "modelDir": str(root),
}, task_id=task_id)
```

保留 `audio_tool_*` 一版作为兼容（或一次性删净，二选一，别长期并存）。

### 3.2 后端：让 `model_hub` 覆盖 B 套的全部信息

B 套目前返回 326 条（和 audio_catalog 一致），字段里有 `supported/downloaded/modelPath`。
统一后 `model_hub` 的条目要补齐前端 store 需要的字段：

```
required: name, label, category, categoryCn, supported, downloaded, sizeBytes,
          modelPath, configPath, task/capabilities（若有）, source
```

### 3.3 前端：store 换数据源

`src/stores/model.ts`

```diff
- invoke('list_models', { payload: {...} })
+ invoke('model_hub', { modelDir: settings.modelDir || null })
- invoke('start_model_download', { payload: { model, taskId } })
+ invoke('start_model_hub_download', { requestId, payload: { modelId, modelDir, source } })
```

保留 store 的 `download_progress/download_done` 处理器（3.1 之后它们会真的收到事件）。

### 3.4 前端：ModelHubView 去掉自己的列表

`src/views/ModelHubView.vue` 改成纯展示共享 store：

```
删除：本地 list ref、load()、自己的 listen()
使用：modelStore.models / modelStore.loadModels() / modelStore.startDownload()
保留：页面特有的筛选、排序、统计（都基于 store 数据算）
```

### 3.5 清理

```
删 python/worker_download.py 里只服务 B 套的分支（保留被 model_hub 复用的工具函数）
删 start_model_download / list_models 两个 Rust 命令与注册
删对应单测，改成断言"这两个命令不存在"
```

## 4. 风险与验证

| 风险 | 缓解 |
|---|---|
| 事件形状不一致导致进度条不动 | 3.1 先发、3.3 后切，中间用日志对比一次真实下载 |
| `modelId` vs `model` 字段命名差异 | 统一用 `modelId`，后端同时接受 `model` 兼容 |
| 删 B 套后某页仍调旧命令 | `vue-tsc` + 全量前端测试 + 手动逐页点开 |
| 图像/视频模型走哪条路 | 它们已经在 A 套（media_catalog），确认 `model_hub` 覆盖即可 |

**验收**：

```
1. 模型库下载一个音频模型 → 音频处理页立刻可见（不刷新）
2. 删除该模型 → 两个页面同时消失
3. 下载中断 → 两个页面都显示"下载中"且进度一致
4. 图像页、视频处理页、媒体工作流页行为不变
```

## 5. 为什么不在 0.0.15 做

0.0.15 先修"音频页看不到已下载模型"这个阻断性问题（一行刷新），让用户能继续用；
统一是结构性改动，动的是下载主链路，值得单独一版 + 全量回归。
