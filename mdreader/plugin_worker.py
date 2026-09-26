# -*- coding: utf-8 -*-
"""插件工作进程：宿主用独立进程执行插件代码，本文件是那个进程的入口。

约定（首版内部协议，不对外发布 SDK）：

1. 宿主把**一个 JSON 对象**写到本进程的标准输入，随后关闭输入。
2. 本进程在标准输出上写 JSON Lines，只有四种消息：
   ``{"type":"progress","stage":…,"percent":…}``、``{"type":"result","data":…}``、
   ``{"type":"error","code":…,"message":…}``、``{"type":"log","message":…}``。
3. 插件入口是一个普通模块，模块里要有清单 ``commands[].method`` 指定的函数，
   形如 ``def insert_image(task): ...``。``task`` 提供 ``input``、``options``、
   ``document``、``workdir``、``plugin``、``progress(stage, percent)`` 和
   ``path(*parts)``（自动限制在任务工作目录内）。
4. 插件只能往 ``task.workdir`` 里写临时产物并回报路径；宿主会重新校验路径、
   文件类型与大小，再由核心写入用户文档。插件拿不到任何界面对象或核心服务。

本进程**不是操作系统沙箱**：受信插件的代码仍有当前用户的系统权限。首版只执行
维护者发布并登记哈希的包，不把它当成运行不可信第三方代码的方案。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback

MAX_MESSAGE = 1024 * 1024


class TaskContext:
    """交给插件的唯一入口对象；不暴露宿主内部状态。"""

    def __init__(self, request: dict, emit):
        self.id = str(request.get("task") or "")
        self.api_version = str(request.get("api_version") or "")
        self.workdir = os.path.abspath(str(request.get("workdir") or "."))
        self.plugin = dict(request.get("plugin") or {})
        self.document = dict(request.get("document") or {})
        self.options = dict(request.get("options") or {})
        self.input = dict(request.get("input") or {})
        self.format = dict(request.get("format") or {})
        self.limits = dict(request.get("limits") or {})
        self._emit = emit

    def progress(self, stage: str = "", percent: "int | None" = None):
        message = {"type": "progress", "stage": str(stage)[:80]}
        if percent is not None:
            try:
                message["percent"] = max(0, min(100, int(percent)))
            except (TypeError, ValueError):
                pass
        self._emit(message)

    def log(self, message: str):
        self._emit({"type": "log", "message": str(message)[:200]})

    def path(self, *parts: str) -> str:
        """任务工作目录内的一个路径（越界直接抛错，插件绕不过去）。"""
        target = os.path.realpath(os.path.join(
            self.workdir, *[str(p).replace("\\", os.sep).replace("/", os.sep) for p in parts if p]))
        root = os.path.realpath(self.workdir)
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:          # 不同盘符
            inside = False
        if not inside:
            raise ValueError("插件只能使用任务工作目录内的文件：%s" % target)
        return target

    def ensure_dir(self, *parts: str) -> str:
        target = self.path(*parts)
        os.makedirs(target, exist_ok=True)
        return target


def load_entrypoint(folder: str, entrypoint: str):
    """按文件路径加载插件入口；只在插件自己目录里找模块。"""
    folder = os.path.abspath(folder)
    entry = os.path.join(folder, entrypoint.replace("\\", os.sep).replace("/", os.sep))
    real_entry = os.path.realpath(entry)
    try:
        inside = os.path.commonpath([os.path.realpath(folder), real_entry]) == os.path.realpath(folder)
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("入口文件越出插件目录")
    if not os.path.isfile(real_entry):
        raise FileNotFoundError("找不到插件入口：%s" % entrypoint)
    module_name = "mdreader_plugin_%s" % os.path.splitext(os.path.basename(real_entry))[0]
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(module_name, real_entry)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载插件入口：%s" % entrypoint)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    # 插件自己的 print 只能进标准错误：标准输出被宿主当成协议通道。
    protocol = sys.stdout
    sys.stdout = sys.stderr
    sys.dont_write_bytecode = True

    # 随程序分发的插件依赖（python-docx / reportlab / Pillow）要能在没装过
    # 这些包的机器上被 import；打包成 exe 时同样按 exe 所在目录找一次。
    vendor = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "plugins", "dependencies")
    if not os.path.isdir(vendor) and getattr(sys, "frozen", False):
        vendor = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                              "plugins", "dependencies")
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.insert(0, vendor)

    def emit(message: dict):
        try:
            text = json.dumps(message, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = json.dumps({"type": "log", "message": "无法序列化的消息"}, ensure_ascii=False)
        protocol.write(text + "\n")
        protocol.flush()

    raw = ""
    try:
        raw = sys.stdin.read() if sys.stdin is not None else ""
    except Exception:
        raw = ""
    try:
        request = json.loads(raw or "{}")
    except ValueError as exc:
        emit({"type": "error", "code": "bad-request", "message": "宿主请求不是 JSON：%s" % exc})
        return 2
    if not isinstance(request, dict):
        emit({"type": "error", "code": "bad-request", "message": "宿主请求必须是对象"})
        return 2

    folder = str((request.get("plugin") or {}).get("dir") or "")
    entrypoint = str(request.get("entrypoint") or "")
    method = str(request.get("method") or "")
    try:
        module = load_entrypoint(folder, entrypoint)
    except Exception as exc:
        emit({"type": "error", "code": type(exc).__name__,
              "message": "插件入口无法加载：%s" % exc})
        return 1

    if method == "__ping__":
        emit({"type": "result", "data": {"ok": True,
                                         "plugin": (request.get("plugin") or {}).get("id"),
                                         "version": (request.get("plugin") or {}).get("version"),
                                         "entry": os.path.basename(entrypoint)}})
        return 0

    handler = getattr(module, method, None)
    if not callable(handler):
        emit({"type": "error", "code": "no-handler",
              "message": "插件入口里没有清单声明的函数：%s" % method})
        return 1

    context = TaskContext(request, emit)
    try:
        result = handler(context)
    except Exception as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        emit({"type": "error", "code": type(exc).__name__, "message": detail[:400]})
        return 1
    try:
        encoded = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        emit({"type": "error", "code": "bad-result",
              "message": "插件结果无法序列化：%s" % exc})
        return 1
    if len(encoded) > MAX_MESSAGE:
        emit({"type": "error", "code": "result-too-large",
              "message": "插件结果过大（超过 %d 字节）" % MAX_MESSAGE})
        return 1
    emit({"type": "result", "data": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
