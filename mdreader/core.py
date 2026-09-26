# -*- coding: utf-8 -*-
"""
MDReader core: workspace model + local HTTP API server.

The app is a single-page front-end served by a loopback-only HTTP server.
Everything runs offline; nothing is written outside the user's MDReader
workspace unless the user imports/exports files explicitly.
"""

from __future__ import annotations

import base64
import datetime as _dt
import html
import json
import mimetypes
import secrets
from . import documents as D
from .recovery import RecoveryStore
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import render as R
from . import exporting as EX
from . import folders as F
from . import formatting as FM
from . import links as LK
from . import plugins as PL
from . import tables as TB
from .storage import atomic_write, is_within, safe_join

APP_NAME = "MDReader"
APP_VERSION = "0.3.0"
WS_DIRNAME = ".mdreader"
PROJECT_FILE = "project.json"
DOC_EXTS = (".md", ".markdown", ".mdown", ".mkd", ".txt")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif")
IGNORE_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode",
               "dist", "build", ".mdreader", "$RECYCLE.BIN", "System Volume Information"}

_SAFE_ID = re.compile('^[^\\\\/:*?"<>|\\x00-\\x1f]+$')

#: Reading themes, shared by the desktop window and the page so their
#: backgrounds cannot drift apart.
THEME_CHOICES = ("light", "dark", "eye")
UI_SETTINGS_FILE = "ui-settings.json"


def read_ui_settings(workspace) -> dict:
    """Preferences for the whole workspace; unknown values fall back to light."""
    settings = {"theme": "light"}
    try:
        with open(os.path.join(workspace, UI_SETTINGS_FILE), encoding="utf-8") as stream:
            stored = json.load(stream)
    except (OSError, ValueError):
        return settings
    if isinstance(stored, dict) and stored.get("theme") in THEME_CHOICES:
        settings["theme"] = stored["theme"]
    return settings


def write_ui_settings(workspace, theme: str) -> dict:
    """Persist the theme for both entries, keeping any other stored keys."""
    if theme not in THEME_CHOICES:
        raise ValueError("主题只能是：" + "、".join(THEME_CHOICES))
    stored = {}
    try:
        with open(os.path.join(workspace, UI_SETTINGS_FILE), encoding="utf-8") as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            stored = loaded
    except (OSError, ValueError):
        stored = {}
    stored["theme"] = theme
    atomic_write(os.path.join(workspace, UI_SETTINGS_FILE),
                 json.dumps(stored, ensure_ascii=False, indent=2))
    return {"theme": theme}



# ==========================================================================
# helpers
# ==========================================================================

def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def slugify(name: str, fallback: str = "untitled") -> str:
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", (name or "").strip())
    s = re.sub(r"\s+", "-", s)
    s = s.strip(".-")
    if not s:
        s = fallback
    return s[:80]


