# -*- coding: utf-8 -*-
"""已授权的用户文件夹：登记根目录、扫描 Markdown 树、在原目录新建文档。

这一层只做文件系统的事，不依赖 HTTP 或 Tk：

* :class:`FolderRoots` —— 记住用户明确选过的**原文件夹**（登记在应用工作区里，
  不在用户资料目录里写任何管理文件），扫描其中的 Markdown，并在原目录新建文档。
* :class:`TreeWatcher` —— 后台增量轮询，外部增删改后通知界面刷新。

文件身份是「已授权根目录 id + 规范化相对路径」，同一个根目录下的同名文件不会
互相顶替；所有路径在真正读写前都重新用 :func:`storage.safe_join` /
:func:`storage.is_within` 校验一次，不依赖界面上显示的那份路径。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

from .storage import is_within, safe_join

FOLDERS_FILE = "folders.json"
DOC_EXTS = (".md", ".markdown", ".mdown", ".mkd", ".txt")

#: 目录扫描上限：超过就把根目录标记为“太大，未完整扫描”，而不是返回半份树
MAX_FILES = 20000
MAX_DIRS = 4000
#: 单个根目录的轮询周期（秒）。外部增删通常两秒内出现在界面上。
WATCH_INTERVAL = 1.5
#: 一次轮询里允许消耗的时间比例（扫描耗时 / 间隔），超过就退回更慢的节奏
WATCH_BUDGET = 0.5

IGNORE_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "dist", "build", ".mdreader", "$RECYCLE.BIN", "System Volume Information",
}

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(("com%d" % n) for n in range(1, 10)),
    *(("lpt%d" % n) for n in range(1, 10)),
}
_ILLEGAL_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# --------------------------------------------------------------------------
# 新建文档的名称校验
# --------------------------------------------------------------------------

def normalize_doc_name(name: str) -> str:
    """把用户输入变成最终文件名（默认补 ``.md``）。只做规范化，不碰磁盘。"""
    text = (name or "").strip()
    if not text:
        raise ValueError("请填写文件名")
    text = text.replace("\\", "/").split("/")[-1].strip()
    if not text:
        raise ValueError("请填写文件名")
    if os.path.splitext(text)[1].lower() not in DOC_EXTS:
        text += ".md"
    return text


def validate_doc_name(name: str) -> str:
    """校验一个新建的 Markdown 文件名，返回可用的名字。

    覆盖任务书要求的空名称、Windows 保留名、非法字符、尾部空格/点，以及
    大小写不敏感的重复名（在 :func:`FolderRoots.create_doc` 里面对磁盘检查）。
    """
    text = normalize_doc_name(name)
    if text in (".", ".."):
        raise ValueError("这个名称不能作为文件名")
    stem = os.path.splitext(text)[0]
    if not stem:
        raise ValueError("请填写文件名")
    if stem != stem.rstrip(" ."):
        raise ValueError("文件名不能以空格或句点结尾：%s" % text)
    match = _ILLEGAL_NAME_CHARS.search(text)
    if match:
        raise ValueError('文件名不能包含 \\ / : * ? " < > | 或控制字符：%s' % text)
    if stem.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise ValueError("「%s」是 Windows 保留名称，请换一个" % stem)
    if len(text) > 120:
        raise ValueError("文件名过长（最多 120 个字符）")
    return text


def _already_exists(parent: str, filename: str) -> bool:
    """大小写不敏感的存在性检查（Windows 下 ``A.md`` 与 ``a.md`` 是同一个文件）。"""
    wanted = os.path.normcase(filename)
    try:
        for entry in os.listdir(parent):
            if os.path.normcase(entry) == wanted:
                return True
    except OSError:
        return os.path.exists(os.path.join(parent, filename))
    return False


def _create_exclusive(path: str, text: str = "") -> None:
    """独占创建：检查与创建之间被人抢先时，这里会失败而不是覆盖。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((text or "").encode("utf-8"))
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


#: 两个模块（原文件夹与工作区项目）共用同一套独占创建语义，名字保留内部别名
create_exclusive = _create_exclusive


