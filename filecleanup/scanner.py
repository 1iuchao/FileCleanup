"""递归目录扫描器。

设计要点
--------
1. **迭代而非递归**：用显式栈做深度优先遍历，避免 Windows 深层目录打爆
   Python 递归栈。
2. **体积自底向上聚合**：栈帧保存"当前目录已累计体积"，子目录帧出栈时
   把自身 size 累加到父帧，父目录体积天然等于所有子项之和。
3. **异常隔离**：单个目录 scandir 失败（无权限 / 路径消失 / 设备未就绪）
   只把该目录标记为 error 节点，不影响整棵树。
4. **符号链接与 Windows 重解析点（junction）不跟随**：既避免 `C:\\Users\\*
   \\Application Data` 这类自引用造成的死循环，也用 (dev, ino) 兜底防环。
5. **大目录可控**：MAX_NODES 上限 + 每 N 条刷新进度 + 可取消，
   扫描跑在后台线程，HTTP 线程只读取进度快照，界面不会卡死。
"""

from __future__ import annotations

import os
import stat
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from .config import MAX_DEPTH, MAX_NODES, MAX_SCAN_SECONDS, PROGRESS_STEP
from .models import (
    KIND_DIR,
    KIND_ERROR,
    KIND_FILE,
    KIND_OTHER,
    KIND_SYMLINK,
    Node,
    ScanState,
)
from .system_ops import long_path


class _Frame:
    """遍历栈帧：一个目录的扫描上下文。"""

    __slots__ = ("node", "acc", "pending", "depth")

    def __init__(self, node: Node, depth: int = 0):
        self.node = node
        self.acc = 0             # 直属文件体积 + 已完成子目录体积
        self.pending: List[Tuple[Node, int, int]] = []   # 待下钻的 (子目录节点, dev, ino)
        self.depth = depth


def _is_link(st: os.stat_result) -> bool:
    """符号链接 / Windows junction / 重解析点判定。"""
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _split_ext(name: str) -> str:
    ext = os.path.splitext(name)[1]
    return ext.lower() if ext else ""


