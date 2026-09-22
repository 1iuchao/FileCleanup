"""全局配置与常量。

集中放置路径、扫描上限、进度刷新频率等可调参数，
方便按需调整而无需改动业务逻辑。

打包说明：PyInstaller 冻结运行（sys.frozen）时，资源目录指向 sys._MEIPASS，
因此打包后不依赖任何绝对路径，拷到别的电脑直接可用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "FileCleanup"
VERSION = "1.0.0"

IS_FROZEN = bool(getattr(sys, "frozen", False))     # PyInstaller / 打包后的运行态

# ---------------------------------------------------------------- 路径
PACKAGE_ROOT = Path(__file__).resolve().parent          # .../filecleanup
if IS_FROZEN:
    # 冻结运行：所有随包资源都解压在 sys._MEIPASS 下
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", PACKAGE_ROOT.parent))
else:
    PROJECT_ROOT = PACKAGE_ROOT.parent                 # 仓库根目录
WEB_DIR = PACKAGE_ROOT / "web"                          # 前端静态资源
RESOURCE_DIR = PROJECT_ROOT / "resources"               # 资源（扩展名映射表）
USER_EXT_FILE = Path.home() / ".filecleanup" / "ext_zh.json"  # 用户自定义扩展名表（可选）

# ---------------------------------------------------------------- 服务
DEFAULT_PORT = int(os.environ.get("FC_PORT", "8770"))
HOST = "127.0.0.1"          # 仅本地回环，不对外暴露
PORT_FALLBACK_TRIES = 20    # 端口被占用时向后尝试的次数

# ---------------------------------------------------------------- 扫描
# 遍历深度上限，兜底防止异常链接结构导致超深递归
MAX_DEPTH = int(os.environ.get("FC_MAX_DEPTH", "256"))
# 单目录条目数极多时的保护阈值：超过后停止新增节点并标记 truncated
MAX_NODES = int(os.environ.get("FC_MAX_NODES", "300000"))
# 每处理多少个条目刷新一次进度计数（避免频繁加锁）
PROGRESS_STEP = 200
# 单次扫描允许的最长运行时间（秒），0 表示不限
MAX_SCAN_SECONDS = int(os.environ.get("FC_MAX_SCAN_SECONDS", "0"))
# 初始返回的树深度（其余层级按需懒加载）
DEFAULT_TREE_DEPTH = 2
# 单个目录下一次最多返回的子节点数（前端渲染性能保护，可翻页继续取）
CHILD_PAGE_SIZE = int(os.environ.get("FC_CHILD_PAGE", "300"))
# 体积 TOP N 榜单的默认条数
TOP_N_DEFAULT = 20
