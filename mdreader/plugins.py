# -*- coding: utf-8 -*-
"""轻量插件机制：清单、受信来源、安装与启用状态。

设计边界（对应任务书 §2.1–§2.4）：

* 只服务本阶段的三类扩展——``editor.image_insert`` 与 ``export.format``——
  不发布通用 SDK、事件总线或任意界面代码扩展。
* 插件包是**普通 zip**，根目录必须有 ``manifest.json``；除清单允许的字段外
  不接受任何其他键，界面只展示校验过的命令/格式文字，不加载插件提供的 HTML。
* 只有出现在随程序分发的受信清单（``plugin_trust.json``）里、且 SHA256 与包
  完全一致的包才能安装：清单自称“官方”或包内自带哈希都不构成信任证明。
* 发现阶段只读清单；真正执行在 :mod:`mdreader.plugin_tasks` 管理的独立工作进程里。
* 启用状态存在应用工作区 ``plugins.json``，桌面端与网页端读同一份文件，因此
  两端看到的可用命令一致（改完立刻生效，不需要重启）。

这一层只碰文件系统与应用工作区，不依赖 Tk、HTTP 或插件自身的代码。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile

from .storage import atomic_write, is_within, safe_join

#: 宿主实现的接口版本；插件的 ``api_version`` 必须在这里面。
PLUGIN_API_VERSION = "1"
SUPPORTED_API = ("1",)

MANIFEST_NAME = "manifest.json"
STATE_FILE = "plugins.json"
TRUST_FILE = "plugin_trust.json"

CAP_IMAGE_INSERT = "editor.image_insert"
CAP_EXPORT = "export.format"
CAPABILITIES = (CAP_IMAGE_INSERT, CAP_EXPORT)

#: 安装包与解包后的上限，防止一个包把磁盘塞满。
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 2000
#: 插件结果（导出产物 / 图片附件）的默认上限。
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

MAX_ID = 64
MAX_TITLE = 40
MAX_COMMANDS = 24

_ID_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)+$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")
_ENTRY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")
_MAGIC_RE = re.compile(r"^[0-9a-fA-F]{2,64}$")
_METHOD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")
_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

MANIFEST_KEYS = {
    "id", "name", "version", "api_version", "app_version_range", "entrypoint",
    "capabilities", "commands", "dependencies", "description", "publisher",
}
COMMAND_KEYS = {
    "id", "title", "capability", "method", "description",
    "format", "extension", "media_type", "magic", "max_bytes",
}

#: 允许的图片类型与文件头（核心在收到插件产物后再校验一次，不信任插件自述）。
IMAGE_MAGIC = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF87a",
    ".gif2": b"GIF89a",
    ".webp": b"RIFF",
    ".bmp": b"BM",
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


# --------------------------------------------------------------------------
# 错误
# --------------------------------------------------------------------------

class PluginError(ValueError):
    """插件机制里可以解释给用户看的失败。"""


class ManifestError(PluginError):
    """清单缺字段、字段非法或包含未允许的键。"""


class PackageError(PluginError):
    """安装包结构不合法（越界、链接、过大、缺清单……）。"""


class TrustError(PluginError):
    """来源不在受信清单里，或包的哈希与受信记录不一致。"""


class TaskError(RuntimeError):
    """插件任务失败、被取消，或结果不可采用。"""


class StaleResult(TaskError):
    """结果基于的文档/缓冲已经不是调用时那一份，必须丢弃。"""


# --------------------------------------------------------------------------
# 版本范围
# --------------------------------------------------------------------------

def version_key(text: str) -> tuple:
    """``"1.2"`` -> ``(1, 2)``；非数字部分忽略，便于比较。"""
    parts = []
    for chunk in str(text or "").strip().split("."):
        digits = re.match(r"\d+", chunk)
        parts.append(int(digits.group()) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def version_in_range(version: str, spec: str) -> bool:
    """判断 ``version`` 是否满足 ``">=0.3.0,<0.4.0"`` 这样的范围。

    空范围或 ``*`` 表示不限制。无法解析的子句按“不满足”处理——宁可不启用，
    也不要在没看懂兼容声明的情况下执行插件。
    """
    text = str(spec or "").strip()
    if not text or text == "*":
        return True
    current = version_key(version)
    for raw in text.split(","):
        clause = raw.strip()
        if not clause:
            continue
        match = re.match(r"^(>=|<=|==|=|>|<)?\s*(\d+(?:\.\d+){0,3})\s*$", clause)
        if not match:
            return False
        operator = match.group(1) or "="
        wanted = version_key(match.group(2))
        if operator == ">=" and not current >= wanted:
            return False
        if operator == "<=" and not current <= wanted:
            return False
        if operator == ">" and not current > wanted:
            return False
        if operator == "<" and not current < wanted:
            return False
        if operator in ("=", "==") and not current == wanted:
            return False
    return True


# --------------------------------------------------------------------------
# 清单校验
# --------------------------------------------------------------------------

def _text(value, field, limit=200, allow_empty=False) -> str:
    if not isinstance(value, str):
        raise ManifestError("清单字段 %s 必须是字符串" % field)
    text = value.strip()
    if not text and not allow_empty:
        raise ManifestError("清单字段 %s 不能为空" % field)
    if len(text) > limit:
        raise ManifestError("清单字段 %s 太长（最多 %d 字）" % (field, limit))
    if any(ord(ch) < 32 for ch in text):
        raise ManifestError("清单字段 %s 含控制字符" % field)
    return text


def safe_entrypoint(value) -> str:
    """入口文件必须是包内的相对 .py 路径，不允许跳出插件目录。"""
    text = _text(value, "entrypoint", 200)
    if os.path.isabs(text) or re.match(r"^[A-Za-z]:", text) or text.startswith(("/", "\\")):
        raise ManifestError("entrypoint 必须是插件包内的相对路径")
    normalized = text.replace("\\", "/")
    if any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ManifestError("entrypoint 不能包含 . 或 .. 路径段")
    if not _ENTRY_RE.match(normalized):
        raise ManifestError("entrypoint 只能是插件包内的 .py 文件")
    return normalized


def validate_manifest(data, *, where: str = "") -> dict:
    """校验并规范化插件清单；不合法时抛 :class:`ManifestError`。"""
    prefix = ("%s：" % where) if where else ""
    if not isinstance(data, dict):
        raise ManifestError(prefix + "清单必须是 JSON 对象")
    unknown = sorted(set(data) - MANIFEST_KEYS)
    if unknown:
        raise ManifestError(prefix + "清单含未允许的字段：" + "、".join(unknown))

    pid = _text(data.get("id"), "id", MAX_ID)
    if not _ID_RE.match(pid):
        raise ManifestError(prefix + "插件 id 只能是小写字母、数字与 . _ -，且至少两段（如 mdreader.export-pdf）")

    name = _text(data.get("name"), "name", 60)
    version = _text(data.get("version"), "version", 24)
    if not _VERSION_RE.match(version):
        raise ManifestError(prefix + "version 必须是 1、1.2、1.2.3 这样的数字")

    api_version = _text(data.get("api_version"), "api_version", 8)
    if api_version not in SUPPORTED_API:
        raise ManifestError(prefix + "不支持 api_version=%s（本程序支持：%s）"
                            % (api_version, "、".join(SUPPORTED_API)))

    range_text = data.get("app_version_range", "*")
    range_text = _text(range_text, "app_version_range", 60, allow_empty=True) or "*"
    if range_text != "*" and not re.match(r"^[<>=0-9.,\s]+$", range_text):
        raise ManifestError(prefix + "app_version_range 只能写 >=1.2,<2 这样的范围")

    entrypoint = safe_entrypoint(data.get("entrypoint"))

    description = _text(data.get("description", ""), "description", 400, allow_empty=True)
    publisher = _text(data.get("publisher", ""), "publisher", 60, allow_empty=True)

    caps = data.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise ManifestError(prefix + "capabilities 必须是非空数组")
    capabilities = []
    for item in caps:
        if item not in CAPABILITIES:
            raise ManifestError(prefix + "不支持的能力：%s" % item)
        if item in capabilities:
            raise ManifestError(prefix + "能力重复声明：%s" % item)
        capabilities.append(item)

    deps = data.get("dependencies", [])
    if not isinstance(deps, list):
        raise ManifestError(prefix + "dependencies 必须是数组")
    dependencies = []
    for item in deps:
        dep = _text(item, "dependencies", MAX_ID)
        if not _ID_RE.match(dep):
            raise ManifestError(prefix + "依赖 id 不合法：%s" % dep)
        if dep == pid:
            raise ManifestError(prefix + "插件不能依赖自己")
        if dep in dependencies:
            raise ManifestError(prefix + "依赖重复声明：%s" % dep)
        dependencies.append(dep)

    raw_commands = data.get("commands", [])
    if not isinstance(raw_commands, list):
        raise ManifestError(prefix + "commands 必须是数组")
    if len(raw_commands) > MAX_COMMANDS:
        raise ManifestError(prefix + "命令数量超出上限（%d）" % MAX_COMMANDS)
    commands, seen = [], set()
    for index, raw in enumerate(raw_commands):
        commands.append(_command(raw, capabilities, seen, prefix, index))
    if not commands:
        raise ManifestError(prefix + "至少要声明一条命令")

    return {
        "id": pid, "name": name, "version": version, "api_version": api_version,
        "app_version_range": range_text, "entrypoint": entrypoint,
        "capabilities": capabilities, "commands": commands,
        "dependencies": dependencies, "description": description, "publisher": publisher,
    }


def _command(raw, capabilities, seen, prefix: str, index: int) -> dict:
    label = "%scommand[%d]" % (prefix, index)
    if not isinstance(raw, dict):
        raise ManifestError(label + " 必须是 JSON 对象")
    unknown = sorted(set(raw) - COMMAND_KEYS)
    if unknown:
        raise ManifestError(label + " 含未允许的字段：" + "、".join(unknown))
    cid = _text(raw.get("id"), "command.id", 40)
    if not re.match(r"^[a-z0-9][a-z0-9._-]{0,39}$", cid):
        raise ManifestError(label + " id 只能是小写字母、数字与 . _ -")
    if cid in seen:
        raise ManifestError(label + " 命令 id 重复：%s" % cid)
    seen.add(cid)
    title = _text(raw.get("title"), "command.title", MAX_TITLE)
    capability = raw.get("capability")
    if capability not in CAPABILITIES:
        raise ManifestError(label + " 能力不受支持：%s" % capability)
    if capability not in capabilities:
        raise ManifestError(label + " 用了清单里没声明的能力：%s" % capability)
    method = _text(raw.get("method"), "command.method", 64)
    if not _METHOD_RE.match(method) or method.startswith("__"):
        raise ManifestError(label + " method 必须是普通函数名")
    command = {"id": cid, "title": title, "capability": capability, "method": method,
               "description": _text(raw.get("description", ""), "command.description", 200,
                                    allow_empty=True)}
    if capability == CAP_EXPORT:
        fmt = _text(raw.get("format"), "command.format", 32)
        if not _FORMAT_RE.match(fmt):
            raise ManifestError(label + " format 只能是小写字母、数字与 . _ -")
        ext = _text(raw.get("extension"), "command.extension", 10).lstrip(".")
        if not _EXT_RE.match(ext):
            raise ManifestError(label + " extension 只能是不带点的字母数字")
        media_type = _text(raw.get("media_type"), "command.media_type", 80)
        if "/" not in media_type:
            raise ManifestError(label + " media_type 形如 application/pdf")
        magic = raw.get("magic", "")
        if magic:
            magic = _text(magic, "command.magic", 64)
            if not _MAGIC_RE.match(magic) or len(magic) % 2:
                raise ManifestError(label + " magic 必须是偶数长度的十六进制")
        limit = raw.get("max_bytes", MAX_ARTIFACT_BYTES)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= MAX_ARTIFACT_BYTES:
            raise ManifestError(label + " max_bytes 必须是 1 到 %d 之间的整数" % MAX_ARTIFACT_BYTES)
        command.update({"format": fmt, "extension": ext, "media_type": media_type,
                        "magic": magic.lower(), "max_bytes": limit})
    else:
        for key in ("format", "extension", "media_type", "magic", "max_bytes"):
            if key in raw:
                raise ManifestError(label + " 图片插入命令不能声明 %s" % key)
    return command


# --------------------------------------------------------------------------
# 受信来源
# --------------------------------------------------------------------------

def trust_path_default() -> str:
    """随程序分发的受信清单；放在代码目录里，用户改不动。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), TRUST_FILE)


