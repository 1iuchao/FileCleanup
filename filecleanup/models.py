"""数据模型：目录树节点与扫描状态。

- Node：一棵目录树上的节点（目录或文件），体积语义如下
    * own_size: 目录 => 直属子文件体积之和；文件 => 文件自身体积
    * size:     目录 => 所有后代体积之和（含子目录）；文件 => 文件自身体积
- ScanState：扫描线程与 HTTP 线程之间共享的进度快照（读写均需持锁）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 节点类型
KIND_DIR = "dir"
KIND_FILE = "file"
KIND_SYMLINK = "symlink"     # 符号链接 / Windows 重解析点（不跟随）
KIND_ERROR = "error"         # 无权限或读取失败
KIND_OTHER = "other"         # 非常规文件（设备、管道等）


@dataclass(slots=True)
class Node:
    """目录树节点。id 即其在扫描器扁平数组中的下标。

    使用 __slots__（dataclass(slots=True)）：扫到 30 万节点时可省下约 40% 内存。
    """

    id: int
    name: str                       # 文件名（不含路径）
    path: str                       # 真实绝对路径（用于定位 / 删除 / 详情）
    is_dir: bool
    kind: str = KIND_FILE
    size: int = 0                   # 汇总体积（目录含子项）
    own_size: int = 0               # 直属体积
    mtime: float = 0.0              # 修改时间（epoch 秒）
    ext: str = ""                   # 小写扩展名，含前导点，如 ".zip"；目录为 ""
    error: Optional[str] = None     # 读取失败原因
    child_count: int = 0            # 直属子项数量（含未加载的子目录）
    children: List["Node"] = field(default_factory=list)
    parent: Optional["Node"] = field(default=None, repr=False, compare=False)

    # ---------------- 序列化 ----------------
    def to_dict(self, depth: int = 0, include_children: bool = True,
                max_children: int = 0) -> Dict:
        """转为可 JSON 序列化的字典。

        depth:          向下携带的层数，0 表示只返回本节点（客户端按需再取子节点）
        max_children:   >0 时每层最多返回这么多子节点（配合 childCount 做前端分页）
        """
        data = {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "isDir": self.is_dir,
            "kind": self.kind,
            "size": self.size,
            "ownSize": self.own_size,
            "mtime": self.mtime,
            "ext": self.ext,
            "error": self.error,
            "childCount": self.child_count,
        }
        if include_children and depth > 0:
            kids = self.children[:max_children] if max_children else self.children
            data["children"] = [c.to_dict(depth - 1, True, max_children) for c in kids]
        else:
            data["children"] = None
        return data


@dataclass
class ScanState:
    """扫描进度快照（线程间共享，访问需加锁）。"""

    running: bool = False
    cancel_requested: bool = False
    finished: bool = False
    root_path: str = ""
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    nodes: int = 0
    current_path: str = ""
    started_at: float = 0.0
    elapsed: float = 0.0
    truncated: bool = False         # 达到 MAX_NODES 被截断
    error: Optional[str] = None     # 根路径不存在等致命错误

    def to_dict(self) -> Dict:
        return {
            "running": self.running,
            "finished": self.finished,
            "cancelRequested": self.cancel_requested,
            "rootPath": self.root_path,
            "files": self.files,
            "dirs": self.dirs,
            "bytes": self.bytes,
            "nodes": self.nodes,
            "currentPath": self.current_path,
            "elapsed": round(self.elapsed, 2),
            "truncated": self.truncated,
            "error": self.error,
        }
