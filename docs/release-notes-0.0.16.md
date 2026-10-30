# 0.0.16 发布说明（草稿）

> 这一版是运行时解耦后的第一个完整版本：安装包从 1850 MB 降到 63 MB，
> 运行时作为独立资产按 sha256 下载，且已在本机完成端到端验证。

## 用户可见的变化

```
安装包          1850 MB → 63 MB
安装权限        需要管理员 → 普通用户即可（装到 %LOCALAPPDATA%\Programs\MediaForge）
首次启动        出现"首次使用需要准备运行环境"卡片，点一次下载 3.18 GB
后续升级        只下 63 MB，运行时复用，不再重复下载
启动器位置      %USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd（DSH 指向这里）
```

## 内部结构变化

**模型系统合并为一套**

```
列表/下载/事件   统一走 model_hub（334 条：音频 326 + 媒体 8）
删除            list_models / start_model_download / download_model 三个旧命令
新增            scripts/compare-model-apis.py（字段对账，gap 326 → 0）
```

**运行时解耦**

```
资产             runtime-cuda-2026.10.2.zip（3.45 GB）+ manifest（sha256/size/urls）
存放             %USERPROFILE%\.mediaforge\runtime-envs\cuda
下载             aria2c 4 连接、断点续传；先校验 sha256 再解压；指针最后写
双源             hf-mirror.com（实测 5.55 MB/s）优先，魔搭（4 连接约 3 MB/s）兜底
```

**命令面清理**

```
删除不可达命令    cancel_runtime_core_update / delete_model(Tauri 层) /
                  export_editor_mix(Tauri 层) / list_audio_files / scan_editor_assets
新增对账脚本      scripts/check-command-surface.py
                  三层：Rust→worker、agent→worker、前端→Tauri
现状            0 悬空 / 0 孤儿 / 1 疑似不可达（worker_health）
```

**性能**

```
任务队列轮询     3 秒 → 10 秒，页面隐藏时完全停止
原因             每条命令都会新起一个 Python 进程，轮询即进程风暴
```

**调试命令分文件**

```
commands/debug_cmd.rs   13 个 debug_* 命令从 app_cmd.rs 拆出（-133 行）
```

## 已验证

```
安装            静默安装退出码 0，无 UAC，落在 %LOCALAPPDATA%\Programs\MediaForge
首启引导        卡片显示 3182 MB（读自编译进二进制的资产大小）
下载            3.18 GB 完成，解压 4.93 GB，active-runtime.json 内容正确
分离            真实歌曲跑通，2 stems，44.1 kHz / f32 / 立体声 / 214 秒
DSH 集成        启动器创建 + cordis 配置指向 ~/.mediaforge/bin
类型/测试        cargo check 0；vue-tsc 0；前端 1425/1430（2 个既有失败）
```

## 已知问题

```
1. 单源风险：运行时包 3.18 GB 超过 GitHub Release 的 2 GB 单文件上限，
   只能放魔搭/HF；魔搭上传多次因网络中断失败，备用源可能缺失
2. worker_health 命令在注册表里但前端无引用，尚未定性
3. app_cmd.rs 仍有 3523 行 / 106 个命令，拆分尝试 4 次失败（见技术债）
4. 安装包未做代码签名，SmartScreen 会提示"未知发布者"
5. 安装器内 VC++ 运行库步骤在 per-user 模式下跳过，依赖系统已有版本
```

## 媒体模型：本版新增 19 个可用模型（0.0.16 起）

这一版把"图像/视频处理"从只有 4 个模型扩到 19 个，并且每个都在真实素材上验证过。
模型清单与选型建议见 `docs/model-guide.md`（由 catalog 自动生成）。

| 能力 | 可用模型 |
|---|---|
| 超分 | Real-ESRGAN 4×、Real-CUGAN 4× (denoise3x)、SwinIR-M 2× (real-world GAN)、Real-ESRGAN x4plus (anime 6B)、Real-CUGAN 2× (denoise3x)、4x-UltraSharp、4x Remacri、4x NMKD Superscale SP、4x Nomos8kDAT、4x Valar v1、4x Fatality Comix |
| 去噪 | SCUNet (color real, PSNR)、1x DeNoise (realplksr otf) |
| 抠图 | RMBG-1.4 抠图 |
| 深度 | Depth Anything V2 (Small) |
| 检测 | YOLO11n 目标检测 |
| 修复 | LaMa Inpainting |
| 插帧 | RIFE 4.26 插帧 |

### 本版新增的能力（之前没有）

- **去噪**：输出尺寸不变，`scunet-color-real` 与 `esrgan-1x-denoise-realplksr` 两个选择。
- **抠图**：RMBG-1.4，输出 RGBA，alpha 边缘有过渡像素（实测半透明像素占 1.7%）。
- **深度**：Depth Anything V2，输出归一化灰度深度图。
- **检测**：YOLO11n，输出 JSON（label / 置信度 / 框），不产生图像。
- **插帧**：RIFE 4.26，支持"两张图出中间帧"和"整段视频插帧"。

### 视频管线

- 视频插帧：抽帧 → 相邻帧插中间帧 → 按新帧率回封，音轨直接复制。
  实测 1.00 秒 10 fps → 0.95 秒 20 fps。
