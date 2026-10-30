"""容错的目录遍历。

真实事故：用户数据根里存在一个 junction（`~/.mediaforge/models` 指向开发目录），
`Path.rglob()` 撞上它就抛 `WinError 448（无法遍历该路径，因为它包含不受信任的装入点）`，
整个模型库页面直接报错、一个模型都列不出来。

原则：**一个坏掉的子目录不该让整份清单失败**。遇到无权限、装入点、损坏链接，
跳过该条并继续，同时在结果里记录跳过了多少。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

# Windows: 448 = 不受信任的装入点；1920(0x780) = 无法访问重解析点
_UNREADABLE_ERRNOS = {13, 448, 1920}


def _is_unreadable(error: OSError) -> bool:
    return getattr(error, "winerror", None) in _UNREADABLE_ERRNOS or error.errno in _UNREADABLE_ERRNOS


def iter_files(root: Path) -> Iterator[Path]:
    """遍历 root 下的文件；跳过无法访问的目录/链接，绝不抛异常。"""
    try:
        stack = [os.fspath(root)]
    except TypeError:
        return
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        try:
            real = os.path.realpath(current)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            if _is_unreadable(error):
                continue
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    # 链接一律不跟随：这正是 448 的来源
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError:
                continue


def path_size(root: Path) -> int:
    """容错地统计目录大小（字节）；读不到的部分按 0 计。"""
    total = 0
    for file_path in iter_files(root):
        try:
            total += file_path.stat().st_size
        except OSError:
            continue
    return total


def count_files(root: Path) -> int:
    return sum(1 for _ in iter_files(root))
