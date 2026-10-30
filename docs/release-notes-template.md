# Release 正文模板

复制这段，替换占位符。**下载区必须出现裸链接**——页面地址对国内用户不友好。

---

```markdown
MediaForge <版本> —— 本地媒体工作站（音频分离 / 图像超分去水印 / 视频修复 / 格式转换）

## 下载

| 渠道 | 链接 | 说明 |
|---|---|---|
| 国内镜像（推荐，免登录） | https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist | 单连接约 1 MB/s，下载器可加速 |
| GitHub | 见下方 Assets | 需要能访问 GitHub |

- 安装包 `MediaForge-<版本>-windows-x64-cuda-setup.exe`（约 1.85 GB）
- 便携版 `MediaForge-<版本>-windows-x64-portable.zip`（约 3.3 GB，解压即用，无需管理员）
- 已安装旧版：**直接装新安装包即可**，模型和数据在原处，不会被覆盖

## 这一版

- 插件/集成：安装时自动接入 DeepSeek Harness（检测不到则静默跳过）
- 安装位置固定为 `C:\Program Files\MediaForge`，向导不再询问目录（升级更可预期）
- 模型库扫描容错：某个子目录不可读不会再让整页报错
- 修复：媒体处理页在没有模型时的错误提示不完整

## 环境要求

- Windows 10/11 x64
- 磁盘 ≥ 7 GB（安装后约 5.2 GB）
- 有 NVIDIA 显卡可启用 CUDA；没有则用 CPU（设置里可切换）

## 已知问题

- 安装包未做代码签名，SmartScreen 会提示「未知发布者」：点「更多信息 → 仍要运行」
- 音频分离目前只验证过 `HTDemucs4`；其他分离模型的兼容性在陆续验证
```

---

## 注意

1. **不要**只放页面链接（`.../datasets/xxx`），要放 `.../resolve/master/release/<文件名>` 这种直链
2. 文件名必须与客户端 `archive_name()` 一致：`MediaForge-<版本>-windows-x64-update.zip`
3. 若这版启用了自动更新，正文里附上 `update.zip` 的 sha256，方便用户核对
