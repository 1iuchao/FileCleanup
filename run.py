#!/usr/bin/env python
"""FileCleanup 启动入口。

    python run.py                     # 默认 http://127.0.0.1:8770
    python run.py --port 9000         # 指定端口
    python run.py --no-browser        # 不自动打开浏览器
    python run.py --scan D:\\Data     # 启动后直接开始扫描该目录
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

from filecleanup.config import DEFAULT_PORT, VERSION
from filecleanup.server import STATE, serve


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="FileCleanup",
        description="本地文件清理 / 分析工具（扫描 + 横向树状可视化）",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--scan", metavar="PATH", help="启动后立即扫描指定目录")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本")
        return 1

    print(f"FileCleanup v{VERSION}  |  Python {sys.version.split()[0]}")

    if args.scan:
        def _kick():
            time.sleep(0.3)
            try:
                STATE.new_scan(args.scan)
                print(f"[FileCleanup] 已开始扫描：{args.scan}")
            except Exception as exc:
                print(f"[FileCleanup] 扫描启动失败：{exc}")
        threading.Thread(target=_kick, daemon=True).start()

    serve(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