def _root_id(path: str) -> str:
    """稳定的根目录身份：同一路径永远得到同一个 id。"""
    real = os.path.realpath(os.path.abspath(path))
    digest = hashlib.sha1(os.path.normcase(real).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return "root-" + digest


# --------------------------------------------------------------------------
# 已登记的用户文件夹
# --------------------------------------------------------------------------

class FolderRoots:
    """用户明确选过的原文件夹；直接读写原件，不复制进工作区。"""

    def __init__(self, ws):
        self.ws = ws
        self.path = os.path.join(ws.root, FOLDERS_FILE)
        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}
        self._watch = TreeWatcher(self)

    # -- 登记表 ----------------------------------------------------------
    def _read(self) -> list:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        if isinstance(data, dict):
            data = data.get("roots", [])
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if isinstance(item, dict) and item.get("path"):
                out.append(item)
        return out

    def _write(self, items: list) -> None:
        self.ws._atomic_write(self.path, json.dumps(items, ensure_ascii=False, indent=2))

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def add(self, raw_path: str) -> dict:
        """登记一个用户选定的文件夹。失败时抛异常，不做任何登记。"""
        text = str(raw_path or "").strip().strip('"')
        if not text:
            raise ValueError("没有选择文件夹")
        path = os.path.abspath(os.path.expanduser(text))
        if not os.path.exists(path):
            raise FileNotFoundError("找不到文件夹：%s" % path)
        if not os.path.isdir(path):
            raise NotADirectoryError("这不是文件夹：%s" % path)
        try:
            os.listdir(path)
        except OSError as exc:
            raise PermissionError("没有权限读取这个文件夹：%s（%s）" % (path, exc)) from exc
        key = self._key(path)
        with self._lock:
            items = self._read()
            existing = next((i for i in items if self._key(i["path"]) == key), None)
            if existing is not None:
                return self.describe(existing["path"])
            entry = {
                "id": _root_id(path),
                "path": path,
                "name": os.path.basename(path.rstrip("\\/")) or path,
                "added": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            # 同一个目录被用不同大小写/短路径再次选中时，id 会相同
            clash = next((i for i in items if i.get("id") == entry["id"]), None)
            if clash is not None:
                items = [i for i in items if i is not clash]
            items.insert(0, entry)
            self._write(items)
        return self.describe(entry["path"])

    def forget(self, rid_or_path: str) -> bool:
        """从列表移除。**只移除应用记录，绝不删除磁盘上的目录。**"""
        target = _s_id(rid_or_path)
        with self._lock:
            items = self._read()
            kept = [i for i in items
                    if i.get("id") != target and self._key(i["path"]) != self._key(rid_or_path)]
            if len(kept) == len(items):
                return False
            self._write(kept)
            self._cache.clear()
        self._watch.reconfigure()
        return True

    def roots(self) -> list:
        """全部登记项，附带可访问状态。"""
        out = []
        for item in self._read():
            path = item["path"]
            entry = dict(item)
            entry["exists"] = os.path.isdir(path)
            entry["readable"] = False
            entry["problem"] = ""
            if entry["exists"]:
                try:
                    os.listdir(path)
                except OSError as exc:
                    entry["problem"] = "暂时无法访问：%s" % exc
                else:
                    entry["readable"] = True
            else:
                entry["problem"] = "文件夹不在了或所在磁盘未连接"
            out.append(entry)
        return out

    def require(self, rid: str) -> dict:
        wanted = _s_id(rid)
        for item in self._read():
            if item.get("id") == wanted:
                if not os.path.isdir(item["path"]):
                    raise FileNotFoundError("文件夹暂时不可访问：%s" % item["path"])
                return item
        raise KeyError("没有授权这个文件夹，请用「打开原文件夹」选择：%s" % wanted)

    def describe(self, rid_or_path: str) -> dict:
        wanted = _s_id(rid_or_path)
        item = next((i for i in self._read()
                     if i.get("id") == wanted or self._key(i["path"]) == self._key(rid_or_path)), None)
        if item is None:
            raise KeyError("没有这个已打开的文件夹")
        entry = dict(item)
        entry["exists"] = os.path.isdir(item["path"])
        entry["readable"] = entry["exists"]
        return entry

    # -- 扫描 ------------------------------------------------------------
    def scan(self, rid: str, force: bool = False, with_dirs: bool = True) -> dict:
        """返回全部文件和目录，同时单列可阅读的 Markdown 文档。

        先做一次**廉价的浅层探测**（根目录与子目录的 mtime + 文件名/大小）；探测
        结果与上次一致就直接给缓存，不一致才真正走一遍目录。这样外部只改一个
        深层子目录时也能发现，而每次键入都不需要全量扫描。
        """
        item = self.require(rid)
        root = item["path"]
        with self._lock:
            cached = self._cache.get(item["id"])
            now = time.monotonic()
            if cached is not None and not force:
                if now - cached["checked"] < WATCH_INTERVAL:
                    out = dict(cached["tree"])
                    out["changed"] = False
                    return out
                probe = _deep_signature(root)
                cached["checked"] = now
                if probe == cached["probe"]:
                    out = dict(cached["tree"])
                    out["changed"] = False
                    return out
            probe = _deep_signature(root)
        tree = self._walk(item, with_dirs=with_dirs, probe=probe)
        return tree

    def _walk(self, item: dict, with_dirs: bool = True, probe: "tuple | None" = None) -> dict:
        root = item["path"]
        docs, dirs, skipped, files = [], [], [], []
        files_seen = dirs_seen = 0
        truncated = ""
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            keep = []
            for name in dirnames:
                full = os.path.join(dirpath, name)
                # 符号链接 / 目录联接越出根目录时跳过，避免越界与循环扫描
                if os.path.islink(full) and not is_within(root, full):
                    skipped.append(os.path.relpath(full, root).replace(os.sep, "/"))
                    continue
                keep.append(name)
            dirnames[:] = keep
            dirs_seen += len(keep)
            if dirs_seen > MAX_DIRS:
                truncated = "子目录过多，只扫描了前 %d 个" % MAX_DIRS
                break
            files_seen += len(filenames)
            if files_seen > MAX_FILES:
                truncated = "文件过多，只统计了前 %d 个" % MAX_FILES
                break
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            if with_dirs and rel_dir:
                dirs.append({"id": rel_dir, "name": os.path.basename(dirpath),
                             "parent": os.path.dirname(rel_dir).replace(os.sep, "/")})
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = (rel_dir + "/" + name) if rel_dir else name
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                entry = {
                    "root": item["id"],
                    "_abs": full,
                    "id": rel,
                    "name": os.path.splitext(name)[0],
                    "file": name,
                    "dir": rel_dir,
                    "size": stat.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                    "mtime_ts": stat.st_mtime,
                }
                files.append(entry)
                if os.path.splitext(name)[1].lower() in DOC_EXTS:
                    docs.append(entry)
        docs.sort(key=lambda d: (d["dir"].count("/"), d["dir"], d["name"].lower()))
        dirs.sort(key=lambda d: d["id"].lower())
        status = _status_of(root)
        payload = {
            "changed": True,
            "root": {k: v for k, v in item.items()},
            "docs": docs,
            "files": files,
            "dirs": dirs,
            "skipped_links": skipped,
            "truncated": truncated,
            "status": status,
            "counts": {"docs": len(docs), "files": len(files), "dirs": len(dirs)},
        }
        with self._lock:
            self._cache[item["id"]] = {"signature": _tree_signature(docs, status),
                                       "probe": probe if probe is not None else _deep_signature(root),
                                       "checked": time.monotonic(),
                                       "tree": payload}
        return payload

    # -- 在原目录新建文档 ------------------------------------------------
    def create_doc(self, rid: str, name: str, subdir: str = "", content: str = "",
                   unique: bool = False) -> dict:
        """在选定根目录（或其子目录）里新建一个 Markdown 文件并返回它的信息。

        ``unique=True``（目录行的「＋」用它）时不报重名，而是顺延成
        ``Untitled-2.md``、``Untitled-3.md``…… 与工作区项目的同名规则保持一致。
        无论是哪条路径，**最终都以独占创建的结果为准**，绝不覆盖已有文件。
        """
        item = self.require(rid)
        root = item["path"]
        filename = validate_doc_name(name)
        parent = safe_join(root, subdir) if _s_id(subdir) else os.path.realpath(root)
        if not is_within(root, parent):
            raise ValueError("路径越界，已拒绝")
        if not os.path.isdir(parent):
            raise FileNotFoundError("目标子目录不存在：%s" % _s_id(subdir))
        stem, ext = os.path.splitext(filename)
        target = os.path.join(parent, filename)
        if not is_within(root, target):
            raise ValueError("路径越界，已拒绝")
        if not unique and _already_exists(parent, filename):
            raise FileExistsError("这里已经有同名文件了，请换一个名字：%s" % filename)
        created = False
        for n in range(2, 1000):
            try:
                create_exclusive(target, content)
                created = True
                break
            except FileExistsError:
                if not unique:
                    raise FileExistsError(
                        "这里已经有同名文件了，请换一个名字：%s" % filename) from None
                target = os.path.join(parent, "%s-%d%s" % (stem, n, ext))
            except OSError as exc:
                raise PermissionError("无法在这个位置创建文件：%s（%s）" % (target, exc)) from exc
        if not created:
            raise FileExistsError("同一目录下同名文件太多了，请换一个名称")
        self.scan(item["id"], force=True)
        return self.doc_info(item["id"], os.path.relpath(target, root).replace(os.sep, "/"))

    def rename_doc(self, rid: str, did: str, new_name: str) -> dict:
        """重命名原文件；目标已存在时拒绝，绝不覆盖。"""
        item = self.require(rid)
        root = item["path"]
        info = self.doc_info(rid, did)
        filename = validate_doc_name(new_name)
        parent = os.path.dirname(info["_abs"])
        if not is_within(root, parent):
            raise ValueError("路径越界，已拒绝")
        target = os.path.join(parent, filename)
        if not is_within(root, target):
            raise ValueError("路径越界，已拒绝")
        if os.path.normcase(target) == os.path.normcase(info["_abs"]):
            return info
        if _already_exists(parent, filename):
            raise FileExistsError("这里已经有同名文件了，请换一个名字：%s" % filename)
        try:
            os.rename(info["_abs"], target)
        except FileExistsError:
            # 检查与重命名之间被抢先：Windows 上 rename 到已存在目标会失败
            raise FileExistsError("这里已经有同名文件了，请换一个名字：%s" % filename) from None
        except OSError as exc:
            raise PermissionError("无法重命名：%s（%s）" % (info["_abs"], exc)) from exc
        self.scan(item["id"], force=True)
        return self.doc_info(rid, os.path.relpath(target, root).replace(os.sep, "/"))

    def remove_doc(self, rid: str, did: str, to_recycle: bool = True,
                   expected_revision: "str | None" = None) -> dict:
        """删除**用户的本地文件**：默认移入回收站，失败时绝不改为永久删除。

        返回删除前的信息（``path`` / ``name`` / ``file`` / ``revision``），
        界面用它来说明“删掉的是哪一个文件”。真正执行前会重新校验路径与修订：
        确认之后如果文件被外部替换过，这里拒绝并让用户重新确认。
        """
        from . import documents as D
        info = self.doc_info(rid, did)
        path = info["_abs"]
        current = D.revision(path)
        expected = expected_revision if expected_revision is not None else current
        if current != expected:
            raise D.ConflictError(path, current)
        if to_recycle:
            # Windows 回收站自己会把文件移走；这里**不**再补一次 os.remove，
            # 否则「移入回收站」在实现上会退化成永久删除。回收站不可用时直接
            # 拒绝，绝不改为永久删除。
            if os.name != "nt" or not type(self)._to_recycle(path):
                raise PermissionError(
                    "无法移入回收站，文件已保留：%s。请关闭占用它的程序或检查权限后重试；"
                    "MDReader 不会改为永久删除。" % path)
            if os.path.exists(path):
                raise PermissionError(
                    "回收站没有真正移走文件，已停止操作：%s。请关闭占用它的程序后重试；"
                    "MDReader 不会改为永久删除。" % path)
        else:
            os.remove(path)
        self.scan(rid, force=True)
        plain = {k: v for k, v in info.items() if k != "_abs"}
        plain["path"] = path              # 界面用它告知“删掉的是哪一个文件”
        plain["deleted"] = True
        return plain

    @staticmethod
    def _to_recycle(path: str) -> bool:
        """复用核心的回收站实现，不另建一条绕过确认的删除通道。"""
        from .core import _send_to_recycle_bin
        return _send_to_recycle_bin(path)

    def doc_info(self, rid: str, did: str) -> dict:
        """按「根目录 + 相对路径」取一份文档的信息，路径在执行前重新校验。"""
        item = self.require(rid)
        full = safe_join(item["path"], did)
        if not os.path.isfile(full):
            raise KeyError("文件不存在：%s" % did)
        rel = os.path.relpath(full, item["path"]).replace(os.sep, "/")
        try:
            stat = os.stat(full)
        except OSError:
            stat = None
        return {
            "root": item["id"],
            "_abs": full,
            "id": rel,
            "name": os.path.splitext(os.path.basename(full))[0],
            "file": os.path.basename(full),
            "dir": os.path.dirname(rel),
            "size": stat.st_size if stat else 0,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)) if stat else "",
            "mtime_ts": stat.st_mtime if stat else 0,
        }

    def read_doc(self, rid: str, did: str) -> str:
        from . import documents as D
        info = self.doc_info(rid, did)
        return D.snapshot(info["_abs"])["text"]

    # -- 监听 ------------------------------------------------------------
    def watch(self) -> "TreeWatcher":
        return self._watch


