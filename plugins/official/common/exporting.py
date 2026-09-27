"""Structured Markdown conversion shared by the two independently packaged exporters.

中文正文字体（SimSun / 微软雅黑 / 黑体）都没有数学减号 U+2212 和上下标 U+2076 ⁻ U+2081 ₁ 这类字形，
而 ReportLab 遇到没有字形的字符仍会照画一个空的 .notdef——导出稿上就成了一块空白（维护者 2026-09-25
反馈的「转换后出现乱码」）。这里按字体文件里真实存在的字形逐个字符挑字体：基础字体画不出来时改用下面
这串覆盖了这些符号的西文字体，Word 与 PDF 走同一套判断；回退字体也没有字形时导出照常完成，但会报告
受影响的字符，不假装成功。
"""
import bisect
import html
import os
import re
import struct
from urllib.parse import unquote, urlsplit

FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")

# 每一项是（Word 字体族名, 常规字体文件, 粗体字体文件）；顺序就是回退顺序。
SYMBOL_FONTS = (("Times New Roman", "times.ttf", "timesbd.ttf"),
                ("Cambria", "cambria.ttc", "cambriab.ttf"),
                ("Segoe UI Symbol", "seguisym.ttf", "seguisym.ttf"))
BODY_FONT = ("Microsoft YaHei", "msyh.ttc", "msyhbd.ttc")
CODE_FONT = ("Consolas", "consola.ttf", "consolab.ttf")
# 代码字体（Consolas）没有中文字形，代码里的中文要退回正文字体，而不是报“没有字形”。
CODE_FALLBACKS = SYMBOL_FONTS + (BODY_FONT,)
# ReportLab 把段内图片放在正文框往右约 6pt 的位置：宽度上限必须预留这段偏移，
# 否则图片会压过右边距（495pt 的公式曾因此多出 2pt）。
INLINE_IMAGE_INSET = 12

_RANGES = {}


def _table_offset(blob, index):
    """字体集合（.ttc）里第 index 个字面的表目录偏移；普通字体返回 0。"""
    if blob[:4] != b"ttcf":
        return 0 if index == 0 else None
    if len(blob) < 12:
        return None
    count = struct.unpack(">I", blob[8:12])[0]
    if index >= count:
        return None
    return struct.unpack(">I", blob[12 + index * 4:16 + index * 4])[0]


def _subtable_spans(blob, table):
    """读一个 cmap 子表，返回有真实字形的码点区间 [(start, end)]；不认识的形式返回空。"""
    form = struct.unpack(">H", blob[table:table + 2])[0]
    spans = []
    if form == 0:
        count = struct.unpack(">H", blob[table + 2:table + 4])[0]
        for code in range(min(count, 256)):
            if blob[table + 6 + code]:
                spans.append((code, code))
    elif form == 4:
        segments = struct.unpack(">H", blob[table + 6:table + 8])[0] // 2
        ends = struct.unpack(">%dH" % segments, blob[table + 14:table + 14 + 2 * segments])
        starts_at = table + 16 + 2 * segments
        starts = struct.unpack(">%dH" % segments, blob[starts_at:starts_at + 2 * segments])
        deltas_at = starts_at + 2 * segments
        deltas = struct.unpack(">%dh" % segments, blob[deltas_at:deltas_at + 2 * segments])
        offsets_at = deltas_at + 2 * segments
        offsets = struct.unpack(">%dH" % segments, blob[offsets_at:offsets_at + 2 * segments])
        for i in range(segments):
            if starts[i] > ends[i]:
                continue
            for code in range(starts[i], ends[i] + 1):
                if deltas[i]:
                    glyph = (code + deltas[i]) & 0xFFFF
                elif offsets[i]:
                    slot = offsets_at + 2 * i + offsets[i] + 2 * (code - starts[i])
                    glyph = struct.unpack(">H", blob[slot:slot + 2])[0]
                else:
                    glyph = 0
                if glyph:
                    spans.append((code, code))
    elif form == 6:
        first, count = struct.unpack(">HH", blob[table + 6:table + 10])
        for i in range(count):
            if struct.unpack(">H", blob[table + 10 + 2 * i:table + 12 + 2 * i])[0]:
                spans.append((first + i, first + i))
    elif form == 12:
        groups = struct.unpack(">I", blob[table + 12:table + 16])[0]
        for group in range(groups):
            at = table + 16 + 12 * group
            start, end, glyph = struct.unpack(">III", blob[at:at + 12])
            if glyph:
                spans.append((start, end))
    return spans


