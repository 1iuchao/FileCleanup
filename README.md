# FileCleanup · 本地文件清理 / 分析工具（含免安装绿色版）

一个跑在本机的磁盘空间分析工具：**选目录 → 递归扫描 → 横向树状图看清"空间被谁吃了" → 挑出来 → 二次确认后删除**。
全程本地运行，只监听 `127.0.0.1`，不联网、不上传任何数据。

仓库地址：https://github.com/1iuchao/FileCleanup

**不想装 Python？** 直接下 Windows 绿色版（7.8 MB，解压即用）：
[FileCleanup-v1.0.0-windows-x64.zip](https://github.com/1iuchao/FileCleanup/releases/download/v1.0.0/FileCleanup-v1.0.0-windows-x64.zip)
（全部版本见 [Releases](https://github.com/1iuchao/FileCleanup/releases)）

本项目按三个阶段完成：**① 界面设计 → ② 性能优化 → ③ 打包封装**，最终产物是
`dist/FileCleanup/` 这个自包含目录，**拷到别的电脑双击 exe 就能用，不需要装 Python**。

---

## 目录结构

```
FileCleanup/
├── README.md                     # 本文件
├── LICENSE                       # MIT
├── THIRD-PARTY-NOTICES.md        # 第三方组件署名与许可证（D3.js / CPython / 构建工具）
├── requirements.txt              # 依赖清单（核心零第三方依赖）
├── .gitignore
├── run.py                        # 源码启动入口（CLI）
│
├── filecleanup/                  # ── 后端 ──
│   ├── config.py                 # 配置；含 PyInstaller 冻结态的路径处理
│   ├── models.py                 # 【数据模型】Node（__slots__）/ ScanState
│   ├── scanner.py                # 【扫描】迭代式 DFS、体积自底向上聚合、异常与环处理
│   ├── extmap.py                 # 【数据模型】扩展名 → 中文释义 / 分类
│   ├── deleter.py                # 【删除】回收站优先，失败不擅自永久删除
│   ├── system_ops.py             # 长路径处理、资源管理器定位
│   ├── server.py                 # 【服务】REST API + 静态资源 + 端口自动顺延
│   └── web/                      # ── 前端 ──
│       ├── index.html            # 【界面】结构（顶栏 / 画布 / 侧栏 / 状态栏）
│       ├── styles.css            # 【界面】主题变量 + 响应式断点
│       ├── app.js                # 【绘图 + 交互】树布局、视口裁剪渲染、清单与删除流程
│       └── vendor/d3.slim.min.js # 第三方：D3.js 子集（ISC，约 50KB，带版权头）
│
├── resources/ext_zh.json         # 扩展名 → 中文释义映射表（200+ 条，可直接补充）
├── tests/test_scanner.py         # 自检用例（unittest，9 项）
├── tools/build_exe.py            # 【打包】一键生成免安装绿色版
├── docs/
│   ├── green/使用说明.txt         # 绿色版随包说明（会拷进 dist）
│   └── screenshots/README.md     # 效果截图占位
└── dist/FileCleanup/             # ★ 打包产物：绿色版（FileCleanup.exe + _internal）
```

分层原则：**扫描层不认识界面，绘图层不认识文件系统**，两侧只通过 `models.Node` 的 JSON 契约通信。

---

## 启动方式

### 方式一：绿色版（推荐，无需 Python）

```
dist/FileCleanup/
├── FileCleanup.exe      ← 双击它
├── _internal/           ← 内含 Python 解释器、标准库、前端资源、扩展名表
├── 使用说明.txt
├── LICENSE.txt
└── THIRD-PARTY-NOTICES.txt
```

双击 `FileCleanup.exe`，浏览器会自动打开 `http://127.0.0.1:8770`。
用完点页面右上角的 **「退出」**：服务会停止扫描、关闭监听、释放端口并结束进程
（也可在控制台窗口按 Ctrl+C）。**直接关掉浏览器页面不会停服务**。

常用参数（在 cmd / PowerShell 里跑）：

```bat
FileCleanup.exe                :: 默认 8770，自动开浏览器
FileCleanup.exe --port 9000    :: 指定端口
FileCleanup.exe --no-browser   :: 不自动开浏览器
FileCleanup.exe --scan D:\Data :: 启动后立刻扫描指定目录
```

### 方式二：源码运行（需要 Python ≥ 3.10）

```bash
python run.py                     # 默认 http://127.0.0.1:8770
python run.py --port 9000
python run.py --no-browser
python run.py --scan D:\Data

python -m unittest discover -s tests -v    # 跑自检（9 项）
```

### 方式三：自己打包

```bash
pip install pyinstaller
python tools/build_exe.py                  # → dist/FileCleanup/（onedir，推荐）
python tools/build_exe.py --onefile        # → dist/FileCleanup.exe（单文件）
```

---

## 最低运行要求

| 项目 | 源码运行 | 绿色版 |
|---|---|---|
| 操作系统 | Windows / macOS / Linux | **Windows 10 及以上，64 位** |
| Python | ≥ 3.10（仅标准库，无需 pip 安装） | **不需要**（已内嵌 CPython 3.13） |
| 浏览器 | 任意现代浏览器（Chrome / Edge / Firefox） | 同左 |
| 内存 | 30 万节点以内约 100 MB 左右 | 同左 |
| 磁盘 | — | 约 21 MB（未压缩） |
| 网络 | 不需要（D3.js 已随包本地化） | 不需要 |

---

## 阶段一：界面设计

### 布局结构

```
┌─────────────────────────────────────────────────────────────┐
│ 顶栏① 品牌 · 路径输入 + 快速选择 + 扫描/取消 · 主题 · 帮助   │
│ 顶栏② 视图开关（中文释义 / 体积 / 粗细 / 比例布局）· 查找     │
│      · 最小体积 · 折叠 · 适应窗口 · 分类图例                 │
│ 顶栏③ 进度条 + 实时状态（已发现文件数 / 目录数 / 当前路径）   │
├──────────────────────────────────────┬──────────────────────┤
│                                      │ 详情 / 空间TOP / 清单 │
│   横向目录树画布（滚轮缩放、拖拽平移） │  （三个标签页）      │
│   右下角缩放按钮；空状态为三步引导     │  侧栏可整体收起      │
├──────────────────────────────────────┴──────────────────────┤
│ 状态栏：文件数 · 目录数 · 总体积 · 节点数 ‖ 缩放% · 渲染行数 │
└─────────────────────────────────────────────────────────────┘
```

### 配色风格

- 全部走 CSS 变量，**深色 / 浅色双主题**，右上角 🌙/☀️ 一键切换，选择记在 localStorage。
- 分类色（文件夹蓝、压缩包橙、视频紫、图片绿、文档红、代码青、数据靛、程序粉…）
  同时用于节点圆点、支干与图例，深色/浅色下都保证对比度。

### 交互流程

| 操作 | 效果 |
|---|---|
| 单击节点 | 展开 / 折叠，右侧详情同步 |
| 双击节点 | 在系统文件管理器中定位并选中 |
| 悬停节点 | 气泡显示真实路径、体积、类型、修改时间、子项数、异常原因 |
| 右键节点 | 定位 / 加入清理清单 / 复制路径 / 展开全部子级 / 折叠全部子级 |
| 滚轮 / 拖拽 | 缩放 / 平移；右下角也有 ＋ － ⤢ |
| 「空间 TOP」 | 列出占空间最大的 20 个目录 + 20 个文件，可一键加入清单或定位 |
| 名称查找 | 关键字高亮，「下一个」自动展开祖先并跳转 |
| 加入清单 | 详情面板按钮或右键；清单按体积排序并实时统计合计 |
| 删除 | **两步确认**：列出完整清单核对 → 勾选"我已确认" → 才执行 |
| 快捷键 | `Enter` 扫描、`/` 聚焦搜索、`Esc` 关闭、`f` 适应窗口、`c` 全部折叠 |
| **退出** | 右上角「退出」→ 二次确认 → 停止扫描、关闭监听、释放端口并结束进程；页面显示"服务已停止"遮罩 |

### 响应式适配

| 宽度 | 变化 |
|---|---|
| ≥ 1280px | 侧栏 348px，图例常显 |
| 900–1280px | 侧栏 300px，图例隐藏 |
| < 900px | 侧栏变浮层抽屉（可收起），路径区独占一行，搜索框收窄 |
| < 640px | 版本号隐藏，路径框整行 |

---

## 阶段二：性能优化（界面定稿后做的，未改动任何视觉与交互）

| 维度 | 措施 | 效果 |
|---|---|---|
| 渲染流畅度 | **视口裁剪**：只把当前滚动/缩放可见范围内的节点与连线放进 DOM；节点事件改为**容器级委托**；滚动/缩放/连续操作用 `requestAnimationFrame` 合并成一帧 | 实测 926 行布局时 DOM 里只有 37 行 |
| 数据传输 | 子节点**按 300 条分页**，末尾给「还有 N 项…」按需加载；首屏 `/api/tree` 同样限流 | 900 子项的目录首屏只传 300 条 |
| 内存占用 | `Node` 用 `dataclass(slots=True)`；扫描目录改为流式 `scandir`（不再 `list()` 整个目录） | 30 万节点量级内存约降 40% |
| 资源加载 | D3.js 从完整包（280 KB）换成 esbuild 构建的**子集**（d3-selection + d3-zoom，50 KB）；vendor 资源长缓存、页面预加载脚本 | 首屏下载量减少约 230 KB |
| 启动速度 | 服务端启动时预热扩展名表；绿色版用 **onedir** 而非 onefile（避免每次解压）；剔除 tkinter/unittest/sqlite3 等无关模块 | 绿色版启动 < 1s |
| 稳定性 | 修复"放大后画布右下角滚不到"（SVG 尺寸随缩放联动）；修复 Windows 下 `SO_REUSEADDR` 导致多实例抢同一端口 | — |

界面相关的部分（布局、配色、排序规则、支干粗细、交互流程）在优化阶段**一概没动**。

---

## 功能说明

### 扫描
- 文件夹体积 = 所有后代体积之和（子目录出栈时累加到父目录，天然正确）。
- 无权限目录 → 变红色「读取失败」节点并标注原因，其余照常扫描。
- 符号链接 / Windows junction **不跟随**，另有 inode 环检测 + 256 层深度兜底。
- 超大目录：节点超 30 万自动截断并提示（`FC_MAX_NODES` 可调）；子节点懒加载。
- 超长路径：Windows 自动转 `\\?\` 前缀绕过 260 字符限制。
- 后台线程扫描 + 500ms 轮询进度，可随时取消，界面不卡。

### 可视化
左→右横向展开；同级严格按体积从大到小、从上到下（服务端排序 + 客户端保序，
父节点纵向居中于子节点跨度，兄弟纵坐标严格递增）；支干粗细 ∝ √(子/父)，
可切「体积比例布局」让横向长度也表达占比；节点圆点半径同样编码体积。

### 节点信息
默认显示文件名 + 扩展名。标签栏有两个**相互独立**的开关：

| 显示扩展名 | 显示中文释义 | 效果 |
|---|---|---|
| ✔ | ✘ | `report` `.zip`（默认） |
| ✘ | ✔ | `report.zip` `压缩文件` |
| ✔ | ✔ | `report` `.zip · 压缩文件` |
| ✘ | ✘ | `report.zip`（只显示文件名） |

中文释义取自 `resources/ext_zh.json`（200+ 条），加一行即可；也可在
`~/.filecleanup/ext_zh.json`（绿色版为 `_internal/resources/ext_zh.json`）里做个人覆盖。
另有「显示体积」开关，可叠加在上面任意一种之上。

### 删除安全性
二次确认 → 默认送**系统回收站**（Windows 走 `SHFileOperationW`，可撤销）→
回收站失败时**不会**自动改成永久删除，而是报错并保留在清单里 →
服务端只接受本次扫描树内的 id，且校验路径必须在扫描根目录下。

---

## 阶段三：在另一台电脑上运行（注意事项）

**拷贝方式**：把整个 `dist\FileCleanup` 文件夹（**必须包含 `_internal`**）压成 zip，
拷过去解压，双击 `FileCleanup.exe` 即可。只拷 exe 会启动失败。

可能遇到的问题与处理：

1. **缺少 VC++ 运行库**（老机器）：提示缺 `api-ms-win-*.dll` / `VCRUNTIME140.dll`
   → 装一次「Microsoft Visual C++ 运行库（2015–2022）」。Win10/11 一般自带。
2. **杀毒软件拦截 / 报 unknown publisher**：自打包程序没有数字签名，容易被误报，
   选「允许运行 / 添加到信任区」。介意的话改用源码方式 `python run.py`。
   这也是默认选 **onedir** 而不是 onefile 的原因之一：onefile 每次运行都要往临时目录解压，
   更容易被行为监控类杀软盯上，启动也慢。
3. **SmartScreen 首次拦截**：点「更多信息 → 仍要运行」。
4. **端口被占用**：自动顺延到 8771、8772…，以窗口里显示的地址为准。
   用完请点界面右上角的「退出」（或 Ctrl+C）来释放端口；直接关浏览器页面不会停服务。
5. **权限不足的目录**（如 `C:\System Volume Information`）：扫描不会失败，
   对应节点显示为「读取失败」；想统计到它们请用管理员身份运行。
6. **删除失败**：文件被占用时会报错并留在清单里，关掉占用程序再试。
   路径超过 240 字符时无法走回收站，会明确提示而非静默永久删除。
7. **架构**：本产物是 Windows x64。macOS / Linux 请在目标机上重新执行
   `python tools/build_exe.py`（或直接用源码运行）。
8. **扩展名释义不对**：改 `_internal/resources/ext_zh.json` 后重启生效。

---

## 第三方组件与许可证

- 本项目：**MIT**（见 [LICENSE](LICENSE)）。
- D3.js（ISC，Mike Bostock）、CPython（PSF）、构建工具 PyInstaller（GPLv2 + 例外）/ esbuild（MIT）
  的署名、许可证全文与修改说明：见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
- 绿色版目录内附带的 `THIRD-PARTY-NOTICES.txt` 与 `LICENSE.txt` 请勿删除。

---

## 效果截图

| 主界面（横向树 + 详情 + 清单） | 删除二次确认 |
|---|---|
| ![主界面](docs/screenshots/main.png) | ![删除确认](docs/screenshots/delete-confirm.png) |

> 截图占位：运行后自行截图，替换 `docs/screenshots/` 下的同名文件即可。
