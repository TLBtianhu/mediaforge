# 魔搭数据集 README（复制到数据集页面）

> 数据集名：`TLBtianhu/MediaForge-Dist`
> 用途：MediaForge 的 Windows 发布包国内镜像。公开、免登录可下。

---

```markdown
---
domain:
  - multimedia
tags:
  - desktop-app
  - windows
  - ffmpeg
  - media-processing
task:
  - other
---

# MediaForge 发布包镜像

[MediaForge](https://github.com/TLBtianhu/mediaforge) 的 Windows 发布包国内镜像，
用于绕过 GitHub Releases 在国内的下载问题。

## 直接下载（免登录）

安装包（推荐，约 1.85 GB）：

https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.14-windows-x64-cuda-setup.exe

便携版（约 3.3 GB，解压即用）：

https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/release/MediaForge-0.0.14-windows-x64-portable.zip

## 说明

- 单连接限速约 1 MB/s；用支持多线程的下载工具（IDR / Motrix / aria2）会快数倍
- 支持断点续传（HTTP 206）
- 安装包未做代码签名，SmartScreen 提示「未知发布者」属正常，点「更多信息 → 仍要运行」
- 数据存放在 `%USERPROFILE%\.mediaforge`，卸载不删除模型
- 升级方式：下载新版安装包覆盖安装即可

## 文件命名

```
MediaForge-<版本>-windows-x64-cuda-setup.exe     安装包（含 CUDA 运行时）
MediaForge-<版本>-windows-x64-portable.zip       便携版
MediaForge-<版本>-windows-x64-update.zip         自动更新包（含 .sig 签名）
```

## 许可证

应用本体：见仓库 LICENSE。随包分发的第三方模型权重遵循各自许可证，
模型清单与来源见仓库 `python/audio_catalog.json` 与 `python/media_ops/media_catalog.json`。
```

---

## 为什么带 `domain` / `task` 标签

魔搭会用这两个字段做数据集分类；缺失时页面顶部会提示
`Consider adding domain and task tags`，并且不利于被检索到。
