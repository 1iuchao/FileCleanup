"""FileCleanup 自检用例（纯标准库 unittest，无需 pytest）。

    python -m unittest discover -s tests -v
    # 或
    python tests/test_scanner.py
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from filecleanup import extmap                     # noqa: E402
from filecleanup.deleter import delete_path        # noqa: E402
from filecleanup.scanner import Scanner            # noqa: E402
from filecleanup.server import Handler             # noqa: E402

TEST_PORT = 8791


def make_tree(base: os.PathLike) -> None:
    """构造：root/{big/, small/, a.bin, b.txt}，其中 big 下再嵌一层。"""
    os.makedirs(os.path.join(base, "big", "nested"), exist_ok=True)
    os.makedirs(os.path.join(base, "small"), exist_ok=True)
    with open(os.path.join(base, "a.bin"), "wb") as f:
        f.write(b"x" * 4096)
    with open(os.path.join(base, "b.txt"), "w", encoding="utf-8") as f:
        f.write("hello")
    with open(os.path.join(base, "big", "nested", "big.bin"), "wb") as f:
        f.write(b"y" * 8192)
    with open(os.path.join(base, "small", "c.log"), "w", encoding="utf-8") as f:
        f.write("log")


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filecleanup-test-")
        self.root = os.path.join(self.tmp, "root")
        make_tree(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, scanner: Scanner, timeout: float = 20.0):
        scanner.start(self.root)
        deadline = time.time() + timeout
        while time.time() < deadline and not scanner.state.finished:
            time.sleep(0.02)
        return scanner

    def test_size_aggregation(self):
        sc = self._run(Scanner())
        self.assertFalse(sc.state.error, sc.state.error)
        root = sc.root
        total = 4096 + 5 + 8192 + 3
        self.assertEqual(root.size, total, "根目录体积应等于所有后代之和")
        big = next(c for c in root.children if c.name == "big")
        self.assertEqual(big.size, 8192)
        self.assertEqual(big.own_size, 0, "big 的直属体积不含 nested")

    def test_sorted_desc_by_size(self):
        sc = self._run(Scanner())
        sizes = [c.size for c in sc.root.children]
        self.assertEqual(sizes, sorted(sizes, reverse=True), "同级应按体积从大到小排列")

    def test_missing_root_sets_error(self):
        sc = Scanner()
        sc.start(os.path.join(self.tmp, "not-exists"))
        deadline = time.time() + 5
        while time.time() < deadline and not sc.state.finished:
            time.sleep(0.02)
        self.assertTrue(sc.state.finished)
        self.assertIsNotNone(sc.state.error)

    def test_permission_error_node(self):
        """无权限目录应变成 error 节点，而不是让整次扫描失败。"""
        import filecleanup.scanner as scanner_mod

        original = scanner_mod.os.scandir

        def fake_scandir(path):
            if path.endswith("small"):
                raise PermissionError(13, "Permission denied")
            return original(path)

        scanner_mod.os.scandir = fake_scandir
        try:
            sc = self._run(Scanner())
        finally:
            scanner_mod.os.scandir = original

        self.assertIsNone(sc.state.error, "单个目录失败不应影响整体扫描")
        small = next(n for n in sc.nodes if n.name == "small")
        self.assertEqual(small.kind, "error")
        self.assertIsNotNone(small.error)
        self.assertEqual(sc.root.size, 4096 + 5 + 8192, "失败目录记 0，其余照常汇总")

    def test_cycle_is_not_followed(self):
        """Windows junction / POSIX symlink 自引用不应导致死循环。"""
        link = os.path.join(self.root, "big", "back")
        target = self.root
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name == "nt":
                r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                                   capture_output=True)
                if r.returncode != 0:
                    self.skipTest("当前环境无法创建链接（需管理员或开发者模式）")
            else:
                self.skipTest("当前环境无法创建链接")
        sc = self._run(Scanner(), timeout=15)
        self.assertTrue(sc.state.finished, "扫描必须能正常结束")
        self.assertIsNone(sc.state.error)
        back = next((n for n in sc.nodes if n.name == "back"), None)
        self.assertIsNotNone(back)
        # 要么被识别为链接，要么因环检测被跳过，总之不能展开成无限深度
        self.assertTrue(back.kind == "symlink" or back.error, back.kind)


class ExtMapTest(unittest.TestCase):
    def test_known_ext(self):
        zh, cat = extmap.describe(".zip")
        self.assertEqual(zh, "压缩文件")
        self.assertEqual(cat, "archive")

    def test_unknown_ext(self):
        zh, cat = extmap.describe(".weirdext")
        self.assertIn("weirdext", zh)
        self.assertEqual(cat, "other")


class DeleteTest(unittest.TestCase):
    def test_permanent_delete_file(self):
        tmp = tempfile.mkdtemp(prefix="filecleanup-del-")
        try:
            p = os.path.join(tmp, "victim.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("bye")
            res = delete_path(p, use_recycle=False)
            self.assertTrue(res["ok"], res)
            self.assertFalse(os.path.exists(p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ApiTest(unittest.TestCase):
    """端到端：起一个真实 HTTP 服务，走一遍前端用到的全部接口。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="filecleanup-api-")
        cls.root = os.path.join(cls.tmp, "root")
        make_tree(cls.root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", TEST_PORT), Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=15)
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, json.dumps(body) if body else None, headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, (json.loads(raw) if raw.startswith("{") else raw)

    def test_endpoints(self):
        code, r = self._req("POST", "/api/scan", {"path": self.root})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"], r)

        deadline = time.time() + 20
        while time.time() < deadline:
            _, p = self._req("GET", "/api/progress")
            if p.get("finished"):
                break
            time.sleep(0.1)
        self.assertEqual(p["files"], 4, p)
        self.assertTrue(p["bytes"] > 0)

        _, t = self._req("GET", "/api/tree?depth=2")
        self.assertTrue(t["ok"])
        self.assertEqual(t["root"]["name"], "root")
        self.assertTrue(t["root"]["children"])

        root_id = t["root"]["id"]
        _, c = self._req("GET", f"/api/children?id={root_id}&depth=0")
        self.assertTrue(c["ok"] and len(c["children"]) >= 2)

        _, n = self._req("GET", f"/api/node?id={root_id}")
        self.assertTrue(n["ok"])
        self.assertEqual(n["node"]["parent"], None)

        _, e = self._req("GET", "/api/extmap")
        self.assertIn(".zip", e["table"])

        _, roots = self._req("GET", "/api/roots")
        self.assertTrue(roots["ok"])

        # 安全边界：拒绝删除扫描范围之外的路径（这里用不存在的 id 触发跳过）
        _, d = self._req("POST", "/api/delete", {"ids": [999999], "useRecycle": False})
        self.assertTrue(d["ok"])
        self.assertEqual(d["results"][0]["ok"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
