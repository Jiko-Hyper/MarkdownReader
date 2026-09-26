# -*- coding: utf-8 -*-
"""核心导出服务（F06）：快照、预检、目标确认与原子提交都留在这里。

任务书 §8.1 把导出拆成两半：

* **核心**：决定“导出哪一份内容”（当前编辑内容还是磁盘版本）、做成不可变快照、
  做通用预检（缺图、失效链接、过宽表格、写不出来的公式、字体）、确认覆盖、把插件
  产物原子地替换到目标；
* **插件**：只把快照转换成某个格式的临时产物，并报告格式特有的警告。

这一层不依赖窗口控件，也不从界面上“抓内容”：桌面端与网页端都把同一份请求交给
:func:`plan` / :func:`prepare`，因此两端的行为（包括提示文字与阻止条件）是同一套。

为什么不把 markdown 直接丢给插件就算了：插件只看到一段文本，就没法知道“这张图
找不到”“这个公式写不出来”是**用户需要先决定**的事。预检把这类信息变成结构化清单，
由宿主决定哪些阻止导出、哪些只要确认。
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlsplit

from . import formula as FX
from .storage import is_within

#: 快照来源：界面上的当前编辑内容，或磁盘上已保存的版本。
SOURCE_BUFFER = "buffer"
SOURCE_DISK = "disk"
SOURCE_LABELS = {SOURCE_BUFFER: "当前编辑内容", SOURCE_DISK: "磁盘已保存版本"}

#: 打印版式默认值：A4、浅色，独立于三种阅读主题。两个导出插件共用同一份声明。
PAGE_DEFAULTS = {
    "size": "A4",
    "orientation": "portrait",
    "margin_mm": 18,
    "theme": "print",                 # 打印版式始终浅色，不跟随阅读主题
    "body_font": "Microsoft YaHei",
    "mono_font": "Consolas",
    "math_font": FX.MATH_FONT,
    "body_pt": 10.5,
    "code_pt": 9,
    "heading_scale": 1.25,
    "image_max_width": 0.92,          # 图片最多占正文宽度的比例
    "table_repeat_header": True,
    "code_wrap": True,
}

#: 超过这个列数就算“过宽表格”，导出前提示（插件仍会换行/压缩列宽，不会裁列）。
MAX_TABLE_COLUMNS = 12
#: 文档超过这个体量先提示一句，避免用户以为程序卡住。
LARGE_DOC_CHARS = 400_000
#: 一次导出最多内嵌多少张图片、总共多大（与 media.snapshot_images 的上限一致）。
MAX_RESOURCE_FILES = 200
MAX_RESOURCE_BYTES = 48 * 1024 * 1024

_LINK = re.compile(r'(?<!!)\[([^\]\n]*(?:\[[^\]\n]*\][^\]\n]*)*)\]\(\s*(<[^>]*>|[^)\s]+)')
_TABLE = re.compile(r'^[ \t]{0,3}\|.*\|[ \t]*$')
_TABLE_SEP = re.compile(r'^[ \t]{0,3}\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$')
_FENCE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})')


class ExportError(ValueError):
    """导出前置条件不满足（源文档、目标或授权有问题）。"""


#: 导出模板里点名的字体 → 本机常见的文件名（用于“字体替代”提示）。
_FONT_FILES = {
    "Microsoft YaHei": ("msyh.ttc", "msyh.ttf"),
    "Microsoft YaHei UI": ("msyh.ttc", "msyh.ttf"),
    "SimSun": ("simsun.ttc",),
    "Consolas": ("consola.ttf",),
    "Cambria Math": ("cambria.ttc", "cambriaz.ttf"),
    "Times New Roman": ("times.ttf",),
}


def font_available(name: str) -> bool:
    """本机有没有这个字体（只看文件名，够用来提示“会用相近字体替代”）。"""
    files = _FONT_FILES.get(name)
    if not files:
        return True
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    return any(os.path.exists(os.path.join(folder, item)) for item in files)


# --------------------------------------------------------------------------
# 快照
# --------------------------------------------------------------------------

def choose_source(has_buffer: bool, dirty: bool, asked: str = "") -> dict:
    """决定导出哪一份内容，并告诉界面要不要问用户。

    未保存（有编辑内容且与磁盘不一致）时**必须由用户选**，默认当前编辑内容；
    已经保存过的文档不需要问，直接用磁盘版本（与界面上看到的一致）。
    """
    if asked in (SOURCE_BUFFER, SOURCE_DISK):
        return {"source": asked, "needs_choice": False}
    if has_buffer and dirty:
        return {"source": SOURCE_BUFFER, "needs_choice": True}
    return {"source": SOURCE_DISK if not has_buffer else SOURCE_BUFFER,
            "needs_choice": False}


def detect_document(text: str) -> dict:
    """看一眼文本里有什么：图片、公式、表格、链接、代码块。预检与统计都用它。"""
    text = text or ""
    images = []
    for match in re.finditer(r'!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)', text):
        source = match.group(2).strip("<>")
        if not _in_code(text, match.start()):
            images.append(source)
    formulas = [entry for entry in FX.scan(text)]
    tables, columns = _tables(text)
    links = []
    for match in _LINK.finditer(text):
        if _in_code(text, match.start()):
            continue
        links.append(match.group(2).strip("<>"))
    return {
        "images": images,
        "formulas": formulas,
        "tables": tables,
        "table_columns": columns,
        "links": links,
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "headings": len(re.findall(r"^#{1,6}\s", text, re.M)),
        "external_links": sum(1 for link in links if _is_external(link)),
    }


def _in_code(text: str, offset: int) -> bool:
    """粗判这个位置是不是在围栏代码块或行内代码里（预检宁可少报也不误报）。"""
    position = 0
    fence = None
    for line in text.split("\n"):
        end = position + len(line)
        inside = position <= offset <= end
        marker = _FENCE.match(line)
        if marker:
            if inside:
                return True                    # 围栏标记所在的行本身也算代码
            token = marker.group(1)[0] * 3
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
        elif inside:
            if fence is not None:
                return True
            head = text.rfind("\n", 0, offset) + 1
            return text[head:offset].count("`") % 2 == 1
        position = end + 1
    return False


def _is_external(link: str) -> bool:
    parsed = urlsplit(link or "")
    return bool(parsed.scheme or parsed.netloc) or (link or "").startswith("//")


def _tables(text: str):
    """返回 ``(表格数, [(列数, 行数)])``；表格行按管道数统计（与渲染器的口径一致）。"""
    rows, tables = text.split("\n"), []
    index, count = 0, 0
    while index < len(rows):
        if _TABLE.match(rows[index]) and index + 1 < len(rows) and _TABLE_SEP.match(rows[index + 1]):
            header = rows[index].strip().strip("|").split("|")
            body = 0
            walk = index + 2
            while walk < len(rows) and _TABLE.match(rows[walk]):
                body += 1
                walk += 1
            tables.append((len(header), body))
            count += 1
            index = walk
            continue
        index += 1
    return count, tables


def snapshot(*, identity: str, markdown: str, revision: str, source: str,
             root: str = "") -> dict:
    """不可变快照：之后源文件或缓冲区再怎么变都不影响这一次导出。"""
    identity = os.path.abspath(str(identity or ""))
    if not identity:
        raise ExportError("没有指定要导出的文档")
    if not isinstance(markdown, str):
        raise ExportError("快照缺少正文")
    return {"identity": identity, "revision": revision or "", "markdown": markdown,
            "source": source if source in SOURCE_LABELS else SOURCE_DISK,
            "name": os.path.basename(identity), "root": root,
            "folder": os.path.dirname(identity)}


# --------------------------------------------------------------------------
# 预检
# --------------------------------------------------------------------------

def _item(kind: str, level: str, message: str, detail: str = "") -> dict:
    return {"kind": kind, "level": level, "message": message, "detail": detail}


def preflight(snapshot: dict, *, resource_reader=None, fonts=None) -> dict:
    """通用预检：严重问题阻止导出，可降级项只要用户确认。

    ``resource_reader(source) -> path`` 由调用方给出（它知道授权范围），这样同一份
    预检逻辑在桌面端与网页端都只走**已授权**的读取路径。
    """
    text = snapshot.get("markdown") or ""
    found = detect_document(text)
    items: list[dict] = []

    if not text.strip():
        items.append(_item("empty", "error", "这篇文档是空的，没有可导出的内容"))
    if found["chars"] > LARGE_DOC_CHARS:
        items.append(_item("large", "warn", "文档很大（约 %d 千字），导出可能需要一会儿"
                           % (found["chars"] // 1000)))

    # 图片：找不到、越界都算严重问题（导出稿会缺内容）
    missing = []
    outside = []
    broken_reader = []
    for source in dict.fromkeys(found["images"]):
        if _is_external(source):
            items.append(_item("remote_image", "warn",
                               "远程图片不会自动抓取：%s" % source))
            continue
        if resource_reader is None:
            continue
        try:
            path = resource_reader(source)
        except FileNotFoundError:
            missing.append(source)
            continue
        except PermissionError:
            outside.append(source)
            continue
        except Exception as error:
            # 授权/解析这一层自己出错时要说清是“读不到”，不能谎报成“文件不存在”
            broken_reader.append("%s（%s: %s）" % (source, type(error).__name__, error))
            continue
        if not path:
            missing.append(source)
    if missing:
        items.append(_item("missing_image", "error",
                           "有 %d 张图片找不到：%s" % (len(missing), "、".join(missing[:3]))))
    if outside:
        items.append(_item("outside_image", "error",
                           "有 %d 张图片不在已授权目录里：%s" % (len(outside), "、".join(outside[:3]))))
    if broken_reader:
        items.append(_item("resource_error", "error",
                           "有 %d 张图片读不出来：%s" % (len(broken_reader),
                                                       "、".join(broken_reader[:2]))))

    # 公式：写不出来的按“降级”处理（导出稿里会留原始表达式）
    broken = []
    for entry in found["formulas"]:
        if entry.get("error"):
            broken.append((entry.get("tex") or "", entry["error"]))
            continue
        checked = FX.validate(entry["tex"])
        if not checked["ok"]:
            broken.append((entry["tex"], checked["reason"]))
    if broken:
        samples = "；".join("%s（%s）" % (tex[:24], reason) for tex, reason in broken[:3])
        items.append(_item("formula", "warn",
                           "有 %d 个公式暂时画不出来，导出稿里会保留原始表达式：%s"
                           % (len(broken), samples)))

    # 表格：过宽只提示，不阻止（插件会换行或压缩列宽，不裁列）
    wide = [columns for columns, _body in found["table_columns"] if columns > MAX_TABLE_COLUMNS]
    if wide:
        items.append(_item("wide_table", "warn",
                           "有 %d 张表超过 %d 列（最多 %d 列），导出时会压缩或换行"
                           % (len(wide), MAX_TABLE_COLUMNS, max(wide))))

    # 本地链接：指向的文件不存在就是死链（只在授权范围内查）
    dead = []
    if resource_reader is not None:
        for link in dict.fromkeys(found["links"]):
            if _is_external(link) or link.startswith("#"):
                continue
            try:
                if not resource_reader(link):
                    dead.append(link)
            except Exception:
                dead.append(link)
    if dead:
        items.append(_item("dead_link", "warn",
                           "有 %d 个本地链接指向的文件不存在：%s" % (len(dead), "、".join(dead[:3]))))

    # 字体：导出用的字体不在本机时说明会替代
    check_font = fonts or font_available
    absent = [name for name in (PAGE_DEFAULTS["body_font"], PAGE_DEFAULTS["mono_font"],
                                PAGE_DEFAULTS["math_font"]) if not check_font(name)]
    if absent:
        items.append(_item("font", "warn",
                           "本机没有这些字体，导出时会用相近字体替代：%s" % "、".join(absent)))

    errors = [item for item in items if item["level"] == "error"]
    warnings = [item for item in items if item["level"] == "warn"]
    return {"ok": not errors, "blocked": bool(errors), "items": items,
            "errors": errors, "warnings": warnings,
            "stats": {"images": len(set(found["images"])), "formulas": len(found["formulas"]),
                      "tables": found["tables"], "links": len(found["links"]),
                      "chars": found["chars"], "lines": found["lines"],
                      "headings": found["headings"]},
            "summary": _summary(errors, warnings)}


def _summary(errors, warnings) -> str:
    if errors:
        return "有 %d 个必须先处理的问题" % len(errors)
    if warnings:
        return "有 %d 条提示，可以继续导出" % len(warnings)
    return "没有发现问题"


def page_options(extra: dict | None = None) -> dict:
    """打印版式选项：A4、浅色，独立于阅读主题；插件可以覆盖其中的条目。"""
    options = dict(PAGE_DEFAULTS)
    options.update({key: value for key, value in (extra or {}).items() if value is not None})
    return options


def resources(markdown: str, *, identify, opener, limit_files: int = MAX_RESOURCE_FILES,
              limit_bytes: int = MAX_RESOURCE_BYTES) -> tuple[list, dict]:
    """把正文里真正用到的本地图片读成附件快照。

    ``identify(source)`` 把 ```` ![说明](相对路径) ```` 解析成授权范围内的真实路径；
    ``opener(path)`` 负责读字节（便于测试与将来换实现）。任何一张图读不到都抛错——
    导出稿缺图比导出失败更糟。
    """
    from .media import image_matches
    files: list[dict] = []
    manifest: dict[str, dict] = {}
    total = 0
    for match in image_matches(markdown or ""):
        source = match.group(2).strip("<>")
        if source in manifest or _is_external(source):
            continue
        path = identify(source)
        if not path or not os.path.isfile(path):
            raise ExportError("导出需要的图片找不到：%s" % unquote(source))
        if len(files) >= limit_files:
            raise ExportError("一次导出最多内嵌 %d 张图片，请先减少图片" % limit_files)
        data = opener(path)
        total += len(data)
        if total > limit_bytes:
            raise ExportError("图片总大小超过 %d MB，请减少图片后导出" % (limit_bytes // (1024 * 1024)))
        name = "resource-%04d%s" % (len(files), os.path.splitext(path)[1].lower())
        files.append({"name": name, "data": data})
        manifest[source] = {"name": name, "size": len(data)}
    return files, manifest


def same_file(left: str, right: str) -> bool:
    """导出目标不能是源文档本身（含大小写、短名与链接别名）。"""
    if not left or not right:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def target_conflict(dest: str, confirmed) -> dict | None:
    """目标已存在且没被确认过就返回冲突描述（界面据此询问覆盖）。"""
    if not dest or not os.path.isfile(dest):
        return None
    from . import documents as D
    current = D.revision(dest)
    if confirmed is True or (isinstance(confirmed, str) and confirmed and confirmed == current):
        return None
    return {"conflict": True, "revision": current, "path": dest,
            "error": "导出目标已存在，确认后覆盖：%s" % dest}


def within_root(root: str, path: str) -> bool:
    return bool(root) and is_within(root, path)
