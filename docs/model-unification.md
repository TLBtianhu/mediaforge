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
