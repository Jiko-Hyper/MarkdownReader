# -*- coding: utf-8 -*-
"""测试包的公共准备。

``unittest discover`` 会先导入本包，所以这里设置的临时目录对所有测试生效。

为什么需要它：``tempfile`` 默认把测试数据放进用户的 ``%TEMP%``。有些环境
（受限沙箱、被策略锁定或只读的配置文件临时目录）会拒绝对那里的写入，
于是测试会因为「临时目录不可写」而失败，看起来像功能坏了。
统一改用项目内、可写的临时根目录，测试结果只反映代码本身。

``MDREADER_TEST_HOME`` 如已设置，就作为临时根目录的父目录；本模块只创建
自己的子目录，不会清空该目录。
"""

from __future__ import annotations

import os
import shutil
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST_HOME = os.environ.get("MDREADER_TEST_HOME") or os.path.join(_PROJECT_ROOT, ".testtmp")
_TEST_ROOT = os.path.join(_TEST_HOME, "python")

os.makedirs(_TEST_ROOT, exist_ok=True)
# 让 TemporaryDirectory / mkstemp / NamedTemporaryFile 都落在项目内
tempfile.tempdir = _TEST_ROOT

__all__ = ["_TEST_ROOT"]


def cleanup() -> None:
    """删除本项目创建的临时目录（只删自己这一层）。"""
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
