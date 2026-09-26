# -*- coding: utf-8 -*-
"""样例图片插入插件（只用于验证插件机制，不是 F03 的正式实现）。

它做两件事：把用户选中的图片复制到任务工作目录，返回标准 Markdown 插入方案。
真正的落附件、撤销与预览由核心负责，插件不直接写用户目录。

选项（测试与验收用）：``alt`` 替代文字、``synthetic`` 生成一张小 PNG 而不读文件、
``fail`` 抛异常、``hang`` 一直挂着、``sleep`` 秒数、``escape`` 返回工作目录之外的
路径、``bad_magic`` 用文本冒充 PNG、``no_asset`` 不返回图片列表。
"""

import os
import shutil
import struct
import time
import zlib


def _png(width=2, height=2):
    """手工拼一张最小合法 PNG，让验收不需要额外的图片素材。"""
    raw = b"".join(b"\x00" + bytes([40, 90, 160]) * width for _ in range(height))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def insert_image(task):
    options = task.options
    if options.get("fail"):
        raise ValueError("样例插件按要求失败")
    if options.get("hang"):
        while True:
            time.sleep(0.5)
    if options.get("sleep"):
        time.sleep(float(options["sleep"]))
    task.progress("准备图片", 20)

    source = options.get("image") or ""
    name = os.path.basename(str(source)) or "image.png"
    if options.get("synthetic"):
        name = "synthetic.png"
        target = task.path("assets", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as stream:
            stream.write(_png())
    else:
        if not source or not os.path.isfile(source):
            raise ValueError("没有可插入的图片：%s" % source)
        target = task.path("assets", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        task.progress("已复制图片", 60)

    if options.get("bad_magic"):
        with open(target, "wb") as stream:
            stream.write(b"this is not a real image")

    alt = str(options.get("alt") or os.path.splitext(name)[0])
    result = {
        "kind": "image-insert",
        "markdown": "![%s](assets/%s)" % (alt, name),
        "assets": [] if options.get("no_asset") else [
            {"path": target, "name": name, "mime": "image/png"}
        ],
        "note": "样例插件：图片暂存在任务工作目录",
    }
    if options.get("escape"):
        outside = os.path.join(os.path.dirname(task.workdir), "outside-%s" % name)
        with open(outside, "wb") as stream:
            stream.write(_png())
        result["assets"] = [{"path": outside, "name": name, "mime": "image/png"}]
    task.progress("已生成插入方案", 100)
    return result