def human_time(ts: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def read_text_file(path: str) -> str:
    """Strict reading shared with the versioned save path; no replacement text."""
    return D.snapshot(path)['text']


def app_dir() -> str:
    """Folder that holds the shipped resources (webui/, main.py).

    Works both from source and from a frozen/PyInstaller build.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # mdreader/core.py -> mdreader/ -> app root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_workspace() -> str:
    env = os.environ.get("MDREADER_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    config = os.path.join(app_dir(), 'installation.json')
    if os.path.isfile(config):
        # This file belongs to this installation, not the user's documents.
        # Do not silently switch workspaces if an existing config is damaged.
        with open(config, encoding='utf-8-sig') as stream:
            data = json.load(stream)
        workspace = data.get('workspace')
        if not isinstance(workspace, str) or not os.path.isabs(workspace):
            raise ValueError('installation.json: workspace must be an absolute path')
        return os.path.abspath(workspace)
    return os.path.join(os.path.expanduser("~"), "MDReader")


# ==========================================================================
# workspace / projects
# ==========================================================================

class Workspace:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self._versions = {}
        self.recovery = RecoveryStore(self.root)
        self.projects_dir = os.path.join(self.root, "projects")
        # 用户自己的文件夹（F01）：登记、扫描、监听都在这一层，界面只负责显示
        self.folders = F.FolderRoots(self)
        # 插件机制（P01）：只登记状态与命令，插件代码在执行时才由工作进程加载
        self.plugins = PL.PluginStore(self.root, app_version=APP_VERSION)
        self.ensure()

    def ensure(self):
        os.makedirs(self.projects_dir, exist_ok=True)
        cfg = os.path.join(self.root, "workspace.json")
        if not os.path.exists(cfg):
            self._atomic_write(cfg, json.dumps(
                {"app": APP_NAME, "version": APP_VERSION, "created": now_iso()}, ensure_ascii=False, indent=2))

    # -- low level -------------------------------------------------------
    @staticmethod
    def _atomic_write(path: str, text: str):
        atomic_write(path, text)

    def project_dir(self, pid: str) -> str:
        if not _SAFE_ID.fullmatch(pid or "") or pid in (".", ".."):
            raise ValueError("非法的项目 ID")
        external = self.external_projects().get(pid)
        if external:
            return os.path.realpath(external)
        return safe_join(self.projects_dir, pid)

    def external_projects(self) -> dict:
        try:
            with open(os.path.join(self.root, "project-locations.json"), encoding="utf-8") as handle:
                locations = json.load(handle)
        except FileNotFoundError:
            return {}
        if not isinstance(locations, dict) or any(
                not isinstance(key, str) or not _SAFE_ID.fullmatch(key) or key in (".", "..")
                or not isinstance(path, str) or not os.path.isabs(path)
                for key, path in locations.items()):
            raise ValueError("项目地址记录无效")
        return locations

    def _write_project_locations(self, locations):
        self._atomic_write(os.path.join(self.root, "project-locations.json"),
                           json.dumps(locations, ensure_ascii=False, indent=2))

    def read_meta(self, pdir: str) -> dict:
        p = os.path.join(pdir, PROJECT_FILE)
        meta = {}
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                meta = {}
        meta.setdefault("id", os.path.basename(pdir))
        meta.setdefault("name", os.path.basename(pdir))
        meta.setdefault("created", now_iso())
        meta.setdefault("updated", meta["created"])
        return meta

    def write_meta(self, pdir: str, meta: dict):
        meta = dict(meta)
        meta["updated"] = now_iso()
        self._atomic_write(os.path.join(pdir, PROJECT_FILE), json.dumps(meta, ensure_ascii=False, indent=2))

    def touch(self, pdir: str):
        try:
            meta = self.read_meta(pdir)
            meta["updated"] = now_iso()
            self._atomic_write(os.path.join(pdir, PROJECT_FILE), json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def project_exists(self, pid: str) -> bool:
        try:
            return os.path.isdir(self.project_dir(pid))
        except Exception:
            return False

    def require_project(self, pid: str) -> str:
        pdir = self.project_dir(pid)
        if not os.path.isdir(pdir):
            raise KeyError("项目不存在：%s" % pid)
        return pdir

    # -- scanning --------------------------------------------------------
    def list_projects(self):
        items = []
        if not os.path.isdir(self.projects_dir):
            return items
        locations = {entry: os.path.join(self.projects_dir, entry)
                     for entry in sorted(os.listdir(self.projects_dir))}
        locations.update(self.external_projects())
        for entry, pdir in locations.items():
            if not os.path.isdir(pdir) or entry.startswith("."):
                continue
            meta = self.read_meta(pdir)
            docs = self.scan_docs(pdir)
            chars = sum(d.get("size", 0) for d in docs)
            items.append({
                "id": meta["id"],
                "name": meta.get("name") or entry,
                "description": meta.get("description", ""),
                "created": meta.get("created"),
                "updated": meta.get("updated"),
                "doc_count": len(docs),
                "chars": chars,
                "path": pdir,
            })
        items.sort(key=lambda x: x.get("updated") or "", reverse=True)
        return items

    def scan_docs(self, pdir: str):
        """Return a flat, sorted list of markdown docs inside a project folder."""
        out = []
        for dirpath, dirnames, filenames in os.walk(pdir):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                if not fn.lower().endswith(DOC_EXTS) or fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, pdir).replace(os.sep, "/")
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out.append({
                    "_abs": full,
                    "id": rel,
                    "name": os.path.splitext(fn)[0],
                    "file": fn,
                    "dir": os.path.dirname(rel),
                    "size": st.st_size,
                    "mtime": human_time(st.st_mtime),
                    "mtime_ts": st.st_mtime,
                })
        out.sort(key=lambda d: (d["dir"].count("/"), d["dir"], d["name"].lower()))
        return out

    def doc_info(self, pdir: str, did: str) -> dict:
        full = safe_join(pdir, did)
        if not os.path.isfile(full):
            raise KeyError("文件不存在：%s" % did)
        rel = os.path.relpath(full, pdir).replace(os.sep, "/")
        try:
            st = os.stat(full)
        except OSError:
            st = None
        return {
            "_abs": full,
            "revision": D.revision(full),
            "id": rel,
            "name": os.path.splitext(os.path.basename(full))[0],
            "file": os.path.basename(full),
            "dir": os.path.dirname(rel),
            "size": st.st_size if st else 0,
            "mtime": human_time(st.st_mtime) if st else "",
            "mtime_ts": st.st_mtime if st else 0,
        }

    # -- mutations -------------------------------------------------------
    def create_project(self, name: str, description: str = "", with_readme: bool = True,
                       parent_dir: str | None = None) -> dict:
        name = (name or "").strip() or "未命名项目"
        parent = os.path.realpath(parent_dir or self.projects_dir)
        if not os.path.isdir(parent):
            raise ValueError("请选择一个已存在的保存文件夹")
        external = os.path.normcase(parent) != os.path.normcase(os.path.realpath(self.projects_dir))
        locations = self.external_projects()
        pid = slugify(name, "project")
        base, n = pid, 2
        while self.project_exists(pid) or pid in locations or os.path.exists(os.path.join(parent, pid)):
            pid = "%s-%d" % (base, n)
            n += 1
        pdir = safe_join(parent, pid)
        os.makedirs(pdir, exist_ok=False)
        for sub in ("docs", "assets"):
            os.makedirs(os.path.join(pdir, sub), exist_ok=True)
        self.write_meta(pdir, {
            "id": pid, "name": name, "description": description,
            "created": now_iso(), "schema": 1, "app": APP_NAME,
        })
        if with_readme:
            readme = os.path.join(pdir, "docs", "%s.md" % slugify(name, "readme"))
            self._atomic_write(readme, _starter_doc(name, description))
        if external:
            locations[pid] = pdir
            self._write_project_locations(locations)
        return {"id": pid, "name": name, "path": pdir}

    def rename_project(self, pid: str, new_name: str) -> dict:
        pdir = self.require_project(pid)
        meta = self.read_meta(pdir)
        meta["name"] = (new_name or "").strip() or meta["name"]
        self.write_meta(pdir, meta)
        return meta

    def delete_project(self, pid: str, to_recycle: bool = True) -> bool:
        pdir = self.require_project(pid)
        if to_recycle:
            if os.name == "nt" and _send_to_recycle_bin(pdir):
                pass
            else:
                raise PermissionError("无法移入回收站，项目已保留。")
        else:
            shutil.rmtree(pdir, ignore_errors=False)
        locations = self.external_projects()
        if pid in locations:
            del locations[pid]
            self._write_project_locations(locations)
        return True

    def read_doc(self, pdir: str, did: str) -> str:
        info = self.doc_info(pdir, did)
        data = D.snapshot(info["_abs"])
        self._versions[info["_abs"]] = data['revision']
        return data['text']

    def save_doc(self, pdir: str, did: str, text: str, expected=None, overwrite=None) -> dict:
        full = safe_join(pdir, did)
        base = expected if expected is not None else self._versions.get(full, D.revision(full))
        self._versions[full] = D.write_checked(full, text, base, overwrite,
                                              os.path.join(self.root, '.recovery', 'conflicts'))
        try: self.touch(pdir)
        except OSError: pass  # the document commit already succeeded
        return self.doc_info(pdir, did)

    def create_doc(self, pdir: str, name: str, subdir: str = "", content: str = "") -> dict:
        base = slugify(name, "Untitled")
        if not base.lower().endswith(DOC_EXTS):
            base += ".md"
        target_dir = safe_join(pdir, subdir) if subdir else pdir
        os.makedirs(target_dir, exist_ok=True)
        stem, ext = os.path.splitext(base)
        full = os.path.join(target_dir, base)
        body = content if content else "# %s\n\n> 创建于 %s\n\n在这里开始记录。\n" % (
            os.path.splitext(os.path.basename(full))[0], now_iso())
        # 独占创建 + 顺延编号：树的显示顺序可能与磁盘不同，只靠一次 listdir
        # 判断“重名”会漏掉刚被别人建好的文件，这里以真正创建的动作为准
        for n in range(2, 1000):
            try:
                F.create_exclusive(full, body)
            except FileExistsError:
                full = os.path.join(target_dir, "%s-%d%s" % (stem, n, ext))
                body = content if content else "# %s\n\n> 创建于 %s\n\n在这里开始记录。\n" % (
                    os.path.splitext(os.path.basename(full))[0], now_iso())
                continue
            break
        else:
            raise FileExistsError("同一目录下同名文件太多了，请换一个名称")
        self.touch(pdir)
        rel = os.path.relpath(full, pdir).replace(os.sep, "/")
        return self.doc_info(pdir, rel)

    def rename_doc(self, pdir: str, did: str, new_name: str) -> dict:
        info = self.doc_info(pdir, did)
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("名称不能为空")
        if not new_name.lower().endswith(DOC_EXTS):
            new_name += os.path.splitext(info["file"])[1] or ".md"
        new_name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", new_name)
        dest = os.path.join(os.path.dirname(info["_abs"]), new_name)
        if os.path.abspath(dest) != os.path.abspath(info["_abs"]):
            if os.path.exists(dest):
                raise FileExistsError("同名文件已存在")
            os.replace(info["_abs"], dest)
        self.touch(pdir)
        rel = os.path.relpath(dest, pdir).replace(os.sep, "/")
        return self.doc_info(pdir, rel)

    def move_doc(self, pdir: str, did: str, new_dir: str) -> dict:
        info = self.doc_info(pdir, did)
        target_dir = safe_join(pdir, new_dir) if new_dir else pdir
        os.makedirs(target_dir, exist_ok=True)
        dest = safe_join(target_dir, info["file"])
        if os.path.abspath(dest) != os.path.abspath(info["_abs"]):
            if os.path.exists(dest):
                raise FileExistsError("目标位置已有同名文件")
            os.replace(info["_abs"], dest)
        self.touch(pdir)
        rel = os.path.relpath(dest, pdir).replace(os.sep, "/")
        return self.doc_info(pdir, rel)

    def delete_doc(self, pdir: str, did: str, to_recycle: bool = True) -> bool:
        info = self.doc_info(pdir, did)
        if to_recycle:
            if os.name == "nt" and _send_to_recycle_bin(info["_abs"]):
                self.touch(pdir)
                return True
            raise PermissionError("无法移入回收站，文档已保留。")
        os.remove(info["_abs"])
        # clean up empty folders left behind
        d = os.path.dirname(info["_abs"])
        while os.path.abspath(d) != os.path.abspath(pdir) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
        self.touch(pdir)
        return True

    def rename_project_entry(self, pid: str, relative: str, title: str) -> dict:
        """Rename a tree entry in place, retaining a file's original extension."""
        pdir = self.require_project(pid)
        source = safe_join(pdir, relative)
        if os.path.normcase(source) == os.path.normcase(os.path.realpath(pdir)):
            raise ValueError("不能通过文件树修改项目根目录")
        if not os.path.exists(source):
            raise FileNotFoundError("文件或文件夹已经不存在")
        title = title.strip()
        if not title or title.startswith(".") or title.endswith((".", " ")):
            raise ValueError("标题不能为空、以点开头或以点结尾")
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', title):
            raise ValueError("标题不能包含路径分隔符或其他非法字符")
        if title.split(".")[0].lower() in F._WINDOWS_RESERVED:
            raise ValueError("这个标题是 Windows 保留名称，请换一个")
        if len(title) > 120:
            raise ValueError("标题过长（最多 120 个字符）")
        directory = os.path.isdir(source)
        extension = "" if directory else os.path.splitext(source)[1]
        if extension and title.lower().endswith(extension.lower()):
            title = title[:-len(extension)]
        name = title + extension
        safe_join(pdir, os.path.join(os.path.dirname(relative), name))
        # realpath normalizes existing names on Windows; retain requested casing.
        target = os.path.join(os.path.dirname(source), name)
        if source != target:
            for entry in os.listdir(os.path.dirname(source)):
                if entry.casefold() == name.casefold() and entry != os.path.basename(source):
                    raise FileExistsError("同名文件或文件夹已存在，请换一个标题")
            os.rename(source, target)  # On Windows, refuses an existing destination.
        try:
            self.touch(pdir)
        except OSError:
            pass
        return {"old_path": source, "path": target,
                "relative": os.path.relpath(target, pdir).replace(os.sep, "/")}

    def delete_project_entry(self, pid: str, relative: str) -> None:
        """Recycle a project file or subtree; never fall back to permanent deletion."""
        pdir = self.require_project(pid)
        target = safe_join(pdir, relative)
        if os.path.normcase(target) == os.path.normcase(os.path.realpath(pdir)):
            raise ValueError("不能通过文件树删除项目根目录")
        if not os.path.exists(target):
            raise FileNotFoundError("文件或文件夹已经不存在")
        if os.name != "nt" or not _send_to_recycle_bin(target):
            raise PermissionError("无法移入回收站，本地内容已保留")
        try:
            self.touch(pdir)
        except OSError:
            pass  # Recycling has already succeeded.

    def list_dirs(self, pdir: str):
        dirs = []
        for dirpath, dirnames, _ in os.walk(pdir):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith("."))
            rel = os.path.relpath(dirpath, pdir).replace(os.sep, "/")
            dirs.append("" if rel == "." else rel)
        return sorted(set(dirs))

    def import_paths(self, pdir: str, paths, subdir: str = "", recursive: bool = True, move: bool = False) -> dict:
        """Copy (or move) files/folders from disk into the project."""
        target_root = safe_join(pdir, subdir) if subdir else pdir
        os.makedirs(target_root, exist_ok=True)
        added, skipped = [], []
        for p in paths or []:
            p = os.path.abspath(os.path.expanduser(str(p)))
            if not os.path.exists(p):
                skipped.append({"path": p, "reason": "不存在"})
                continue
            if os.path.isfile(p):
                ext = os.path.splitext(p)[1].lower()
                if ext not in DOC_EXTS:
                    skipped.append({"path": p, "reason": "不是 Markdown 文件"})
                    continue
                dest = _unique_path(os.path.join(target_root, os.path.basename(p)))
                _transfer(p, dest, move)
                added.append(os.path.relpath(dest, pdir).replace(os.sep, "/"))
            else:
                base = os.path.basename(_unique_path(os.path.join(target_root, os.path.basename(os.path.normpath(p)))))
                for dirpath, dirnames, filenames in os.walk(p):
                    if not recursive:
                        dirnames[:] = []
                    dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
                    for fn in filenames:
                        if fn.startswith(".") or not fn.lower().endswith(DOC_EXTS + ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')):
                            continue
                        src = os.path.join(dirpath, fn)
                        if not is_within(p, src):
                            skipped.append({'path': src, 'reason': '附件路径越界'})
                            continue
                        rel = os.path.relpath(src, p)
                        dest = _unique_path(os.path.join(target_root, base, rel))
                        _transfer(src, dest, move)
                        if fn.lower().endswith(DOC_EXTS):
                            added.append(os.path.relpath(dest, pdir).replace(os.sep, "/"))
                # Never remove the source directory: it may contain skipped
                # attachments or unrelated files. Only selected files move.
        if added:
            self.touch(pdir)
        return {"added": added, "skipped": skipped, "count": len(added)}

    def import_uploads(self, pdir: str, files, subdir: str = "") -> dict:
        """files: list of {"name": relpath, "data": bytes}"""
        target_root = safe_join(pdir, subdir) if subdir else pdir
        added, skipped = [], []
        for f in files or []:
            rel = (f.get("name") or "").replace("\\", "/").lstrip("/")
            if not rel or ".." in rel.split("/"):
                skipped.append({"path": rel, "reason": "非法路径"})
                continue
            ext = os.path.splitext(rel)[1].lower()
            if ext not in DOC_EXTS:
                skipped.append({"path": rel, "reason": "不是 Markdown 文件"})
                continue
            dest = _unique_path(safe_join(target_root, rel))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(f.get("data") or b"")
            added.append(os.path.relpath(dest, pdir).replace(os.sep, "/"))
        if added:
            self.touch(pdir)
        return {"added": added, "skipped": skipped, "count": len(added)}

    def save_assets(self, pdir: str, files, subdir: str = "assets") -> dict:
        target_root = safe_join(pdir, subdir) if subdir else pdir
        os.makedirs(target_root, exist_ok=True)
        saved = []
        for f in files or []:
            rel = (f.get("name") or "").replace("\\", "/").lstrip("/")
            if not rel or ".." in rel.split("/"):
                continue
            ext = os.path.splitext(rel)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            dest = _unique_path(safe_join(target_root, os.path.basename(rel)))
            with open(dest, "wb") as fh:
                fh.write(f.get("data") or b"")
            saved.append(os.path.relpath(dest, pdir).replace(os.sep, "/"))
        if saved:
            self.touch(pdir)
        return {"saved": saved}

    def search(self, pdir: str, query: str, limit: int = 60):
        q = (query or "").strip().lower()
        if not q:
            return []
        hits = []
        for d in self.scan_docs(pdir):
            try:
                text = read_text_file(d["_abs"])
            except Exception:
                continue
            low = text.lower()
            idx = low.find(q)
            if idx < 0 and q not in d["name"].lower():
                continue
            snippet = ""
            if idx >= 0:
                start = max(0, idx - 60)
                snippet = text[start:idx + 90].replace("\n", " ").strip()
                snippet = ("…" if start > 0 else "") + snippet + "…"
            hits.append({
                "id": d["id"], "name": d["name"], "dir": d["dir"],
                "mtime": d["mtime"], "snippet": snippet,
                "in_title": q in d["name"].lower(),
            })
            if len(hits) >= limit:
                break
        hits.sort(key=lambda h: (not h["in_title"], -(h["mtime"] or "").__len__()))
        return hits

    # -- rendering -------------------------------------------------------
    def render_doc(self, pdir: str, did: str, pid: str = "", inline_assets: bool = False):
        info = self.doc_info(pdir, did)
        raw = self.read_doc(pdir, did)
        info['revision'] = self._versions[info['_abs']]
        theme = read_ui_settings(self.root).get("theme", "light")
        frag, meta, engine = R.render_markdown(
            raw, formula=formula_resolver(self.root, theme=theme, inline=inline_assets))
        frag = _rewrite_links(frag, pdir, info, pid=pid or info["id"].split("/")[0], inline_assets=inline_assets)
        title = meta.get("title") or info["name"]
        stats = {
            "chars": len(raw),
            "lines": raw.count("\n") + 1,
            "headings": len(re.findall(r"^#{1,6}\s", raw, re.M)),
            "words": len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", raw)),
        }
        return {
            "info": {k: v for k, v in info.items() if not k.startswith("_")},
            "raw": raw,
            "html": frag,
            "meta": meta,
            "engine": engine,
            "title": title,
            "stats": stats,
        }

    def export_doc_html(self, pdir: str, did: str, dest: str, pid: str = "") -> str:
        res = self.render_doc(pdir, did, pid=pid, inline_assets=True)
        page = R.build_page(
            title=res["title"], body_html=res["html"], meta=res["meta"],
            subtitle="导出自 %s / %s" % (os.path.basename(pdir), did),
            doc_path=did, engine=res["engine"], standalone=True,
        )
        page = _inject_theme_toggle(page)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._atomic_write(dest, page)
        return dest

    def export_project_html(self, pid: str, dest: str) -> str:
        pdir = self.require_project(pid)
        meta = self.read_meta(pdir)
        docs = self.scan_docs(pdir)
        parts = []
        toc = []
        for d in docs:
            res = self.render_doc(pdir, d["id"], pid=pid, inline_assets=True)
            anchor = "doc-" + re.sub(r"[^\w\u4e00-\u9fff]+", "-", d["id"]).strip("-")
            toc.append('<li><a href="#%s">%s</a> <span class="toc-dir">%s</span></li>'
                       % (anchor, html.escape(res["title"]), html.escape(d["dir"] or "根目录")))
            parts.append(
                '<section class="export-doc" id="%s"><div class="export-sep">%s</div>%s</section>'
                % (anchor, html.escape(d["id"]), res["html"])
            )
        body = (
            '<nav class="export-toc"><h2>目录</h2><ul>%s</ul></nav>%s'
            % ("".join(toc), "".join(parts))
        ) if docs else "<p>这个项目里还没有文档。</p>"
        page = R.build_page(
            title=meta.get("name") or pid, body_html=body, meta={"docs": str(len(docs))},
            subtitle="项目合集 · 导出于 %s" % now_iso(), doc_path=pid,
            engine=R.ENGINE_NAME, standalone=True,
        )
        page = _inject_theme_toggle(page)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._atomic_write(dest, page)
        return dest

    def open_in_explorer(self, path: str, select: bool = False):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        if os.name != "nt":
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path if os.path.isdir(path) else os.path.dirname(path)])
            return
        if select and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["explorer", os.path.normpath(path if os.path.isdir(path) else os.path.dirname(path))])


