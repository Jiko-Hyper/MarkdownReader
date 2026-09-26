# -*- coding: utf-8 -*-
"""插件任务的宿主侧：独立工作进程、结构化协议、取消与超时、结果复核。

任务书 §2.3 的几条硬要求都落在这里：

* 每次调用都在**独立的工作进程**里执行插件代码（宿主不向插件传 Tk 控件、
  网页 DOM、可变文档对象或任意核心服务对象），一个任务超时、异常退出或
  返回坏结果只结束该任务，宿主保持可用。
* 任务绑定“文档身份 + 缓冲修订 + 调用入口”。提交结果前再比一次，用户
  换了标签、改了正文或关了文档就一律丢弃（:class:`~mdreader.plugins.StaleResult`）。
* 结果路径、文件类型、大小都在宿主侧重新校验，工作目录之外的产物直接拒绝。
* 禁用插件会立刻取消它正在跑的任务，被取消的结果永远不会写入文档。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque

from . import plugins as P
from .storage import is_within, safe_join

#: 单次任务的默认超时（秒）。导出类插件可能慢，但绝不允许无限等待。
DEFAULT_TIMEOUT = 60.0
#: 启用插件时的握手超时：只验证入口能不能导入，不执行插件逻辑。
HANDSHAKE_TIMEOUT = 20.0
#: 工作进程标准错误保留的行数（只用于报错，不默认收集正文）。
LOG_LINES = 40

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")


def _safe_name(name: str, fallback: str = "file") -> str:
    text = os.path.basename(str(name or "").replace("\\", "/")).strip()
    text = _SAFE_NAME.sub("_", text).strip("._")
    return text[:80] or fallback


def _unique_target(folder: str, name: str) -> str:
    """在 ``folder`` 里挑一个还没被占用的文件名（绝不覆盖已有文件）。"""
    stem, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, "%s-%d%s" % (stem, index, ext))
        index += 1
        if index > 1000:
            raise P.TaskError("目标目录里同名文件太多：%s" % name)
    return candidate


class PluginTask:
    """一次插件调用的全部状态；只有 :class:`TaskManager` 改它。"""

    def __init__(self, task_id, command, *, doc="", revision="", entry="", workdir=""):
        self.id = task_id
        self.command = dict(command)
        self.plugin = command.get("plugin", "")
        self.capability = command.get("capability", "")
        self.method = command.get("method", "")
        self.doc = doc
        self.revision = revision
        self.entry = entry
        self.workdir = workdir
        self.state = "queued"
        self.stage = ""
        self.percent = 0
        self.result = None
        self.checked = None
        self.error = ""
        self.error_code = ""
        self.cancel_reason = ""
        self.log = []
        self.returncode = None
        self.created = time.time()
        self.finished = None
        self.process = None
        self.cancel_requested = False
        self.timed_out = False
        self.timeout = DEFAULT_TIMEOUT
        self.snapshot = ""
        self.markdown = ""
        self.options: dict = {}
        self.done = threading.Event()
        self._lock = threading.Lock()

    def set(self, state: str, **kwargs):
        with self._lock:
            self.state = state
            for key, value in kwargs.items():
                setattr(self, key, value)
            if state in ("done", "failed", "cancelled", "timeout"):
                self.finished = time.time()
                self.done.set()

    def view(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "plugin": self.plugin, "command": self.command.get("command", ""),
                "capability": self.capability, "method": self.method, "state": self.state,
                "stage": self.stage, "percent": self.percent, "error": self.error,
                "error_code": self.error_code, "cancel_reason": self.cancel_reason,
                "doc": self.doc, "revision": self.revision, "entry": self.entry,
                "created": self.created, "finished": self.finished,
                "returncode": self.returncode,
                "has_result": self.result is not None,
                "log": list(self.log[-6:]),
            }


class TaskManager:
    """任务注册表 + 工作进程管理。线程安全，一个实例服务一个 :class:`PluginStore`。"""

    def __init__(self, store: P.PluginStore, *, python: str = "", task_timeout: float = 0.0,
                 worker_dir: str = ""):
        self.store = store
        self.python = python or sys.executable or "python"
        self.timeout = float(task_timeout or DEFAULT_TIMEOUT)
        if worker_dir:
            self.worker_dir = worker_dir
        else:
            # 源码运行时是仓库根目录；打包成 exe 后是 exe 所在目录。
            from .core import app_dir as _app_dir
            try:
                self.worker_dir = _app_dir()
            except Exception:
                self.worker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._lock = threading.RLock()
        self._tasks: "dict[str, PluginTask]" = {}

    # -- 提交 -------------------------------------------------------------
    def submit(self, command_name: str, *, options=None, doc: str = "", revision: str = "",
               entry: str = "", markdown: "str | None" = None,
               input_files=None, timeout: "float | None" = None, wait: float = 0.0) -> PluginTask:
        command = self.store.find_command(command_name)
        task_id = "t-%d-%s" % (time.time_ns(), uuid.uuid4().hex[:6])
        workdir = os.path.join(self.store.tasks_dir, task_id)
        os.makedirs(workdir, exist_ok=True)
        task = PluginTask(task_id, command, doc=doc, revision=revision, entry=entry,
                          workdir=workdir)
        task.timeout = float(timeout or self.timeout)
        payload = dict(options or {})
        if input_files:
            folder = os.path.join(workdir, "in")
            os.makedirs(folder, exist_ok=True)
            saved = []
            for item in input_files:
                if isinstance(item, dict):
                    name, data = item.get("name"), item.get("data")
                else:
                    name, data = os.path.basename(str(item)), None
                if isinstance(data, (bytes, bytearray)):
                    target = _unique_target(folder, _safe_name(name))
                    with open(target, "wb") as stream:
                        stream.write(bytes(data))
                else:
                    source = str(item)
                    if not os.path.isfile(source):
                        raise P.TaskError("找不到要交给插件的文件：%s" % source)
                    target = _unique_target(folder, _safe_name(os.path.basename(source)))
                    shutil.copyfile(source, target)
                saved.append(target)
            payload.setdefault("files", saved)
            if command["capability"] == P.CAP_IMAGE_INSERT and saved:
                payload.setdefault("image", saved[0])
        if markdown is not None:
            snapshot = os.path.join(workdir, "snapshot.md")
            with open(snapshot, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(markdown)
            task.snapshot = snapshot
            task.markdown = markdown
        else:
            task.snapshot = ""
            task.markdown = ""
        task.options = payload
        with self._lock:
            self._tasks[task_id] = task
        thread = threading.Thread(target=self._run, args=(task,), name="plugin-%s" % task_id,
                                  daemon=True)
        thread.start()
        if wait:
            task.done.wait(max(0.05, float(wait)))
        return task

    # -- 工作进程 ---------------------------------------------------------
    def _worker_command(self) -> list:
        """起工作进程的命令行。

        一律走 `main.py --plugin-worker`，而不是 `-m mdreader.plugin_worker`：
        免安装版用的是 Python embeddable，它的 `python._pth` 只把 runtime 目录放进
        sys.path，`-m` 找不到仓库里的 mdreader 包；`main.py` 会自己把程序目录
        加到 sys.path 上，源码运行与打包运行因此是同一条路径。
        """
        entry = os.path.join(self.worker_dir, "main.py")
        if os.path.isfile(entry):
            return [self.python, entry, "--plugin-worker"]
        return [self.python, "-m", "mdreader.plugin_worker"]

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        existing = env.get("PYTHONPATH") or ""
        env["PYTHONPATH"] = self.worker_dir + (os.pathsep + existing if existing else "")
        # 随程序分发的插件依赖（python-docx / reportlab / Pillow）优先于同名全局包
        from .core import app_dir as _app_dir
        vendor = os.path.join(_app_dir(), "plugins", "dependencies")

        if os.path.isdir(vendor):
            env["PYTHONPATH"] = vendor + os.pathsep + env["PYTHONPATH"]
        return env

    def _request(self, task: PluginTask) -> dict:
        manifest = {}
        info = self.store.entry(task.plugin) or {}
        folder = info.get("path") or ""
        try:
            manifest = self.store.read_manifest(folder)
        except P.ManifestError:
            manifest = {}
        return {
            "api_version": P.PLUGIN_API_VERSION,
            "task": task.id,
            "plugin": {"id": task.plugin, "version": info.get("version", ""), "dir": folder},
            "entrypoint": manifest.get("entrypoint", ""),
            "capability": task.capability,
            "method": task.method,
            "format": {k: task.command.get(k) for k in
                       ("format", "extension", "media_type", "magic", "max_bytes")},
            "workdir": task.workdir,
            "snapshot": getattr(task, "snapshot", ""),
            "document": {"identity": task.doc, "revision": task.revision, "entry": task.entry},
            "options": task.options,
            "input": {"markdown": task.markdown, "snapshot": task.snapshot},
            "limits": {"max_artifact_bytes": P.MAX_ARTIFACT_BYTES},
        }

    def _spawn(self, request: dict):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            self._worker_command(),
            cwd=self.worker_dir, env=self._env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", creationflags=flags,
        )
        try:
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
        return proc

    def _drain(self, task: PluginTask, stream):
        try:
            for line in stream:
                text = line.rstrip("\r\n")
                if text:
                    task.log.append(text[:400])
                    if len(task.log) > LOG_LINES:
                        del task.log[0]
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _kill(self, task: PluginTask, why: str, *, timeout: bool = False):
        if timeout:
            task.timed_out = True
        else:
            task.cancel_requested = True
            task.cancel_reason = why
        proc = task.process
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def _run(self, task: PluginTask):
        request = self._request(task)
        task.set("running", stage="启动插件工作进程")
        try:
            proc = self._spawn(request)
        except Exception as exc:
            task.set("failed", error="无法启动插件工作进程：%s" % exc, error_code="spawn")
            return
        task.process = proc
        drain = threading.Thread(target=self._drain, args=(task, proc.stderr), daemon=True)
        drain.start()
        timer = threading.Timer(task.timeout, self._kill, args=(task, "任务超时"),
                                kwargs={"timeout": True})
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    task.log.append("非协议输出：" + text[:200])
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "progress":
                    task.stage = str(message.get("stage") or "")[:80]
                    try:
                        task.percent = max(0, min(100, int(message.get("percent") or 0)))
                    except (TypeError, ValueError):
                        pass
                elif kind == "result":
                    task.result = message.get("data")
                elif kind == "error":
                    task.error = str(message.get("message") or "插件报告了错误")[:400]
                    task.error_code = str(message.get("code") or "plugin")[:40]
                elif kind == "log":
                    task.log.append(str(message.get("message") or "")[:200])
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
        timer.cancel()
        drain.join(timeout=2)
        try:
            task.returncode = proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
                task.returncode = proc.wait(timeout=5)
            except Exception:
                task.returncode = None
        task.process = None

        if task.timed_out:
            task.set("timeout", error="插件任务超时（%.0f 秒），已结束该任务" % task.timeout)
            return
        if task.cancel_requested:
            task.set("cancelled", error=task.cancel_reason or "任务已取消")
            return
        if task.error:
            task.set("failed", error=task.error, error_code=task.error_code or "plugin")
            return
        if task.result is None:
            tail = task.log[-1] if task.log else ""
            task.set("failed", error_code="no-result",
                     error="插件没有返回结果（退出码 %s）%s"
                           % (task.returncode, ("：" + tail) if tail else ""))
            return
        try:
            task.checked = self._check(task)
        except P.TaskError as exc:
            task.set("failed", error_code="bad-result", error=str(exc))
            return
        task.set("done", stage="完成", percent=100)

    def _check(self, task: PluginTask) -> dict:
        command = task.command
        if task.capability == P.CAP_IMAGE_INSERT:
            return P.check_image_plan(task.result, task.workdir)
        if task.capability == P.CAP_EXPORT:
            return P.check_export_artifact(task.result, task.workdir, command)
        if task.capability == P.CAP_CONVERT:
            checked = P.check_export_artifact(task.result, task.workdir, command)
            assets = task.result.get("assets") or []
            if not isinstance(assets, list) or len(assets) > 500:
                raise P.TaskError("转换附件数量无效或超过 500 张")
            checked["assets"] = [P.check_image_asset(path, task.workdir) for path in assets]
            return checked
        raise P.TaskError("不支持的能力：%s" % task.capability)

    # -- 查询 -------------------------------------------------------------
    def get(self, task_id: str) -> PluginTask:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
        if task is None:
            raise P.TaskError("找不到这个插件任务：%s" % task_id)
        return task

    def poll(self, task_id: str, *, wait: float = 0.0) -> dict:
        task = self.get(task_id)
        if wait:
            task.done.wait(max(0.0, float(wait)))
        view = task.view()
        if task.state == "done" and task.checked is not None:
            view["result"] = task.checked
        return view

    def active(self) -> list:
        with self._lock:
            tasks = list(self._tasks.values())
        return [t.view() for t in tasks if t.state in ("queued", "running")]

    def result(self, task_id: str, *, now_doc: "str | None" = None,
               now_revision: "str | None" = None) -> dict:
        """取回校验过的结果；任务没成功、被取消，或调用方已经不是同一份文档就拒绝。"""
        task = self.get(task_id)
        if task.state == "cancelled":
            raise P.TaskError("任务已取消，结果不会写入：%s" % (task.cancel_reason or task.error))
        if task.state == "timeout":
            raise P.TaskError("任务已超时，结果不会写入")
        if task.state != "done" or task.checked is None:
            raise P.TaskError("任务还没有成功完成（当前：%s）%s"
                              % (task.state, ("：" + task.error) if task.error else ""))
        if now_doc is not None and str(now_doc) != str(task.doc):
            raise P.StaleResult("结果属于另一份文档（%s），已丢弃" % os.path.basename(task.doc or ""))
        if now_revision is not None and str(now_revision) != str(task.revision):
            raise P.StaleResult("文档在插件运行期间被修改过，已丢弃这次结果，请重新发起")
        return task.checked

    # -- 取消与清理 -------------------------------------------------------
    def cancel(self, task_id: str, reason: str = "用户取消") -> dict:
        task = self.get(task_id)
        if task.state in ("done", "failed", "cancelled", "timeout"):
            return task.view()
        self._kill(task, reason)
        task.done.wait(timeout=10)
        if task.state in ("queued", "running"):
            task.set("cancelled", error=reason)
        return task.view()

    def cancel_plugin(self, pid: str, reason: str = "插件不可用") -> int:
        with self._lock:
            tasks = [t for t in self._tasks.values()
                     if t.plugin == pid and t.state in ("queued", "running")]
        for task in tasks:
            self.cancel(task.id, reason)
        return len(tasks)

    def cleanup_finished(self):
        with self._lock:
            tasks = [t for t in self._tasks.values()
                     if t.state in ("done", "failed", "cancelled", "timeout")]
            for task in tasks:
                self._tasks.pop(task.id, None)
        for task in tasks:
            shutil.rmtree(task.workdir, ignore_errors=True)

    def shutdown(self):
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.state in ("queued", "running"):
                self._kill(task, "程序正在退出")
                task.done.wait(timeout=5)

    # -- 启用握手 ---------------------------------------------------------
    def handshake(self, pid: str, manifest: dict) -> dict:
        """只验证入口文件能不能导入；失败说明这个版本根本起不来。"""
        info = self.store.entry(pid) or {}
        folder = info.get("path") or ""
        workdir = os.path.join(self.store.staging_dir, "handshake-%s-%d"
                               % (re.sub(r"[^a-z0-9._-]", "_", pid), time.time_ns()))
        os.makedirs(workdir, exist_ok=True)
        request = {
            "api_version": P.PLUGIN_API_VERSION,
            "task": "handshake",
            "plugin": {"id": pid, "version": info.get("version", ""), "dir": folder},
            "entrypoint": manifest.get("entrypoint", ""),
            "capability": "", "method": "__ping__", "workdir": workdir, "snapshot": "",
            "document": {}, "options": {}, "input": {},
            "limits": {"max_artifact_bytes": P.MAX_ARTIFACT_BYTES},
        }
        proc = None
        timer = None
        try:
            proc = self._spawn(request)
            holder = {"proc": proc}
            timer = threading.Timer(HANDSHAKE_TIMEOUT, self._kill_process, args=(holder,))
            timer.daemon = True
            timer.start()
            result, error = None, ""
            for line in proc.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if message.get("type") == "result":
                    result = message.get("data")
                elif message.get("type") == "error":
                    error = str(message.get("message") or "入口无法加载")
            if result is None:
                raise P.TaskError(error or "入口没有响应（可能是文件损坏或语法错误）")
            if result.get("plugin") not in (None, pid):
                raise P.TaskError("入口自报的插件 id 与清单不一致")
            return result
        except P.TaskError:
            raise
        except Exception as exc:
            raise P.TaskError("入口无法加载：%s" % exc)
        finally:
            if timer is not None:
                timer.cancel()
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _kill_process(holder: dict):
        proc = holder.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    # -- 结果提交（实际写入由核心负责，插件不能绕过）----------------------
    def commit_assets(self, task_id: str, target_dir: str, *, now_doc: "str | None" = None,
                      now_revision: "str | None" = None) -> dict:
        """把暂存图片写进文档的资源目录，返回可插入的 Markdown 片段。"""
        checked = self.result(task_id, now_doc=now_doc, now_revision=now_revision)
        if checked.get("kind") != "image-insert":
            raise P.TaskError("这个任务的结果不是图片插入")
        folder = os.path.abspath(target_dir)
        os.makedirs(folder, exist_ok=True)
        created = []
        assets = []
        try:
            for asset in checked["assets"]:
                target = _unique_target(folder, asset["name"])
                if not is_within(folder, target):
                    raise P.TaskError("写入位置越界，已拒绝")
                shutil.copyfile(asset["path"], target)
                created.append(target)
                assets.append({"name": os.path.basename(target), "path": target,
                               "bytes": asset["bytes"]})
        except Exception:
            for path in created:
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise
        return {"markdown": checked["markdown"], "assets": assets, "created": created,
                "note": checked.get("note", "")}

    def commit_export(self, task_id: str, dest: str, *, now_doc: "str | None" = None,
                      now_revision: "str | None" = None) -> dict:
        """把插件产物原子地放到用户选定的目标；失败保留原目标。"""
        checked = self.result(task_id, now_doc=now_doc, now_revision=now_revision)
        dest = os.path.abspath(str(dest or ""))
        if not dest:
            raise P.TaskError("没有指定导出目标")
        if os.path.normcase(dest) == os.path.normcase(checked["path"]):
            raise P.TaskError("导出目标不能是插件的工作文件")
        folder = os.path.dirname(dest)
        os.makedirs(folder, exist_ok=True)
        temporary = os.path.join(folder, ".mdreader-plugin-%s.tmp" % uuid.uuid4().hex[:10])
        try:
            with open(checked["path"], "rb") as stream:
                raw = stream.read()
            with open(temporary, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, dest)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
        return {"path": dest, "bytes": len(raw), "extension": checked["extension"],
                "media_type": checked["media_type"], "warnings": checked.get("warnings", [])}

    def discard(self, task_id: str):
        task = self.get(task_id)
        shutil.rmtree(task.workdir, ignore_errors=True)
