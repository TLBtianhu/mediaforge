"""失败恢复建议：把"报错了"变成"下一步该动哪个开关"。

agent 拿到一句 traceback 只能原样重试；有了结构化建议才能收敛：

    {"code": "out_of_memory", "retryable": true,
     "summary": "显存不足",
     "actions": [{"action": "reduce_param", "slot": "tile", "to": 0, "why": "..."},
                 {"action": "switch_device", "to": "cpu", "why": "..."}]}

设计原则：
* **保守**——只认确实见过的模式，认不出就返回 None（不加噪音、不猜）
* **可执行**——每条动作都指向契约里真实存在的槽位或设备切换
* **不自动做**——这里只给建议，动手的是调用方（内嵌 loop 或人）

动作词汇表与 `agent_contract` 的工具名对齐，agent 可以据此选下一个调用。
"""
from __future__ import annotations

import re
from typing import Any

#: action id → 说明（agent 可发现这份词汇表）
ACTIONS: dict[str, str] = {
    "reduce_param": "把某个槽位调到更省资源的值（如 tile=0 关闭分块外的额外开销）",
    "switch_device": "换计算设备（cuda → cpu）",
    "download_model": "下载缺失的权重（需要用户明确同意）",
    "convert_input": "先把输入转成受支持的容器/编码",
    "free_space": "清理磁盘空间或换输出目录",
    "install_tool": "安装缺失的外部工具（如 ffmpeg）",
    "retry_later": "换时间段重试（多为临时资源占用）",
    "fix_input_path": "确认输入路径存在（批量任务可先用 media.library 重新列举）",
}

#: (code, 正则, 可重试, 摘要, 动作) —— 顺序即优先级
_RULES: list[tuple[str, str, bool, str, list[dict[str, Any]]]] = [
    (
        "out_of_memory",
        r"CUDA out of memory|CUDA error: out of memory|OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED|not enough memory|内存不足|显存不足",
        True,
        "显存/内存不足",
        [
            {"action": "reduce_param", "slot": "tile", "to": 0, "why": "关掉分块外的额外缓冲，降低峰值"},
            {"action": "reduce_param", "slot": "scale", "to": 2, "why": "降倍率，显存占用近似按面积增长"},
            {"action": "switch_device", "to": "cpu", "why": "CPU 慢但不受显存限制"},
        ],
    ),
    (
        "device_unavailable",
        r"no CUDA-capable device|CUDA is not available|torch not compiled with CUDA|device unavailable|no kernel image is available",
        True,
        "没有可用的 CUDA 设备",
        [{"action": "switch_device", "to": "cpu", "why": "改用 CPU 后端"}],
    ),
    (
        "missing_weights",
        r"model (file )?not found|weights .*not found|cannot find model|No such file or directory: .*\.(pth|pt|safetensors|onnx|ckpt)|checkpoint.*missing",
        True,
        "缺少模型权重",
        [{"action": "download_model", "why": "先把权重下载到模型目录（需要用户同意）"}],
    ),
    (
        "input_missing",
        r"No such file or directory|FileNotFoundError|input not found|Input file does not exist",
        True,
        "输入文件不存在",
        [{"action": "fix_input_path", "why": "确认路径拼写与相对路径基准，或先列举媒体库"}],
    ),
    (
        "bad_input",
        r"Invalid data found when processing input|moov atom not found|could not open|Unsupported codec|Error opening input|invalid media",
        True,
        "输入文件无法解码",
        [{"action": "convert_input", "why": "先转成受支持的容器/编码再处理"}],
    ),
    (
        "disk_full",
        r"No space left on device|ENOSPC|磁盘空间不足",
        True,
        "磁盘空间不足",
        [{"action": "free_space", "why": "清理空间或换输出目录"}],
    ),
    (
        "tool_missing",
        r"ffmpeg (was )?not found|FFmpeg not found|ffprobe: not found|No such file.*ffmpeg",
        True,
        "缺少 ffmpeg/ffprobe",
        [{"action": "install_tool", "why": "安装或把工具放到 bin/ 目录"}],
    ),
    (
        "cancelled",
        r"\bcancell?ed\b|KeyboardInterrupt|operation was cancelled",
        False,
        "任务被取消",
        [],
    ),
]


def classify(message: str | None, detail: str | None = None) -> dict[str, Any] | None:
    """按错误文本给出恢复建议；认不出返回 None（调用方不要凭空造建议）。"""
    text = f"{message or ''}\n{detail or ''}"
    if not text.strip():
        return None
    for code, pattern, retryable, summary, actions in _RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return {
                "code": code,
                "retryable": retryable,
                "summary": summary,
                "actions": [dict(action) for action in actions],
            }
    return None
