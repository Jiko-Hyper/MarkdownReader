# -*- coding: utf-8 -*-
"""管道表格的识别、修改与序列化。

桌面端（Tk 编辑控件）与网页端（textarea）**共用这一份规则**：网页端通过
``/api/edit/table`` 调它，桌面端直接 import。放在核心而不是各自界面里，
是因为“什么样的表格能改、改完怎么写回去”属于内容规则，不属于界面。

三条原则（对应任务书 F04）：

1. **只改能看懂的结构**：表头、分隔行、列数、管道写法逐项核对；任何一处
   对不上就返回 ``ok=False`` 与原因，界面保留源码并提示，不猜测着重写。
2. **改的是内容，不是排版**：单元格文本、对齐、行列数原样保留（``|`` 与
   ``\\`` 写入时转义、读回时还原）。被修改的表格会按本模块的规范样式重新
   排版（分隔行的破折号数量、单元格两侧空格），这一点写在返回值与说明里，
   不假装“一个字节都没动”。
3. **改动只发生在缓冲区**：本模块是纯函数，不碰磁盘、不碰界面控件。

不支持合并单元格、嵌套表格和 Excel 公式：识别到这类结构就拒绝修改（见
:func:`review` 的原因文本）。
"""

from __future__ import annotations

import re

MAX_COLUMNS = 24
MAX_ROWS = 400
MAX_CELL_CHARS = 400

ALIGNS = ("left", "center", "right")

