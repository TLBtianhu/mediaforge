# 模型选择指南（自动生成）

> 这份文档由 `python/media_ops/media_catalog.json` 生成，`available` 表示权重已下载且通过真实素材验证；`planned` 表示还没接入，原因写在最后一节。改 catalog 后请重新生成，不要手改这里。

当前可用模型 **20** 个，计划中 **10** 个。


## 超分辨率（放大）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `esrgan-comix-x4`<br>4x Fatality Comix | 4× 超分，面向漫画/线稿，边缘更硬。 | 63.9 MB | `MIT` |
| `esrgan-superscale-sp-x4`<br>4x NMKD Superscale SP | 4× 超分，风格偏柔和自然，适合人像与风景。 | 63.9 MB | `MIT` |
| `esrgan-yandereneoxl-x4`<br>4x NMKD YandereNeoXL | 4× 超分，二次元/插画向，线条干净、色彩偏鲜。 | 63.9 MB | `MIT` |
| `esrgan-nomos8kdat-x4`<br>4x Nomos8kDAT | 训练于 8000 张素材的 4× 超分，体积大（295 MB）但细节更稳。 | 295.0 MB | `MIT` |
| `esrgan-remacri-x4`<br>4x Remacri | 写实向 4× 超分，纹理保留好，适合照片。 | 63.9 MB | `MIT` |
| `esrgan-valar-x4`<br>4x Valar v1 | 4× 超分，锐度与纹理平衡，适合游戏截图与插画。 | 64.4 MB | `MIT` |
| `esrgan-ultrasharp-x4`<br>4x-UltraSharp | 社区常用的通用 4× 超分，锐度与噪点平衡较好。 | 63.9 MB | `MIT` |
| `real-cugan-x2`<br>Real-CUGAN 2× (denoise3x) | 面向动漫/插画的 2× 超分，denoise3x 变体自带较强降噪。 | 4.9 MB | `MIT` |
| `real-cugan-x4`<br>Real-CUGAN 4× (denoise3x) | 面向动漫/插画的 4× 超分，denoise3x 变体自带较强降噪。 | 5.4 MB | `MIT` |
| `real-esrgan-x4`<br>Real-ESRGAN 4× | 4× photo super-resolution, used for 2K/4K targets. | 63.9 MB | `BSD-3-Clause` |
| `real-esrgan-anime-x4`<br>Real-ESRGAN x4plus (anime 6B) | 面向动漫/插画的 4× 超分，6B 变体更轻，线条更锐。 | 17.1 MB | `BSD-3-Clause` |
| `swinir-m-x2`<br>SwinIR-M 2× (real-world GAN) | Transformer 超分（real-world 2× GAN 权重），细节强但 fp16 下会出 NaN，已自动降级 fp32。 | 63.9 MB | `Apache-2.0` |

## 去噪（尺寸不变）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `esrgan-1x-denoise-realplksr`<br>1x DeNoise (realplksr otf) | 1× 去噪（不改变尺寸），适合轻噪点与压缩伪影。 | 28.2 MB | `MIT` |
| `scunet-color-real`<br>SCUNet (color real, PSNR) | 彩色图盲去噪，PSNR 变体；输出尺寸与输入一致。 | 68.6 MB | `Apache-2.0` |

## 抠图（输出 RGBA）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `rmbg-1.4`<br>RMBG-1.4 抠图 | 通用背景移除，输出 RGBA（alpha 即前景概率）。 | 168.5 MB | `BRIA-1.4-NC` |

## 深度估计（输出灰度图）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `depth-anything-v2-small`<br>Depth Anything V2 (Small) | 单目深度估计，输出归一化的灰度深度图（近白远黑）。 | 94.6 MB | `Apache-2.0` |

## 目标检测（输出 JSON）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `yolo11n`<br>YOLO11n 目标检测 | 通用目标检测，输出 JSON（label/置信度/框），不生成图像。 | 5.4 MB | `AGPL-3.0` |

## 图像修复

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `lama`<br>LaMa Inpainting | Mask-guided inpainting for watermark and object removal. | 196.1 MB | `Apache-2.0` |