class TrustRegistry:
    """受信清单：`插件 id → 版本 → 包 SHA256`。"""

    def __init__(self, path: str = ""):
        self.path = os.path.abspath(path) if path else trust_path_default()
        self.problem = ""
        self._data = {}
        self.load()

    def load(self):
        self._data = {}
        self.problem = ""
        try:
            with open(self.path, encoding="utf-8") as stream:
                raw = json.load(stream)
        except FileNotFoundError:
            self.problem = "找不到受信清单：%s" % self.path
            return
        except (OSError, ValueError) as exc:
            self.problem = "受信清单无法读取：%s" % exc
            return
        if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), dict):
            self.problem = "受信清单格式不正确：%s" % self.path
            return
        for pid, entry in raw["plugins"].items():
            if not isinstance(entry, dict):
                continue
            versions = entry.get("versions") or {}
            if not isinstance(versions, dict):
                versions = {}
            self._data[pid] = {
                "versions": {str(k): str(v).lower() for k, v in versions.items()},
                "publisher": str(entry.get("publisher") or ""),
                "reserved": str(entry.get("reserved") or ""),
            }

    def lookup(self, pid: str, sha256: str):
        """返回 ``(ok, entry, reason)``；reason 是给用户看的中文原因。"""
        entry = self._data.get(pid)
        if entry is None:
            return False, None, ("这个包不在受信清单里，已拒绝安装：%s。"
                                 "首版只接受维护者发布并登记哈希的插件包。" % pid)
        recorded = entry["versions"]
        if not recorded:
            note = entry.get("reserved") or "尚未发布"
            return False, entry, ("%s 还没有发布可安装的版本（%s）" % (pid, note))
        wanted = str(sha256).lower()
        if wanted not in recorded.values():
            return False, entry, ("包内容与受信清单里的哈希不一致，可能被改动过：%s" % pid)
        return True, entry, ""

    def reserved(self) -> list:
        return [{"id": pid, "publisher": entry.get("publisher", ""),
                 "reserved": entry.get("reserved", ""), "released": bool(entry["versions"])}
                for pid, entry in sorted(self._data.items())]

    def as_dict(self) -> dict:
        return {"path": self.path, "problem": self.problem,
                "plugins": {pid: {"versions": sorted(entry["versions"]),
                                  "publisher": entry["publisher"],
                                  "reserved": entry["reserved"]}
                            for pid, entry in sorted(self._data.items())}}