def _transfer(src: str, dest: str, move: bool):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if move:
        shutil.move(src, dest)
    else:
        shutil.copy2(src, dest)


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s (%d)%s" % (stem, n, ext)):
        n += 1
    return "%s (%d)%s" % (stem, n, ext)


def _export_guard(dest: str, payload: dict):
    """Repeated exports must be confirmed instead of silently replaced (5.5).

    ``overwrite`` is ``True`` when the path came from the OS save dialog (which
    already asked), or the revision the caller was shown for the in-app prompt.
    """
    if not os.path.isfile(dest):
        return None
    current = D.revision(dest)
    confirmed = payload.get("overwrite")
    if confirmed is True or (isinstance(confirmed, str) and confirmed and confirmed == current):
        return None
    return {"ok": False, "conflict": True, "revision": current, "path": dest,
            "error": "导出目标已存在，确认后覆盖：%s" % dest}


def _send_to_recycle_bin(path: str) -> bool:
    """Move to the Windows Recycle Bin via the shell so deletes are undoable."""
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = os.path.abspath(path) + "\x00\x00"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return res == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


# --------------------------------------------------------------------------
# loose documents: view / edit a .md that lives outside any project
# --------------------------------------------------------------------------

RECENT_FILE = "recent.json"
RECENT_LIMIT = 40
#: a single loose document is capped so a huge file cannot freeze the界面
LOOSE_MAX_BYTES = 8 * 1024 * 1024


class LooseDocs:
    """Documents opened straight from disk, without importing them.

    拖进来的 / 双击打开的 .md 属于这一类：软件只记住它的绝对路径，
    阅读和编辑都直接作用在**原文件**上，不会复制进工作区。

    ``id`` 就是规范化后的绝对路径；草稿（还没保存的新文档）用
    ``draft:<n>`` 这样的临时 id，用户选择保存位置后才绑定真实路径。
    """

    def __init__(self, ws: "Workspace"):
        self.ws = ws
        self.path = os.path.join(ws.root, RECENT_FILE)
        self._drafts = {}     # draft id -> {"text": str, "name": str}
        self._draft_seq = 0
        self._opened = set()

    def authorized(self, did):
        if did.startswith('draft:'): return did in self._drafts
        return self._key(os.path.realpath(did)) in self._opened or any(
            self._key(os.path.realpath(i['path'])) == self._key(os.path.realpath(did)) for i in self._read())

    def require_opened(self, did):
        if not self.authorized(did):
            raise PermissionError('此文件尚未授权，请使用“打开本地文件”选择')

    # -- recent list -----------------------------------------------------
    def _read(self) -> list:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        if isinstance(data, dict):
            data = data.get("recent", [])
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if isinstance(item, dict) and item.get("path"):
                out.append(item)
        return out

    def _write(self, items: list):
        self.ws._atomic_write(self.path, json.dumps(items[:RECENT_LIMIT],
                                                    ensure_ascii=False, indent=2))

    def list_recent(self) -> list:
        """Recent entries, newest first, annotated with existence on disk."""
        out = []
        for item in self._read():
            path = item["path"]
            exists = os.path.isfile(path)
            entry = {
                "id": path,
                "path": path,
                "name": item.get("name") or os.path.splitext(os.path.basename(path))[0],
                "dir": os.path.dirname(path),
                "mtime": item.get("mtime", ""),
                "missing": not exists,
                "size": item.get("size", 0),
            }
            if exists:
                try:
                    stat = os.stat(path)
                    entry["size"] = stat.st_size
                    entry["mtime"] = human_time(stat.st_mtime)
                except OSError:
                    pass
            out.append(entry)
        return out

    def forget(self, path: str) -> bool:
        path = self._key(path)
        items = [i for i in self._read() if self._key(i["path"]) != path]
        self._write(items)
        return True

    def _remember(self, path: str, name: str = "", size: int = 0):
        path = os.path.abspath(path)
        items = [i for i in self._read() if self._key(i["path"]) != self._key(path)]
        items.insert(0, {"path": path, "name": name or os.path.splitext(os.path.basename(path))[0],
                         "size": size, "mtime": human_time(os.path.getmtime(path)) if os.path.exists(path) else ""})
        self._write(items)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def folder_of(self, did: str) -> str:
        """Folder a loose document lives in (drafts fall back to the workspace)."""
        if not did:
            raise KeyError("没有给出文档")
        if did.startswith("draft:"):
            return self.ws.root
        path = os.path.abspath(did)
        if not os.path.isfile(path):
            # a document that was recently open may have been moved away; the
            # entry stays usable for its folder as long as the folder exists
            parent = os.path.dirname(path)
            if not os.path.isdir(parent):
                raise FileNotFoundError("文件已经不在了：%s" % path)
            return parent
        return os.path.dirname(path)

    # -- opening ---------------------------------------------------------
    def open_path(self, raw_path: str) -> dict:
        """Open (or re-open) one file from disk. Raises on anything unusable."""
        if not raw_path or not str(raw_path).strip():
            raise ValueError("没有给出文件路径")
        path = os.path.abspath(os.path.expanduser(str(raw_path).strip().strip('"')))
        if os.path.isdir(path):
            raise IsADirectoryError("这是一个文件夹，请选择 .md 文件：%s" % path)
        if not os.path.exists(path):
            raise FileNotFoundError("找不到文件：%s" % path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in DOC_EXTS:
            raise ValueError("只支持 Markdown 文件（%s），收到：%s" % ("/".join(DOC_EXTS), os.path.basename(path)))
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > LOOSE_MAX_BYTES:
            raise ValueError("文件超过 %d MB，请用专业编辑器打开" % (LOOSE_MAX_BYTES // 1024 // 1024))
        data = D.snapshot(path)
        text = data['text']
        self._remember(path, size=size)
        self._opened.add(self._key(os.path.realpath(path)))
        info, _body = self._load(path, text)
        info.update(text=text, revision=data['revision'])
        self.ws._versions[os.path.realpath(path)] = data['revision']
        return info

    def new_draft(self, name: str = "未命名文档") -> dict:
        """A brand new document that has no location on disk yet."""
        self._draft_seq += 1
        did = "draft:" + secrets.token_hex(16)
        self._drafts[did] = {"text": "", "name": name or "未命名文档"}
        info, _body = self._load(did)
        return info

    def describe(self, did: str, text: "str | None" = None) -> dict:
        """Read a loose document and render it for the reading view."""
        info, raw = self._load(did, text)
        frag, meta, engine = R.render_markdown(raw)
        folder = os.path.dirname(info["path"]) if info.get("path") else self.ws.root
        frag = _rewrite_loose_links(frag, folder, info, did)
        title = meta.get("title") or info["name"]
        return {
            "kind": "loose",
            "info": info,
            "raw": raw,
            "html": frag,
            "meta": meta,
            "engine": engine,
            "title": title,
            "unsaved": not info.get("path"),
            "stats": {
                "chars": len(raw),
                "lines": raw.count("\n") + 1,
                "headings": len(re.findall(r"^#{1,6}\s", raw, re.M)),
                "words": len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", raw)),
            },
        }

    def _load(self, did: str, text: "str | None" = None):
        if did.startswith("draft:"):
            draft = self._drafts.get(did)
            if draft is None:
                raise KeyError("草稿已经不存在：%s" % did)
            body = draft["text"] if text is None else text
            info = {
                "id": did, "name": draft["name"], "file": draft["name"] + ".md",
                "dir": "", "path": "", "size": len(body.encode("utf-8")),
                "mtime": "", "mtime_ts": 0, "draft": True,
            }
            return info, body
        path = os.path.abspath(did)
        if not os.path.isfile(path):
            raise FileNotFoundError("文件已经不在了：%s（可能被移动或删除，可从最近列表移除）" % path)
        data = D.snapshot(path)
        body = data['text'] if text is None else text
        try:
            stat = os.stat(path)
        except OSError:
            stat = None
        info = {
            "id": path, "name": os.path.splitext(os.path.basename(path))[0],
            "revision": data['revision'],
            "file": os.path.basename(path), "dir": os.path.dirname(path),
            "path": path, "size": stat.st_size if stat else len(body.encode("utf-8")),
            "mtime": human_time(stat.st_mtime) if stat else "", "mtime_ts": stat.st_mtime if stat else 0,
            "draft": False,
        }
        return info, body

    # -- saving ----------------------------------------------------------
    def save(self, did: str, text: str, path: "str | None" = None, expected=None, overwrite=None) -> dict:
        """Write straight back to the original file (or to ``path`` for drafts)."""
        body = (text or "").replace("\r\n", "\n")
        if did.startswith("draft:"):
            draft = self._drafts.get(did)
            if draft is None:
                raise KeyError("草稿已经不存在：%s" % did)
            if not path:
                raise ValueError("新文档还没有保存位置，请先选择保存路径")
            if os.path.isdir(path):
                path = os.path.join(path, _safe_filename(draft["name"]) + ".md")
            if os.path.splitext(path)[1].lower() not in DOC_EXTS:
                path += ".md"
            D.write_checked(path, body, 'missing', overwrite, os.path.join(self.ws.root, '.recovery', 'conflicts'))
            self._drafts.pop(did, None)
            self._remember(path, name=os.path.splitext(os.path.basename(path))[0])
            return self.describe(path, body)
        target = os.path.abspath(did)
        if path and self._key(path) != self._key(target):
            # "另存为"：写到新位置，并继续跟踪新文件
            dest = path
            if os.path.isdir(dest):
                dest = os.path.join(dest, os.path.basename(target))
            if os.path.splitext(dest)[1].lower() not in DOC_EXTS:
                dest += ".md"
            D.write_checked(dest, body, 'missing', overwrite, os.path.join(self.ws.root, '.recovery', 'conflicts'))
            self._remember(dest, name=os.path.splitext(os.path.basename(dest))[0])
            return self.describe(dest, body)
        if not os.path.isfile(target):
            raise FileNotFoundError("原文件已不存在，请用“另存为”选择新位置：%s" % target)
        base = expected if expected is not None else self.ws._versions.get(os.path.realpath(target), D.revision(target))
        self.ws._versions[os.path.realpath(target)] = D.write_checked(target, body, base, overwrite,
                                                 os.path.join(self.ws.root, '.recovery', 'conflicts'))
        self._remember(target, name=os.path.splitext(os.path.basename(target))[0], size=len(body.encode("utf-8")))
        return self.describe(target, body)

    def save_as_dialog(self, suggest: str = "未命名文档.md") -> str:
        """Ask Windows for a destination; empty string means the user cancelled."""
        return _dialog_save(suggest)

    # -- promote into a project -----------------------------------------
    def add_to_project(self, did: str, pdir: str, subdir: str = "", move: bool = False) -> dict:
        """Copy the loose document into a project as a normal managed document."""
        info, body = self._load(did)
        target_dir = safe_join(pdir, subdir) if subdir else pdir
        os.makedirs(target_dir, exist_ok=True)
        stem = info["name"] or "未命名文档"
        ext = os.path.splitext(info["file"])[1] or ".md"
        dest = _unique_path(os.path.join(target_dir, _safe_filename(stem) + ext))
        self.ws._atomic_write(dest, body)
        if move and info.get("path") and os.path.isfile(info["path"]):
            try:
                os.remove(info["path"])
                self.forget(info["path"])
            except OSError:
                pass
        self.ws.touch(pdir)
        rel = os.path.relpath(dest, pdir).replace(os.sep, "/")
        managed = self.ws.doc_info(pdir, rel)
        managed.pop("_abs", None)
        return managed


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", (name or "").strip()).strip(". ")
    return cleaned[:80] or "未命名文档"


def _rewrite_loose_links(frag: str, folder: str, info: dict, did: str) -> str:
    """Point images / sibling links of a loose document at the local file API."""
    pack = urllib.parse.quote(did, safe="")

    def repl(match):
        head, url, tail = match.group(1), match.group(2), match.group(3)
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|data:)", url, re.I):
            return match.group(0)
        anchor = "#" + url.split("#", 1)[1] if "#" in url else ""
        clean = urllib.parse.unquote(url.split("#")[0].split("?")[0]).replace("\\", "/")
        if not clean:
            return match.group(0)
        target = os.path.abspath(os.path.join(folder, clean.lstrip("/")))
        ext = os.path.splitext(target)[1].lower()
        if ext in IMAGE_EXTS and os.path.isfile(target):
            return head + "/api/localfile?doc=%s&p=%s" % (
                pack, urllib.parse.quote(clean.lstrip("/"))) + tail
        if ext in DOC_EXTS and os.path.isfile(target):
            return '%s#doc=%s" data-loose-path="%s"' % (head, urllib.parse.quote(target), urllib.parse.quote(target))
        return match.group(0)

    return _TAG_REF_RE.sub(repl, frag)


# --------------------------------------------------------------------------
# html post-processing
# --------------------------------------------------------------------------

_TAG_REF_RE = re.compile(r'(<(?:img|source|video|audio|a|iframe)\b[^>]*?\b(?:src|href)=")([^"]+)(")', re.I)


def _rewrite_links(frag: str, pdir: str, info: dict, pid: str = "", inline_assets: bool = False) -> str:
    doc_dir = os.path.dirname(info["id"])
    doc_folder = os.path.join(pdir, *[p for p in doc_dir.split("/") if p]) if doc_dir else pdir
    pdir_abs = os.path.abspath(pdir)

    def quote(s: str) -> str:
        return urllib.parse.quote(s.replace(os.sep, "/"))

    def repl(m):
        head, url, tail = m.group(1), m.group(2), m.group(3)
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|data:)", url, re.I):
            return m.group(0)
        anchor = "#" + url.split("#", 1)[1] if "#" in url else ""
        clean = urllib.parse.unquote(url.split("#")[0].split("?")[0]).replace("\\", "/")
        if not clean:
            return m.group(0)
        base = pdir if clean.startswith("/") else doc_folder
        target = os.path.abspath(os.path.join(base, clean.lstrip("/")))
        if not (target == pdir_abs or target.startswith(pdir_abs + os.sep)):
            return m.group(0)  # outside the project: leave the original link alone
        ext = os.path.splitext(target)[1].lower()
        rel = os.path.relpath(target, pdir).replace(os.sep, "/")

        # other markdown files open inside the reader
        if ext in DOC_EXTS:
            if not os.path.isfile(target):
                return m.group(0)
            return '%s#doc=%s" data-md-doc="%s"' % (head, quote(rel), quote(rel))

        # images: inline them for standalone exports, otherwise route through the API
        if ext in IMAGE_EXTS:
            if inline_assets and os.path.isfile(target):
                data = _data_uri(target)
                if data:
                    return head + data + tail
            return head + "/api/file?pid=%s&p=%s" % (quote(pid), quote(rel)) + anchor + tail

        if os.path.exists(target):
            api = "/api/file?pid=%s&p=%s" % (quote(pid), quote(rel))
            return head + api + anchor + tail
        return m.group(0)

    return _TAG_REF_RE.sub(repl, frag)


def _rewrite_folder_links(frag: str, root: str, info: dict, rid: str) -> str:
    """把原文件夹里的图片/同级文档指向本地文件接口。

    相对路径按**已授权的根目录**解析，并且在生成链接前确认目标没有越出根目录，
    真正的读取在 ``/api/localfile`` 里还会再校验一次。
    """
    folder = os.path.dirname(info.get("_abs") or "")
    pack = urllib.parse.quote(rid, safe="")

    def repl(match):
        head, url, tail = match.group(1), match.group(2), match.group(3)
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|data:)", url, re.I):
            return match.group(0)
        anchor = "#" + url.split("#", 1)[1] if "#" in url else ""
        clean = urllib.parse.unquote(url.split("#")[0].split("?")[0]).replace("\\", "/")
        if not clean:
            return match.group(0)
        target = os.path.abspath(os.path.join(folder, clean.lstrip("/")))
        if not is_within(root, target) or not os.path.isfile(target):
            return match.group(0)
        rel = os.path.relpath(target, root).replace(os.sep, "/")
        ext = os.path.splitext(target)[1].lower()
        if ext in IMAGE_EXTS:
            return head + "/api/localfile?root=%s&p=%s" % (
                pack, urllib.parse.quote(rel)) + tail
        if ext in DOC_EXTS:
            return '%s#root=%s&doc=%s" data-folder-doc="%s"' % (
                head, pack, urllib.parse.quote(rel), urllib.parse.quote(rel))
        return match.group(0)

    return _TAG_REF_RE.sub(repl, frag)