## 水印检测

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `watermark-yolo11x`<br>YOLO11x Watermark | Detects watermark and logo regions before LaMa repair. | 109.1 MB | `AGPL-3.0` |

## 插帧（视频/两帧之间）

| 模型 | 说明 | 大小 | 许可 |
|---|---|---|---|
| `rife-v46`<br>RIFE 4.26 插帧 | 两帧之间插中间帧；输入宽高需为 32 的倍数。 | 47.0 MB | `MIT` |

## 还没接的模型，以及卡在哪

- **BasicVSR++**（`basicvsrpp`）：缺少权重地址与加载代码，尚不可下载
- **BiRefNet**（`birefnet`）：权重与代码可下载，依赖已装通（timm/kornia/kornia-rs + torchvision 垫片），transformers 远程代码能加载 BiRefNet 并在 CUDA 上 fp16 前向。但三种预处理都出不来可用掩膜：ImageNet 归一化 → 原始输出 3.2~14.8，sigmoid 后前景 100%；0-1 输入 → 前景 0%；(x-0.5)/0.5 → 前景 26.6%，但看图是**一团模糊光斑**，边缘完全不对。对照 handler.py：官方用 ImageNet 归一化 + 取 preds[0].squeeze()，与实测不符，说明这个 checkpoint 的输出版本/配置（mul_scl_ipt、decoder 结构）与仓库默认不一致。下一步：改用仓库自带的 inference 脚本或其 config.json 里的完整配置，而不是手拼预处理。
- **CodeFormer**（`codeformer`）：codeformer.pth（359 MB）在 hf-mirror 上可下，但 CodeFormer 的架构代码依赖 basicsr，且需要人脸检测与对齐；本次已装通 basicsr/facexlib，先解决 GFPGAN 的架构匹配再复用它。
- **DAT-2**（`dat-2`）：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **DDColor**（`ddcolor`）：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **GFPGAN v1.4**（`gfpgan-v14`）：依赖已装通（basicsr/facexlib/gfpgan + torchvision functional_tensor 垫片），GFPGANv1.pth（587 MB）也下好了。但参数扫描表明该权重与包内两种架构都不匹配：GFPGANv1Clean 在 (channel_multiplier, different_w, input_is_latent, sft_half) 全组合下最好也只有 166 个键不匹配（缺 conv_body_first.weight，说明权重是 Sequential 结构）；GFPGANv1 则在 cm=1/2 与 sft_half True/False 下都报 final_linear 形状不符。结论：这个 v1 权重与当前 gfpgan 1.3.8 的架构定义对不上，需要换官方 GFPGANv1.4.pth（配 GFPGANv1Clean）。
- **HAT-L**（`hat-l`）：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证
- **ProPainter**（`propainter`）：缺少权重地址与加载代码，尚不可下载
- **Real-ESRGAN 2×**（`real-esrgan-x2`）：目录里没有 RealESRGAN_x2plus.pth：上游 GitHub 的 v0.2.1 需要代理，而当前代理不可用，魔搭上也找不到对应仓库。视频管线现在会退到已安装的超分模型，所以缺它只影响 2× 选项，不再阻塞整条视频超分。已有 real-cugan-x2 与 swinir-m-x2 可作为 2× 替代。
- **SAM 2**（`sam2`）：能力清单里的候选，尚未接入：需要权重地址、加载代码与一次真实出图验证

## 怎么选

- **照片放大**：先试 `esrgan-ultrasharp-x4` 或 `esrgan-remacri-x4`；想更柔和用 `esrgan-superscale-sp-x4`。
- **动漫/线稿**：`real-esrgan-anime-x4`、`real-cugan-x4`（自带降噪）、`esrgan-comix-x4`（硬边）。
- **细节要求高、能等**：`esrgan-nomos8kdat-x4`（295 MB，慢但稳）或 `swinir-m-x2`（Transformer）。
- **只想要 2×**：`real-cugan-x2` 或 `swinir-m-x2`。
- **去噪**：`scunet-color-real` 更彻底，`esrgan-1x-denoise-realplksr` 更轻。
- **抠图 / 深度 / 检测**：各自只有一个模型，直接选。
- **视频插帧**：`rife-v46`，在视频页选“插帧”。

