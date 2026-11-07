from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _app_root() -> Path:
    # python/shared/worker_paths.py -> 应用根（安装版的 app、便携根或仓库根）
    return Path(__file__).resolve().parents[2]


def default_layout() -> tuple[Path, Path, Path]:
    """返回 data/models/runtime-envs 三个根，禁止再各自推导旧路径。"""
    app_root = _app_root()
    if app_root.name.lower() == "app":
        root = app_root.parent
        return root / "data", root / "models", root / "runtime-envs"
    if (app_root / "mediaforge.portable").is_file():
        return app_root / "data", app_root / "models", app_root / "runtime-envs"
    dev_root = app_root / ".dev-data"
    return dev_root, dev_root / "models", dev_root / "runtime-envs"


def bundled_runtime_envs_dir() -> Path | None:
    value = os.environ.get("MEDIAFORGE_BUNDLED_RUNTIME_ENVS_DIR") or os.environ.get("PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR")
    return Path(value).expanduser() if value else None


def allow_debug_runtime_override() -> bool:
    return (
        os.environ.get("MEDIAFORGE_ALLOW_DEBUG_RUNTIME_OVERRIDE") == "1"
        or os.environ.get("PYMSS_STUDIO_ALLOW_DEBUG_RUNTIME_OVERRIDE") == "1"
    )


def data_root(override: Any = None) -> Path:
    explicit = (str(override or "").strip() or os.environ.get("MEDIAFORGE_DATA_ROOT")
                or os.environ.get("MEDIAFORGE_DATA_DIR") or os.environ.get("PYMSS_STUDIO_DATA_ROOT"))
    if explicit:
        return Path(explicit).expanduser()
    return default_layout()[0]


def _sibling_or_child(root: Path, name: str) -> Path:
    # 只认实际安装/便携布局，不能根据任意临时目录名为 data 就逃出显式数据根。
    layout = default_layout()
    if root == layout[0] and not any(os.environ.get(key) for key in (
        "MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT"
    )):
        return layout[1 if name == "models" else 2]
    return root / name


def runtime_envs_root(override: Any = None, *, data_root: Any = None) -> Path:
    explicit = str(override or "").strip() or os.environ.get("MEDIAFORGE_RUNTIME_ENVS_DIR")
    if explicit:
        return Path(explicit).expanduser()
    for candidate in (data_root, os.environ.get("MEDIAFORGE_DATA_ROOT"), os.environ.get("MEDIAFORGE_DATA_DIR"), os.environ.get("PYMSS_STUDIO_DATA_ROOT")):
        text = str(candidate or "").strip()
        if text:
            return _sibling_or_child(Path(text).expanduser(), "runtime-envs")
    return default_layout()[2]


def model_root(override: Any = None, *, data_root: Any = None) -> Path:
    """模型根目录：全仓**唯一**的解析点。

    2026-10-29 之前这套规则存在五份（`model_hub` / `media_ops.capabilities` /
    `worker_custom_models.managed_root` / `worker_models._default_model_dir` /
    `agent_bridge`），而且其中三份互不相同——同一个"模型放哪"的问题，
    下载、导入、推理、MCP 四个入口给出不同答案。

    解析链（顺序固定，别在别处再写一遍）：

    1. ``override`` —— payload 里的 ``modelDir`` / 调用方显式参数
    2. ``MEDIAFORGE_MODEL_DIR`` / ``PYMSS_MODEL_DIR``
    3. ``data_root`` / ``MEDIAFORGE_DATA_ROOT`` / ``MEDIAFORGE_DATA_DIR``
       —— 显式数据根取其 ``models`` 子目录，只有默认安装/便携布局取同级目录
    4. ``default_layout()`` —— 安装、便携或开发布局的唯一默认模型根

    不再回退到 ``~/.mediaforge/models``，也不包含仓库根的 ``all_models`` 或程序目录。

    返回绝对路径；是否创建目录由调用方决定（下载器要 mkdir，只读查询不必）。
    """

    explicit = str(override or "").strip()
    if explicit:
        return Path(explicit).expanduser()

    for key in ("MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return Path(value).expanduser()

    for candidate in (data_root, os.environ.get("MEDIAFORGE_DATA_ROOT"), os.environ.get("MEDIAFORGE_DATA_DIR"), os.environ.get("PYMSS_STUDIO_DATA_ROOT")):
        text = str(candidate or "").strip()
        if text:
            return _sibling_or_child(Path(text).expanduser(), "models")

    return default_layout()[1]


def export_model_root(model_dir: Any) -> None:
    """把 payload 里的 `modelDir` 固化成进程环境变量（三个 media worker 共用）。

    为什么**两个都写**：`model_root()` 认 `MEDIAFORGE_MODEL_DIR` / `PYMSS_MODEL_DIR`，
    而 pymss 自己的 `DEFAULT_MODEL_DIR` 是在**导入时**求值的——只写前者的话，
    之后才 import pymss 的代码仍然会落到 `~/.cache/pymss/models`。
    两边都写，才能保证"同一份模型根"这句话在整个进程里成立。

    原来这三行在 image/video/workflow 三个 worker 里各写一遍，
    哪天约定变了（比如再加一个变量）必然会漏改一处。
    """
    text = str(model_dir or "").strip()
    if not text:
        return
    os.environ["MEDIAFORGE_MODEL_DIR"] = text
    os.environ["PYMSS_MODEL_DIR"] = text


def normalize_output_dir(value: Any) -> str:
    text = str(value or "")
    default = os.environ.get("PYMSS_STUDIO_DEFAULT_OUTPUT_DIR", "")
    output_path = Path(text or default or "results")
    if text and not output_path.is_absolute() and default:
        output_path = Path(default).parent / output_path
    return str(output_path.resolve())


# 媒体权重（image/video）统一放在 <模型根>/media/ 下；
# 音频权重保持 pymss 的分类相对路径（cleanup/… vocal/…），两套布局并列不冲突。
MEDIA_SUBDIR = "media"

def media_model_relpath(name: str) -> str:
    """媒体权重在模型根下的相对路径：`media/<文件名>`。"""
    text = str(name or "").strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("media model name is empty")
    return f"{MEDIA_SUBDIR}/{text}"
