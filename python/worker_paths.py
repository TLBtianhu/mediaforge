from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def model_root(override: Any = None, *, data_root: Any = None) -> Path:
    """模型根目录：全仓**唯一**的解析点。

    2026-10-29 之前这套规则存在五份（`model_hub` / `media_ops.capabilities` /
    `worker_custom_models.managed_root` / `worker_models._default_model_dir` /
    `agent_bridge`），而且其中三份互不相同——同一个"模型放哪"的问题，
    下载、导入、推理、MCP 四个入口给出不同答案。

    解析链（顺序固定，别在别处再写一遍）：

    1. ``override`` —— payload 里的 ``modelDir`` / 调用方显式参数
    2. ``MEDIAFORGE_MODEL_DIR``
    3. ``PYMSS_MODEL_DIR``
    4. ``data_root`` 参数 / ``MEDIAFORGE_DATA_DIR`` / ``MEDIAFORGE_DATA_ROOT`` 下的 ``models``
    5. ``~/.mediaforge/models`` —— 打包版的默认值

    刻意**不包含**这两种兜底：

    - ``Path(__file__).parents[N] / "models"``：打包后等于安装目录（Program Files、只读，
      而且安装包里根本没有 ``models/``）。它只在"某条路径忘了传 modelDir"时发作，
      也就是**只在发布之后才会炸**。
    - 仓库根的 ``all_models``：它在打包版里永远不存在，留着只会让开发态和发布态
      走两条不同的路。需要仓库内模型时显式设 ``MEDIAFORGE_MODEL_DIR``。

    返回绝对路径；是否创建目录由调用方决定（下载器要 mkdir，只读查询不必）。
    """

    explicit = str(override or "").strip()
    if explicit:
        return Path(explicit).expanduser()

    for key in ("MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return Path(value).expanduser()

    for candidate in (data_root, os.environ.get("MEDIAFORGE_DATA_DIR"), os.environ.get("MEDIAFORGE_DATA_ROOT")):
        text = str(candidate or "").strip()
        if text:
            return Path(text).expanduser() / "models"

    return Path.home() / ".mediaforge" / "models"


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

_MIGRATED_ROOTS: set[str] = set()


def media_model_relpath(name: str) -> str:
    """媒体权重在模型根下的相对路径：`media/<文件名>`。"""
    text = str(name or "").strip().replace("\\", "/").lstrip("/")
    if not text:
        raise ValueError("media model name is empty")
    return f"{MEDIA_SUBDIR}/{text}"


def migrate_media_models(root: Any, names: Any) -> list[str]:
    """把老布局（权重直接放在模型根下）搬进 `media/`。幂等、只动点名的文件。

    为什么必须有迁移：0.0.13 之前下载的模型就躺在根目录。布局一改，那些文件会
    **看起来消失**（列表显示"未下载"），用户重下的代价是几百 MB 和一脸问号。
    迁移在第一次解析模型根时跑一次，之后靠进程内标记跳过。

    只搬 `names` 里点名的文件：模型根是应用管理的，但用户可能往里放过别的东西，
    不该顺手搬家。
    """
    base = Path(root)
    key = str(base).lower()
    if key in _MIGRATED_ROOTS:
        return []
    _MIGRATED_ROOTS.add(key)

    moved: list[str] = []
    for name in names or ():
        text = str(name or "").strip()
        # 带路径分隔符的（音频那套分类相对路径）本来就在子目录里，不用搬
        if not text or "/" in text or "\\" in text:
            continue
        source = base / text
        target = base / MEDIA_SUBDIR / text
        if target.exists() or not source.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
        except OSError:
            # 迁移失败不是致命错误：退回旧位置仍然能被旧路径读到，
            # 让下一次解析再试（标记已置位，本进程不再重试）。
            continue
        moved.append(text)
    return moved


def reset_migration_cache() -> None:
    """测试用：清掉"这个根已经迁移过"的进程内标记。"""
    _MIGRATED_ROOTS.clear()