def _unicode_subtable(platform, encoding):
    """只认 Unicode 子表；符号字体里的 Mac Roman 子表会谎报一堆控制字符有字形。"""
    return platform == 0 or (platform == 3 and encoding in (0, 1, 10))


def glyph_spans(path, index=0):
    """字体文件里有字形的码点区间，返回 (starts, ends)；读不到或坏了就返回空。"""
    try:
        info = os.stat(path)
    except OSError:
        return (), ()
    key = (path, index, info.st_mtime_ns, info.st_size)
    cached = _RANGES.get(key)
    if cached is None:
        merged = []
        try:
            with open(path, "rb") as stream:
                blob = stream.read()
            base = _table_offset(blob, index)
            if base is not None:
                tables = struct.unpack(">H", blob[base + 4:base + 6])[0]
                for i in range(tables):
                    record = base + 12 + 16 * i
                    if blob[record:record + 4] != b"cmap":
                        continue
                    cmap = struct.unpack(">I", blob[record + 8:record + 12])[0]
                    subtables = struct.unpack(">H", blob[cmap + 2:cmap + 4])[0]
                    spans = []
                    for j in range(subtables):
                        at = cmap + 4 + 8 * j
                        platform, encoding = struct.unpack(">HH", blob[at:at + 4])
                        if not _unicode_subtable(platform, encoding):
                            continue
                        spans.extend(_subtable_spans(blob, cmap + struct.unpack(">I", blob[at + 4:at + 8])[0]))
                    for start, end in sorted(spans):
                        if merged and start <= merged[-1][1] + 1:
                            merged[-1][1] = max(merged[-1][1], end)
                        else:
                            merged.append([start, end])
                    break
        except Exception:
            merged = []
        cached = (tuple(row[0] for row in merged), tuple(row[1] for row in merged))
        _RANGES[key] = cached
    return cached


def _covered(spans, char):
    starts, ends = spans
    if not starts:
        return False
    code = ord(char)
    position = bisect.bisect_right(starts, code) - 1
    return position >= 0 and code <= ends[position]


def _file_of(entry, bold):
    return os.path.join(FONT_DIR, entry[2] if bold else entry[1])


def _base_has_glyph(entry, char, bold):
    """基础字体是否有这个字形；读不到字体文件时按“有”处理，保持原有行为。"""
    spans = glyph_spans(_file_of(entry, bold))
    return True if not spans[0] else _covered(spans, char)


def _fallback_for(char, bold, chain=SYMBOL_FONTS):
    """返回回退链里第一个有这个字形的字体；都没有返回 None。"""
    for entry in chain:
        if _covered(glyph_spans(_file_of(entry, bold)), char):
            return entry
    return None


def split_runs(text, base, bold, deficit, chain=SYMBOL_FONTS):
    """把一段文字按「谁有这个字形」切成 [(字体族名, 文字片段)]，并记录连回退字体也没有的字符。"""
    runs = []
    for char in text:
        if ord(char) < 0x20 or ord(char) == 0x7F:      # 换行/制表不是字形缺失，别当成导出缺陷
            entry = base
        else:
            entry = base if _base_has_glyph(base, char, bold) else _fallback_for(char, bold, chain)
        if entry is None:
            deficit[char] = deficit.get(char, 0) + 1
            entry = base
        if runs and runs[-1][0] == entry[0]:
            runs[-1][1].append(char)
        else:
            runs.append((entry[0], [char]))
    return [(family, "".join(chars)) for family, chars in runs]


