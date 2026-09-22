"""把 FileCleanup 打包成免安装绿色版（PyInstaller onedir）。

用法（在装了 PyInstaller 的环境里执行）：

    python tools/build_exe.py                 # 默认 onedir，输出到 dist/FileCleanup/
    python tools/build_exe.py --onefile       # 单 exe（启动慢、更易被杀软误报）
    python tools/build_exe.py --windowed      # 不弹控制台窗口

产物是一个自包含目录：内含 Python 解释器、标准库、前端资源与扩展名映射表，
拷到任何一台同架构 Windows 上双击 FileCleanup.exe 即可运行，不需要装 Python。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "FileCleanup"

COPY_FILES = [
    ("LICENSE", "LICENSE.txt"),
    ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.txt"),
    ("docs/green/使用说明.txt", "使用说明.txt"),
]


def build_command() -> list:
    """构造 PyInstaller 命令。

    优先走 `python -m PyInstaller`：比直接 spawn pyinstaller.exe 稳（后者在部分
    环境下会因为子进程启动方式报 FileNotFoundError）。
    """
    base = [sys.executable, "-m", "PyInstaller"]
    try:
        import PyInstaller  # noqa: F401
        return base
    except ImportError:
        folder = "Scripts" if os.name == "nt" else "bin"
        exe = "pyinstaller.exe" if os.name == "nt" else "pyinstaller"
        candidate = Path(sys.executable).parent / folder / exe
        return [str(candidate)] if candidate.exists() else ["pyinstaller"]


def build(onefile: bool, windowed: bool) -> Path:
    cmd = build_command() + [
        "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed" if windowed else "--console",
        "--name", NAME,
        # 随包资源：前端页面 + 扩展名映射表（冻结后通过 sys._MEIPASS 读取）
        "--add-data", f"{ROOT / 'resources'}{os.pathsep}resources",
        "--add-data", f"{ROOT / 'filecleanup' / 'web'}{os.pathsep}filecleanup{os.sep}web",
        # 用不到的重型模块，减小体积、加快启动
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "doctest",
        "--exclude-module", "lib2to3",
        "--exclude-module", "sqlite3",
        str(ROOT / "run.py"),
    ]
    print("[build] " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    out = ROOT / "dist" / NAME
    if not onefile:
        out.mkdir(parents=True, exist_ok=True)
        for src, dst in COPY_FILES:
            s = ROOT / src
            if s.exists():
                shutil.copy2(s, out / dst)
                print(f"[build] 已附带 {dst}")
    else:
        for src, dst in COPY_FILES:
            s = ROOT / src
            if s.exists():
                shutil.copy2(s, ROOT / "dist" / dst)

    target = ROOT / "dist" / (NAME + ".exe") if onefile else out
    print(f"[build] 完成：{target}")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 FileCleanup 绿色版")
    ap.add_argument("--onefile", action="store_true", help="打成单个 exe（默认 onedir）")
    ap.add_argument("--windowed", action="store_true", help="不显示控制台窗口")
    args = ap.parse_args()
    build(args.onefile, args.windowed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
