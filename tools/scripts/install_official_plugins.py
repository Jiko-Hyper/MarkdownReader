"""Install and enable this release's three trusted local packages.

版本不同才安装，走真正的升级路径（旧版本留着，失败可以回退）；同版本重复安装会被插件机制拒绝。
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mdreader import core

WANTED = ("mdreader.image-insert", "mdreader.export-pdf", "mdreader.export-docx",
          "mdreader.document-convert")

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", default=core.default_workspace())
args = parser.parse_args()

ws = core.Workspace(args.workspace)
try:
    installed = {row["id"]: row for row in ws.plugins.list_plugins()}
    for package in sorted((ROOT / "plugins" / "packages").glob("*.zip")):
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        pid, version = manifest["id"], manifest["version"]
        if (installed.get(pid) or {}).get("version") == version:
            print("已是最新版本：", pid, version)
            continue
        result = ws.plugins.install(str(package))
        print("已安装：", pid, version, "（替换了上一版本）" if result.get("replaced") else "")
    for pid in WANTED:
        row = ws.plugins.enable(pid)
        print("已启用：", pid, row.get("version") or "")
    for command in ws.plugins.commands():
        if command["plugin"] in WANTED:
            print("命令：", command["plugin"], command["command"], command.get("extension") or "")
    print("Workspace:", args.workspace)
finally:
    ws.plugins.shutdown()
