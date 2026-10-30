# 媒体能力验收（怎么证明"能用"）

这份文档说明：改了媒体相关代码后，怎么确认 20 个模型仍然能跑、依赖仍然声明完整。

## 一键检查

```bash
# 依赖声明 + 模型冒烟
python scripts/check-media.py

# 只查依赖（不需要权重在盘上，秒级）
python scripts/check-media.py --deps

# 只跑模型（需要权重与运行时）
python scripts/check-media.py --smoke
```

系统 python 启动时会自动切到应用运行时（`~/.mediaforge/runtime-envs/cuda`），
因为只有那里装了 torch/transformers 等依赖。想强制用当前解释器可以设
`MEDIAFORGE_SKIP_REEXEC=1`。

## 全量验收顺序

```bash
cargo check --manifest-path src-tauri/Cargo.toml
node_modules/.bin/vue-tsc --noEmit
node --test "tests/**/*.test.*"
python -m unittest discover -s tests/python -t .
python scripts/check-media.py
```

## 基线（0.0.16 收尾时）

| 检查 | 期望结果 |
|---|---|
| cargo check | 通过 |
| vue-tsc | 0 错误 |
| 前端测试 | 1430 个，**2 个已知失败** |
| Python 测试 | 1153 个，17 失败 + 5 错误（全部与 WSL/macOS 等平台相关） |
| 依赖检查 | 0 缺失 |
| 模型冒烟 | 19 项全过（18 个单独模型 + 1 个水印组合流程） |

## 三个容易误判的地方

**① 前端测试的 `fail` 计数不等于失败用例数**

`node --test` 的汇总行会把失败测试的父级也计入，所以可能出现
`fail 4` 而实际只有 2 个 `not ok`。判断基线请数明细：

```bash
node --test --test-reporter=tap "tests/**/*.test.*" | grep '^not ok'
```

**② 一定要用应用运行时，不是仓库里的开发环境**

两个解释器同时存在：

```
~/.mediaforge/runtime-envs/cuda          应用实际用的（有全部依赖）
.dev-data/runtime-envs/cuda              开发环境（可能没有新装的包）
```

用错会得到"模块找不到"的假故障。这一期就因为用错解释器误判过一次。

**③ 冒烟脚本需要权重在盘上**

`--deps` 不需要权重；`--smoke` 需要 `~/.mediaforge/models/media/` 下每个
available 模型的文件都在。缺文件时冒烟会失败——这是设计如此，因为
"标着可用但文件不在"本身就是缺陷。

## 相关脚本

| 脚本 | 作用 |
|---|---|
| `scripts/smoke-media-models.py` | 走真实 worker 入口跑每个 available 模型 |
| `scripts/check-media-deps.py` | 静态比对 import 与 requirements |
| `scripts/generate-model-guide.py` | 从 catalog 生成 `docs/model-guide.md` |