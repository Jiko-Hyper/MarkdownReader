# -*- coding: utf-8 -*-
"""发布包功能核对：随包插件能不能装、能不能启用。

在**发布包目录**里用随包解释器运行：

    runtime\\python.exe tools\\smoke_release.py

「启用」这一步会真的拉起插件工作进程做一次导入握手，所以它同时验证了：

* 免安装运行时能跑起来（python.exe / tkinter / 插件依赖都在）；
* 工作进程能用同一个 exe（或同一个解释器）重新进入并按协议应答；
* 随包插件包的 SHA256 与受信清单一致，不会被拒绝。

源码目录里也可以直接跑（用本机 Python）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def manifest_id(package: Path) -> str:
    """插件包里的 manifest.json 才是权威 id（安装结果里的字段不一定是 id）。"""
    try:
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                if name.endswith("manifest.json"):
                    data = json.loads(archive.read(name).decode("utf-8"))
                    return str(data.get("id") or "")
    except Exception:
        pass
    return ""


def main() -> int:
    from mdreader import core

    workspace = Path(tempfile.mkdtemp(prefix="mdreader-release-"))
    failures: list[str] = []
    installed: list[str] = []
    try:
        ws = core.Workspace(str(workspace))
        packages = sorted((ROOT / "plugins" / "packages").glob("*.zip"))
        if not packages:
            print("找不到 plugins\\packages\\*.zip，跳过")
            return 0
        print("插件包: %s" % ", ".join(p.name for p in packages))
        print("Python : %s" % sys.version.split()[0])

        for package in packages:
            pid = manifest_id(package)
            try:
                info = ws.plugins.install(str(package))
                print("  安装 %-34s -> %s" % (package.name, info.get("version") or pid or "OK"))
            except Exception as exc:
                failures.append("安装 %s 失败: %s" % (package.name, exc))
                continue
            if not pid:
                failures.append("读不出 %s 的插件 id" % package.name)
                continue
            try:
                result = ws.plugins.enable(pid)
                enabled = bool(result.get("enabled")) if isinstance(result, dict) else True
                print("  启用 %-34s -> %s" % (pid, "OK" if enabled else result))
                if not enabled:
                    failures.append("启用 %s 失败: %s" % (pid, result))
                else:
                    installed.append(pid)
            except Exception as exc:
                failures.append("启用 %s 失败: %s" % (pid, exc))

        commands = [row.get("command") for row in (ws.plugins.commands() or [])]
        print("  可用命令: %s" % (", ".join(str(c) for c in commands) or "（无）"))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    if failures:
        print("")
        print("失败项:")
        for item in failures:
            print("  - %s" % item)
        return 1
    print("")
    print("发布包插件自检通过（%d 个插件已安装并启用）。" % len(installed))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
