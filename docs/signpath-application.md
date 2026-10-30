# SignPath 申请表（草稿，复制粘贴用）

表单地址：<https://signpath.org/apply>
下面是本项目可用的答案。带 **待你确认** 的字段我无法替你决定。

## Project

| 字段 | 建议填写 |
|---|---|
| Project name | `MediaForge` |
| Repository URL | `https://github.com/TLBtianhu/mediaforge` |
| Repository host | GitHub |
| Licence | `AGPL-3.0` |
| Project website | `https://github.com/TLBtianhu/mediaforge`（暂无独立站点） |
| Short description | Windows desktop app for local audio separation and image/video processing, forked from pymss-studio. |
| Long description | 见下方 "Long description" |

## Long description

```text
MediaForge is a Windows desktop application for local media processing:
audio source separation, format conversion, image super-resolution,
denoising, background removal, depth estimation and object detection, plus
video frame interpolation and per-frame upscaling. It ships as an installer
and a portable zip; model weights and the CUDA runtime are downloaded on
first use rather than bundled, and every downloaded asset is verified by
size and SHA-256.

The project is a fork of pymss-project/pymss-studio (AGPL-3.0) and keeps that
licence. Audio inference comes from the pymss package; the media pipeline,
model catalogue and UI are developed in this repository.

We would like to sign the Windows installer so that users do not see an
"unknown publisher" warning and so that enterprise environments can allow it.
```

## Build

| 字段 | 建议填写 |
|---|---|
| Build system / CI | GitHub Actions (`.github/workflows/release-windows.yml`) |
| Build platform | `windows-latest`, x64 |
| Artifact to sign | `MediaForge-<version>-windows-x64-cuda-setup.exe` |
| How the artifact is produced | `pnpm build` → `pnpm tauri build --no-bundle` → staged directory → Inno Setup (`installer/mediaforge.isf`) |
| Reproducibility notes | 外部二进制（FFmpeg 9.0.2、aria2 1.37.0）按 `.github/third-party.json` 固定版本与 SHA-256 下载并校验；基础 Python 用 `actions/setup-python` 固定 3.11.9 |


## Contact / ownership

| 字段 | 建议填写 |
|---|---|
| Maintainer | **待你确认**：你的姓名与邮箱 |
| GitHub account | `TLBtianhu` |
| Do you own the copyright of the code? | **待你确认**。事实是：代码为 pymss-studio 的衍生作品，你的新增部分归你；上游部分归 pymss-project，按 AGPL-3.0 授权。建议照实写："The project is a derivative of pymss-project/pymss-studio under AGPL-3.0; I hold copyright on the additional code and redistribute the upstream parts under the same licence." |
| Commercial offering? | **待你确认**。若没有付费版本，照实写 "No commercial version". |

## 提交前自查

- [ ] 仓库是公开的（当前 `TLBtianhu/mediaforge` 已是 public）
- [ ] `LICENSE` 是完整 AGPL-3.0（661 行，已确认）
- [ ] `NOTICE` 列出了上游与随包第三方（已添加）
- [ ] 仓库里没有真实密钥（已扫描：受控文件与历史里都没有字面值）
- [ ] 表格里没有残留的占位符或草稿提示

## 审批通过后要做的（对应 docs/signpath-setup.md）

1. 在 SignPath 建 project / signing policy，slug 记下来
2. 导入 `.signpath/artifact-configurations/windows-installer.xml`
3. 授权 GitHub App
4. 生成 API token → 仓库 Secret `SIGNPATH_API_TOKEN`
5. 填仓库 Variables：`SIGNPATH_ORGANIZATION_ID`、`SIGNPATH_PROJECT_SLUG`、`SIGNPATH_SIGNING_POLICY_SLUG`


## 附：为什么不是买证书

- 商业 OV/EV 证书要求硬件令牌或云 HSM，个人申请需实体验证，年费约 ¥1500–6000
- SignPath 对 OSI 许可的开源项目免费，签名由 CI 触发，私钥不出他们的 HSM
- 对本项目而言，需要对外证明的是"这个包来自这个仓库"，SignPath 正好担保这件事
