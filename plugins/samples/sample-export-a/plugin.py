# -*- coding: utf-8 -*-
"""样例导出插件 A（机制验证用；**不生成真正的 PDF 或 Word**）。

它把宿主给的不可变快照写成一个带固定文件头的临时产物，返回路径；由宿主校验
路径、扩展名、文件头与大小，再原子替换到用户选择的目标。
"""

import os
import time

MAGIC = b"SAMPA\x00"


def export(task):
    options = task.options
    if options.get("fail"):
        raise RuntimeError("样例导出插件按要求失败")
    if options.get("hang"):
        while True:
            time.sleep(0.5)
    if options.get("sleep"):
        time.sleep(float(options["sleep"]))
    task.progress("读取快照", 30)

    markdown = task.input.get("markdown") or ""
    extension = (task.format or {}).get("extension") or "samplea"
    target = task.path("out.%s" % extension)
    with open(target, "wb") as stream:
        stream.write(MAGIC)
        stream.write(("format=sample-a\nchars=%d\n" % len(markdown)).encode("utf-8"))
        stream.write(markdown.encode("utf-8"))
    task.progress("已生成产物", 80)

    if options.get("bad_magic"):
        with open(target, "wb") as stream:
            stream.write(b"not the declared format")
    if options.get("oversize"):
        with open(target, "wb") as stream:
            stream.write(MAGIC + b"x" * (2 * 1024 * 1024))
    result = {
        "kind": "export",
        "path": target,
        "warnings": list(options.get("warnings") or []),
    }
    if options.get("escape"):
        outside = os.path.join(os.path.dirname(task.workdir), "escape.%s" % extension)
        with open(outside, "wb") as stream:
            stream.write(MAGIC + b"outside")
        result["path"] = outside
    if options.get("no_output"):
        return {"kind": "export", "path": "", "warnings": []}
    task.progress("完成", 100)
    return result
