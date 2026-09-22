"""扩展名 -> 中文释义 / 分类 映射表。

单一数据源：`resources/ext_zh.json`。
用户可在 `~/.filecleanup/ext_zh.json` 中同名键覆盖或补充条目，
前端通过 /api/extmap 拉取同一份数据，保证前后端语义一致。

扩展格式（便于后续补充）：
    {
      ".zip": {"zh": "压缩文件", "cat": "archive"}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from .config import RESOURCE_DIR, USER_EXT_FILE

# JSON 缺失时的兜底最小表（正常情况下不会用到）
_FALLBACK: Dict[str, Dict[str, str]] = {
    ".zip": {"zh": "压缩文件", "cat": "archive"},
    ".txt": {"zh": "文本文件", "cat": "doc"},
    ".jpg": {"zh": "图片文件", "cat": "image"},
    ".mp4": {"zh": "视频文件", "cat": "video"},
    ".mp3": {"zh": "音频文件", "cat": "audio"},
    ".exe": {"zh": "可执行程序", "cat": "app"},
}

_CACHE: Dict[str, Dict[str, str]] = {}
_LOADED = False

# 分类 -> 展示名（目录节点使用 cat="dir"）
CATEGORY_LABELS: Dict[str, str] = {
    "dir": "文件夹",
    "archive": "压缩包",
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "doc": "文档",
    "sheet": "表格",
    "slide": "演示文稿",
    "pdf": "PDF 文档",
    "code": "源代码",
    "data": "数据文件",
    "db": "数据库",
    "app": "程序",
    "font": "字体",
    "disk": "磁盘镜像",
    "system": "系统文件",
    "log": "日志",
    "temp": "临时/缓存",
    "web": "网页资源",
    "model": "模型/工程",
    "other": "文件",
}


def _load() -> Dict[str, Dict[str, str]]:
    table: Dict[str, Dict[str, str]] = {}
    for src in (RESOURCE_DIR / "ext_zh.json", USER_EXT_FILE):
        try:
            if src and Path(src).is_file():
                raw = json.loads(Path(src).read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if isinstance(v, str):          # 允许简写：".xx": "中文名"
                            table[str(k).lower()] = {"zh": v, "cat": "other"}
                        elif isinstance(v, dict):
                            table[str(k).lower()] = {
                                "zh": v.get("zh", "文件"),
                                "cat": v.get("cat", "other"),
                            }
        except Exception:
            # 映射表损坏不应影响主流程
            continue
    return table or dict(_FALLBACK)


def table() -> Dict[str, Dict[str, str]]:
    """返回完整映射表（带缓存）。"""
    global _CACHE, _LOADED
    if not _LOADED:
        _CACHE = _load()
        _LOADED = True
    return _CACHE


def reload() -> None:
    """编辑 JSON 后调用可热更新（或重启服务）。"""
    global _CACHE, _LOADED
    _CACHE = _load()
    _LOADED = True


def describe(ext: str) -> Tuple[str, str]:
    """返回 (中文释义, 分类)。未知扩展名给出可读兜底。"""
    key = (ext or "").lower()
    if not key:
        return "无扩展名", "other"
    item = table().get(key)
    if item:
        return item.get("zh", "文件"), item.get("cat", "other")
    return f"{key.lstrip('.')} 文件", "other"


def category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, "文件")
