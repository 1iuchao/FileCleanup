"""安全删除：优先送回收站，失败再回退到永久删除。

Windows 通过 SHFileOperationW + FOF_ALLOWUNDO 走系统回收站（纯 ctypes，无第三方依赖）；
其他平台退化为永久删除。删除范围由 server 层限制在"本次扫描结果"内。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Dict

# ---------------------------------------------------------------- Windows 回收站
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def _recycle_windows(path: str) -> bool:
    """使用 SHFileOperationW 把单个路径送入回收站。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # pFrom 必须是双 \0 结尾的字符串
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = path + "\0\0"
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    try:
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:
        return False
    return res == 0 and not op.fAnyOperationsAborted


def _permanent(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=False)
    else:
        raise FileNotFoundError(path)


def delete_path(path: str, use_recycle: bool = True) -> Dict:
    """删除单个路径。

    返回 {"path","ok","method","message"}；不抛异常，失败原因写进 message。
    """
    if not os.path.lexists(path):
        return {"path": path, "ok": False, "method": "none", "message": "路径已不存在"}

    if use_recycle and sys.platform == "win32" and len(path) < 240:
        if _recycle_windows(path):
            return {"path": path, "ok": True, "method": "recycle", "message": "已移入回收站"}
        return {"path": path, "ok": False, "method": "recycle",
                "message": "回收站删除失败（路径过长或文件被占用），未执行删除"}

    try:
        _permanent(path)
        return {"path": path, "ok": True, "method": "permanent",
                "message": "已永久删除" if not use_recycle else "回收站不可用，已永久删除"}
    except Exception as exc:
        return {"path": path, "ok": False, "method": "permanent",
                "message": f"{type(exc).__name__}: {exc}"}