def _s_id(value) -> str:
    return str(value or "").strip()


def _status_of(root: str) -> str:
    """``ok`` / ``missing`` / ``denied``——扫描失败绝不等于“文件都没了”。"""
    if not os.path.isdir(root):
        return "missing"
    try:
        os.listdir(root)
    except OSError:
        return "denied"
    return "ok"


def _deep_signature(root: str) -> tuple:
    """有界的深层探测：子目录 mtime + Markdown 文件的 mtime/大小。

    Windows 上文件内容变化**不会**改动父目录的 mtime，所以只探测目录是不够的；
    这里同时也看文件的时间戳，但设了上限，避免大目录每次轮询都全量扫描。
    """
    status = _status_of(root)
    if status != "ok":
        return (status,)
    try:
        root_stamp = os.stat(root).st_mtime_ns
    except OSError:
        return ("denied",)
    dirs = []
    files = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if is_within(root, os.path.join(dirpath, d))
                              and not os.path.islink(os.path.join(dirpath, d)))
        try:
            dirs.append((os.path.relpath(dirpath, root).replace(os.sep, "/"),
                         os.stat(dirpath).st_mtime_ns))
        except OSError:
            dirs.append((os.path.relpath(dirpath, root).replace(os.sep, "/"), 0))
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                files.append((full, 0, 0))
                continue
            files.append((full, stat.st_mtime_ns, stat.st_size))
        if len(dirs) > MAX_DIRS or len(files) > MAX_FILES:
            truncated = True
            break
    if truncated:
        return (status, root_stamp, len(dirs), len(files), "large")
    files.sort()
    dirs.sort()
    return (status, root_stamp, tuple(dirs), tuple(files))


