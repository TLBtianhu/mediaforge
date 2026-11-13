# 媒体能力验收（2026-11-07）

所有验证遵守 [DEVELOPMENT.md](../DEVELOPMENT.md)：使用明确的测试解释器、模型根、数据根与临时 WebView profile。不得自动发现已安装应用或用户/狗粮目录，也不得为测试复制整套多 GB 运行时。

## 静态检查与真实模型检查

仅比对依赖声明，不需要权重，不会自动切换到用户运行时：

```powershell
python scripts/check-media.py --deps
```

真实模型冒烟必须显式选择测试资产。以下路径以已有开发 CPU 环境为例，不表示它包含全部模型依赖或权重：

```powershell
python scripts/check-media.py --smoke `
  --python .dev-data/runtime-envs/cpu/Scripts/python.exe `
  --model-dir .dev-data/models `
  --data-root .tmp/media-model-smoke-profile
```

省略 `--smoke` 会同时运行依赖声明检查。无参数运行会拒绝真实模型检查，这是隔离保护，不是自动改用用户环境的入口。可用相同参数直接运行 `scripts/smoke-media-models.py`；`--help` 不执行推理或发现模型。

- `--python`、`--model-dir`、`--data-root` 可来自明确设置的测试环境变量；继承的其它应用路径不会传给 worker。
- 输入默认生成小型合成 PNG；`--input` 只读取明确选择的文件，不扫描历史输出。
- 输出默认放在本次创建的临时目录，子进程结束后清理；指定 `--output-dir` 时保留输出，不自动删除该目录。
- 子进程的数据、模型、运行时、HOME、WebView、HF/ModelScope/Torch 缓存和临时路径均隔离，禁止自动下载。
- 缺少权重会报告失败；没有对应单任务适配器的 catalog 条目会明确 skip。必须有实际成功项，不能用“全部跳过”冒充验收通过。
- 旧水印双模型组合已移出单模型冒烟，组合应通过媒体工作流单独验证。

高级音频图工作流的真实冒烟同样要求显式 `--python`、`--data-root`、输入、输出及模型目录，并通过子进程 worker 执行。运行前先看 `tests/manual/advanced_workflow_real_smoke.py --help`。

## 单任务与媒体串行工作流的支持边界

支持声明由 `python/media_ops/workflow_contract.py` 提供，`media_capabilities` 的每个能力包含 `workflow.image/video`；不要用单任务的 `implemented` 代替串行执行能力。

| 能力 | 图像串行 | 视频串行 |
| --- | --- | --- |
| 超分 | 支持 | 支持逐帧组合 |
| 去水印 | 支持 | 支持逐帧组合 |
| 去噪 | 支持 | 当前无串行适配器 |
| BasicVSR++ 视频超分 | 不适用 | 只能独立单步 |
| ProPainter 视频修复 | 不适用 | 只能独立单步 |
| RIFE 插值 | 两图到一图，使用单任务入口 | 使用单任务入口 |
| 人脸修复、检测、深度、抠图等 | 当前无串行适配器 | 当前无串行适配器 |

工作流在创建输出目录或加载模型之前检查全部静态步骤、输入文件、参数支持、角色绑定及多文件模型依赖。视频逐帧步骤共享模型槽与 tile，冲突会拒绝；整段序列能力不能混入多步组合，也不接受未实现的顶层 device/tile 覆盖。ProPainter 检查显式 mask，或检查隐式水印检测器；遮罩帧数、实际视频内容及权重兼容性仍在运行时确认。

界面仅列出该模态可串行执行的能力与参数，不会自动把恢复的旧方案改成另一种能力。合法的零值、图像自定义尺寸与 tile 必须传到真实适配器。

## 命名方案与任务记录

`settings/media-workflows.json` 保存 version=1 的独立媒体方案库，不混入音频 graph/simple workflow-state。方案只含名称、模态、步骤、bindings、params；不保存素材、输出/模型/mask 位置或 worker ID。

加载方案只替换模态与步骤。更新/删除要求最初选择的 revision；刷新列表不会提高旧草稿的 revision，冲突时草稿保留。后端在单次事务中读最新状态、检查 revision、原子写入；损坏或未知版本的库会报错，不能以空库覆盖。

任务运行使用常驻 `mediaJobs` 与后端 journal；切页后仍更新，重新进入恢复提交快照。结果页的媒体/Agent 产物目前只读，音频删除入口只作用音频记录。

## 本地回归与证据边界

```powershell
pnpm test
pnpm build
python scripts/check-architecture.py
python scripts/check-command-surface.py --strict
```

Rust/Python 测试必须显式使用临时数据/模型/runtime/WebView 根；真实 UI 开发只通过 `scripts/dev.ps1`，安装包验证只通过 `scripts/clean-test.ps1`。不要直接执行开发 target 二进制来做狗粮或安装验证。

测试证据分别说明：纯函数/模拟适配器回归、固定快照浏览器截图、真实权重推理、安装包/跨版本验证。前三者不能互相替代。本轮没有下载新模型/运行时、覆盖安装、触发发布或宣称全模型推理验收通过。

运行时发布遵循现有资产 manifest 与 prepare/stage/package 流程，使用明确的构建输入；不要从用户 profile 临时打包运行时。发布校验需要匹配资产身份、大小、SHA-256、签名版本清单以及安装/回滚测试；当前工作区本地通过不代表正式跨平台发布链已通过。
