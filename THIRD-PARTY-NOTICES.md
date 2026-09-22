# THIRD-PARTY NOTICES · 第三方组件署名

本项目尊重并感谢所有上游作者的劳动。以下列出本项目使用 / 再分发的第三方组件、
原作者、来源与许可证。除明确说明外，**所有第三方代码均为原样使用，未做逻辑修改**。

---

## 1. D3.js（Data-Driven Documents）

| 项目 | 内容 |
|---|---|
| 名称 | D3.js（本项目只用到 `d3-selection` 与 `d3-zoom` 两个模块） |
| 原作者 | Mike Bostock（及 D3 全体贡献者） |
| 版本 | d3-selection 3.0.0、d3-zoom 3.0.0（依赖 d3-drag 3.0.0、d3-transition 3.0.1、d3-interpolate 3.0.1、d3-color / d3-dispatch / d3-ease / d3-timer） |
| 项目主页 | https://d3js.org/ |
| 源码仓库 | https://github.com/d3/d3 |
| 许可证 | **ISC License** |
| 本地位置 | `filecleanup/web/vendor/d3.slim.min.js`（约 50 KB） |
| 用途 | 目录树的 SVG 数据绑定（selection / join）与画布缩放平移（d3-zoom） |
| 修改说明 | **源码逻辑未修改**。为减小体积、加快首屏加载，用 esbuild 从官方 npm 包打包出"只含 d3-selection + d3-zoom"的子集（完整 d3.min.js 约 280 KB，子集约 50 KB）；文件头保留了版权与 ISC 声明，完整许可证全文见下 |

### ISC License 全文（源自 D3.js 分发文件）

```
Copyright 2010-2023 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
```

### 重新生成该子集

```bash
cd <任意临时目录>
npm install d3-selection d3-zoom esbuild
echo 'export * from "d3-selection"; export * from "d3-zoom";' > entry.js
npx esbuild entry.js --bundle --format=iife --global-name=d3 --minify --target=es2019 \
  --outfile=d3.slim.min.js
# 再把上面的文件头声明拼到文件最前面，然后覆盖 filecleanup/web/vendor/d3.slim.min.js
```

---

## 2. CPython（运行时）

| 项目 | 内容 |
|---|---|
| 名称 | CPython 及标准库（`os`、`stat`、`threading`、`http.server`、`socketserver`、`ctypes`、`json`、`heapq`、`unittest` 等） |
| 原作者 | Python Software Foundation 及 CPython 贡献者 |
| 主页 | https://www.python.org/ |
| 许可证 | **PSF License Agreement**（开源，允许商用与再分发） |
| 使用方式 | 源码运行时依赖用户本机 Python；**绿色版（dist/FileCleanup）内嵌了一份 CPython**，属于再分发，遵循 PSF 许可证 |
| 修改说明 | 未修改 |

---

## 3. 构建期工具（不随产物分发源码，仅用于生成产物）

| 工具 | 许可证 | 说明 |
|---|---|---|
| PyInstaller | **GPL v2 + 例外条款**（该例外允许用 PyInstaller 打包任意许可证的应用并分发产物；部分组件为 BSD/MIT） | 仅用于把本项目打包成免安装绿色版。产物中的 bootloader 受上述例外条款约束，本项目源码（MIT）不受影响。未修改 PyInstaller 源码 |
| esbuild | **MIT License** | 仅用于生成第 1 节所述的 D3 子集文件。未修改其源码 |

---

## 4. 操作系统接口（非第三方开源组件）

- Windows Shell API `SHFileOperationW`（`shell32.dll`）：通过 Python `ctypes` 直接调用，用于把文件送入系统回收站；仅调用，未分发其实现。
- `explorer.exe /select,`、`open -R`、`xdg-open`：定位文件时调用的系统命令，同上。

---

## 5. 本项目自带内容（非第三方）

- `resources/ext_zh.json`：扩展名 → 中文释义映射表（200+ 条），本项目编写，MIT 许可证。
- `filecleanup/web/app.js`、`styles.css`、`index.html`、`filecleanup/*.py`：本项目原创代码，MIT 许可证。
- `docs/green/使用说明.txt`、`tools/build_exe.py`：本项目原创，MIT 许可证。

---

## 再分发清单

把本项目（或绿色版目录）分发给他人时，请一并保留：

1. 本文件（`THIRD-PARTY-NOTICES.md`，绿色版中为 `THIRD-PARTY-NOTICES.txt`）；
2. `LICENSE`（MIT，绿色版中为 `LICENSE.txt`）；
3. `filecleanup/web/vendor/d3.slim.min.js` 文件头的版权与 ISC 声明（**不要裁剪该文件头注释**）；
4. 若分发绿色版，还需保留内嵌 CPython 的 PSF 许可证声明（位于 `_internal/` 内的 Python 运行库文件中，不要删除）。