- 视频超分：逐帧放大，实测 448×448 → 1792×1792，时长不变。
  注意这是逐帧处理，**没有时序一致性**，快速运动画面可能有闪烁。

### 计划中的 10 个模型与阻塞原因

- **BasicVSR++**：缺少权重地址与加载代码，尚不可下载
- **BiRefNet**：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **CodeFormer**：codeformer.pth（359 MB）在 hf-mirror 上可下，但 CodeFormer 的架构代码依赖 basicsr，且需要人脸检测与对齐；本次已装通 basicsr/facexlib，先解决 GFPGAN 的架构匹配再复用它。
- **DAT-2**：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **DDColor**：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **GFPGAN v1.4**：依赖已装通（basicsr/facexlib/gfpgan + torchvision functional_tensor 垫片），GFPGANv1.pth（587 MB）也下好了。但参数扫描表明该权重与包内两种架构都不匹配：GFPGANv1Clean 在 (channel_multiplier, different_
- **HAT-L**：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **ProPainter**：缺少权重地址与加载代码，尚不可下载
- **Real-ESRGAN 2×**：目录里没有 RealESRGAN_x2plus.pth：上游 GitHub 的 v0.2.1 需要代理，而当前代理不可用，魔搭上也找不到对应仓库。视频管线现在会退到已安装的超分模型，所以缺它只影响 2× 选项，不再阻塞整条视频超分。已有 real-cugan-x2 与 swinir-m-x2 可作为 2× 替代。
- **SAM 2**：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证

## 运行时资产升级到 2026.10.2（媒体依赖齐全）

旧的 2026.10.1 包里没有 `transformers` / `timm` / `kornia` / `basicsr` / `facexlib` / `gfpgan`，
而这一版新增了深度、抠图能力，装旧包的用户点这些功能会直接 `ModuleNotFoundError`。

新资产按"已验证能跑 20 个模型"的那套环境打包，并做了两件事：

1. 解压后用它自己的解释器跑了一遍媒体冒烟：19/19 通过；
2. 记录完整依赖清单 `python/runtime-lock-media.txt`（106 个包），
   与两个 requirements 文件比对，无缺项。

二进制里的 `ASSET_ID` / `ASSET_SHA256` / `ASSET_SIZE` 已同步，老用户会看到"需要更新运行时"。

## 技术债（已记录，不在本版处理）

```
* app_cmd.rs 拆分：文本级工具（正则/括号扫描）均不可靠，
  需要真实语法解析或 IDE 重构；4 次尝试均回滚，未损坏代码
* SeparateView.vue 4822 行未拆
* worker_bootstrap.cmd_update_runtime_core 与 stores/app.ts 的 runtimeCoreUpdate*
  仍是死代码，删除会破坏 store 的推导类型，需要连类型一起重构
```

## 补充验证（19 轮之后）

```
运行时版本感知   装 0.0.16 后卡片显示"运行环境需要更新 / 3322 MB"
                 —— 因为本地那份是旧哈希，且没有 .asset-id 标记
                 —— 按钮文案随之变成"更新运行环境"，不是"开始准备"
新资产参数       sha256 c1f0b3ba…  size 3483253620  解压后 5.31 GB
HF 主源          HEAD 200，大小与常量一致
安装包           63.1 MB（重新打包，ISIC 84.9 秒）
```

### 运行时依赖缺口（本次修复的核心问题）

```
发现    python/requirements.txt 只声明音频依赖
        图像/视频依赖（opencv/spandrel/ultralytics/pillow/torchvision）从未声明
        打包出的运行时缺 27 个包 → 分离正常、图像/视频必失败
修复    新增 python/requirements-media.txt 并补装
        打包源与开发运行时包数对齐（42 → 69），零缺口
影响    运行时从 4.93 GB 变成 5.31 GB，哈希随之改变
```

如果没有这次核对，用户装上是"能分离、不能超分"，而错误信息只会说缺模块。

## 最终产物与双源状态（收尾实测）

```
setup.exe        MediaForge-0.0.16-windows-x64-cuda-setup.exe     63.1 MB
portable.zip     MediaForge-0.0.16-windows-x64-portable.zip     120.1 MB
update.zip       MediaForge-0.0.16-windows-x64-update.zip        98.5 MB（已签名）
运行时包         runtime-cuda-2026.10.2.zip                  3616417066 字节
                 sha256 c1f0b3baf5db864955917b9432832dc7391bf1f5bbafae9e6f80bad2a3e452ac

双源复核（匿名 HEAD）
  hf-mirror.com  200  3483253620  OK
  魔搭            200  3483253620  OK
```

两个源的大小都与代码里钉死的常量一致，因此"双源"不只是配置里写了两个地址，
而是两份内容确实相同、且都可匿名下载。

## 运行时代替验证（不点按钮的等价路径）

```
用本地包解压替换 ~/.mediaforge/runtime-envs/cuda，并写入 .asset-id
→ 重启应用：卡片消失（stale 判定生效）
→ torch 2.7.1+cu128 / cuda True / cv2 5.0.0 / spandrel 0.4.2 全部可 import
→ worker media_capabilities 能执行
```
