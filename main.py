# -*- coding: utf-8 -*-
"""
MDReader — 把 Markdown 变成好读的排版，并把散落的 .md 文件收进项目里管理。

用法:
    MDReader.exe                    双击即可（桌面快捷方式指向这里）
    python main.py --browser        只用系统浏览器，不开内嵌窗口
    python main.py --workspace DIR  指定工作区目录
    python main.py --port 8642      固定端口
    python main.py --selftest       自检：窗口能否创建并渲染
    python main.py --console        保留控制台输出（排错用；exe 外壳也认这个开关）
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 免安装版把 Tcl/Tk 放在 runtime\tcl 下，要让 tkinter 知道去哪找；
# 源码运行时这里什么也不做。
from mdreader.appicon import ensure_tcl_environment

ensure_tcl_environment()

from mdreader.taskbar import set_process_identity
set_process_identity()

from mdreader.display import enable_high_dpi

enable_high_dpi()

from mdreader import launcher  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(launcher.main())
