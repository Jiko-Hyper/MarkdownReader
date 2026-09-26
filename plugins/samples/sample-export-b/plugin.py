# -*- coding: utf-8 -*-
"""样例导出插件 B（机制验证用；**不生成真正的 PDF 或 Word**）。"""

import os
import time

MAGIC = b"SAMPB"


def export(task):
    options = task.options
    if options.get("fail"):
        raise RuntimeError("样例导出插件 B 按要求失败")
    if options.get("hang"):
        while True:
            time.sleep(0.5)
    if options.get("sleep"):
        time.sleep(float(options["sleep"]))
    task.progress("读取快照", 40)
    markdown = task.input.get("markdown") or ""
    extension = (task.format or {}).get("extension") or "sampleb"
    target = task.path("out.%s" % extension)
    with open(target, "wb") as stream:
        stream.write(MAGIC)
        stream.write(("format=sample-b\nchars=%d\n" % len(markdown)).encode("utf-8"))
        stream.write(markdown.encode("utf-8"))
    if options.get("bad_magic"):
        with open(target, "wb") as stream:
            stream.write(b"nope")
    task.progress("完成", 100)
    return {"kind": "export", "path": target, "warnings": list(options.get("warnings") or [])}