def _data_uri(path: str):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if mime.startswith("image/"):
            return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))
    except Exception:
        return None
    return None


#: 公式图片的缓存目录（在工作区里，不碰用户资料）与默认字号。
FORMULA_DIRNAME = "formula-cache"
FORMULA_SIZE = 17
#: 交给导出插件的公式字号（像素）：插件按 0.75 折算成点，与正文 11pt 相当。
FORMULA_PRINT_SIZE = 16
#: 打印用的公式配色：浅底深字，与阅读主题无关。
FORMULA_PRINT_THEME = "print"


def formula_cache_dir(root: str) -> str:
    return os.path.join(root, FORMULA_DIRNAME)


def formula_resolver(root: str, *, theme: str = "light", size: int = FORMULA_SIZE,
                     scale: float = 1.0, inline: bool = False):
    """给 ``render.render_markdown`` 用的公式解析器（F05）。

    ``inline=True``（导出）时把图片编成 data URI，导出的 HTML 断网也能看；否则给一个
    指向宿主 ``/api/formula`` 的地址，由阅读视图按需取图。两种方式用的是**同一份
    缓存**（键包含表达式、字号、缩放、主题与渲染器版本）。
    """
    from . import formula as FX

    def resolve(tex: str, display: bool):
        color, background = FX.colors_for(theme)
        picture = FX.cached(formula_cache_dir(root), tex, size=size, display=display,
                            theme=theme, color=color, background=background, scale=scale)
        if not picture.get("ok"):
            return {"ok": False, "reason": picture.get("reason", "")}
        if inline:
            src = "data:image/png;base64," + base64.b64encode(picture["png"]).decode("ascii")
        else:
            query = urllib.parse.urlencode({"tex": tex, "size": int(size), "theme": theme,
                                            "scale": "%g" % float(scale),
                                            "display": "1" if display else "0"})
            src = "/api/formula?" + query
        return {"ok": True, "src": src, "width": picture["width"], "height": picture["height"]}

    return resolve



def _inject_theme_toggle(page: str) -> str:
    """Add a floating theme toggle + print button to exported pages."""
    extra = """
<style>
  .export-bar{position:fixed;right:18px;bottom:18px;display:flex;gap:8px;z-index:99}
  .export-bar button{border:1px solid var(--border);background:var(--bg-soft);color:var(--fg);
    border-radius:8px;padding:7px 14px;font-size:13px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.12)}
  .export-toc{border:1px solid var(--border);border-radius:10px;background:var(--bg-soft);padding:12px 18px;margin:24px 0}
  .export-toc h2{margin:.2em 0 .4em;font-size:1.1em;border:0;padding:0}
  .export-toc ul{margin:0;padding-left:1.2em}
  .toc-dir{color:var(--fg-muted);font-size:.82em;font-family:var(--font-mono)}
  .export-sep{margin:56px 0 12px;padding-top:12px;border-top:2px dashed var(--border);
    color:var(--fg-muted);font-family:var(--font-mono);font-size:12px}
</style>
<div class="export-bar">
  <button type="button" onclick="document.documentElement.dataset.theme='light'">明亮</button>
  <button type="button" onclick="document.documentElement.dataset.theme='dark'">夜间</button>
  <button type="button" onclick="document.documentElement.dataset.theme='eye'">护眼</button>
  <button type="button" onclick="window.print()">打印 / 存 PDF</button>
</div>
<script>
  try{ if(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.dataset.theme='dark'; }catch(e){}
</script>
"""
    return page.replace("</body>", extra + "</body>")


# ==========================================================================
# HTTP server
# ==========================================================================