def font_warnings(deficit):
    """没有字形可用的字符写成一条提示，交给宿主显示，不假装导出完全正常。"""
    if not deficit:
        return []
    listed = "".join(sorted(deficit)[:12])
    return ["有 %d 个字符在可用字体里都没有字形，导出稿上会留空：%s" % (len(deficit), listed)]


def _set_run_font(run, family):
    """让一个 Word run 连同东亚字体一起改用回退字体，避免只换西文字体。"""
    from docx.oxml.ns import qn
    run.font.name = family
    run._element.rPr.rFonts.set(qn("w:eastAsia"), family)


#: 正文里的公式写法（与核心 `mdreader.formula.scan` 的规则一致）。
_FORMULA_TEXT = re.compile(
    r"(?<!\\)\$\$(?=\S)(.+?)(?<=\S)\$\$|(?<!\\)\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)|(@@MDF\d+@@)",
    re.S)
#: 独立公式块：`$$\n…\n$$` 或单行的 `$$ … $$`。
_DISPLAY_BLOCK = re.compile(
    r"(?m)^[ \t]{0,3}\$\$[ \t]*\n(?P<body>.+?)\n[ \t]{0,3}\$\$[ \t]*$"
    r"|^[ \t]{0,3}\$\$[ \t]*(?P<inline>\S.*?)[ \t]*\$\$[ \t]*$", re.S)


