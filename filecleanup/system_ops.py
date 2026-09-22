"""跨平台系统操作：长路径处理、在资源管理器中定位。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def long_path(path: str) -> str:
    """Windows 下转换为 \\\\?\\ 前缀形式，绕过 260 字符路径长度限制。

    其他平台原样返回。注意：带前缀的路径不能传给 explorer，
    因此仅用于内部 stat/scandir，界面展示仍使用原始路径。
    """
    if sys.platform != "win32":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):                      # UNC 网络路径
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def open_in_explorer(path: str) -> None:
    """在系统文件管理器中定位到该路径（文件则选中该文件）。"""
    p = str(Path(path).resolve())
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{p}"], shell=False)
        elif sys.platform == "darwin":
            if os.path.isdir(p):
                subprocess.Popen(["open", p], shell=False)
            else:
                subprocess.Popen(["open", "-R", p], shell=False)
        else:
            target = p if os.path.isdir(p) else os.path.dirname(p)
            subprocess.Popen(["xdg-open", target], shell=False)
    except Exception:
        # 打不开也不该让主程序崩掉
        pass