_DELIM_CELL = re.compile(r"^:?-+:?$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_NEWLINE = re.compile(r"\r\n|\n|\r")


# --------------------------------------------------------------------------
# 行级读写
# --------------------------------------------------------------------------

def _ends_with_escaped_pipe(text: str) -> bool:
    """``|a\\|`` 的结尾竖线属于单元格内容，不是行分隔符。"""
    backslashes = 0
    index = len(text) - 2
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def split_row(line: str) -> tuple[list[str], bool, bool]:
    """把一行拆成 ``(单元格原文, 是否有首竖线, 是否有尾竖线)``。

    单元格原文保持原样（含转义与两侧空白）；用户看到的文本用 :func:`cell_text`。
    """
    stripped = line.rstrip("\r").strip()
    if not stripped:
        return [], False, False
    leading = stripped.startswith("|")
    trailing = len(stripped) > 1 and stripped.endswith("|") and not _ends_with_escaped_pipe(stripped)
    body = stripped[1:] if leading else stripped
    if trailing:
        body = body[:-1]
    cells: list[str] = []
    buffer: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            buffer.append(char)
            buffer.append(body[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append("".join(buffer))
    return cells, leading, trailing


def cell_text(raw: str) -> str:
    """单元格原文 → 用户看到的文本（``\\|`` → ``|``，``\\\\`` → ``\\``）。"""
    out: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw) and raw[index + 1] in "|\\":
            out.append(raw[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def escape_cell(text: str) -> str:
    """用户文本 → 单元格原文；反斜杠先转义，否则 ``\\|`` 会被读成字面竖线。"""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def align_spec(align: str) -> str:
    if align == "center":
        return ":---:"
    if align == "right":
        return "---:"
    return "---"


def align_of(spec: str) -> str:
    spec = spec.strip()
    left, right = spec.startswith(":"), spec.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def _padding(raw: str) -> str | None:
    """单元格两侧空白的可识别形态：无、单空格、其他（不可原样保留）。

    没有首尾竖线的表格里，第一个单元格只有尾空格、最后一个只有首空格，
    所以“单空格”包含三种写法。
    """
    if raw == raw.strip():
        return "none"
    stripped = raw.strip()
    if raw in (" " + stripped, stripped + " ", " " + stripped + " "):
        return "one"
    return None


# --------------------------------------------------------------------------
# 块识别
# --------------------------------------------------------------------------

def _collect_rows(lines: list[str], start: int) -> int:
    index = start
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or "|" not in raw or _FENCE.match(raw):
            break
        index += 1
    return index


def blocks(text: str) -> list[dict]:
    """文档里所有管道表格块（跳过围栏代码块内部）。"""
    lines = _NEWLINE.sub("\n", text).split("\n")
    found: list[dict] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        raw = lines[index]
        marker = _FENCE.match(raw)
        if marker:
            token = marker.group(1)[0] * 3
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            index += 1
            continue
        if fence is not None or "|" not in raw or index + 1 >= len(lines):
            index += 1
            continue
        header, leading, trailing = split_row(raw)
        specs, _spec_leading, _spec_trailing = split_row(lines[index + 1])
        if not header or len(specs) != len(header) or not all(_DELIM_CELL.match(s.strip()) for s in specs):
            index += 1
            continue
        end = _collect_rows(lines, index + 2)
        rows = [split_row(lines[position]) for position in range(index + 2, end)]
        found.append({"start": index, "end": end, "header": header, "specs": specs,
                      "aligns": [align_of(spec) for spec in specs],
                      "rows": [cells for cells, _l, _t in rows],
                      "row_flags": [(left, right) for _cells, left, right in rows],
                      "leading": leading, "trailing": trailing,
                      "lines": lines[index:end]})
        index = max(end, index + 1)
    return found


def block_at_line(text: str, line: int) -> dict | None:
    """光标所在行（0 基）属于哪个表格块。"""
    for block in blocks(text):
        if block["start"] <= line < block["end"]:
            return block
    return None


def line_of_offset(text: str, offset: int) -> int:
    offset = max(0, min(len(text), int(offset or 0)))
    return text.count("\n", 0, offset)


def block_at_offset(text: str, offset: int) -> dict | None:
    return block_at_line(text, line_of_offset(text, offset))


# --------------------------------------------------------------------------
# 可修改性核对
# --------------------------------------------------------------------------

def review(block: dict) -> tuple[bool, str]:
    """返回 ``(能否安全修改, 原因)``；原因在拒绝时给用户看。"""
    header = block["header"]
    if not header:
        return False, "表头是空的"
    if len(header) > MAX_COLUMNS:
        return False, "表格有 %d 列，超过本版上限 %d 列" % (len(header), MAX_COLUMNS)
    if len(block["rows"]) > MAX_ROWS:
        return False, "表格有 %d 行，超过本版上限 %d 行" % (len(block["rows"]), MAX_ROWS)
    styles = {_padding(raw) for raw in header}
    if len(styles) > 1 or None in styles:
        return False, "表头单元格两侧的空白不统一，改动会顺手改掉排版"
    for row, flags in zip(block["rows"], block["row_flags"]):
        if len(row) != len(header):
            return False, "有一行是 %d 个单元格，与 %d 列的表头对不上" % (len(row), len(header))
        if flags != (block["leading"], block["trailing"]):
            return False, "各行的首尾竖线写法不一致"
    for group in (header, *block["rows"]):
        for raw in group:
            if len(cell_text(raw).strip()) > MAX_CELL_CHARS:
                return False, "单元格文本超过 %d 个字符" % MAX_CELL_CHARS
    return True, ""


def model(block: dict) -> dict:
    """块的语义模型：单元格已还原转义并去掉两侧空白。"""
    return {"header": [cell_text(raw).strip() for raw in block["header"]],
            "rows": [[cell_text(raw).strip() for raw in row] for row in block["rows"]],
            "aligns": list(block["aligns"])}


def read(text: str, offset: int = 0, line: int | None = None) -> dict:
    """光标处的表格：能否修改、模型是什么、源码范围在哪。"""
    block = block_at_line(text, line) if line is not None else block_at_offset(text, offset)
    if block is None:
        return {"ok": False, "reason": "光标不在普通管道表格里（围栏代码块内的表格不算）",
                "found": False}
    editable, reason = review(block)
    return {"ok": editable, "reason": reason, "found": True,
            "start": block["start"], "end": block["end"],
            "lines": block["end"] - block["start"],
            "leading": block["leading"], "trailing": block["trailing"],
            "pad": _padding(block["header"][0]) == "one" if block["header"] else False,
            "table": model(block) if editable else {"header": [], "rows": [], "aligns": []},
            "raw": "\n".join(block["lines"])}


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------

def render(header, rows, aligns, *, leading=True, trailing=True, pad=True) -> str:
    """模型 → 管道表格文本（不带结尾换行）。"""
    columns = max(1, len(header))
    cells = [[escape_cell(_clean_cell(value)) for value in header]]
    for row in rows:
        line = [_clean_cell(value) for value in row][:columns]
        cells.append([escape_cell(value) for value in line] + [""] * (columns - len(line)))
    specs = [align_spec(align if align in ALIGNS else "left")
             for align in (list(aligns) + ["left"] * columns)[:columns]]

    def line(values: list[str]) -> str:
        text = "|".join((" " + value + " ") if pad else value for value in values)
        text = ("|" + text) if leading else text.lstrip(" ")
        text = (text + "|") if trailing else text.rstrip(" ")
        return text

    return "\n".join([line(cells[0]), line(specs)] + [line(row) for row in cells[1:]])


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def build(columns, rows, *, header: bool = True, aligns=None, fills=None) -> dict:
    """新建表格的模型；``fills`` 是对话框里已填的二维文本。"""
    columns = _limit(columns, 1, MAX_COLUMNS, "列")
    rows = _limit(rows, 0, MAX_ROWS, "行")
    aligns = (list(aligns or []) + ["left"] * columns)[:columns]
    head = [""] * columns
    body = [[""] * columns for _ in range(rows)]
    if fills:
        data = [([_clean_cell(value) for value in list(row)][:columns] + [""] * columns)[:columns]
                for row in fills]
        if header:
            head = data[0] if data else [""] * columns
            body = data[1:]
        else:
            body = data
    return {"header": head, "rows": body, "aligns": aligns}


def _limit(value, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s数必须是数字" % label)
    if number < low or number > high:
        raise ValueError("%s数要在 %d 到 %d 之间" % (label, low, high))
    return number


def _clean_cell(value) -> str:
    return str(value if value is not None else "").replace("\t", " ").strip()


def check_cell(value) -> str | None:
    """单元格内容的问题描述；``None`` 表示可以写入。"""
    text = "" if value is None else str(value)
    if "\n" in text or "\r" in text:
        return "单元格只能是一行文本，请把换行整理掉"
    if len(text.strip()) > MAX_CELL_CHARS:
        return "单元格超过 %d 个字符" % MAX_CELL_CHARS
    return None


# --------------------------------------------------------------------------
# 结构操作
# --------------------------------------------------------------------------

def apply_op(data: dict, op: str, **kw) -> dict:
    """在模型上做一次结构修改，返回新模型；不认识的输入直接拒绝。"""
    header = list(data.get("header") or [])
    rows = [list(row) for row in (data.get("rows") or [])]
    columns = len(header)
    aligns = (list(data.get("aligns") or []) + ["left"] * columns)[:columns]

    if op == "set_cell":
        row, column = int(kw.get("row", 0)), int(kw.get("column", 0))
        value = kw.get("value", "")
        problem = check_cell(value)
        if problem:
            raise ValueError(problem)
        if not (0 <= column < columns):
            raise ValueError("列号超出范围")
        if row == -1:
            header[column] = _clean_cell(value)
        elif 0 <= row < len(rows):
            rows[row][column] = _clean_cell(value)
        else:
            raise ValueError("行号超出范围")
    elif op == "set_header":
        header = [_clean_cell(value) for value in (kw.get("values") or [])][:columns]
        header += [""] * (columns - len(header))
    elif op == "set_align":
        column = int(kw.get("column", 0))
        if not (0 <= column < columns):
            raise ValueError("列号超出范围")
        align = kw.get("align", "left")
        if align not in ALIGNS:
            raise ValueError("对齐方式只能是 left / center / right")
        aligns[column] = align
    elif op == "insert_row":
        index = max(0, min(len(rows), int(kw.get("index", len(rows)))))
        line = [_clean_cell(value) for value in (kw.get("values") or [])][:columns]
        rows.insert(index, line + [""] * (columns - len(line)))
    elif op == "delete_row":
        index = int(kw.get("index", -1))
        if not (0 <= index < len(rows)):
            raise ValueError("行号超出范围")
        rows.pop(index)
    elif op == "insert_col":
        index = max(0, min(columns, int(kw.get("index", columns))))
        if columns + 1 > MAX_COLUMNS:
            raise ValueError("最多 %d 列" % MAX_COLUMNS)
        header.insert(index, _clean_cell(kw.get("title", "")))
        for row in rows:
            row.insert(index, _clean_cell(kw.get("value", "")))
        aligns.insert(index, kw.get("align") if kw.get("align") in ALIGNS else "left")
    elif op == "delete_col":
        index = int(kw.get("index", -1))
        if not (0 <= index < columns):
            raise ValueError("列号超出范围")
        if columns <= 1:
            raise ValueError("表格至少要留一列")
        header.pop(index)
        for row in rows:
            row.pop(index)
        aligns.pop(index)
    else:
        raise ValueError("不认识的表格操作：%s" % op)
    return {"header": header, "rows": rows, "aligns": aligns}


def _splice(text: str, start: int, end: int, body: list[str]) -> str:
    newline = newline_of(text)
    lines = text.split(newline)
    lines[start:end] = body
    return newline.join(lines)


def write_model(text: str, header, rows, aligns, *, line: int | None = None,
                offset: int = 0) -> dict:
    """把光标所在的表格整体换成给定模型（“编辑表格”对话框的提交路径）。

    会先按 :func:`read` 核对这张表能不能安全修改；不能就原样返回失败，
    不会去猜用户想改的是哪一张表。
    """
    for group in (header, *rows):
        for value in group:
            problem = check_cell(value)
            if problem:
                return {"ok": False, "reason": problem}
    info = read(text, offset=offset, line=line)
    if not info["found"]:
        return {"ok": False, "reason": info["reason"], "found": False}
    if not info["ok"]:
        return {"ok": False, "found": True,
                "reason": "这个表格不能安全修改：%s。已保留源码。" % info["reason"]}
    data = {"header": [str(value if value is not None else "") for value in header],
            "rows": [[str(value if value is not None else "") for value in row] for row in rows],
            "aligns": [align if align in ALIGNS else "left" for align in aligns]}
    body = render(data["header"], data["rows"], data["aligns"],
                  leading=info["leading"], trailing=info["trailing"], pad=info["pad"]).split("\n")
    return {"ok": True, "text": _splice(text, info["start"], info["end"], body), "table": data,
            "start": info["start"], "end": info["start"] + len(body)}


def operate(text: str, op: str, offset: int = 0, line: int | None = None, **kw) -> dict:
    """按光标位置改表格；失败时返回 ``ok=False`` 与原因，正文一个字节都不动。"""
    if op == "insert_table":
        return insert(text, offset=offset, line=line, **kw)
    if op == "set_table":
        return write_model(text, kw.get("header") or [], kw.get("rows") or [],
                           kw.get("aligns") or [], line=line, offset=offset)
    info = read(text, offset=offset, line=line)
    if not info["found"]:
        return {"ok": False, "reason": info["reason"], "found": False}
    if not info["ok"]:
        return {"ok": False, "found": True,
                "reason": "这个表格不能安全修改：%s。已保留源码。" % info["reason"]}
    try:
        data = apply_op(info["table"], op, **kw)
    except ValueError as error:
        return {"ok": False, "reason": str(error), "found": True}
    body = render(data["header"], data["rows"], data["aligns"],
                  leading=info["leading"], trailing=info["trailing"], pad=info["pad"]).split("\n")
    return {"ok": True, "text": _splice(text, info["start"], info["end"], body), "table": data,
            "start": info["start"], "end": info["start"] + len(body)}


def _place(lines: list[str], at: int, block: list[str]) -> tuple[int, list[str], int, int]:
    """``block`` 放在第 ``at`` 行之后，需要时补空行。

    返回 ``(插入下标, 要插入的行, 表格首行, 表格末行)``——插入下标与“表格首行”
    相差 ``before`` 的那一行空行，调用方按前者写回才对得上行号。
    """
    at = max(0, min(len(lines), at))
    before = [""] if at > 0 and lines[at - 1].strip() else []
    after = [""] if at < len(lines) and lines[at].strip() else []
    return at, before + block + after, at + len(before), at + len(before) + len(block)


def _insertion_point(lines: list[str], text: str, offset: int, line: int | None) -> int:
    at = line_of_offset(text, offset) if line is None else max(0, min(len(lines), int(line)))
    if at < len(lines) and lines[at].strip():
        at += 1                      # 光标停在正文行上：插到这一行之后
    return at


def insert(text: str, columns=3, rows=2, *, header: bool = True, aligns=None, fills=None,
           offset: int = 0, line: int | None = None) -> dict:
    """在光标所在行之后插入一张新表格（前后留空行，不与正文粘连）。"""
    try:
        data = build(columns, rows, header=header, aligns=aligns, fills=fills)
    except ValueError as error:
        return {"ok": False, "reason": str(error)}
    newline = newline_of(text)
    lines = text.split(newline)
    block = render(data["header"], data["rows"], data["aligns"]).split("\n")
    index, body, start, end = _place(lines, _insertion_point(lines, text, offset, line), block)
    lines[index:index] = body
    return {"ok": True, "text": newline.join(lines), "table": data, "start": start, "end": end,
            "columns": len(data["header"]), "header": bool(header), "rows": len(data["rows"])}


# --------------------------------------------------------------------------
# 粘贴 TSV
# --------------------------------------------------------------------------

def parse_tsv(payload: str) -> dict:
    """剪贴板文本 → 矩形行列表，并列出所有不能悄悄处理的地方。"""
    raw = _NEWLINE.sub("\n", payload or "")
    if raw.endswith("\n"):
        raw = raw[:-1]
    lines = raw.split("\n") if raw else []
    warnings: list[str] = []
    rows: list[list[str]] = []
    ragged: list[int] = []
    width = 0
    for number, line in enumerate(lines, 1):
        if '"' in line:
            warnings.append("第 %d 行含引号：本版不解析 CSV 引号，按普通文本处理" % number)
        cells = [_clean_cell(cell) for cell in line.split("\t")]
        if len(cells) > MAX_COLUMNS:
            return {"ok": False, "rows": [], "columns": len(cells), "has_header": False,
                    "warnings": ["第 %d 行有 %d 列，超过本版上限 %d 列"
                                 % (number, len(cells), MAX_COLUMNS)]}
        if width == 0:
            width = len(cells)
        elif len(cells) != width:
            ragged.append(number)
        rows.append(cells)
    if not rows:
        return {"ok": False, "rows": [], "columns": 0, "has_header": False,
                "warnings": ["剪贴板里没有可用的表格文本"]}
    if ragged:
        warnings.append("第 %s 行的列数与第一行不同，已按空单元格补齐"
                        % "、".join(str(number) for number in ragged[:5]))
    if len(rows) > MAX_ROWS + 1:
        return {"ok": False, "rows": [], "columns": width, "has_header": False,
                "warnings": ["粘贴内容有 %d 行，超过本版上限 %d 行" % (len(rows), MAX_ROWS + 1)]}
    return {"ok": True, "rows": [row + [""] * (width - len(row)) for row in rows],
            "columns": width, "warnings": warnings, "ragged": ragged,
            "has_header": len(rows) > 1}


def from_tsv(payload: str, *, header: bool = True) -> dict:
    """TSV → 表格模型（默认第一行作表头）。"""
    parsed = parse_tsv(payload)
    if not parsed["ok"]:
        return {"ok": False, "reason": "；".join(parsed["warnings"]) or "没有可用的表格内容",
                "warnings": parsed["warnings"]}
    rows = parsed["rows"]
    fills = rows if header else [[""] * parsed["columns"]] + rows
    data = build(parsed["columns"], 0, header=True, aligns=["left"] * parsed["columns"], fills=fills)
    return {"ok": True, "table": data, "warnings": parsed["warnings"],
            "columns": parsed["columns"], "rows": len(rows) - (1 if header else 0)}


def paste_tsv(text: str, payload: str, offset: int = 0, *, start: int | None = None,
              end: int | None = None, header: bool = True) -> dict:
    """把 TSV 变成管道表格：有选区就替换选区，否则插到光标所在行之后。

    只有用户确认之后才应该调用它——预览用 :func:`parse_tsv`，本函数负责写入。
    """
    built = from_tsv(payload, header=header)
    if not built["ok"]:
        return {"ok": False, "reason": built["reason"], "warnings": built["warnings"]}
    data = built["table"]
    newline = newline_of(text)
    lines = text.split(newline)
    block = render(data["header"], data["rows"], data["aligns"]).split("\n")
    if start is None or end is None or end <= start:
        index, body, first, last = _place(lines, _insertion_point(lines, text, offset, None), block)
        lines[index:index] = body
    else:
        first = line_of_offset(text, start)
        last = line_of_offset(text, end)
        if end > start and last > first and end == _line_start(text, last):
            last -= 1                      # 选区正好停在行首：不要吞掉这一行
        last += 1
        body = list(block)
        pad_before = first > 0 and bool(lines[first - 1].strip())
        pad_after = last < len(lines) and bool(lines[last].strip())
        if pad_before:
            body = [""] + body             # 表格前面要有空行，否则会被当成上一段正文
        if pad_after:
            body = body + [""]
        lines[first:last] = body
        first = first + (1 if pad_before else 0)
        last = first + len(block)
    return {"ok": True, "text": newline.join(lines), "table": data,
            "warnings": built["warnings"], "columns": built["columns"], "rows": built["rows"],
            "start": first, "end": last}


def _line_start(text: str, line: int) -> int:
    """第 ``line`` 行（0 基）在 ``text`` 里的起始偏移。"""
    if line <= 0:
        return 0
    return sum(len(part) + 1 for part in text.split("\n")[:line])