class Api:
    #: 'server' -> the app owns a desktop window, so OS file dialogs make sense.
    #: 'browser' -> head-less / browser tab, the UI falls back to upload/download.
    mode = "server"

    def __init__(self, ws: Workspace, webui_dir: str):
        self.ws = ws
        self.webui = webui_dir
        self.loose = LooseDocs(ws)
        self.token = secrets.token_urlsafe(32)
        self.mode = 'server'
        self.selected_paths = set()

    # -- dispatch --------------------------------------------------------
    def get(self, path: str, q: dict):
        ws = self.ws
        if path in ("/", "/index.html"):
            with open(os.path.join(self.webui,'index.html'),encoding='utf-8') as stream: page=stream.read()
            page=page.replace('<head>', '<head><meta name="mdreader-session" content="'+self.token+'">')
            return _Raw(200,'text/html; charset=utf-8',page.encode('utf-8'),
                        {'Set-Cookie':'mdreader_session='+self.token+'; HttpOnly; SameSite=Strict; Path=/'})
        if path == "/app.js":
            return _file_response(os.path.join(self.webui, "app.js"))
        if path == "/app.css":
            return _file_response(os.path.join(self.webui, "app.css"))
        if path == "/api/mode":
            return {"ok": True, "mode": self.mode}
        if path == "/api/recovery":
            # Snapshots are shared with the desktop window; the text only travels
            # when one specific snapshot is asked for.
            records = ws.recovery.list()
            wanted = q.get("key")
            if wanted:
                match = next((r for r in records if r.get("key") == wanted), None)
                if match is None:
                    raise KeyError("快照不存在：%s" % wanted)
                return {"ok": True, "records": [match]}
            return {"ok": True, "records": [
                {k: v for k, v in record.items() if k != "text"} for record in records]}
        if path == "/api/settings":
            # One theme for both entries: the desktop window and the page read
            # and write the same workspace file, so they cannot drift apart.
            return {"ok": True, **read_ui_settings(ws.root)}
        # ---- 公式图片（F05）：按缓存键取图，键含表达式/字号/缩放/主题/渲染器版本 ----
        if path == "/api/formula":
            return self._formula_image(q)
        # ---- 插件（P01）：清单、状态、可用命令 ------------------------------
        if path == "/api/plugins":
            return {"ok": True, **ws.plugins.status()}
        if path == "/api/plugins/task":
            return {"ok": True, "task": ws.plugins.tasks.poll(
                q.get("id") or "", wait=min(20.0, float(q.get("wait") or 0)))}
        if path == "/api/state":
            return {"ok": True, "app": APP_NAME, "version": APP_VERSION,
                    "engine": R.ENGINE_NAME, "workspace": ws.root,
                    "projects": ws.list_projects(),
                    "root_folders": ws.folders.roots(),
                    "recent": self.loose.list_recent()}
        # ---- 用户原文件夹（F01）：树、刷新、身份都是「根 id + 相对路径」 -----
        if path == "/api/folder":
            rid = q.get("root") or ""
            tree = ws.folders.scan(rid, force=q.get("refresh") == "1")
            return {"ok": True, "folder": ws.folders.describe(rid),
                    "docs": [{k: v for k, v in d.items() if k != "_abs"} for d in tree["docs"]],
                    "dirs": tree["dirs"], "counts": tree["counts"],
                    "status": tree["status"], "changed": tree["changed"],
                    "skipped_links": tree["skipped_links"], "truncated": tree["truncated"],
                    "watch": ws.folders.watch().stats()}
        if path == "/api/folder/doc":
            rid, did = q.get("root") or "", q.get("doc") or ""
            info = ws.folders.doc_info(rid, did)
            raw = ws.folders.read_doc(rid, did)
            data = self._render_folder_doc(info, raw)
            # 桌面端与网页端都用「绝对路径」作为已打开文档的身份，两个入口因此
            # 共用同一份标签/最近列表，不各自维护一套
            data["path"] = info["_abs"]
            return {"ok": True, **data}
        if path == "/api/loose":
            self.loose.require_opened(q['doc'])
            return {"ok": True, **self.loose.describe(q["doc"])}
        if path == "/api/localfile":
            return self._local_file(q)
        if path == "/api/project":
            pdir = ws.require_project(q["pid"])
            meta = ws.read_meta(pdir)
            docs = ws.scan_docs(pdir)
            for d in docs:
                d.pop("_abs", None)
            return {"ok": True, "project": meta, "docs": docs, "dirs": ws.list_dirs(pdir)}
        if path == "/api/doc":
            pdir = ws.require_project(q["pid"])
            data = ws.render_doc(pdir, q["doc"], pid=q["pid"])
            # Same identity the desktop window uses, so both entries share one
            # recovery snapshot for the same file.
            data["identity"] = ws.doc_info(pdir, q["doc"])["_abs"]
            return {"ok": True, **data}
        if path == "/api/search":
            pdir = ws.require_project(q["pid"])
            return {"ok": True, "hits": ws.search(pdir, q.get("q", ""))}
        if path == "/api/file":
            pdir = ws.require_project(q["pid"])
            full = safe_join(pdir, q["p"])
            if not os.path.isfile(full):
                raise KeyError("文件不存在")
            return _resource_response(full, download=q.get("dl") == "1")
        raise KeyError("未知接口：%s" % path)

    # -- 公式（F05）--------------------------------------------------------
    def _formula_image(self, q: dict):
        """把公式渲染成 PNG 返回给页面。参数与缓存键一一对应，所以可以直接缓存。"""
        from . import formula as FX
        tex = q.get("tex") or ""
        if not tex:
            raise ValueError("缺少公式内容")
        theme = q.get("theme") or read_ui_settings(self.ws.root).get("theme", "light")
        try:
            size = int(float(q.get("size") or FORMULA_SIZE))
            scale = float(q.get("scale") or 1.0)
        except ValueError:
            raise ValueError("公式字号/缩放参数不对")
        display = q.get("display") == "1"
        color, background = FX.colors_for(theme)
        picture = FX.cached(formula_cache_dir(self.ws.root), tex, size=size, display=display,
                            theme=theme, color=color, background=background, scale=scale)
        if not picture.get("ok"):
            raise ValueError(picture.get("reason") or "这个公式暂时画不出来")
        return _Raw(200, "image/png", picture["png"],
                    {"Cache-Control": "private, max-age=86400"})

    def _render_folder_doc(self, info: dict, raw: str) -> dict:
        """渲染原文件夹里的一份文档，读法与其他入口一致。"""
        rid = info["root"]
        folder_root = self.ws.folders.require(rid)["path"]
        theme = read_ui_settings(self.ws.root).get("theme", "light")
        frag, meta, engine = R.render_markdown(raw, formula=formula_resolver(self.ws.root,
                                                                            theme=theme))
        frag = _rewrite_folder_links(frag, folder_root, info, rid)
        title = meta.get("title") or info["name"]
        return {
            "kind": "folder",
            "info": dict({k: v for k, v in info.items() if k != "_abs"},
                         revision=D.revision(info["_abs"])),
            "identity": info["_abs"],
            "raw": raw,
            "html": frag,
            "meta": meta,
            "engine": engine,
            "title": title,
            "stats": {
                "chars": len(raw),
                "lines": raw.count("\n") + 1,
                "headings": len(re.findall(r"^#{1,6}\s", raw, re.M)),
                "words": len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", raw)),
            },
        }

    # ---- 插件（P01）：宿主侧的授权、快照与最终提交 ----------------------
    def _plugin_upload(self, payload: dict) -> str:
        """网页端上传的插件包先落到工作区的 incoming 目录，再走同一套检查。"""
        try:
            raw = base64.b64decode(payload.get("b64") or "", validate=True)
        except Exception as exc:
            raise ValueError("插件包内容不是合法的 base64：%s" % exc) from exc
        if len(raw) > PL.MAX_ARCHIVE_BYTES:
            raise ValueError("插件包超过上限（%d MB）" % (PL.MAX_ARCHIVE_BYTES // (1024 * 1024)))
        folder = os.path.join(self.ws.plugins.plugins_dir, "incoming")
        os.makedirs(folder, exist_ok=True)
        name = slugify(str(payload.get("name") or "plugin"), "plugin")[:60] + ".zip"
        target = os.path.join(folder, name)
        atomic_write(target, raw)
        return target

    def _plugin_drop_upload(self, archive: str):
        """上传的临时包在安装结束后删掉；用户自己选的路径不动。"""
        try:
            if archive and is_within(os.path.join(self.ws.plugins.plugins_dir, "incoming"), archive):
                os.remove(archive)
        except OSError:
            pass

    def _first_command(self, capability: str) -> str:
        wanted = [c for c in self.ws.plugins.commands() if not capability or c["capability"] == capability]
        if not wanted:
            rows = [r for r in self.ws.plugins.list_plugins()
                    if capability in (r.get("capabilities") or [])]
            reason = rows[0]["reason"] if rows else "还没有安装能提供这个能力的插件"
            raise PL.PluginError("没有可用的插件命令：%s" % reason)
        return wanted[0]["command"]

    def _check_revision(self, path: str, claimed) -> str:
        """调用方说“我看到的是这一版”时，先跟磁盘对一次；不一致就拒绝。"""
        current = D.revision(path)
        if claimed and str(claimed) != current:
            raise PL.StaleResult("这份文档在磁盘上已经变了，请重新加载后再发起插件操作")
        return current

    def _plugin_document(self, payload: dict) -> dict:
        """文档身份 + 可以写附件的位置 + 相对链接前缀；只认已授权范围。"""
        ws = self.ws
        pid = payload.get("pid") or ""
        if pid:
            pdir = ws.require_project(pid)
            info = ws.doc_info(pdir, payload.get("doc") or "")
            path = info["_abs"]
            assets = os.path.join(pdir, "assets")
        else:
            path = os.path.abspath(str(payload.get("doc") or ""))
            if not os.path.isfile(path):
                raise ValueError("请先用「另存为」把当前文档保存到磁盘，再用插件插入图片")
            allowed = any(os.path.normcase(os.path.abspath(item.get("path") or "")) == os.path.normcase(path)
                          for item in self.loose.list_recent())
            if not allowed:
                for root in ws.folders.roots():
                    if is_within(root["path"], path):
                        allowed = True
                        break
            if not allowed:
                raise PermissionError("这个文档不在已授权范围内，插件不会往它的目录里写东西")
            assets = os.path.join(os.path.dirname(path), "assets")
        prefix = os.path.relpath(assets, os.path.dirname(path)).replace(os.sep, "/") + "/"
        return {"identity": path, "assets": assets, "prefix": prefix,
                "revision": self._check_revision(path, payload.get("revision")),
                "name": os.path.basename(path)}

    def _plugin_snapshot(self, payload: dict) -> dict:
        """导出用的不可变快照：优先用界面上的当前编辑内容，否则读磁盘版本。"""
        return self._export_plan(payload)["snapshot"]

    # ---- 统一导出服务（F06）：快照、预检、目标确认都在这里 -----------------
    def _export_plan(self, payload: dict) -> dict:
        """先把“导出什么、有没有问题、写到哪”算清楚，再决定要不要真的跑插件。

        两端（桌面与网页）都调它，所以阻止条件与提示文字只有一套；这里的任何一步
        都不写用户文件。
        """
        ws = self.ws
        # 预检不需要先选格式：插件还没装/没启用时也要能告诉用户“这篇文档有什么问题”
        command = None
        wanted = payload.get("command") or ""
        try:
            wanted = wanted or self._first_command(PL.CAP_EXPORT)
            command = ws.plugins.find_command(wanted)
        except PL.PluginError:
            command = None
        if command is not None and command["capability"] != PL.CAP_EXPORT:
            raise ValueError("这个插件命令不是导出格式：%s" % command["command"])
        context = self._document_context(payload, need_file="导出")
        path, revision, root = context["path"], context["revision"], context["root"]
        buffer, on_disk, dirty = context["buffer"], context["on_disk"], context["dirty"]
        choice = EX.choose_source(buffer is not None, dirty, str(payload.get("source") or ""))
        markdown = buffer if choice["source"] == EX.SOURCE_BUFFER and buffer is not None else on_disk
        snapshot = EX.snapshot(identity=path, markdown=markdown, revision=revision,
                               source=choice["source"], root=root)
        reader = self._resource_reader(snapshot)
        report = EX.preflight(snapshot, resource_reader=reader)
        dest = str(payload.get("dest") or "")
        if not dest and command is not None:
            stem = slugify(os.path.splitext(snapshot["name"])[0])
            dest = _unique_path(os.path.join(ws.root, "exports",
                                             "%s.%s" % (stem, command["extension"])))
        if dest and EX.same_file(dest, path):
            raise ValueError("导出目标不能是源文档本身")
        return {"command": command, "snapshot": snapshot, "preflight": report, "dest": dest,
                "conflict": EX.target_conflict(dest, payload.get("overwrite")),
                "needs_choice": choice["needs_choice"], "source": choice["source"],
                "dirty": dirty, "reader": reader,
                "options": EX.page_options(payload.get("options"))}

    def _document_context(self, payload: dict, *, need_file: str = "导出") -> dict:
        """当前文档的身份、修订、授权根目录与缓冲区（导出与链接检查共用一套判定）。

        ``need_file`` 只用在错误文案里（“请先保存再用插件导出 / 再检查链接”）。
        """
        if payload.get("pid"):
            pdir = self.ws.require_project(payload["pid"])
            info = self.ws.doc_info(pdir, payload.get("doc") or "")
            path = info["_abs"]
            revision = info.get("revision") or D.revision(path)
            root = pdir
        else:
            path = os.path.abspath(str(payload.get("doc") or ""))
            if not os.path.isfile(path):
                raise ValueError("当前文档还没有磁盘文件，请先保存再%s" % need_file)
            allowed = any(os.path.normcase(os.path.abspath(item.get("path") or "")) == os.path.normcase(path)
                          for item in self.loose.list_recent())
            if not allowed:
                allowed = any(is_within(root["path"], path) for root in self.ws.folders.roots())
            if not allowed:
                raise PermissionError("这个文档不在已授权范围内，已拒绝")
            revision = self._check_revision(path, payload.get("revision"))
            root = os.path.dirname(path)
        buffer = payload.get("markdown") if isinstance(payload.get("markdown"), str) else None
        try:
            on_disk = read_text_file(path)
        except OSError:
            on_disk = ""
        return {"path": path, "revision": revision, "root": root, "buffer": buffer,
                "on_disk": on_disk,
                "dirty": buffer is not None and buffer != on_disk}

    def _resource_reader(self, snapshot: dict):
        """把正文里的相对路径解析成**授权范围内**的真实路径（图片与本地链接共用）。"""
        root = snapshot.get("root") or snapshot.get("folder") or ""
        folder = snapshot.get("folder") or ""

        def read(source: str) -> str:
            raw = urllib.parse.unquote(urllib.parse.urlsplit(str(source)).path)
            if not raw:
                raise FileNotFoundError(source)
            candidate = os.path.abspath(os.path.join(folder, raw.replace("/", os.sep)))
            if root and not is_within(root, candidate):
                raise PermissionError(source)
            if not os.path.isfile(candidate):
                raise FileNotFoundError(source)
            return candidate

        return read

    # ---- 链接检查（F09）：只查当前这一篇，不扫全盘、不改别的文档 ------------
    def _export_formulas(self, snapshot: dict):
        """把正文里的公式渲染成 PNG 交给导出插件（F07/F08 的图片降级）。

        插件进程里没有渲染器，也不该依赖核心内部；由核心把图与映射一起放进任务的输入
        文件，插件只负责按公式源码把图贴到正确的位置。画不出来的公式**不进映射**——
        插件会照旧把它们当普通文本输出，不会丢掉内容。
        """
        from . import formula as FX
        found = FX.scan(snapshot.get("markdown") or "")
        color, background = FX.colors_for(FORMULA_PRINT_THEME)
        files, mapping = [], {}
        for entry in found:
            tex = (entry.get("tex") or "").strip()
            if entry.get("error") or not tex or tex in mapping:
                continue
            picture = FX.cached(formula_cache_dir(self.ws.root), tex, size=FORMULA_PRINT_SIZE,
                                display=bool(entry["display"]), theme=FORMULA_PRINT_THEME,
                                color=color, background=background)
            if not picture.get("ok"):
                continue
            name = "formula-%04d.png" % len(files)
            files.append({"name": name, "data": picture["png"]})
            mapping[tex] = {"name": name, "display": bool(entry["display"]),
                            "width": picture["width"], "height": picture["height"]}
        return files, mapping

    def _link_check(self, payload: dict) -> dict:
        context = self._document_context(payload, need_file="再检查链接")
        buffer = context["buffer"]
        markdown = buffer if buffer is not None else context["on_disk"]
        snapshot = EX.snapshot(identity=context["path"], markdown=markdown,
                               revision=context["revision"],
                               source=EX.SOURCE_BUFFER if buffer is not None else EX.SOURCE_DISK,
                               root=context["root"])
        report = LK.check(markdown, identify=self._resource_reader(snapshot))
        return {"ok": True, "document": context["path"], "name": snapshot["name"],
                "dirty": context["dirty"],
                "source": snapshot["source"], "source_label": EX.SOURCE_LABELS[snapshot["source"]],
                "report": report}

    def _link_relink(self, payload: dict) -> dict:
        """用户重新选了一个文件：复制到文档自己的 assets/ 里，并把正文里的引用改过去。

        只改调用方给的正文（桌面端随后按自己的撤销/保存流程落盘），只往**文档所在目录**
        里写，重名顺延不覆盖，也绝不碰其他文档。
        """
        context = self._document_context(payload, need_file="再替换链接")
        chosen = os.path.abspath(str(payload.get("path") or ""))
        if not chosen or not os.path.isfile(chosen):
            raise ValueError("选择的文件不存在：%s" % chosen)
        source = str(payload.get("source") or "")
        if not source:
            raise ValueError("没有指定要替换的引用")
        markdown = payload.get("markdown")
        if not isinstance(markdown, str):
            markdown = context["on_disk"]
        folder = os.path.dirname(context["path"])
        assets = os.path.join(folder, "assets")
        size = os.path.getsize(chosen)
        if size > 32 * 1024 * 1024:
            raise ValueError("这个文件超过 32 MB，不适合复制到文档旁边")
        extension = os.path.splitext(chosen)[1].lower()
        stem = re.sub(r'[^A-Za-z0-9_\u4e00-\u9fff-]+', '-',
                      os.path.splitext(os.path.basename(chosen))[0]).strip('-')[:40] or "file"
        os.makedirs(assets, exist_ok=True)
        target = os.path.join(assets, stem + extension)
        counter = 1
        while os.path.exists(target):
            counter += 1
            target = os.path.join(assets, "%s-%d%s" % (stem, counter, extension))
        if os.path.normcase(os.path.abspath(chosen)) != os.path.normcase(os.path.abspath(target)):
            with open(chosen, "rb") as stream:
                raw = stream.read()
            with open(target, "wb") as stream:
                stream.write(raw)
        relative = LK.relative_target(context["path"], target)
        result = LK.replace_reference(markdown, source, relative)
        if not result["ok"]:
            return {"ok": False, "reason": result["reason"], "document": context["path"]}
        return {"ok": True, "markdown": result["markdown"], "replaced": result["replaced"],
                "line": result["line"], "link": relative, "saved": target,
                "document": context["path"], "name": os.path.basename(target),
                "bytes": os.path.getsize(target)}

    def _export_check(self, payload: dict) -> dict:
        """预检接口：只算不写，界面拿它决定“返回修改 / 仍然导出 / 确认覆盖”。"""
        plan = self._export_plan(dict(payload or {}))
        snapshot = plan["snapshot"]
        command = plan["command"] or {}
        return {"command": command.get("command", ""), "plugin": command.get("plugin", ""),
                "extension": command.get("extension", ""), "dest": plan["dest"],
                "destination_exists": os.path.isfile(plan["dest"]),
                "conflict": plan["conflict"], "preflight": plan["preflight"],
                "source": plan["source"], "source_label": EX.SOURCE_LABELS[plan["source"]],
                "needs_source": plan["needs_choice"], "dirty": plan["dirty"],
                "document": snapshot["identity"], "name": snapshot["name"],
                "page": plan["options"]}

    def _plugin_wait(self, task, seconds: float) -> dict:
        view = self.ws.plugins.tasks.poll(task.id, wait=min(120.0, float(seconds)))
        if view["state"] in ("queued", "running"):
            return {"task": view, "pending": True}
        if view["state"] != "done":
            raise ValueError(view["error"] or "插件任务没有完成")
        return {"task": view, "pending": False}

    def _plugin_insert(self, payload: dict) -> dict:
        """图片插入：插件只给方案，落附件与正文提交都由这里决定。"""
        ws = self.ws
        doc = self._plugin_document(payload)
        command = payload.get("command") or self._first_command(PL.CAP_IMAGE_INSERT)
        files = []
        if payload.get("b64"):
            files = [{"name": payload.get("name") or "image.png",
                      "data": base64.b64decode(payload["b64"])}]
        elif payload.get("path"):
            files = [str(payload["path"])]
        else:
            raise ValueError("没有选择要插入的图片")
        task = ws.plugins.tasks.submit(
            command, options=payload.get("options") or {}, doc=doc["identity"],
            revision=doc["revision"], entry=payload.get("entry") or self.mode,
            input_files=files)
        outcome = self._plugin_wait(task, payload.get("wait") or 60)
        if outcome["pending"]:
            return {"pending": True, "task": outcome["task"]}
        committed = ws.plugins.tasks.commit_assets(
            task.id, doc["assets"], now_doc=doc["identity"], now_revision=D.revision(doc["identity"]))
        markdown = committed["markdown"]
        for asset, landed in zip(ws.plugins.tasks.result(task.id)["assets"], committed["assets"]):
            # 插件只知道“assets/名字”，真正的相对路径由宿主决定（项目里可能是 ../assets/）
            markdown = markdown.replace("assets/" + asset["name"], doc["prefix"] + landed["name"])
        return {"pending": False, "task": outcome["task"], "markdown": markdown,
                "assets": committed["assets"], "note": committed.get("note", ""),
                "assets_dir": doc["assets"], "document": doc["identity"]}

    def _plugin_export(self, payload: dict) -> dict:
        """导出：核心管快照、预检、覆盖确认与最终替换，插件只产临时文件。"""
        ws = self.ws
        plan = self._export_plan(payload)
        command = plan["command"]
        if command is None:
            raise PL.PluginError("没有可用的导出插件命令：请先安装并启用 PDF 或 Word 导出插件")
        snapshot = plan["snapshot"]
        report = plan["preflight"]
        dest = plan["dest"]
        if report["blocked"]:
            return {"ok": False, "preflight": report, "path": dest, "blocked": True,
                    "error": "%s：%s" % (report["summary"],
                                        "；".join(item["message"] for item in report["errors"]))}
        if plan["needs_choice"] and not payload.get("source"):
            return {"ok": False, "preflight": report, "path": dest, "needs_source": True,
                    "source": plan["source"],
                    "error": "这篇文档有未保存的修改，请先选择导出「当前编辑内容」还是「磁盘已保存版本」"}
        if report["warnings"] and not payload.get("confirm"):
            return {"ok": False, "preflight": report, "path": dest, "needs_confirm": True,
                    "error": report["summary"]}
        if plan["conflict"]:
            return {"ok": False, **plan["conflict"]}
        target_before = D.revision(dest)
        from .media import snapshot_images
        image_root = snapshot["root"] if payload.get("pid") else os.path.dirname(snapshot["identity"])
        files, resources = snapshot_images(snapshot["markdown"], snapshot["identity"], image_root)
        formula_files, formulas = self._export_formulas(snapshot)
        files.extend(formula_files)
        options = dict(plan["options"])
        options["resources"] = resources
        options["formulas"] = formulas
        options["title"] = os.path.splitext(snapshot["name"])[0]
        task = ws.plugins.tasks.submit(
            command["command"], doc=snapshot["identity"], revision=snapshot["revision"],
            entry=payload.get("entry") or self.mode, markdown=snapshot["markdown"],
            options=options, input_files=files)
        outcome = self._plugin_wait(task, payload.get("wait") or 120)
        if outcome["pending"]:
            return {"pending": True, "task": outcome["task"]}
        # 确认之后目标要是被外部换过，就重新确认，不覆盖刚出现的新版本
        if D.revision(dest) != target_before:
            return {"ok": False, "conflict": True, "revision": D.revision(dest), "path": dest,
                    "error": "导出目标在插件运行期间被改动，请确认后重试：%s" % dest}
        # 导出用的是启动时的快照：源文档之后再被编辑不影响这次导出的内容
        result = ws.plugins.tasks.commit_export(task.id, dest)
        return {"pending": False, "task": outcome["task"], "path": result["path"],
                "bytes": result["bytes"], "extension": result["extension"],
                "media_type": result["media_type"], "warnings": result["warnings"],
                "preflight": report, "source": plan["source"],
                "command": command["command"], "plugin": command["plugin"]}

    def _local_file(self, q: dict):
        """Serve an image that sits next to a loose document.

        Only files inside the loose document's own folder are reachable, and the
        folder itself comes from the server-side recent list rather than from the
        caller, so this cannot be used to read arbitrary paths.
        """
        rid = q.get("root") or ""
        if rid:
            # 原文件夹里的资源：根目录来自已授权登记表，相对路径再校验一次
            root = self.ws.folders.require(rid)["path"]
            rel = q.get("p") or ""
            full = safe_join(root, rel)
            if not os.path.isfile(full):
                raise KeyError("文件不存在：%s" % rel)
            return _resource_response(full, download=q.get("dl") == "1")
        did = q.get("doc") or ""
        self.loose.require_opened(did)
        rel = q.get("p") or ""
        folder = self.loose.folder_of(did)
        full = os.path.abspath(os.path.join(
            folder, rel.replace("/", os.sep).replace("\\", os.sep)))
        if not is_within(folder, full):
            raise ValueError("路径越界，已拒绝")
        if not os.path.isfile(full):
            raise KeyError("文件不存在：%s" % rel)
        return _resource_response(full, download=q.get("dl") == "1")

    # -- 表格与格式（F04）--------------------------------------------------
    #: 一次请求能带的正文上限。网页端把整个缓冲区发过来，7.5 MB 的长文也在范围内；
    #: 再大就直接拒绝，不要用一个“算不动的请求”把服务端拖住。
    EDIT_MAX_CHARS = 8 * 1024 * 1024

    def _edit_text(self, payload: dict) -> str:
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("缺少正文（text）")
        if len(text) > self.EDIT_MAX_CHARS:
            raise ValueError("正文超过 %d 个字符，本接口不处理这么大的文档"
                             % self.EDIT_MAX_CHARS)
        return text

    def _edit_table(self, payload: dict):
        text = self._edit_text(payload)
        op = payload.get("op") or "read"
        line = payload.get("line")
        line = int(line) if line is not None else None
        offset = int(payload.get("offset") or 0)
        if op == "read":
            return self._edit_result(TB.read(text, offset=offset, line=line))
        if op == "parse":
            parsed = TB.parse_tsv(payload.get("payload") or "")
            if not parsed["ok"]:
                raise ValueError("；".join(parsed["warnings"]) or "没有可用的表格内容")
            return {"ok": True, **parsed}
        if op == "paste":
            result = TB.paste_tsv(text, payload.get("payload") or "", offset=offset,
                                  start=payload.get("start"), end=payload.get("end"),
                                  header=bool(payload.get("header", True)))
        else:
            args = dict(payload.get("args") or {})
            result = TB.operate(text, op, offset=offset, line=line, **args)
        return self._edit_result(result)

    def _edit_format(self, payload: dict):
        text = self._edit_text(payload)
        action = payload.get("action")
        if not action:
            raise ValueError("缺少格式动作（action）")
        try:
            result = FM.apply(text, int(payload.get("start") or 0), int(payload.get("end") or 0),
                              action, level=int(payload.get("level") or 1),
                              url=payload.get("url") or "", language=payload.get("language") or "")
        except FM.FormatError as error:
            raise ValueError(str(error))
        return self._edit_result(result)

    @staticmethod
    def _edit_result(result: dict):
        """改不动就报错：网页端把 ``ok:false`` 当成普通错误提示出来，不静默失败。"""
        if not result.get("ok"):
            raise ValueError(result.get("reason") or "这一步没有可修改的内容")
        return {"ok": True, **result}

    def post(self, path: str, payload: dict):
        ws = self.ws
        act = payload.get("action") or path.rsplit("/", 1)[-1]
        if path.startswith('/api/loose/') and payload.get('doc'):
            self.loose.require_opened(payload['doc'])

        if path == "/api/project/create":
            name = payload.get("name", "")
            res = ws.create_project(name, payload.get("description", ""))
            # keep the project folder name aligned with the display name when possible
            return {"ok": True, **res}

        if path == "/api/project/rename":
            pdir = ws.require_project(payload["pid"])
            return {"ok": True, "project": ws.rename_project(payload["pid"], payload.get("name", ""))}

        if path == "/api/project/delete":
            ws.delete_project(payload["pid"], to_recycle=payload.get("recycle", True))
            return {"ok": True}

        if path == "/api/project/reveal":
            ws.open_in_explorer(ws.require_project(payload["pid"]))
            return {"ok": True}

        # ---- 用户原文件夹（F01）：打开、新建、移除记录 -----------------------
        if path == "/api/folder/open":
            raw = payload.get("path")
            if not raw:
                paths = _dialog_folder()
                if not paths:
                    return {"ok": True, "cancelled": True}
                raw = paths[0]
            info = ws.folders.add(raw)
            ws.folders.watch().start()
            tree = ws.folders.scan(info["id"], force=True)
            return {"ok": True, "folder": info,
                    "docs": [{k: v for k, v in d.items() if k != "_abs"} for d in tree["docs"]],
                    "dirs": tree["dirs"], "counts": tree["counts"], "status": tree["status"],
                    "roots": ws.folders.roots()}

        if path == "/api/folder/create":
            info = ws.folders.create_doc(payload.get("root") or "", payload.get("name") or "",
                                         payload.get("dir") or "", payload.get("content") or "",
                                         unique=bool(payload.get("unique")))
            return {"ok": True, "doc": {k: v for k, v in info.items() if k != "_abs"},
                    "path": info["_abs"]}

        if path == "/api/folder/remove":
            rid = payload.get("root") or payload.get("path") or ""
            ws.folders.forget(rid)
            # 只移除应用记录：磁盘目录与其中的文件都不动
            return {"ok": True, "roots": ws.folders.roots(),
                    "note": "已从列表移除，磁盘上的文件夹没有改动"}

        if path == "/api/folder/delete":
            # 删的是用户的本地文件：默认移入回收站，失败不改为永久删除。
            # 客户端必须先展示确认；服务端只负责校验路径、修订与执行。
            info = ws.folders.remove_doc(payload.get("root") or "", payload.get("doc") or "",
                                         to_recycle=payload.get("recycle", True) is not False,
                                         expected_revision=payload.get("expected"))
            return {"ok": True, "removed": info,
                    "note": "已移入回收站，可从回收站还原",
                    "docs": [{k: v for k, v in d.items() if k != "_abs"}
                             for d in ws.folders.scan(payload.get("root") or "")["docs"]]}

        if path == "/api/folder/reveal":
            rid = payload.get("root") or ""
            did = payload.get("doc") or ""
            root = ws.folders.require(rid)["path"]
            if did:
                ws.open_in_explorer(ws.folders.doc_info(rid, did)["_abs"], select=True)
            else:
                ws.open_in_explorer(root)
            return {"ok": True}

        # ---- loose documents: view / edit a file that lives outside the workspace
        if path == "/api/loose/open":
            paths = payload.get("paths") or ([payload["path"]] if payload.get("path") else [])
            opened, failed = [], []
            for one in paths:
                try:
                    if os.path.realpath(one) not in self.selected_paths:
                        self.loose.require_opened(one)
                    self.loose.open_path(one)
                except Exception as exc:
                    failed.append({"path": str(one), "error": str(exc)})
                else:
                    opened.append(one)
            if not opened and failed:
                raise ValueError(failed[0]["error"])
            # return ready-to-render payloads so the UI needn't ask twice
            docs = [self.loose.describe(self.loose.open_path(one)["id"]) for one in opened]
            return {"ok": True, "docs": docs, "failed": failed,
                    "recent": self.loose.list_recent(),
                    "doc": docs[0] if docs else None}

        if path == "/api/loose/draft":
            info = self.loose.new_draft(payload.get("name", "未命名文档"))
            return {"ok": True, "doc": self.loose.describe(info["id"])}

        if path == "/api/loose/save":
            res = self.loose.save(payload["doc"], payload.get("content", ""),
                                  payload.get("path") or None, payload.get('expected'), payload.get('overwrite'))
            return {"ok": True, "recent": self.loose.list_recent(), **res}

        if path == "/api/loose/saveas":
            suggested = payload.get("name") or "未命名文档.md"
            target = payload.get("path") or self.loose.save_as_dialog(suggested)
            if not target:
                return {"ok": True, "cancelled": True}
            res = self.loose.save(payload["doc"], payload.get("content", ""), target,
                                  payload.get('expected'), payload.get('overwrite'))
            return {"ok": True, "recent": self.loose.list_recent(), **res}

        if path == "/api/loose/forget":
            self.loose.forget(payload.get("path") or payload.get("doc") or "")
            return {"ok": True, "recent": self.loose.list_recent()}

        if path == "/api/loose/reveal":
            target = payload.get("path") or payload.get("doc") or ""
            if os.path.isdir(target):
                ws.open_in_explorer(target)
            else:
                ws.open_in_explorer(target, select=os.path.isfile(target))
            return {"ok": True}

        if path == "/api/loose/to_project":
            pdir = ws.require_project(payload["pid"])
            info = self.loose.add_to_project(payload["doc"], pdir,
                                             payload.get("dir", ""),
                                             bool(payload.get("move")))
            return {"ok": True, "doc": info, "recent": self.loose.list_recent()}

        if path == "/api/doc/create":
            pdir = ws.require_project(payload["pid"])
            info = ws.create_doc(pdir, payload.get("name", "新文档"),
                                 payload.get("dir", ""), payload.get("content", ""))
            info.pop("_abs", None)
            return {"ok": True, "doc": info}

        if path == "/api/doc/save":
            pdir = ws.require_project(payload["pid"])
            info = ws.save_doc(pdir, payload["doc"], payload.get("content", ""), payload.get('expected'), payload.get('overwrite'))
            info.pop("_abs", None)
            return {"ok": True, "doc": info}

        if path == "/api/doc/rename":
            pdir = ws.require_project(payload["pid"])
            info = ws.rename_doc(pdir, payload["doc"], payload.get("name", ""))
            info.pop("_abs", None)
            return {"ok": True, "doc": info}

        if path == "/api/doc/move":
            pdir = ws.require_project(payload["pid"])
            info = ws.move_doc(pdir, payload["doc"], payload.get("dir", ""))
            info.pop("_abs", None)
            return {"ok": True, "doc": info}

        if path == "/api/doc/delete":
            pdir = ws.require_project(payload["pid"])
            ws.delete_doc(pdir, payload["doc"], to_recycle=payload.get("recycle", True))
            return {"ok": True}

        if path == "/api/doc/reveal":
            pdir = ws.require_project(payload["pid"])
            ws.open_in_explorer(ws.doc_info(pdir, payload["doc"])["_abs"], select=True)
            return {"ok": True}

        if path == "/api/doc/export":
            pdir = ws.require_project(payload["pid"])
            did = payload["doc"]
            dest = payload.get("dest") or _unique_path(os.path.join(
                ws.root, "exports", "%s.html" % slugify(os.path.splitext(os.path.basename(did))[0])))
            blocked = _export_guard(dest, payload)
            if blocked:
                return blocked
            out = ws.export_doc_html(pdir, did, dest, pid=payload["pid"])
            if payload.get("reveal"):
                ws.open_in_explorer(out, select=True)
            return {"ok": True, "path": out}

        if path == "/api/project/export":
            pdir = ws.require_project(payload["pid"])
            meta = ws.read_meta(pdir)
            dest = payload.get("dest") or _unique_path(os.path.join(
                ws.root, "exports", "%s-合集.html" % slugify(meta.get("name") or payload["pid"])))
            blocked = _export_guard(dest, payload)
            if blocked:
                return blocked
            out = ws.export_project_html(payload["pid"], dest)
            if payload.get("reveal"):
                ws.open_in_explorer(out, select=True)
            return {"ok": True, "path": out}

        if path == "/api/import/paths":
            pdir = ws.require_project(payload["pid"])
            return {"ok": True, **ws.import_paths(pdir, payload.get("paths", []),
                                                  payload.get("dir", ""),
                                                  payload.get("recursive", True),
                                                  payload.get("move", False))}

        if path == "/api/import/upload":
            pdir = ws.require_project(payload["pid"])
            files = [(f.get("name"), base64.b64decode(f.get("b64") or "")) for f in payload.get("files", [])]
            return {"ok": True, **ws.import_uploads(pdir, [{"name": n, "data": d} for n, d in files],
                                                    payload.get("dir", ""))}

        if path == "/api/asset/upload":
            pdir = ws.require_project(payload["pid"])
            files = [{"name": f.get("name"), "data": base64.b64decode(f.get("b64") or "")}
                     for f in payload.get("files", [])]
            return {"ok": True, **ws.save_assets(pdir, files, payload.get("dir", "assets"))}

        if path == "/api/dialog/files":
            return {"ok": True, "paths": _dialog_files(payload.get("multi", True))}

        if path == "/api/dialog/open":
            paths = _dialog_open_files(payload.get("multi", True))
            self.selected_paths.update(os.path.realpath(p) for p in paths)
            return {"ok": True, "paths": paths}

        if path == "/api/dialog/folder":
            return {"ok": True, "paths": _dialog_folder()}

        if path == "/api/dialog/save":
            return {"ok": True, "path": _dialog_save(payload.get("name", "export.html"))}

        if path == "/api/recovery/save":
            identity = str(payload.get("identity") or "").strip()
            if not identity:
                raise ValueError("缺少身份标识，无法建立快照")
            key = ws.recovery.save(identity, str(payload.get("text") or ""),
                                   str(payload.get("path") or ""),
                                   str(payload.get("baseline") or "missing"))
            return {"ok": True, "key": key}

        if path == "/api/recovery/discard":
            ws.recovery.discard(str(payload.get("identity") or ""), payload.get("saved_text"))
            return {"ok": True}

        if path == "/api/settings":
            theme = payload.get("theme")
            if theme not in THEME_CHOICES:
                raise ValueError("主题只能是：" + "、".join(THEME_CHOICES))
            return {"ok": True, **write_ui_settings(ws.root, theme)}

        # ---- 插件（P01）：安装、启停、调用、结果提交 ------------------------
        if path == "/api/plugins/install":
            archive = self._plugin_upload(payload) if payload.get("b64") else str(payload.get("path") or "")
            try:
                result = ws.plugins.install(archive, allow_unverified=bool(payload.get("allow_unverified")))
            finally:
                self._plugin_drop_upload(archive)
            return {"ok": True, "result": result, **ws.plugins.status()}

        if path == "/api/plugins/remove":
            result = ws.plugins.uninstall(payload.get("id") or "")
            return {"ok": True, "result": result, **ws.plugins.status()}

        if path == "/api/plugins/toggle":
            pid = payload.get("id") or ""
            result = (ws.plugins.enable(pid) if payload.get("enabled")
                      else ws.plugins.disable(pid))
            return {"ok": True, "result": result, **ws.plugins.status()}

        if path == "/api/plugins/task/cancel":
            return {"ok": True, "task": ws.plugins.tasks.cancel(
                payload.get("id") or "", payload.get("reason") or "用户取消")}

        if path == "/api/plugins/command":
            command = payload.get("command") or self._first_command(payload.get("capability") or "")
            task = ws.plugins.tasks.submit(
                command, options=payload.get("options") or {},
                doc=payload.get("doc") or "", revision=payload.get("revision") or "",
                entry=payload.get("entry") or self.mode, markdown=payload.get("markdown"),
                timeout=payload.get("timeout"), wait=min(60.0, float(payload.get("wait") or 0)))
            view = ws.plugins.tasks.poll(task.id)
            return {"ok": True, "task": view,
                    "pending": view["state"] in ("queued", "running")}

        if path == "/api/plugins/insert":
            return {"ok": True, **self._plugin_insert(payload)}

        if path == "/api/plugins/export":
            return {"ok": True, **self._plugin_export(payload)}

        # ---- 统一导出服务（F06）：预检只算不写，界面据此决定下一步 ---------
        if path == "/api/export/check":
            return {"ok": True, **self._export_check(payload)}

        # ---- 链接检查（F09）：只查当前这一篇；替换也只动这一次的正文 ---------
        if path == "/api/check/links":
            return self._link_check(payload)

        if path == "/api/check/links/relink":
            return self._link_relink(payload)

        # ---- 表格与常用格式（F04）：纯文本规则，两端共用同一份实现 --------
        # 这些接口不碰磁盘：正文由调用方带进来，算完再带回去；桌面端直接 import
        # 同一批函数（mdreader.tables / mdreader.formatting），所以两端的判定一致。
        if path == "/api/edit/table":
            return self._edit_table(payload)

        if path == "/api/edit/format":
            return self._edit_format(payload)

        if path == "/api/open/external":
            p = payload.get("path") or ""
            if not is_within(ws.root,p) or (not os.path.isdir(p) and not p.lower().endswith('.html')):
                raise PermissionError('只允许打开本工作区的目录或导出 HTML')
            if p and os.path.exists(p):
                os.startfile(p)  # noqa: S606 - intentional, user-initiated
            return {"ok": True}

        raise KeyError("未知接口：%s" % path)


def _resource_response(path, download=False):
    if os.path.splitext(path)[1].lower() not in DOC_EXTS + ('.png','.jpg','.jpeg','.gif','.webp','.bmp'):
        raise PermissionError('此资源类型不允许通过阅读接口访问')
    return _file_response(path, download)


def _file_response(path: str, download: bool = False):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as fh:
        raw = fh.read()
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.endswith((".html", ".js", ".css", ".json", ".md", ".txt")):
        mime += "; charset=utf-8"
    return _Raw(200, mime, raw, {"Content-Disposition": "attachment"} if download else {})


class _Raw:
    def __init__(self, status, ctype, body, headers=None):
        self.status = status
        self.ctype = ctype
        self.body = body
        self.headers = headers or {}


class Handler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (APP_NAME, APP_VERSION)
    protocol_version = "HTTP/1.1"
    api: Api = None  # set by serve()

    def log_message(self, fmt, *args):  # keep the console clean
        # NOTE: pythonw.exe sets sys.stderr to None, so check before writing.
        if os.environ.get("MDREADER_DEBUG") and sys.stderr is not None:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # -- plumbing --------------------------------------------------------
    def _send(self, status, ctype, body: bytes, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", raw)

    def _error(self, status, message):
        self._json({"ok": False, "error": str(message)}, status)

    def _emit(self, result):
        if isinstance(result, _Raw):
            self._send(result.status, result.ctype, result.body, result.headers)
        else:
            self._json(result)

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        # NOTE: only the path is percent-decoded here; parse_qs already decodes
        # the values, and decoding them twice corrupts non-ASCII names.
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return urllib.parse.unquote(parsed.path), q

    def _read_payload(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            return json.loads(raw.decode("utf-8"))
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}

    def _guard(self, fn):
        try:
            self._emit(fn())
        except D.ConflictError as e:
            self._json({'ok':False,'error':str(e),'conflict':True,'revision':e.current},409)
        except KeyError as e:
            self._error(404, e.args[0] if e.args else e)
        except FileNotFoundError as e:
            self._error(404, "找不到文件或目录：%s" % (e.args[0] if e.args else ""))
        except (ValueError, FileExistsError, PermissionError) as e:
            self._error(400, e.args[0] if e.args else e)
        except Exception as e:  # pragma: no cover
            import traceback
            traceback.print_exc()
            self._error(500, "%s: %s" % (type(e).__name__, e))

    def do_GET(self):
        if not self._authorize(): return
        path, q = self._query()
        self._guard(lambda: self.api.get(path, q))

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if not self._authorize(write=True): return
        path, _ = self._query()

        def run():
            payload = self._read_payload()
            return self.api.post(path, payload)

        self._guard(run)

    def _authorize(self, write=False):
        from http.cookies import SimpleCookie
        host='127.0.0.1:%d'%self.server.server_address[1]
        if self.headers.get('Host') not in (host,host.replace('127.0.0.1','localhost')):
            self._error(403,'Host 已拒绝');return False
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://'+host,'http://'+host.replace('127.0.0.1','localhost')):
            self._error(403,'来源已拒绝');return False
        if self.path.startswith('/api/'):
            token=self.headers.get('X-MDReader-Session','')
            if not write and not token:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                if 'mdreader_session' in cookie:token=cookie['mdreader_session'].value
            if not secrets.compare_digest(token,self.api.token):
                self._error(403,'会话凭据无效，请从程序重新打开页面');return False
        try: length=int(self.headers.get('Content-Length') or 0)
        except ValueError:
            self._error(400,'无效内容长度');return False
        if length>24*1024*1024 or length<0:
            self._error(413,'请求内容过大');return False
        return True


def free_port(preferred: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", preferred))
        return s.getsockname()[1]


def serve(workspace_root: str | None = None, port: int = 0, quiet: bool = False):
    ws = Workspace(workspace_root or default_workspace())
    api = Api(ws, os.path.join(app_dir(), "webui"))
    if not os.path.isdir(api.webui):
        raise RuntimeError("找不到界面资源目录：%s" % api.webui)
    # Each server owns its API; a second window must not redirect the first.
    bound_handler = type("WorkspaceHandler", (Handler,), {"api": api})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), bound_handler)
    httpd.daemon_threads = True
    return ws, httpd, httpd.server_address[1]


def api_of(httpd) -> "Api":
    """The :class:`Api` instance bound to a server (never a shared global)."""
    return httpd.RequestHandlerClass.api


class ServerThread(threading.Thread):
    def __init__(self, httpd):
        super().__init__(daemon=True)
        self.httpd = httpd

    def run(self):
        self.httpd.serve_forever(poll_interval=0.2)

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# native dialogs via PowerShell (pythonw has no console; tkinter stays free
# for the app's own window, so we shell out to the OS pickers)
# --------------------------------------------------------------------------

#: Windows PowerShell 5.1 writes *redirected* stdout with the machine's ANSI/OEM
#: code page, not UTF-8 (on a Chinese system: GBK). Reading those bytes as UTF-8
#: turned ``C:\Users\简\Desktop\笔记`` into mojibake, so the app then looked for a
#: folder that cannot exist on disk and reported “找不到文件夹” even though the
#: picker had returned a real directory. Ask PowerShell for BOM-less UTF-8 first;
#: `_decode_ps_output` keeps the code-page decode as a fallback for shells that
#: refuse the switch, so a picker result is never silently replaced by U+FFFD.
_PS_UTF8_PREAMBLE = (
    "try{[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)}catch{};"
    "try{$OutputEncoding=[System.Text.UTF8Encoding]::new($false)}catch{};"
)


def _decode_ps_output(raw: bytes) -> str:
    """Decode picker results: UTF-8 when possible, local code page otherwise."""
    data = raw or b""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    encodings = ["utf-8"] + (["mbcs"] if os.name == "nt" else [])
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _run_ps(script: str):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = None
    for shell in ("powershell.exe", "pwsh"):
        try:
            proc = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-STA", "-Command",
                 _PS_UTF8_PREAMBLE + script],
                capture_output=True, timeout=600, creationflags=flags,
            )
            break
        except FileNotFoundError:
            continue
        except Exception:
            return []
    if proc is None:
        return []
    out = _decode_ps_output(proc.stdout).strip()
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    return paths