def unescaped_markdown(text):
    """CommonMark 解析后的文本：``\\,`` ``\\{`` 这类转义会变成普通字符。

    公式源码按**原始 Markdown** 扫描，正文 token 却是解析后的文本；两边的反斜杠
    数量不一致，查表就会落空（含 ``\\,`` 的公式曾静默留在正文里没有变成图片）。
    """
    return re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\\\]^_`{|}~])', r'\1', text)


def protect_display_formulas(markdown, formulas):
    """把独立公式块换成单行哨兵，返回 ``(新正文, 查找表)``。

    为什么需要这一步：CommonMark 会把 ``$$`` 那几行当成**一个段落里的多段文本**
    （中间还有软换行），按 token 拆就永远拼不出完整的公式。先把它压成一行哨兵，
    它在词法上是普通文本，于是整块公式必然落在同一个 text token 里。
    """
    lookup = dict(formulas)
    for tex, info in formulas.items():
        plain = unescaped_markdown(tex)
        if plain != tex:
            lookup.setdefault(plain, info)
    if not formulas or "$$" not in markdown:
        return markdown, lookup

    def repl(match):
        tex = (match.group("body") or match.group("inline") or "").strip()
        info = formulas.get(tex)
        if not info:
            return match.group(0)                 # 画不出来的公式保持原文
        token = "@@MDF%d@@" % len(lookup)
        lookup[token] = dict(info, tex=tex)
        return token

    return _DISPLAY_BLOCK.sub(repl, markdown), lookup


def split_formulas(text, formulas):
    """把一段正文按公式切成 ``("text", str)`` / ``("formula", info)`` 片段。

    ``formulas`` 由宿主（核心）给出：``{公式源码: {"name", "display", "width",
    "height"}}``，其中的图片已经放进任务的输入目录。**不在映射里的 ``$…$`` 原样当
    文本**——画不出来的公式不该被吞掉，读者至少能看到原始写法。
    """
    if not formulas or ("$" not in text and "@@MDF" not in text):
        return [("text", text)]
    pieces, cursor = [], 0
    for match in _FORMULA_TEXT.finditer(text):
        key = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        info = None
        if match.group(3):
            info = formulas.get(match.group(3))
        elif key:
            info = formulas.get(key)
        if not info:
            continue
        if match.start() > cursor:
            pieces.append(("text", text[cursor:match.start()]))
        pieces.append(("formula", dict(info, tex=info.get("tex", key))))
        cursor = match.end()
    if cursor == 0:
        return [("text", text)]
    if cursor < len(text):
        pieces.append(("text", text[cursor:]))
    return pieces


def formula_image(task, info, width_limit=None, height_limit=None):
    """返回插件做图用的 ``(路径, 宽, 高, 是否缩小)``（点）。

    公式图先前按像素 1:1 换算点数，长公式会比正文还宽：Word 里压出版心，
    PDF 里 ReportLab 直接报错、整份文档导不出来。这里按版面等比缩小，
    内容不变、只是字更小，缩小这件事必须报给用户。
    """
    from PIL import Image
    path = task.path("in", info["name"])
    with Image.open(path) as picture:
        width, height = picture.size
    wanted = max(8.0, width * 0.75)                 # 像素 → 点，与正文 11pt 相当
    scale = 1.0
    if width_limit and wanted > width_limit:
        scale = width_limit / wanted
    if height_limit and height * .75 * scale > height_limit:
        scale = min(scale, height_limit / max(1e-6, height * .75))
    if scale < 1.0:
        wanted *= scale
    return path, wanted, max(1, round(height * wanted / max(1, width))), scale < 1.0


def formula_warnings(degraded):
    """公式以图片降级时报一条提示（明确写出不可编辑/不可检索，不假装是原生公式）。"""
    if not degraded:
        return []
    sample = "、".join(degraded[:3])
    if len(degraded) > 3:
        sample += " 等"
    return ["%d 个公式在导出稿里是**图片**（Word 里不可编辑、PDF 里不可检索），"
            "原始写法已写进图片的替代文字：%s" % (len(degraded), sample)]


def formula_scale_warnings(shrunk):
    """过宽公式被缩小时必须说清楚，不能让用户以为导出稿和原文一样大。"""
    if not shrunk:
        return []
    sample = "、".join(shrunk[:3])
    if len(shrunk) > 3:
        sample += " 等"
    return ["%d 个公式比正文宽，已**等比缩小**到版面内（内容不丢，字会更小）：%s"
            % (len(shrunk), sample)]


def blocks(markdown):
    from markdown_it import MarkdownIt
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    environment = {}
    tokens = parser.parse(markdown, environment)
    # CommonMark otherwise consumes [^note]: text as a link definition. Preserve
    # the literal footnote until native footnotes are supported. Parser-provided
    # line ranges exclude fenced/indented code and cover multi-line definitions.
    footnotes = [entry for name, entry in environment.get("references", {}).items()
                 if name.startswith("^")]
    if footnotes:
        lines = markdown.splitlines(keepends=True)
        for entry in footnotes:
            start = entry["map"][0]
            lines[start] = lines[start].replace("[^", "\\[^", 1)
        tokens = parser.parse("".join(lines), {})
    result, lists, heading, quote, list_id = [], [], 0, 0, 0
    if footnotes:
        result.append({"type": "warning", "message": "脚注按原始文字保留，暂不生成页脚脚注或跳转。"})
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "table_open":
            rows, row = [], []
            i += 1
            while i < len(tokens) and tokens[i].type != "table_close":
                item = tokens[i]
                if item.type == "tr_open": row = []
                elif item.type == "inline": row.append(item.children or [])
                elif item.type == "tr_close": rows.append(row)
                i += 1
            result.append({"type": "table", "rows": rows})
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            list_id += 1
            lists.append({"ordered": t.type == "ordered_list_open", "number": int(t.attrGet("start") or 1) - 1,
                          "id": list_id, "first": False})
        elif t.type in ("bullet_list_close", "ordered_list_close"): lists.pop()
        elif t.type == "list_item_open":
            lists[-1]["number"] += 1
            lists[-1]["first"] = True
        elif t.type == "heading_open": heading = int(t.tag[1:])
        elif t.type == "heading_close": heading = 0
        elif t.type == "blockquote_open": quote += 1
        elif t.type == "blockquote_close": quote -= 1
        elif t.type == "inline":
            result.append({"type": "paragraph", "tokens": t.children or [], "heading": heading,
                           "depth": len(lists), "list": dict(lists[-1]) if lists else None, "quote": quote})
            if lists: lists[-1]["first"] = False
        elif t.type in ("fence", "code_block"):
            result.append({"type": "code", "text": t.content})
            if t.type == "fence" and t.info.strip().lower() == "mermaid":
                result.append({"type": "warning", "message": "Mermaid 按代码保留，暂不生成图表。"})
        elif t.type == "hr": result.append({"type": "rule"})
        i += 1
    return result


def image_info(task, token):
    from PIL import Image, ImageOps
    src = token.attrGet("src") or ""
    resources = task.options.get("resources") or {}
    resource = resources.get(src) or next((value for key, value in resources.items()
                                         if unquote(key) == unquote(src)), None)
    if not resource:
        raise ValueError("图片未能读取，导出已停止：" + src)
    cache = getattr(task, '_image_cache', {})
    if src not in cache:
        source = task.path("in", resource["name"])
        path = task.path("converted-%04d.png" % len(cache))
        with Image.open(source) as original:
            picture = ImageOps.exif_transpose(original)
            width, height = picture.size
            picture.convert("RGBA" if "A" in picture.getbands() else "RGB").save(path, "PNG")
        cache[src] = (path, width, height)
        task._image_cache = cache
    path, width, height = cache[src]
    match = re.search(r"(?:^|\s)width=(\d+)", token.attrGet("title") or "")
    requested = int(match.group(1)) if match else min(width, 720)
    return path, max(16, min(requested, 4096)) * .75, height / width


def docx(task):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.top_margin = section.bottom_margin = Inches(.8)
    section.left_margin = section.right_margin = Inches(.8)
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6"):
        style = document.styles[name]
        style.font.name = "Microsoft YaHei"
        style.font.color.rgb = RGBColor(31, 35, 40)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    document.styles["Normal"].font.size = Pt(11)
    document.styles["Normal"].paragraph_format.space_after = Pt(8)
    document.styles["Normal"].paragraph_format.line_spacing = 1.2
    deficit = {}
    formulas = task.options.get("formulas") or {}
    degraded = []
    shrunk = []
    list_numbers = {}

    def number_paragraph(paragraph, block):
        item = block["list"]
        paragraph.paragraph_format.left_indent = Inches(.25 * block["depth"])
        if not item["first"]:
            return
        paragraph.paragraph_format.first_line_indent = Inches(-.16)
        if item["id"] not in list_numbers:
            numbering = document.part.numbering_part.element
            ids = [int(node.get(qn("w:abstractNumId"))) for node in numbering.findall(qn("w:abstractNum"))]
            abstract_id = max(ids, default=-1) + 1
            abstract = OxmlElement("w:abstractNum")
            abstract.set(qn("w:abstractNumId"), str(abstract_id))
            level = OxmlElement("w:lvl")
            level.set(qn("w:ilvl"), "0")
            for name, value in (("start", str(item["number"])),
                                ("numFmt", "decimal" if item["ordered"] else "bullet"),
                                ("lvlText", "%1." if item["ordered"] else "•"),
                                ("lvlJc", "left")):
                node = OxmlElement("w:" + name)
                node.set(qn("w:val"), value)
                level.append(node)
            abstract.append(level)
            numbering.insert(0, abstract)
            list_numbers[item["id"]] = numbering.add_num(abstract_id).numId
        properties = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        properties.get_or_add_ilvl().val = 0
        properties.get_or_add_numId().val = list_numbers[item["id"]]

    def add_formula(paragraph, info, limit=480):
        """公式在 Word 里以图片嵌入，并把原始写法写进图片的替代文字。"""
        path, width, height, scaled = formula_image(task, info, width_limit=limit, height_limit=660)
        run = paragraph.add_run()
        shape = run.add_picture(path, width=Pt(width), height=Pt(height))
        try:
            shape._inline.docPr.set("descr", "公式：%s" % info.get("tex", ""))
            shape._inline.docPr.set("title", info.get("tex", ""))
        except Exception:                       # 老版本 python-docx 没有 docPr 也不影响导出
            pass
        degraded.append(info.get("tex", ""))
        if scaled:
            shrunk.append(info.get("tex", ""))

    def inline(paragraph, tokens, limit=480, heading=False):
        bold, italic, link = heading, False, None
        for token in tokens:
            kind = token.type
            if kind == "strong_open": bold = True
            elif kind == "strong_close": bold = heading
            elif kind == "em_open": italic = True
            elif kind == "em_close": italic = False
            elif kind == "link_open": link = token.attrGet("href")
            elif kind == "link_close": link = None
            elif kind == "image":
                path, width, ratio = image_info(task, token)
                width = min(width, limit, 650 / ratio)
                paragraph.add_run().add_picture(path, width=Pt(width))
            elif kind in ("text", "code_inline", "softbreak", "hardbreak", "html_inline"):
                text = "\n" if kind in ("softbreak", "hardbreak") else token.content
                base = CODE_FONT if kind == "code_inline" else BODY_FONT
                chain = CODE_FALLBACKS if kind == "code_inline" else SYMBOL_FONTS
                for piece_kind, piece in split_formulas(text, lookup if kind == "text" else {}):
                    if piece_kind == "formula":
                        add_formula(paragraph, piece, limit)
                        continue
                    for family, run_text in split_runs(piece, base, bold, deficit, chain):
                        run = paragraph.add_run(run_text)
                        run.bold, run.italic = bold, italic
                        if family != base[0]:
                            _set_run_font(run, family)
                        elif kind == "code_inline":
                            run.font.name = base[0]
                        if link and urlsplit(link).scheme.lower() in ("http", "https", "mailto"):
                            hyperlink = OxmlElement("w:hyperlink")
                            hyperlink.set(qn("r:id"), document.part.relate_to(link, RT.HYPERLINK, is_external=True))
                            hyperlink.append(run._r)
                            paragraph._p.append(hyperlink)
    markdown, lookup = protect_display_formulas(task.input.get("markdown") or "",
                                             formulas)
    content = blocks(markdown)
    for index, block in enumerate(content):
        task.progress("生成 Word", int(10 + 80 * index / max(1, len(content))))
        if block["type"] == "table":
            rows = block["rows"]
            if not rows: continue
            table = document.add_table(rows=0, cols=max(map(len, rows)))
            table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                cells = table.add_row().cells
                for col, tokens in enumerate(row):
                    inline(cells[col].paragraphs[0], tokens, 460 / len(cells), heading=row_index == 0)
                if row_index == 0:
                    prop = table.rows[0]._tr.get_or_add_trPr()
                    prop.append(OxmlElement("w:tblHeader"))
                    for cell in cells:
                        for run in cell.paragraphs[0].runs: run.bold = True
            document.add_paragraph()
        elif block["type"] == "code":
            paragraph = document.add_paragraph()
            for family, piece in split_runs(block["text"].rstrip("\n"), CODE_FONT, False, deficit, CODE_FALLBACKS):
                run = paragraph.add_run(piece)
                run.font.size = Pt(9)
                if family == CODE_FONT[0]: run.font.name = CODE_FONT[0]
                else: _set_run_font(run, family)
        elif block["type"] == "paragraph":
            level = block["heading"]
            style = "Heading %d" % level if level else "Normal"
            paragraph = document.add_paragraph(style=style)
            if block["list"]: number_paragraph(paragraph, block)
            if block["quote"]: paragraph.paragraph_format.left_indent = Inches(.3)
            inline(paragraph, block["tokens"], heading=bool(level))
        elif block["type"] == "rule": document.add_paragraph("―" * 24)
    target = task.path("document.docx")
    document.save(target)
    warnings = font_warnings(deficit) + formula_warnings(degraded) + formula_scale_warnings(shrunk)
    warnings += list(dict.fromkeys(block["message"] for block in content if block["type"] == "warning"))
    return {"kind": "export", "path": target, "warnings": warnings}


def _pdf_fonts():
    """注册基础字体与回退字体，返回 (基础字体项, {族名: (常规名, 粗体名)})。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    base = ("MDBase", "simsun.ttc", "simhei.ttf")
    registered = {}
    regular = "STSong-Light"
    if os.path.exists(os.path.join(FONT_DIR, base[1])):
        try:
            pdfmetrics.registerFont(TTFont("MDRegular", os.path.join(FONT_DIR, base[1]), subfontIndex=0))
            regular = "MDRegular"
        except Exception:
            pass
    bold = regular
    if os.path.exists(os.path.join(FONT_DIR, base[2])):
        try:
            pdfmetrics.registerFont(TTFont("MDBold", os.path.join(FONT_DIR, base[2])))
            bold = "MDBold"
        except Exception:
            pass
    if regular == "STSong-Light":
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold, italic=regular, boldItalic=bold)
    registered[base[0]] = (regular, bold)
    for family, normal_file, bold_file in SYMBOL_FONTS:
        if not os.path.exists(os.path.join(FONT_DIR, normal_file)):
            continue
        names = ["MDS-" + family.replace(" ", ""), "MDS-" + family.replace(" ", "") + "-Bold"]
        for position, filename in enumerate((normal_file, bold_file)):
            path = os.path.join(FONT_DIR, filename)
            if not os.path.exists(path):
                path = os.path.join(FONT_DIR, normal_file)
            try:
                pdfmetrics.registerFont(TTFont(names[position], path))
            except Exception:
                names[position] = None
        if names[0]:
            registered[family] = (names[0], names[1] or names[0])
    return base, registered


def pdf(task):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable
    from reportlab.lib.pagesizes import A4
    media_width = A4[0] - 96 - INLINE_IMAGE_INSET
    base, registered = _pdf_fonts()
    root = registered[base[0]][0]
    deficit = {}
    normal = ParagraphStyle("body", fontName=root, fontSize=11, leading=17, autoLeading="max",
                            spaceAfter=9, wordWrap="CJK", splitLongWords=True)
    # ReportLab 的 CJK 折行遇到“文本为空的图片片段”会执行 ord("") 直接抛错：
    # 图片或公式比该行剩余宽度长时，PDF 会整份导不出来。含图片/公式的段落改用
    # 通用折行（长段中文仍由 splitLongWords 逐字折行），纯文字段落保持 CJK 折行。
    media = ParagraphStyle("body-media", parent=normal, wordWrap=None)
    def one_font(text, bold, nbsp=False):
        """按字形覆盖率挑字体；基础字体能画的字符不打标签，保持原有排版。"""
        pieces = []
        for family, piece in split_runs(text, base, bold, deficit):
            body = html.escape(piece)
            if nbsp: body = body.replace(" ", "&#160;")
            names = registered.get(family)
            if family == base[0] or not names:
                pieces.append(body)
            else:
                pieces.append('<font name="%s">%s</font>' % (names[1 if bold else 0], body))
        return "".join(pieces)
    formulas = task.options.get("formulas") or {}
    degraded = []
    shrunk = []

    def formula_markup(info, limit):
        """公式在 PDF 里以图片嵌入；过宽时先等比缩小，否则 ReportLab 会直接报错。"""
        path, width, height, scaled = formula_image(task, info, width_limit=limit,
                                                    height_limit=A4[1] - 200)
        degraded.append(info.get("tex", ""))
        if scaled:
            shrunk.append(info.get("tex", ""))
        return ('<img src="%s" width="%.1f" height="%.1f" valign="middle"/>'
                % (html.escape(path, quote=True), width, height))

    def formula_media(tokens):
        """这些 token 里有没有图片或公式：有就必须换用 media 折行样式。"""
        for token in tokens:
            if token.type == "image":
                return True
            if token.type in ("text", "code_inline", "html_inline") and any(
                    kind == "formula" for kind, _ in split_formulas(token.content, lookup)):
                return True
        return False

    def rich(tokens, limit=None):
        out, links, bold = [], [], False
        for token in tokens:
            kind = token.type
            if kind == "strong_open":
                bold = True
                out.append("<b>")
            elif kind == "strong_close":
                bold = False
                out.append("</b>")
            elif kind == "em_open": out.append("<i>")
            elif kind == "em_close": out.append("</i>")
            elif kind == "link_open":
                url = token.attrGet("href") or ""
                allowed = urlsplit(url).scheme.lower() in ("http", "https", "mailto")
                links.append(allowed)
                if allowed: out.append('<link href="%s" color="#2563a6">' % html.escape(url, quote=True))
            elif kind == "link_close":
                if links and links.pop(): out.append('</link>')
            elif kind in ("softbreak", "hardbreak"): out.append("<br/>")
            elif kind == "image":
                out.append(one_font(token.content, bold))
            elif kind in ("text", "code_inline", "html_inline"):
                for piece_kind, piece in split_formulas(token.content,
                                                        lookup if kind == "text" else {}):
                    out.append(formula_markup(piece, limit if limit else media_width)
                               if piece_kind == "formula" else one_font(piece, bold))
        return "".join(out)
    story = []
    markdown, lookup = protect_display_formulas(task.input.get("markdown") or "",
                                             formulas)
    content = blocks(markdown)
    for index, block in enumerate(content):
        task.progress("生成 PDF", int(10 + 80 * index / max(1, len(content))))
        if block["type"] == "table":
            if not block["rows"]: continue
            columns = max(map(len, block["rows"]))
            cell_width = (A4[0] - 96) / columns - 20
            rows = []
            for row in block["rows"]:
                cells = []
                for tokens in row:
                    # Flowables participate in the row height calculation. Inline
                    # images with a middle baseline can paint over the header.
                    cell = [Paragraph(rich(tokens, cell_width),
                                      media if formula_media(tokens) else normal)]
                    for token in tokens:
                        if token.type == "image":
                            path, width, ratio = image_info(task, token)
                            width = min(width, cell_width, 400 / ratio)
                            cell.append(Image(path, width=width, height=width * ratio, hAlign="LEFT"))
                    cells.append(cell)
                rows.append(cells)
            table = Table(rows, colWidths=[A4[0] - 96] if columns == 1 else [(A4[0] - 96) / columns] * columns,
                          repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#cbd0d9")),
                                      ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                                      ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7)]))
            story.extend([table, Spacer(1, 12)])
        elif block["type"] == "code":
            for line in block["text"].splitlines():
                story.append(Paragraph(one_font(line, False, nbsp=True) or "&#160;", normal))
        elif block["type"] == "rule": story.append(HRFlowable(width="100%", color=colors.lightgrey))
        elif block["type"] == "paragraph":
            level = block["heading"]
            style = ParagraphStyle("p", parent=media if formula_media(block["tokens"]) else normal,
                                   fontSize=24 - level * 2 if level else 11,
                                   leading=30 - level * 2 if level else 17,
                                   keepWithNext=bool(level), leftIndent=14 * (block["depth"] + block["quote"]))
            prefix = (str(block["list"]["number"]) + ". " if block["list"]["ordered"] else "• ") if block["list"] and block["list"]["first"] else ""
            story.append(Paragraph(prefix + rich(block["tokens"], media_width), style))
            for token in block["tokens"]:
                if token.type == "image":
                    path, width, ratio = image_info(task, token)
                    width = min(width, media_width, (A4[1] - 130) / ratio)
                    story.extend([Image(path, width=width, height=width * ratio, hAlign="LEFT"), Spacer(1, 10)])
    def footer(canvas, doc):
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(A4[0] - 48, 24, str(doc.page))
    target = task.path("document.pdf")
    SimpleDocTemplate(target, pagesize=A4, leftMargin=48, rightMargin=48, topMargin=48,
                      bottomMargin=48, title=task.options.get("title", "Markdown Document")).build(
                          story or [Paragraph(" ", normal)], onFirstPage=footer, onLaterPages=footer)
    return {"kind": "export", "path": target,
            "warnings": font_warnings(deficit) + formula_warnings(degraded)
            + formula_scale_warnings(shrunk)
            + list(dict.fromkeys(block["message"] for block in content if block["type"] == "warning"))}
