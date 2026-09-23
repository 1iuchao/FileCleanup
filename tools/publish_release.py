"""把 dist 下的绿色版压缩包发布成 GitHub Release（含附件上传）。

用法：

    python tools/publish_release.py                       # 自动找 dist/*.zip，tag 取文件名里的版本
    python tools/publish_release.py --tag v1.1.0
    python tools/publish_release.py --repo other/project

依赖：本机 Bash 里的 git（取凭据管理器里已存的 GitHub 令牌）+ curl.exe。
本机代理会拦 TLS 校验，所以 curl 统一带 -k，git 操作不带（不推送，只取凭据）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "1iuchao/FileCleanup"


def github_token() -> tuple[str, str]:
    """从 Git Credential Manager 取出 github.com 的账号与令牌。"""
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True,
    ).stdout
    user = token = ""
    for line in out.splitlines():
        if line.startswith("username="):
            user = line[len("username="):]
        elif line.startswith("password="):
            token = line[len("password="):]
    if not (user and token):
        sys.exit("取不到 GitHub 凭据：请先在 Git Credential Manager 里登录 github.com")
    return user, token


def curl(user: str, token: str, args: list[str]) -> dict:
    cmd = ["curl.exe", "-k", "-s", "-m", "300", "-u", f"{user}:{token}"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(res.stdout)
    except Exception:
        sys.exit(f"GitHub 返回异常：{res.stdout[:300]}{res.stderr[:300]}")


def build_body(zip_path: Path, sha: str, notes: str = "") -> str:
    size_mb = zip_path.stat().st_size / 1048576
    extra = f"## 本次更新\n\n{notes}\n\n" if notes else ""
    return f"""## FileCleanup {zip_path.stem.split('-')[1]} · Windows 绿色版

{extra}**免安装、不需要 Python**：下载解压后双击 `FileCleanup.exe`，浏览器会自动打开 `http://127.0.0.1:8770`。

### 能干什么

- 递归扫描任意目录，文件夹体积 = 其所有子项体积之和
- **左 → 右横向树状图**：同一层级严格按体积从大到小、从上到下排列，支干粗细表示占比
- 单击展开/折叠、双击在资源管理器中定位、右键加入清理清单、悬停看真实路径 / 体积 / 修改时间
- 「空间 TOP」直接列出占地方最大的目录和文件
- 扩展名可切换中文释义（`.zip` → 压缩文件），也可只显示扩展名或两者同显；映射表可自行补充
- 加入待清理清单后需**两步确认**才删除，默认送系统回收站
- 深色 / 浅色双主题、响应式布局、滚轮缩放与拖拽平移

### 怎么用

1. 解压（**整个 `FileCleanup` 目录一起解压，`_internal` 不能丢**，只拷 exe 会启动失败）
2. 双击 `FileCleanup.exe`
3. 上方填目录（或用「快速选择」挑盘符 / 下载目录）→ 开始扫描
4. 用完点右上角「退出」，服务关闭、端口释放

### 注意事项

- 需要 **Windows 10 及以上、64 位**；老机器若提示缺 `VCRUNTIME140.dll`，装一次 Microsoft Visual C++ 运行库（2015–2022）
- 程序没有数字签名，杀毒软件 / SmartScreen 可能拦截 → 选「允许运行 / 仍要运行」
- 只监听 `127.0.0.1`，不联网、不上传任何数据
- 端口被占用时自动顺延到 8771、8772…，以窗口里显示的地址为准

### 文件信息

| 项 | 值 |
| --- | --- |
| 压缩包 | {size_mb:.1f} MB |
| SHA256 | `{sha}` |

源码与文档见本仓库；第三方组件（D3.js，ISC 许可证）的署名与许可证全文见 `THIRD-PARTY-NOTICES.md`。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="发布 GitHub Release 并上传绿色版压缩包")
    ap.add_argument("--zip", help="zip 路径，默认取 dist 下最新的一个")
    ap.add_argument("--tag", help="tag 名，默认从文件名推断，如 v1.0.0")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo")
    ap.add_argument("--notes", help="本次更新说明，支持 \\n 换行，追加到正文「本次更新」小节")
    args = ap.parse_args()

    if args.zip:
        zip_path = Path(args.zip)
    else:
        candidates = sorted((ROOT / "dist").glob("*.zip"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            sys.exit("dist 下没有 zip，请先 python tools/build_exe.py 并打包")
        zip_path = candidates[-1]

    tag = args.tag
    if not tag:
        m = re.search(r"-v(\d+\.\d+\.\d+)-", zip_path.name)
        tag = f"v{m.group(1)}" if m else sys.exit("无法从文件名推断 tag，请用 --tag 指定")

    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    user, token = github_token()

    payload = {
        "tag_name": tag,
        "name": f"FileCleanup {tag} · Windows 绿色版",
        "body": build_body(zip_path, sha, (args.notes or "").replace("\\n", "\n")),
        "draft": False,
        "prerelease": False,
        "target_commitish": "main",
    }
    tmp = ROOT / ".release-payload.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rel = curl(user, token, [
        "-X", "POST", "-H", "Accept: application/vnd.github+json",
        "-d", f"@{tmp}", f"https://api.github.com/repos/{args.repo}/releases",
    ])
    tmp.unlink(missing_ok=True)

    if "id" not in rel:
        print("创建 Release 失败：", rel.get("message"), rel.get("errors"))
        return 1
    print("Release:", rel["html_url"])

    asset = curl(user, token, [
        "-X", "POST", "-H", "Accept: application/vnd.github+json",
        "-H", "Content-Type: application/zip",
        "--data-binary", f"@{zip_path}",
        f"https://uploads.github.com/repos/{args.repo}/releases/{rel['id']}/assets?name={zip_path.name}",
    ])
    if "id" not in asset:
        print("上传附件失败：", asset.get("message"), asset.get("errors"))
        return 1
    print("附件:", asset["browser_download_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