_PS_FILTER = ("Markdown 文件 (*.md;*.markdown;*.txt)|*.md;*.markdown;*.mdown;*.mkd;*.txt|"
              "所有文件 (*.*)|*.*")


def _dialog_files(multi=True) -> list:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='选择要导入的 Markdown 文件';"
        "$d.Filter='%s';"
        "$d.Multiselect=$%s;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {$_}}"
        % (_PS_FILTER, "true" if multi else "false")
    )
    return _run_ps(script)


def _dialog_open_files(multi=True) -> list:
    """Pick files to read/edit in place (temporary view, nothing is imported)."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='打开 Markdown 文件（临时查看，不导入）';"
        "$d.Filter='%s';"
        "$d.Multiselect=$%s;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {$_}}"
        % (_PS_FILTER, "true" if multi else "false")
    )
    return _run_ps(script)


def _dialog_folder() -> list:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description='选择包含 Markdown 的文件夹';"
        "$d.ShowNewFolderButton=$true;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.SelectedPath}"
    )
    return _run_ps(script)


_PS_IMAGE_FILTER = ("图片 (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp|"
                    "所有文件 (*.*)|*.*")
_PS_PLUGIN_FILTER = ("MDReader 插件包 (*.zip;*.mdplugin)|*.zip;*.mdplugin|所有文件 (*.*)|*.*")


def _dialog_images(multi=False) -> list:
    """选择要交给图片插入插件的本地图片（复制原图，不移动）。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='选择要插入的图片';"
        "$d.Filter='%s';"
        "$d.Multiselect=$%s;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {$_}}"
        % (_PS_IMAGE_FILTER, "true" if multi else "false")
    )
    return _run_ps(script)


