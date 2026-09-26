# -*- coding: utf-8 -*-
"""把插件源码目录打成可分发的插件包（开发者工具）。

    python scripts/build_plugin.py plugins/samples/sample-image -o dist/plugins

打包是确定性的：成员按名字排序、时间戳固定，所以同一份源码总得到同一个
SHA256；受信清单 `mdreader/plugin_trust.json` 里登记的正是这个值。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mdreader import plugins as P  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打包一个插件目录")
    parser.add_argument("source", help="插件源码目录（根下有 manifest.json）")
    parser.add_argument("-o", "--out", default="", help="输出目录或 .zip 路径")
    parser.add_argument("--name", default="", help="输出文件名（默认 <id>-<版本>.mdplugin.zip）")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    manifest = P.validate_manifest(__import__("json").loads(
        (source / P.MANIFEST_NAME).read_text(encoding="utf-8")))
    out = Path(args.out).resolve() if args.out else source.parent
    if out.suffix.lower() == ".zip":
        target = out
    else:
        name = args.name or "%s-%s.mdplugin.zip" % (manifest["id"], manifest["version"])
        target = out / name
    P.build_package(str(source), str(target))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print("插件包：%s" % target)
    print("id     ：%s" % manifest["id"])
    print("版本   ：%s" % manifest["version"])
    print("SHA256 ：%s" % digest)
    print()
    print("要让它可安装，把上面这行哈希登记进 mdreader/plugin_trust.json：")
    print('  "%s": {"versions": {"%s": "%s"}, "publisher": "MDReader"}'
          % (manifest["id"], manifest["version"], digest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
