import json
from pathlib import Path

cat = json.loads(Path("python/media_ops/media_catalog.json").read_text(encoding="utf-8"))
caps = {}
cap_file = json.loads(Path("python/media_ops/capabilities.json").read_text(encoding="utf-8"))
for c in cap_file["capabilities"]:
    caps[c["id"]] = c

def mb(size):
    return f"{size/1024/1024:.1f} MB" if size else "—"

lines = []
lines.append("# 模型选择指南（自动生成）\n")
lines.append("> 这份文档由 `python/media_ops/media_catalog.json` 生成，"
             "`available` 表示权重已下载且通过真实素材验证；`planned` 表示还没接入，"
             "原因写在最后一节。改 catalog 后请重新生成，不要手改这里。\n")

avail = [m for m in cat["models"] if m.get("status") == "available"]
planned = [m for m in cat["models"] if m.get("status") != "available"]

lines.append(f"当前可用模型 **{len(avail)}** 个，计划中 **{len(planned)}** 个。\n")

groups = {}
for m in avail:
    groups.setdefault(str(m.get("task") or "other"), []).append(m)

TITLES = {
    "super_resolution": "超分辨率（放大）",
    "denoise": "去噪（尺寸不变）",
    "object_detection": "目标检测（输出 JSON）",
    "depth": "深度估计（输出灰度图）",
    "matting": "抠图（输出 RGBA）",
    "inpainting": "图像修复",
    "watermark_detection": "水印检测",
    "frame_interpolation": "插帧（视频/两帧之间）",
}
ORDER = ["super_resolution", "denoise", "matting", "depth", "object_detection",
         "inpainting", "watermark_detection", "frame_interpolation"]

for task in ORDER + [t for t in groups if t not in ORDER]:
    items = groups.get(task)
    if not items:
        continue
    lines.append(f"\n## {TITLES.get(task, task)}\n")
    lines.append("| 模型 | 说明 | 大小 | 许可 |")
    lines.append("|---|---|---|---|")
    for m in sorted(items, key=lambda x: x.get("label", "")):
        label = str(m.get("label") or m["id"])
        desc = str(m.get("description") or "").replace("|", "/")
        spdx = m.get("licenseSpdx")
        license_text = f"`{spdx}`" if spdx else "未标注"
        lines.append(f"| `{m['id']}`<br>{label} | {desc} | {mb(m.get('size') or 0)} | {license_text} |")

if planned:
    lines.append("\n## 还没接的模型，以及卡在哪\n")
    for m in sorted(planned, key=lambda x: x["id"]):
        reason = str(m.get("blockedReason") or "尚未验证").replace("\n", " ")
        lines.append(f"- **{m.get('label') or m['id']}**（`{m['id']}`）：{reason}")

lines.append("\n## 怎么选\n")
lines.append("- **照片放大**：先试 `esrgan-ultrasharp-x4` 或 `esrgan-remacri-x4`；想更柔和用 `esrgan-superscale-sp-x4`。")
lines.append("- **动漫/线稿**：`real-esrgan-anime-x4`、`real-cugan-x4`（自带降噪）、`esrgan-comix-x4`（硬边）。")
lines.append("- **细节要求高、能等**：`esrgan-nomos8kdat-x4`（295 MB，慢但稳）或 `swinir-m-x2`（Transformer）。")
lines.append("- **只想要 2×**：`real-cugan-x2` 或 `swinir-m-x2`。")
lines.append("- **去噪**：`scunet-color-real` 更彻底，`esrgan-1x-denoise-realplksr` 更轻。")
lines.append("- **抠图 / 深度 / 检测**：各自只有一个模型，直接选。")
lines.append("- **视频插帧**：`rife-v46`，在视频页选“插帧”。\n")

out = Path("docs/model-guide.md")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"  生成 {out}：{len(avail)} 个可用 / {len(planned)} 个计划中")