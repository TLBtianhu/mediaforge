# 参与开发

> 这份文件改编自上游 [pymss-studio](https://github.com/pymss-project/pymss-studio) 的
> CONTRIBUTING.md。上游在早期阶段只接受协作者 PR；MediaForge 作为衍生项目，贡献规则以本文件为准。

## 先读这三件

1. 许可证是 **AGPL-3.0**（见 `LICENSE`）。提交 PR 即表示同意以同一许可证发布你的贡献。
2. 音频能力来自上游 [pymss](https://github.com/pymss-project/pymss)，**不要删除上游的版权与许可证声明**。
3. 改媒体能力前先读 `docs/model-guide.md` 与 `docs/media-verification.md`——后者定义了"什么算验证通过"。

## 环境

```bash
pnpm install
pnpm tauri dev      # 完整应用；数据走 .dev-data，不碰正式配置
pnpm build          # vue-tsc + vite
cargo check --manifest-path src-tauri/Cargo.toml
```

Python 侧测试用开发运行时：

```bash
.dev-data/runtime-envs/cuda/Scripts/python.exe -m unittest discover -s tests/python -t .
```

没有这个目录时，用任意 Python 装依赖：

```bash
pip install -r python/requirements.txt -r python/requirements-media.txt
# 另需 torch（cu128 或 CPU 版）
```

## 提交前必须跑

```bash
cargo check --manifest-path src-tauri/Cargo.toml
node_modules/.bin/vue-tsc --noEmit
node --test "tests/**/*.test.*"                       # 基线：2 个既有失败
python -m unittest discover -s tests/python -t .
python scripts/check-media-deps.py                    # 依赖声明完整性
```

改了媒体能力再加一条：

```bash
python scripts/smoke-media-models.py                  # 每个 available 模型走真实 worker 入口
```

**读这些数字要小心**：`node --test` 的汇总行会把失败测试的父级重复计入（出现 `fail 4` 而实际只有 2 个 `not ok`）。判断回归请数 `not ok` 明细，`docs/media-verification.md` 里有说明。

## 新增一个模型

1. 权重放到 `~/.mediaforge/models/media/`：单文件用文件名，目录型模型（config + safetensors）用目录名
2. `python/media_ops/media_catalog.json` 加条目：`id`、`name`、`task`、`size`、`url`、`licenseSpdx`、`status`
3. `python/media_ops/capabilities.json` 的对应能力 `modelIds` 加 `media:<id>`
4. `python/media_ops/model_registry.json` 补一条 `source.status = local` 的登记
5. 跑 `python scripts/smoke-media-models.py`。**只有它通过，才把 `status` 改成 `available`**
6. `python scripts/generate-model-guide.py` 重新生成选型指南

架构在 spandrel 里已支持的（ESRGAN / SwinIR / SCUNet / RealCUGAN…）不需要写加载代码；不在里面的（Depth Anything、RMBG、RIFE）需要写适配函数，并在 `IMAGE_CAPABILITIES` 里登记。两种情况都必须有一次**真实素材**跑通的记录，不能只加 catalog 条目。

## 代码约定

- 注释写"为什么"，不写"是什么"
- 不写死阈值、条目数、模型 id——能让数据决定就别硬编码
- 报错要能定位：带路径、模型 id、期望值与实际值
- 新增外部依赖必须同时更新 `python/requirements*.txt`，并跑 `scripts/check-media-deps.py`

## 提 PR

- 一个 PR 一件事，能单独回滚
- 描述写清：改了什么、怎么验证的、有没有引入新依赖
- 改了行为就同步改 `docs/` 下对应文档

## 报告问题

- Bug / 功能建议：GitHub Issues，附版本号、复现步骤、相关日志
- 安全漏洞：不要开公开 issue，见 `SECURITY.md`