class Scanner:
    """一次扫描对应一个 Scanner 实例；重复扫描请新建实例。"""

    def __init__(self, follow_symlinks: bool = False, max_nodes: int = MAX_NODES):
        self.follow_symlinks = follow_symlinks
        self.max_nodes = max_nodes
        self.nodes: List[Node] = []          # 扁平数组，index == node.id
        self.state = ScanState()
        self._lock = threading.Lock()
        self._visited: Set[Tuple[int, int]] = set()
        self._visited_paths: Set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._counter = 0
        # 待刷新的临时计数（减少对共享状态的加锁次数）
        self._pending_files = 0
        self._pending_dirs = 0
        self._pending_bytes = 0

    # ------------------------------------------------------------------ 对外接口
    def start(self, root_path: str) -> None:
        """在后台线程启动扫描；重复调用会被忽略。"""
        with self._lock:
            if self.state.running:
                return
            self.state.running = True
            self.state.root_path = os.path.abspath(os.path.expanduser(root_path))
            self.state.started_at = time.time()
        self._thread = threading.Thread(target=self._run, args=(self.state.root_path,),
                                        name="scanner", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.state.cancel_requested = True

    def snapshot(self) -> Dict:
        """读取进度快照（加锁，避免读到半更新状态）。"""
        with self._lock:
            if self.state.running:
                self.state.elapsed = time.time() - self.state.started_at
            return self.state.to_dict()

    def node(self, node_id: int) -> Optional[Node]:
        if 0 <= node_id < len(self.nodes):
            return self.nodes[node_id]
        return None

    @property
    def root(self) -> Optional[Node]:
        return self.nodes[0] if self.nodes else None

    # ------------------------------------------------------------------ 内部实现
    def _new_node(self, path: str, name: str, is_dir: bool, parent: Optional[Node]) -> Node:
        node = Node(
            id=len(self.nodes),
            name=name,
            path=path,
            is_dir=is_dir,
            parent=parent,
            kind=KIND_DIR if is_dir else KIND_FILE,
        )
        self.nodes.append(node)
        if parent is not None:
            parent.children.append(node)
        return node

    def _touch_progress(self, current: str = "", files: int = 0, dirs: int = 0,
                        size: int = 0, force: bool = False) -> None:
        """累计计数，每 PROGRESS_STEP 次或 force 时刷新一次共享状态。"""
        self._pending_files += files
        self._pending_dirs += dirs
        self._pending_bytes += size
        self._counter += 1
        if not force and self._counter % PROGRESS_STEP:
            return
        with self._lock:
            self.state.files += self._pending_files
            self.state.dirs += self._pending_dirs
            self.state.bytes += self._pending_bytes
            self.state.nodes = len(self.nodes)
            if current:
                self.state.current_path = current
        self._pending_files = self._pending_dirs = self._pending_bytes = 0
        self._counter = 0

    def _run(self, root_path: str) -> None:
        try:
            if not os.path.exists(long_path(root_path)):
                raise FileNotFoundError(f"路径不存在：{root_path}")

            root = self._new_node(root_path, os.path.basename(root_path) or root_path, True, None)
            try:
                st = os.stat(long_path(root_path), follow_symlinks=False)
                root.mtime = st.st_mtime
                self._visited.add((st.st_dev, st.st_ino))
            except OSError:
                pass

            stack: List[_Frame] = [self._scan_dir(root, 0)]

            while stack:
                if self.state.cancel_requested:
                    break
                if MAX_SCAN_SECONDS and (time.time() - self.state.started_at) > MAX_SCAN_SECONDS:
                    with self._lock:
                        self.state.truncated = True
                    break

                frame = stack[-1]

                if frame.pending:
                    child, dev, ino = frame.pending.pop()
                    # 深度兜底（异常链接结构 / 超长路径）
                    if frame.depth + 1 > MAX_DEPTH:
                        child.kind = KIND_ERROR
                        child.error = f"已跳过：超过最大遍历深度 {MAX_DEPTH}"
                        continue
                    # 环检测：仅在能拿到有效 inode 时生效。
                    # Windows 的 DirEntry.stat() 恒返回 st_ino=0，此时退化为
                    # "重解析点/符号链接一律不跟随"，同样不会死循环。
                    if ino and (dev, ino) in self._visited:
                        child.kind = KIND_SYMLINK
                        child.error = "已跳过：检测到循环引用或重复入口"
                        continue
                    if ino:
                        self._visited.add((dev, ino))
                    if not ino and self.follow_symlinks:
                        # 无 inode 可用、又要跟随链接时，用真实路径兜底防环
                        rp = os.path.realpath(child.path)
                        if rp in self._visited_paths:
                            child.kind = KIND_SYMLINK
                            child.error = "已跳过：检测到循环引用"
                            continue
                        self._visited_paths.add(rp)
                    try:
                        stack.append(self._scan_dir(child, frame.depth + 1))
                    except OSError as exc:
                        child.kind = KIND_ERROR
                        child.error = f"无法读取：{exc.strerror or exc}"
                    continue

                # 本目录处理完毕：汇总 -> 排序 -> 回填父帧
                node = frame.node
                node.size = frame.acc
                node.child_count = len(node.children)
                node.children.sort(key=lambda n: (-n.size, n.name.lower()))
                stack.pop()
                if stack:
                    stack[-1].acc += node.size
                with self._lock:
                    self.state.current_path = node.path
        except Exception as exc:                      # 兜底：任何异常都转成状态
            with self._lock:
                self.state.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._touch_progress(force=True)
            with self._lock:
                self.state.running = False
                self.state.finished = True
                self.state.nodes = len(self.nodes)
                self.state.elapsed = time.time() - self.state.started_at
                if self.root is not None:
                    self.state.bytes = self.root.size

    def _scan_dir(self, node: Node, depth: int = 0) -> _Frame:
        """扫描单个目录：创建直属子节点，子目录留待后续下钻。"""
        frame = _Frame(node, depth)
        scan_path = long_path(node.path)
        files_added = size_added = 0
        try:
            # 流式迭代，不 list() 整个目录：超大目录（十万级条目）下内存峰值低得多
            with os.scandir(scan_path) as it:
                for entry in it:
                    if self.state.cancel_requested:
                        break
                    if len(self.nodes) >= self.max_nodes:
                        with self._lock:
                            self.state.truncated = True
                        break

                    child_path = os.path.join(node.path, entry.name)
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        child = self._new_node(child_path, entry.name, False, node)
                        child.kind = KIND_ERROR
                        child.error = f"无法读取：{exc.strerror or exc}"
                        continue

                    if _is_link(st):
                        child = self._new_node(child_path, entry.name, stat.S_ISDIR(st.st_mode), node)
                        child.kind = KIND_SYMLINK
                        child.size = child.own_size = st.st_size
                        child.mtime = st.st_mtime
                        child.ext = "" if child.is_dir else _split_ext(entry.name)
                        if child.is_dir and self.follow_symlinks:
                            frame.pending.append((child, st.st_dev, st.st_ino))
                        else:
                            child.error = "未跟随链接（避免循环统计）"
                        frame.acc += child.size
                        continue

                    if stat.S_ISDIR(st.st_mode):
                        child = self._new_node(child_path, entry.name, True, node)
                        child.mtime = st.st_mtime
                        frame.pending.append((child, st.st_dev, st.st_ino))
                        continue

                    if stat.S_ISREG(st.st_mode):
                        child = self._new_node(child_path, entry.name, False, node)
                        child.kind = KIND_FILE
                        child.size = child.own_size = st.st_size
                        child.mtime = st.st_mtime
                        child.ext = _split_ext(entry.name)
                        frame.acc += child.size
                        files_added += 1
                        size_added += child.size
                        continue

                    # 设备 / 管道 / socket 等非常规文件
                    child = self._new_node(child_path, entry.name, False, node)
                    child.kind = KIND_OTHER
                    child.size = child.own_size = st.st_size
                    child.mtime = st.st_mtime
                    child.ext = _split_ext(entry.name)
                    frame.acc += child.size
        except OSError as exc:
            node.kind = KIND_ERROR
            node.error = f"无法读取：{exc.strerror or exc}（多为权限不足）"

        node.own_size = frame.acc
        self._touch_progress(node.path, files=files_added, dirs=1, size=size_added)
        return frame
