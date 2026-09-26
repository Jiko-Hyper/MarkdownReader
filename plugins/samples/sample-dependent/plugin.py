# -*- coding: utf-8 -*-
"""样例“依赖别的插件”的导出插件（机制验证用）。"""

import time

MAGIC = b"SAMPD"


def export(task):
    options = task.options
    if options.get("hang"):
        while True:
            time.sleep(0.5)
    extension = (task.format or {}).get("extension") or "sampledep"
    target = task.path("out.%s" % extension)
    with open(target, "wb") as stream:
        stream.write(MAGIC + b"depends on sample-export-b\n")
    return {"kind": "export", "path": target, "warnings": []}