def _dialog_plugin_package() -> list:
    """选择要安装的本地插件包（首版只接受维护者发布并登记哈希的包）。"""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='选择 MDReader 插件包';"
        "$d.Filter='%s';"
        "$d.Multiselect=$false;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.FileName}"
        % _PS_PLUGIN_FILTER
    )
    return _run_ps(script)


def _dialog_save(default_name="export.html", filter_spec="") -> str:
    safe = default_name.replace("'", "")
    filters = (filter_spec or "网页文件 (*.html)|*.html|所有文件 (*.*)|*.*").replace("'", "")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d=New-Object System.Windows.Forms.SaveFileDialog;"
        "$d.Title='另存为';"
        "$d.Filter='%s';"
        "$d.FileName='%s';"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.FileName}"
        % (filters, safe)
    )
    res = _run_ps(script)
    return res[0] if res else ""


# --------------------------------------------------------------------------
# starter content
# --------------------------------------------------------------------------

DEFAULT_PID = "__导入__"


def ensure_inbox(ws: "Workspace") -> str:
    """Project used when a .md file is opened from outside the app."""
    pdir = ws.project_dir(DEFAULT_PID)
    if not os.path.isdir(pdir):
        os.makedirs(os.path.join(pdir, "docs"), exist_ok=True)
        os.makedirs(os.path.join(pdir, "assets"), exist_ok=True)
        ws.write_meta(pdir, {
            "id": DEFAULT_PID, "name": "导入的文件", "schema": 1, "app": APP_NAME,
            "description": "双击打开或拖进来的 Markdown 文件会先放在这里，"
                           "你可以再把它们移动/整理到正式项目中。",
            "created": now_iso(),
        })
    return pdir


