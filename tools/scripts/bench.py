"""Performance baseline for the reading/scanning paths (开发框架.md 7.2).

Builds a disposable corpus (default 1,000 ordinary documents plus one long
document and one near the 8 MB limit), then records what the framework asks for:
cold scan, project search, rendering a typical document, and reading a long one.

    python scripts/bench.py [--docs 1000] [--long-mb 1] [--big-mb 7.5]

Nothing here touches a real workspace: the corpus lives in a temporary directory
and is deleted afterwards.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdreader import core  # noqa: E402

PARAGRAPH = (
    "## 小节 %d\n\n"
    "这是一段普通的正文，用于填充文档内容，包含中文、English words 和数字 12345。\n"
    "它带有 **粗体**、*斜体*、`行内代码` 以及 [链接](https://example.com)。\n\n"
    "- 列表项一\n- 列表项二\n  - 嵌套项\n\n"
    "> 一段引用文字。\n\n"
)


def repeat_to_size(block: str, target_bytes: int) -> str:
    """A document of roughly ``target_bytes`` built from whole blocks."""
    unit = max(1, len(block.encode("utf-8")))
    return block * max(1, int(target_bytes / unit))


def build_corpus(root: str, documents: int, long_mb: float, big_mb: float) -> dict:
    api = core.Workspace(root)
    pid = api.create_project("基線")["id"]
    pdir = api.require_project(pid)
    for index in range(documents):
        body = "".join(PARAGRAPH % part for part in range(4))
        api.create_doc(pdir, "文档-%04d" % index, content="# 文档 %04d\n\n%s" % (index, body))
    long_doc = api.create_doc(pdir, "长文", content="# 长文\n\n" + repeat_to_size(
        PARAGRAPH % 1, int(long_mb * 1024 * 1024)))
    big_doc = api.create_doc(pdir, "大文档", content="# 接近上限\n\n" + repeat_to_size(
        "填充内容 " * 8 + "\n", int(big_mb * 1024 * 1024)))
    return {"workspace": api, "pid": pid, "pdir": pdir, "long": long_doc["id"], "big": big_doc["id"]}


def timed(runs: int, action):
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        result = action()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MDReader performance baseline")
    parser.add_argument("--docs", type=int, default=1000)
    parser.add_argument("--long-mb", type=float, default=1.0)
    parser.add_argument("--big-mb", type=float, default=7.5)
    parser.add_argument("--keep", action="store_true", help="keep the corpus for inspection")
    args = parser.parse_args(argv)

    print("Python:", sys.version.split()[0], "| engine:", core.R.ENGINE_NAME)
    print("CPU count:", os.cpu_count())

    with tempfile.TemporaryDirectory(prefix="mdreader-bench-") as root:
        started = time.perf_counter()
        corpus = build_corpus(root, args.docs, args.long_mb, args.big_mb)
        ws, pid, pdir = corpus["workspace"], corpus["pid"], corpus["pdir"]
        print("corpus built in %.2fs (%d docs + 长文 + 大文档)"
              % (time.perf_counter() - started, args.docs))

        size = sum(os.path.getsize(os.path.join(pdir, name)) for name in os.listdir(pdir))
        print("corpus size: %.1f MB" % (size / 1024 / 1024))

        scan, docs = timed(3, lambda: ws.scan_docs(pdir))
        print("scan_docs        : %6.1f ms  (%d docs)" % (scan * 1000, len(docs)))

        search, hits = timed(3, lambda: ws.search(pdir, "普通"))
        print("search 常见词    : %6.1f ms  (%d hits)" % (search * 1000, len(hits)))

        miss, _ = timed(3, lambda: ws.search(pdir, "不存在的关键词zzz"))
        print("search 无结果    : %6.1f ms" % (miss * 1000))

        render, _ = timed(3, lambda: ws.render_doc(pdir, "文档-0000.md", pid=pid))
        print("render 普通文档  : %6.1f ms" % (render * 1000))

        long_render, _ = timed(2, lambda: ws.render_doc(pdir, corpus["long"], pid=pid))
        print("render %.0fMB 长文 : %6.1f ms" % (args.long_mb, long_render * 1000))

        big_render, _ = timed(1, lambda: ws.render_doc(pdir, corpus["big"], pid=pid))
        print("render %.1fMB 大文档: %6.1f ms" % (args.big_mb, big_render * 1000))

        export_start = time.perf_counter()
        ws.export_project_html(pid, os.path.join(root, "合集.html"))
        print("导出整本 %.0f 篇  : %6.1f ms" % (args.docs + 2, (time.perf_counter() - export_start) * 1000))

        if args.keep:
            print("corpus kept at:", pdir)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