def _tree_signature(docs: list, status: str) -> tuple:
    return (status, tuple(sorted((d["id"], d["mtime_ts"], d["size"]) for d in docs)))


class TreeWatcher:
    """后台轮询：外部增删改后回调一次，重复事件合并。

    只做扫描与比较，不碰界面；界面层决定什么时候刷。根目录掉线（``missing``/
    ``denied``）与“真的空了”是两种不同的信号。
    """

    def __init__(self, manager: FolderRoots):
        self.manager = manager
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._listeners = []
        self._last: dict[str, tuple] = {}
        self._interval = WATCH_INTERVAL
        self._scans = 0
        self._slow = 0

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def configure(self, interval: float | None = None) -> None:
        if interval:
            self._interval = max(0.25, float(interval))

    def stats(self) -> dict:
        return {"running": bool(self._thread and self._thread.is_alive()),
                "interval": self._interval, "scans": self._scans, "slow": self._slow,
                "watched": len(self.manager.roots())}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mdreader-folders", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def reconfigure(self) -> None:
        """登记项变化后立刻重新扫一遍（新增/移除根目录）。"""
        self._last = {k: v for k, v in self._last.items()
                      if any(r["id"] == k for r in self.manager.roots())}
        self._wake.set()

    def poll_once(self) -> list:
        """扫一遍全部根目录，返回发生变化的根 id 列表（供测试与手动刷新用）。"""
        changed = []
        for item in self.manager.roots():
            rid = item["id"]
            status = _status_of(item["path"])
            try:
                tree = self.manager.scan(rid, force=True)
                status = tree.get("status", status)
            except (OSError, KeyError, ValueError):
                tree = {"docs": [], "status": status if status != "ok" else "denied"}
            signature = (_tree_signature(tree.get("files", tree.get("docs", [])), status),
                         tuple(d["id"] for d in tree.get("dirs", [])))
            previous = self._last.get(rid)
            self._last[rid] = signature
            if previous is not None and previous != signature:
                changed.append(rid)
        for rid in changed:
            for listener in list(self._listeners):
                try:
                    listener(rid)
                except Exception:
                    continue
        return changed

    def _loop(self) -> None:
        # 第一圈只建立基线，不把登录时的既有内容当成“外部新增”
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
                self._scans += 1
            except Exception:
                pass
            spent = time.monotonic() - started
            if spent > self._interval * WATCH_BUDGET:
                self._slow += 1
                delay = min(self._interval * 4, max(self._interval * 2, spent))
            else:
                delay = self._interval
            self._wake.wait(delay)
            self._wake.clear()