def import_document(ws: "Workspace", path: str, pid: str = DEFAULT_PID):
    """Copy an external .md file into a project and return (pid, doc_id, project_dir)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError("找不到文件：%s" % path)
    if os.path.splitext(path)[1].lower() not in DOC_EXTS:
        raise ValueError("不是 Markdown 文件：%s" % path)
    ensure_inbox(ws)
    pdir = ws.project_dir(pid)
    base = os.path.basename(path)
    dest = os.path.join(pdir, "docs", base)
    if os.path.abspath(os.path.dirname(path)) != os.path.abspath(os.path.join(pdir, "docs")):
        try:
            if not (os.path.exists(dest) and os.path.samefile(path, dest)):
                dest = _unique_path(dest)
                shutil.copy2(path, dest)
        except OSError:
            dest = _unique_path(dest)
            shutil.copy2(path, dest)
    ws.touch(pdir)
    rel = os.path.relpath(dest, pdir).replace(os.sep, "/")
    return pid, rel, pdir


def _starter_doc(name: str, description: str = "") -> str:
    return """---
title: %s
created: %s
tags: [说明]
---

# %s

%s

## 这个项目文件夹怎么用

1. 左侧点 **新建文档** 直接从零开始写。
2. 点 **导入文件** 把电脑里已有的 `.md` 文件复制进来。
3. 也可以把 `.md` 文件或整个文件夹 **拖到窗口里** 直接导入。
4. 点 **保存** 写入磁盘（`Ctrl + S` 同样可以），磁盘上的改动刷新即见。

## 支持的写法

| 语法 | 效果 |
| --- | --- |
| `# 标题` | 一级标题 |
| `**粗体**` · `*斜体*` | **粗体** · *斜体* |
| `==高亮==` | ==高亮== |
| `- [ ] 待办` | 复选框 |
| 表格 · 引用 · 代码块 | 都支持 |

> 提示：文档里的图片请放在 `assets` 目录，用 `![说明](../assets/图片.png)` 引用。

```python
def hello(name: str) -> str:
    return f"你好，{name}！"
```

祝写作愉快。
""" % (name, _dt.datetime.now().strftime("%Y-%m-%d"), name, description or "这里是项目说明。")