# --------------------------------------------------------------------------
# 安装包检查
# --------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_member(info: zipfile.ZipInfo, *, seen: set, total: list, count: list):
    """逐条检查归档成员：绝对路径、``..``、链接、大小与数量。"""
    raw = info.filename
    if not raw or raw.endswith("/"):
        return None
    name = raw.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackageError("插件包里含绝对路径，已拒绝：%s" % raw)
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PackageError("插件包里含越界路径，已拒绝：%s" % raw)
    if info.flag_bits & 0x1:
        raise PackageError("插件包里有加密文件，已拒绝：%s" % raw)
    mode = (info.external_attr >> 16) & 0xF000
    if mode == 0xA000:
        raise PackageError("插件包里含符号链接，已拒绝：%s" % raw)
    if name in seen:
        raise PackageError("插件包里含重复路径，已拒绝：%s" % raw)
    seen.add(name)
    count[0] += 1
    if count[0] > MAX_ENTRIES:
        raise PackageError("插件包文件数超过上限（%d）" % MAX_ENTRIES)
    total[0] += int(info.file_size or 0)
    if total[0] > MAX_UNPACKED_BYTES:
        raise PackageError("插件包解包后超过上限（%d MB）" % (MAX_UNPACKED_BYTES // (1024 * 1024)))
    return name


def _read_manifest(zf: zipfile.ZipFile, names) -> tuple:
    if MANIFEST_NAME not in names:
        raise PackageError("插件包根目录缺少 %s" % MANIFEST_NAME)
    try:
        raw = zf.read(MANIFEST_NAME)
    except (KeyError, OSError) as exc:
        raise PackageError("清单无法读取：%s" % exc) from exc
    if len(raw) > 256 * 1024:
        raise PackageError("清单文件过大")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackageError("清单不是合法的 UTF-8 JSON：%s" % exc) from exc
    return data, raw


def build_package(src_dir: str, out_path: str, *, extra: "dict | None" = None) -> str:
    """把插件源码目录打成可分发的 zip（开发者工具，也是测试与验收的造包方式）。

    刻意做成**确定性**的：成员按名字排序、时间戳固定，所以同一份源码永远得到
    同一个 SHA256——否则受信清单里的哈希没法复现。
    """
    src = os.path.abspath(src_dir)
    if not os.path.isdir(src):
        raise PackageError("找不到要打包的插件目录：%s" % src)
    files = []
    for folder, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
        for name in sorted(names):
            if name.endswith(".pyc") or name.startswith("."):
                continue
            full = os.path.join(folder, name)
            files.append((os.path.relpath(full, src).replace(os.sep, "/"), full))
    if extra:
        for name, payload in sorted(extra.items()):
            files.append((name.replace(os.sep, "/"), payload))
    if not any(name == MANIFEST_NAME for name, _ in files):
        raise PackageError("插件目录根下必须有 %s" % MANIFEST_NAME)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, source in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            if isinstance(source, (bytes, bytearray)):
                zf.writestr(info, bytes(source))
            else:
                with open(source, "rb") as stream:
                    zf.writestr(info, stream.read())
    return out_path


# --------------------------------------------------------------------------
# 结果校验（宿主侧，插件自述一律不信）
# --------------------------------------------------------------------------

def _real_inside(workdir: str, path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise TaskError("插件没有给出产物路径")
    candidate = os.path.realpath(os.path.abspath(path))
    if not is_within(workdir, candidate):
        raise TaskError("插件产物的路径在任务工作目录之外，已拒绝：%s" % path)
    return candidate


def check_image_asset(path: str, workdir: str, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> dict:
    """校验一张由插件暂存的图片：在工作目录内、大小受限、文件头对得上。"""
    full = _real_inside(workdir, path)
    if not os.path.isfile(full):
        raise TaskError("插件声明的图片不存在：%s" % os.path.basename(full))
    size = os.path.getsize(full)
    if size <= 0:
        raise TaskError("插件产出的图片是空文件：%s" % os.path.basename(full))
    if size > max_bytes:
        raise TaskError("插件产出的图片超过上限：%s" % os.path.basename(full))
    ext = os.path.splitext(full)[1].lower()
    if ext not in IMAGE_EXTS:
        raise TaskError("不支持的图片类型：%s" % ext)
    with open(full, "rb") as stream:
        head = stream.read(16)
    expected = IMAGE_MAGIC.get(ext)
    if ext == ".gif":
        if not (head.startswith(b"GIF87a") or head.startswith(b"GIF89a")):
            raise TaskError("文件内容不像 %s 图片：%s" % (ext, os.path.basename(full)))
    elif ext == ".webp":
        if not (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
            raise TaskError("文件内容不像 WebP 图片：%s" % os.path.basename(full))
    elif expected and not head.startswith(expected):
        raise TaskError("文件内容不像 %s 图片：%s" % (ext, os.path.basename(full)))
    return {"path": full, "name": os.path.basename(full), "bytes": size, "ext": ext}


def check_markdown_fragment(text, *, limit: int = 200 * 1024) -> str:
    """插件返回的 Markdown 片段只能是普通文本。"""
    if not isinstance(text, str) or not text.strip():
        raise TaskError("插件没有给出可插入的 Markdown 内容")
    if len(text) > limit:
        raise TaskError("插件给出的 Markdown 片段过长")
    if "\x00" in text:
        raise TaskError("插件给出的 Markdown 片段含二进制字符")
    return text


def check_image_plan(data, workdir: str) -> dict:
    """校验图片插入方案：Markdown 片段 + 落在工作目录里的图片。"""
    if not isinstance(data, dict):
        raise TaskError("插件返回的结果不是对象")
    if data.get("kind") not in (None, "image-insert"):
        raise TaskError("插件返回的结果类型不对：%s" % data.get("kind"))
    markdown = check_markdown_fragment(data.get("markdown"))
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise TaskError("插件没有给出要写入的图片")
    if len(raw_assets) > 8:
        raise TaskError("一次插入的图片太多（最多 8 张）")
    assets = []
    for item in raw_assets:
        if not isinstance(item, dict):
            raise TaskError("插件给出的图片条目不是对象")
        assets.append(check_image_asset(item.get("path"), workdir))
    return {"kind": "image-insert", "markdown": markdown, "assets": assets,
            "note": str(data.get("note") or "")[:200]}


def check_export_artifact(data, workdir: str, command: dict) -> dict:
    """校验导出产物：在任务工作目录内、扩展名与文件头符合清单声明。"""
    if not isinstance(data, dict):
        raise TaskError("插件返回的结果不是对象")
    if data.get("kind") not in (None, "export"):
        raise TaskError("插件返回的结果类型不对：%s" % data.get("kind"))
    full = _real_inside(workdir, data.get("path"))
    if not os.path.isfile(full):
        raise TaskError("插件声明的导出文件不存在：%s" % os.path.basename(full))
    size = os.path.getsize(full)
    if size <= 0:
        raise TaskError("插件产出的导出文件是空文件")
    limit = int(command.get("max_bytes") or MAX_ARTIFACT_BYTES)
    if size > limit:
        raise TaskError("插件产出的导出文件超过清单声明的上限（%d 字节）" % limit)
    ext = "." + command["extension"]
    if os.path.splitext(full)[1].lower() != ext.lower():
        raise TaskError("插件产出的文件扩展名与清单不符：应为 %s" % ext)
    magic = command.get("magic") or ""
    if magic:
        with open(full, "rb") as stream:
            head = stream.read(len(magic) // 2)
        if head != bytes.fromhex(magic):
            raise TaskError("插件产出的文件内容与清单声明的格式不符")
    return {"path": full, "bytes": size, "extension": command["extension"],
            "media_type": command.get("media_type") or "application/octet-stream",
            "warnings": [str(w)[:200] for w in (data.get("warnings") or [])][:20]}


# --------------------------------------------------------------------------
# 插件仓库
# --------------------------------------------------------------------------

class PluginStore:
    """已安装插件的状态机：安装、启用、禁用、升级回退、卸载。

    ``trust_path`` / ``python`` / ``task_timeout`` 只在测试与本地开发时注入；
    默认一律使用随程序分发的受信清单。
    """

    def __init__(self, workspace: str, *, app_version: str = "", trust_path: str = "",
                 python: str = "", task_timeout: float = 0.0, worker_dir: str = ""):
        if not app_version:
            from .core import APP_VERSION
            app_version = APP_VERSION
        self.workspace = os.path.abspath(workspace)
        self.app_version = app_version
        self.plugins_dir = os.path.join(self.workspace, "plugins")
        self.installed_dir = os.path.join(self.plugins_dir, "installed")
        self.cache_dir = os.path.join(self.plugins_dir, "cache")
        self.staging_dir = os.path.join(self.plugins_dir, "staging")
        self.tasks_dir = os.path.join(self.plugins_dir, "tasks")
        self.state_path = os.path.join(self.workspace, STATE_FILE)
        self.trust = TrustRegistry(trust_path)
        self.python = python or ""
        self.task_timeout = task_timeout
        self.worker_dir = worker_dir
        self._lock = threading.RLock()
        self._state = None
        self._state_stamp = None
        self.state_problem = ""
        self._tasks = None

    # -- 任务管理（延后到真正要跑插件时才 import 工作进程那一层）------------
    @property
    def tasks(self):
        if self._tasks is None:
            from .plugin_tasks import TaskManager
            self._tasks = TaskManager(self, python=self.python,
                                      task_timeout=self.task_timeout,
                                      worker_dir=self.worker_dir)
        return self._tasks

    # -- 状态文件 ---------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            stamp = os.path.getmtime(self.state_path)
        except OSError:
            stamp = None
        if self._state is not None and stamp == self._state_stamp:
            return self._state
        state = {"version": 1, "plugins": {}}
        self.state_problem = ""
        if stamp is not None:
            try:
                with open(self.state_path, encoding="utf-8") as stream:
                    loaded = json.load(stream)
            except (OSError, ValueError) as exc:
                self.state_problem = "插件状态文件无法读取，已按未安装处理：%s" % exc
            else:
                if isinstance(loaded, dict) and isinstance(loaded.get("plugins"), dict):
                    state = {"version": 1, "plugins": loaded["plugins"]}
                else:
                    self.state_problem = "插件状态文件格式不正确，已按未安装处理"
        self._state, self._state_stamp = state, stamp
        return state

    def _save_state(self, state: dict):
        state["version"] = 1
        atomic_write(self.state_path, json.dumps(state, ensure_ascii=False, indent=2))
        self._state = state
        try:
            self._state_stamp = os.path.getmtime(self.state_path)
        except OSError:
            self._state_stamp = None

    def _entry(self, pid: str) -> "dict | None":
        return self._load_state()["plugins"].get(pid)

    def entry(self, pid: str) -> "dict | None":
        """已安装插件的状态记录（只读副本），供任务层查安装目录与版本。"""
        info = self._entry(pid)
        return dict(info) if info else None

    def _plugin_dir(self, pid: str, version: str) -> str:
        return os.path.join(self.installed_dir, pid, version)

    # -- 读清单（只读文件，不执行插件代码）--------------------------------
    def read_manifest(self, path: str) -> dict:
        manifest_path = os.path.join(path, MANIFEST_NAME)
        try:
            with open(manifest_path, encoding="utf-8") as stream:
                raw = json.load(stream)
        except FileNotFoundError:
            raise ManifestError("插件目录里找不到 manifest.json")
        except (OSError, ValueError) as exc:
            raise ManifestError("清单无法读取：%s" % exc) from exc
        return validate_manifest(raw)

    def _installed_manifest(self, entry: dict) -> "tuple[dict|None, str]":
        folder = entry.get("path") or ""
        if not folder or not is_within(self.plugins_dir, folder):
            return None, "插件目录不在插件仓库内，已忽略"
        if not os.path.isdir(folder):
            return None, "插件文件已不存在（可能被手工删除）"
        try:
            return self.read_manifest(folder), ""
        except ManifestError as exc:
            return None, "清单损坏：%s" % exc

    # -- 兼容性与依赖 -----------------------------------------------------
    def compatibility(self, manifest: dict, state: dict) -> "tuple[bool, str]":
        if manifest["api_version"] not in SUPPORTED_API:
            return False, ("插件需要 api_version=%s，本程序支持 %s"
                           % (manifest["api_version"], "、".join(SUPPORTED_API)))
        if not version_in_range(self.app_version, manifest["app_version_range"]):
            return False, ("插件要求 %s 版本 %s，当前是 %s"
                           % (manifest["name"], manifest["app_version_range"], self.app_version))
        return True, ""

    def dependency_status(self, manifest: dict, state: dict) -> list:
        rows = []
        for dep in manifest["dependencies"]:
            info = state["plugins"].get(dep) or {}
            installed = bool(info)
            dep_manifest = None
            if installed:
                dep_manifest, problem = self._installed_manifest(info)
                if dep_manifest is None:
                    installed = False
            ok, reason = (True, "") if dep_manifest else (False, "依赖的插件没有可用版本")
            if dep_manifest:
                ok, reason = self.compatibility(dep_manifest, state)
            rows.append({"id": dep, "installed": installed, "enabled": bool(info.get("enabled")),
                         "ok": bool(ok and installed and info.get("enabled")),
                         "name": (dep_manifest or {}).get("name", dep), "reason": reason})
        return rows

    def _unavailable_reason(self, manifest: dict, state: dict, deps: list) -> str:
        ok, reason = self.compatibility(manifest, state)
        if not ok:
            return reason
        bad = [d for d in deps if not d["ok"]]
        if bad:
            names = "、".join(d["name"] for d in bad)
            missing = [d for d in bad if not d["installed"]]
            if missing:
                return "缺少依赖插件：%s" % names
            return "依赖的插件没有启用：%s" % names
        return ""

    # -- 列表 -------------------------------------------------------------
    def list_plugins(self) -> list:
        state = self._load_state()
        rows = []
        for pid, info in sorted(state["plugins"].items()):
            manifest, problem = self._installed_manifest(info)
            row = {
                "id": pid,
                "version": info.get("version", ""),
                "enabled": bool(info.get("enabled")),
                "user_disabled": bool(info.get("user_disabled")),
                "installed": True,
                "installed_at": info.get("installed_at", ""),
                "sha256": info.get("sha256", ""),
                "publisher": info.get("publisher", ""),
                "source": info.get("source", "trusted"),
                "previous": info.get("previous") or None,
                "needs_restart": bool(info.get("needs_restart")),
                "last_error": info.get("last_error", ""),
                "path": info.get("path", ""),
                "commands": [], "capabilities": [], "dependencies": [],
                "name": (manifest or {}).get("name", pid),
                "description": (manifest or {}).get("description", ""),
                "api_version": (manifest or {}).get("api_version", ""),
                "app_version_range": (manifest or {}).get("app_version_range", ""),
            }
            if manifest is None:
                row.update({"state": "broken", "reason": problem or "清单损坏",
                            "available": False})
                rows.append(row)
                continue
            deps = self.dependency_status(manifest, state)
            reason = self._unavailable_reason(manifest, state, deps)
            row.update({
                "name": manifest["name"], "description": manifest["description"],
                "capabilities": list(manifest["capabilities"]),
                "commands": [dict(c, command="%s:%s" % (pid, c["id"])) for c in manifest["commands"]],
                "dependencies": deps,
                "api_version": manifest["api_version"],
                "app_version_range": manifest["app_version_range"],
                "publisher": manifest["publisher"] or row["publisher"],
            })
            if reason:
                row.update({"state": "unavailable", "reason": reason, "available": False})
            elif row["needs_restart"]:
                row.update({"state": "restart", "reason": "有文件被占用：重启后生效",
                            "available": False})
            elif row["enabled"]:
                row.update({"state": "enabled", "reason": "", "available": True})
            else:
                row.update({"state": "disabled",
                            "reason": row["last_error"] or "未启用", "available": False})
            rows.append(row)

        for item in self.trust.reserved():
            if item["id"] in state["plugins"]:
                continue
            rows.append({
                "id": item["id"], "name": item["id"], "version": "", "enabled": False,
                "user_disabled": False, "installed": False, "state": "not-installed",
                "reason": ("尚未发布：%s" % item["reserved"]) if item["reserved"] else "未安装",
                "available": False, "commands": [], "capabilities": [], "dependencies": [],
                "publisher": item["publisher"], "description": "", "installed_at": "",
                "sha256": "", "source": "reserved", "previous": None, "needs_restart": False,
                "last_error": "", "path": "", "api_version": "", "app_version_range": "",
            })
        return rows

    def commands(self) -> list:
        """当前**可用**的命令（已启用、兼容、依赖满足）。"""
        out = []
        for row in self.list_plugins():
            if not row.get("available"):
                continue
            for command in row["commands"]:
                out.append({
                    "command": "%s:%s" % (row["id"], command["id"]),
                    "plugin": row["id"], "plugin_name": row["name"],
                    "id": command["id"], "title": command["title"],
                    "capability": command["capability"], "method": command["method"],
                    "description": command.get("description", ""),
                    "format": command.get("format", ""),
                    "extension": command.get("extension", ""),
                    "media_type": command.get("media_type", ""),
                    "magic": command.get("magic", ""),
                    "max_bytes": command.get("max_bytes", 0),
                })
        return out

    def find_command(self, name: str) -> dict:
        for command in self.commands():
            if command["command"] == name or command["id"] == name:
                return command
        known = self._load_state()["plugins"]
        pid = str(name or "").split(":", 1)[0]
        if pid in known:
            row = next((r for r in self.list_plugins() if r["id"] == pid), None)
            reason = (row or {}).get("reason") or "插件当前不可用"
            raise PluginError("插件命令不可用：%s（%s）" % (name, reason))
        raise PluginError("没有这个插件命令：%s" % name)

    def status(self) -> dict:
        rows = self.list_plugins()
        return {
            "api_version": PLUGIN_API_VERSION,
            "app_version": self.app_version,
            "trust": self.trust.as_dict(),
            "state_problem": self.state_problem,
            "plugins": rows,
            "commands": self.commands(),
            "active_tasks": self.tasks.active() if self._tasks is not None else [],
        }

    # -- 安装 -------------------------------------------------------------
    def install(self, archive: str, *, allow_unverified: bool = False,
                enable: bool = False) -> dict:
        """安装一个插件包；返回 ``{"plugin":…, "replaced":…, "note":…}``。

        顺序刻意是「先全部检查、再解包到暂存区、最后才改名进安装目录」：
        任何一步失败都不会动到已安装的上一版本。
        """
        archive = os.path.abspath(str(archive or ""))
        if not os.path.isfile(archive):
            raise PackageError("找不到插件包：%s" % archive)
        size = os.path.getsize(archive)
        if size <= 0:
            raise PackageError("插件包是空文件")
        if size > MAX_ARCHIVE_BYTES:
            raise PackageError("插件包超过上限（%d MB）"
                               % (MAX_ARCHIVE_BYTES // (1024 * 1024)))
        if not zipfile.is_zipfile(archive):
            raise PackageError("这不是有效的 zip 插件包（首版只接受 .zip/.mdplugin）")
        sha = _sha256_file(archive)
        verified = False
        trust_note = ""
        with zipfile.ZipFile(archive) as zf:
            names, seen, total, count = [], set(), [0], [0]
            for info in zf.infolist():
                name = _check_member(info, seen=seen, total=total, count=count)
                if name:
                    names.append(name)
            raw_manifest, _ = _read_manifest(zf, set(names))
            manifest = validate_manifest(raw_manifest, where="插件包 %s" % os.path.basename(archive))
            ok, entry, reason = self.trust.lookup(manifest["id"], sha)
            if ok:
                verified = True
                trust_note = "来源已核对（受信清单 SHA256 一致）"
            elif not allow_unverified:
                raise TrustError(reason)
            else:
                trust_note = "来源未核对：受信清单里没有这个包的哈希，按明确要求继续安装"
            if manifest["entrypoint"] not in names:
                raise PackageError("清单里的入口文件不在包里：%s" % manifest["entrypoint"])
            staging = self._new_staging(manifest["id"])
            try:
                zf.extractall(staging)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        entry_path = safe_join(staging, manifest["entrypoint"])
        if not os.path.isfile(entry_path):
            shutil.rmtree(staging, ignore_errors=True)
            raise PackageError("解包后找不到入口文件：%s" % manifest["entrypoint"])

        with self._lock:
            state = self._load_state()
            old = state["plugins"].get(manifest["id"]) or {}
            old_manifest, _problem = (self._installed_manifest(old) if old else (None, ""))
            if old and old.get("version") == manifest["version"]:
                shutil.rmtree(staging, ignore_errors=True)
                raise PackageError("同一个版本已经安装过了：%s %s"
                                   % (manifest["id"], manifest["version"]))
            if old_manifest and not version_in_range(self.app_version, manifest["app_version_range"]):
                shutil.rmtree(staging, ignore_errors=True)
                raise PluginError("新版本与当前程序不兼容，已保留上一可用版本：%s"
                                  % manifest["app_version_range"])
            target = self._plugin_dir(manifest["id"], manifest["version"])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target):
                shutil.rmtree(staging, ignore_errors=True)
                raise PackageError("安装目录已存在，未覆盖：%s" % target)
            os.replace(staging, target)
            previous = None
            if old.get("path") and old.get("path") != target:
                previous = {"version": old.get("version", ""), "path": old["path"],
                            "enabled": bool(old.get("enabled")),
                            "user_disabled": bool(old.get("user_disabled"))}
            state["plugins"][manifest["id"]] = {
                "version": manifest["version"], "path": target, "sha256": sha,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "enabled": bool(enable and not old.get("user_disabled")),
                "user_disabled": bool(old.get("user_disabled")),
                "publisher": manifest["publisher"], "source": "trusted" if verified else "manual",
                "previous": previous, "needs_restart": False, "last_error": "",
            }
            self._save_state(state)
            result = {"plugin": self._row_for(manifest["id"]) or {}, "replaced": bool(previous),
                      "previous": previous, "note": trust_note,
                      "verified": verified, "sha256": sha}
            if previous:
                # 换版本后旧目录留着没有意义，但只有确认新版本能用才删
                result["note"] += "；已保留上一版本以便回退"
            if enable:
                try:
                    result["enable"] = self.enable(manifest["id"])
                except PluginError as exc:
                    result["enable_error"] = str(exc)
            return result

    def _row_for(self, pid: str) -> "dict | None":
        return next((row for row in self.list_plugins() if row["id"] == pid), None)

    def _new_staging(self, pid: str) -> str:
        os.makedirs(self.staging_dir, exist_ok=True)
        for _ in range(8):
            candidate = os.path.join(self.staging_dir, "%s-%d" % (re.sub(r"[^a-z0-9._-]", "_", pid),
                                                                  time.time_ns()))
            if not os.path.exists(candidate):
                os.makedirs(candidate)
                return candidate
        raise PackageError("无法创建暂存目录")

    # -- 启用 / 禁用 / 卸载 -----------------------------------------------
    def enable(self, pid: str) -> dict:
        with self._lock:
            state = self._load_state()
            info = state["plugins"].get(pid)
            if not info:
                raise PluginError("没有安装这个插件：%s" % pid)
            manifest, problem = self._installed_manifest(info)
            if manifest is None:
                info["enabled"] = False
                info["last_error"] = problem
                self._save_state(state)
                raise PluginError("插件无法启用：%s" % problem)
            deps = self.dependency_status(manifest, state)
            reason = self._unavailable_reason(manifest, state, deps)
            if reason:
                info["enabled"] = False
                info["last_error"] = reason
                self._save_state(state)
                raise PluginError("插件无法启用：%s" % reason)
            try:
                self.tasks.handshake(pid, manifest)
            except TaskError as exc:
                return self._after_failed_activation(state, info, manifest, str(exc))
            info["enabled"] = True
            info["user_disabled"] = False
            info["last_error"] = ""
            self._save_state(state)
            return {"id": pid, "enabled": True, "version": manifest["version"],
                    "commands": len(manifest["commands"])}

    def _after_failed_activation(self, state: dict, info: dict, manifest: dict, reason: str) -> dict:
        """启用时握手失败：有上一版本就回退，没有就留下原因并保持禁用。"""
        previous = info.get("previous") or {}
        info["enabled"] = False
        if previous.get("path") and os.path.isdir(previous["path"]):
            failed = info.get("path")
            info.update({"version": previous.get("version", ""), "path": previous["path"],
                         "enabled": bool(previous.get("enabled")),
                         "user_disabled": bool(previous.get("user_disabled")),
                         "previous": None, "needs_restart": False,
                         "last_error": "新版本启动失败，已回退到上一版本：%s" % reason})
            self._save_state(state)
            shutil.rmtree(failed, ignore_errors=True)
            return {"id": manifest["id"], "enabled": bool(info["enabled"]),
                    "rolled_back": True, "reason": reason,
                    "version": info["version"]}
        info["last_error"] = "启动检查失败：%s" % reason
        self._save_state(state)
        raise PluginError("插件无法启用：%s" % reason)

    def disable(self, pid: str) -> dict:
        with self._lock:
            state = self._load_state()
            info = state["plugins"].get(pid)
            if not info:
                raise PluginError("没有安装这个插件：%s" % pid)
            cancelled = self.tasks.cancel_plugin(pid, "插件已被禁用") if self._tasks else 0
            info["enabled"] = False
            info["user_disabled"] = True
            info["last_error"] = ""
            self._save_state(state)
            return {"id": pid, "enabled": False, "cancelled": cancelled,
                    "note": "已禁用；已开始的插件任务已取消，结果不会写入文档"}

    def uninstall(self, pid: str) -> dict:
        """卸载只删插件代码与它自己的缓存，绝不碰用户的文档、图片或导出文件。"""
        with self._lock:
            state = self._load_state()
            info = state["plugins"].get(pid)
            if not info:
                raise PluginError("没有安装这个插件：%s" % pid)
            cancelled = self.tasks.cancel_plugin(pid, "插件已被卸载") if self._tasks else 0
            folder = info.get("path") or ""
            previous = info.get("previous") or {}
            removed, needs_restart = True, False
            for target in (folder, previous.get("path") or ""):
                if not target:
                    continue
                if not is_within(self.plugins_dir, target):
                    continue                       # 只允许删插件仓库里的东西
                try:
                    shutil.rmtree(target)
                except FileNotFoundError:
                    pass
                except OSError:
                    removed = False
                    needs_restart = True
            cache = os.path.join(self.cache_dir, pid)
            if os.path.isdir(cache):
                try:
                    shutil.rmtree(cache)
                except OSError:
                    needs_restart = True
            try:
                parent = os.path.join(self.installed_dir, pid)
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
            except OSError:
                pass
            if needs_restart:
                info.update({"enabled": False, "user_disabled": True, "needs_restart": True,
                             "last_error": "插件文件被占用，已禁用；重启后完成卸载"})
            else:
                state["plugins"].pop(pid, None)
            self._save_state(state)
            return {"id": pid, "removed": removed, "needs_restart": needs_restart,
                    "cancelled": cancelled,
                    "note": ("已卸载插件；已插入的图片、源文档与已导出的文件都不受影响"
                             if not needs_restart else
                             "插件已禁用；文件被占用，重启后完成卸载（用户文件不受影响）")}

    def rollback(self, pid: str) -> dict:
        """手工回退到上一版本（回退不会自动复活用户禁用的插件）。"""
        with self._lock:
            state = self._load_state()
            info = state["plugins"].get(pid)
            if not info:
                raise PluginError("没有安装这个插件：%s" % pid)
            previous = info.get("previous") or {}
            if not previous.get("path") or not os.path.isdir(previous["path"]):
                raise PluginError("这个插件没有可回退的上一版本")
            failed = info.get("path")
            info.update({"version": previous.get("version", ""), "path": previous["path"],
                         "enabled": False, "user_disabled": True, "previous": None,
                         "last_error": "已回退到上一版本，需要时请手动启用"})
            self._save_state(state)
            if failed and failed != info["path"]:
                shutil.rmtree(failed, ignore_errors=True)
            return {"id": pid, "version": info["version"], "enabled": False}

    def cleanup(self):
        """清掉暂存目录与已经结束的任务工作目录。"""
        if os.path.isdir(self.staging_dir):
            for name in os.listdir(self.staging_dir):
                shutil.rmtree(os.path.join(self.staging_dir, name), ignore_errors=True)
        if self._tasks is not None:
            self._tasks.cleanup_finished()

    def shutdown(self):
        if self._tasks is not None:
            self._tasks.shutdown()
