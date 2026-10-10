"""Conservative digital-PDF layout recovery; no OCR or claims of universal fidelity."""
from collections import Counter
from io import BytesIO
from statistics import median
import re
from urllib.parse import quote, urlsplit


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>#+.!|~-])", r"\\\1", text)


def inside(char, box):
    x, y = (char["x0"] + char["x1"])/2, (char["top"] + char["bottom"])/2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def reading_order(items):
    """Detect a repeated vertical gutter, then read each column top to bottom.

    Full-width blocks split columns into vertical bands; drawing-stream order is ignored.
    This is deliberately limited to one gutter (two columns).
    """
    ordered = sorted(items, key=lambda i: (i["top"], i["x0"]))
    candidates = []
    for i, left in enumerate(ordered):
        for right in ordered[i+1:]:
            if right["top"] - left["top"] > 4: break
            if right["x0"] - left["x1"] >= 30:
                candidates.append((left["x1"] + right["x0"])/2)
    if len(candidates) < 3:
        return ordered
    cut = median(candidates)
    left = [i for i in ordered if i["x1"] <= cut]
    right = [i for i in ordered if i["x0"] >= cut]
    if min(len(left), len(right)) < 3:
        return ordered
    spans = [i for i in ordered if i["x0"] < cut < i["x1"]]
    result, remaining = [], ordered
    for span in spans:
        before = [i for i in remaining if i["top"] < span["top"] and i is not span]
        result.extend(sorted([i for i in before if i["x1"] <= cut], key=lambda i:i["top"]))
        result.extend(sorted([i for i in before if i["x0"] >= cut], key=lambda i:i["top"]))
        result.append(span)
        remaining = [i for i in remaining if i["top"] >= span["top"] and i is not span]
    result.extend(sorted([i for i in remaining if i["x1"] <= cut], key=lambda i:i["top"]))
    result.extend(sorted([i for i in remaining if i["x0"] >= cut], key=lambda i:i["top"]))
    return result


def lines_from_words(words):
    rows = []
    for word in sorted(words, key=lambda w:(w["top"],w["x0"])):
        if rows and abs(rows[-1]["top"] - word["top"]) <= 3:
            rows[-1]["words"].append(word)
        else:
            rows.append({"top":word["top"], "words":[word]})
    lines = []
    for row in rows:
        current = []
        for word in sorted(row["words"],key=lambda w:w["x0"]):
            if current and word["x0"] - current[-1]["x1"] >= 30:
                lines.append(current); current = []
            current.append(word)
        if current: lines.append(current)
    return [dict(kind="text", words=words, x0=min(w["x0"] for w in words),
                 x1=max(w["x1"] for w in words), top=min(w["top"] for w in words),
                 bottom=max(w["bottom"] for w in words)) for words in lines]


def line_text(item):
    text, previous = "", None
    for word in item["words"]:
        if previous is not None and word["x0"]-previous["x1"] > 1.5 and not text.endswith(" "):
            text += " "
        text += word["text"]
        previous = word
    return text


def linked_text(item, links):
    """Retain a hyperlink only when its annotation covers the whole text run.

    Partial-run links are split by character positions, not guessed from text.
    """
    chunks, last_uri, buffer = [], None, ""
    previous = None
    for word in item["words"]:
        if previous is not None and word["x0"]-previous["x1"] > 1.5 and not buffer.endswith(" "):
            buffer += " "
        for char in word["chars"]:
            uri = next((link["uri"] for link in links if inside(char, (link["x0"],link["top"],link["x1"],link["bottom"]))), None)
            if uri != last_uri and buffer:
                chunks.append((buffer,last_uri)); buffer=""
            last_uri = uri
            buffer += char["text"]
        previous = word
    if buffer: chunks.append((buffer,last_uri))
    result = []
    for text, uri in chunks:
        if uri and urlsplit(uri).scheme.lower() in ("http","https","mailto"):
            result.append("[%s](%s)" % (escape(text), quote(uri,safe=":/?#@!$&*+,;=%")))
        else: result.append(escape(text))
    return "".join(result)


def convert(conversion):
    import pdfplumber
    from pypdf import PdfReader
    reader = PdfReader(conversion.source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF 已加密，请先解除密码后重试")
    if len(reader.pages) > 1000:
        raise ValueError("PDF 超过 1000 页，请拆分后转换")
    pages, has_text = [], False
    with pdfplumber.open(conversion.source) as pdf:
        # Infer heading levels from document-wide typography, independently of fixture text.
        sizes = Counter()
        for page in pdf.pages:
            for char in page.chars:
                if char["text"].strip(): sizes[round(char["size"],1)] += len(char["text"])
            page.close()
        body = sizes.most_common(1)[0][0] if sizes else 12
        heading_sizes = sorted([s for s in sizes if s >= body * 1.25], reverse=True)[:6]
        for page_number, original in enumerate(pdf.pages,1):
            conversion.task.progress("分析 PDF 第 %d 页" % page_number, int(page_number/len(pdf.pages)*85))
            page = original.dedupe_chars()
            if page.chars: has_text = True
            tables = page.find_tables()
            boxes, items = [], []
            for table in tables:
                values = table.extract()
                if not values or len(values)<2 or max(map(len,values))<2: continue
                boxes.append(table.bbox)
                width = max(map(len,values))
                rows = [[escape(str(cell or "")).replace("\n", "<br>") for cell in row] + [""]*(width-len(row)) for row in values]
                lines = ["| " + " | ".join(row) + " |" for row in rows]
                lines.insert(1,"| " + " | ".join(["---"]*width) + " |")
                items.append(dict(kind="block",text="\n".join(lines),x0=table.bbox[0],x1=table.bbox[2],top=table.bbox[1],bottom=table.bbox[3]))
            text_page = page.filter(lambda obj: obj.get("object_type") != "char" or not any(inside(obj,b) for b in boxes))
            words = text_page.extract_words(keep_blank_chars=True,extra_attrs=["fontname","size"],return_chars=True)
            items.extend(lines_from_words(words))
            for obj in page.images:
                try:
                    image = reader.pages[page_number-1].images["/" + obj["name"]]
                    pil = image.image
                    if pil.width * pil.height > 40_000_000: raise ValueError("图片像素数量过大")
                    stream = BytesIO(); pil.save(stream,format="PNG")
                    src = conversion.save_image(stream.getvalue(),"image/png")
                    image_md = "![PDF 图片](%s)" % src
                except Exception as exc:
                    conversion.warn("PDF 图片未提取：" + str(exc)[:100])
                    image_md = "[未能提取的 PDF 图片]"
                items.append(dict(kind="block",text=image_md,x0=obj["x0"],x1=obj["x1"],top=obj["top"],bottom=obj["bottom"]))
            ordered = reading_order(items)
            blocks, code = [], []
            def flush_code():
                if not code: return
                fence = "`" * max(3, max((len(s) for text in code for s in re.findall(r"`+",text)), default=0)+1)
                blocks.append(fence+"\n"+"\n".join(code)+"\n"+fence)
                code.clear()
            for item in ordered:
                if item["kind"] == "block":
                    flush_code(); blocks.append(item["text"]); continue
                text = line_text(item)
                if not text.strip(): continue
                mono = all(any(n in w["fontname"].lower() for n in ("courier","consolas","mono")) for w in item["words"])
                if mono: code.append(text.rstrip()); continue
                flush_code()
                size = max(round(w["size"],1) for w in item["words"])
                if size in heading_sizes:
                    blocks.append("#"*(heading_sizes.index(size)+1)+" "+linked_text(item,page.hyperlinks))
                elif re.match(r"^\s*(?:[-•●▪]|\d+[.)])\s+",text):
                    match = re.match(r"^\s*([-•●▪]|\d+[.)])\s+(.*)",text)
                    marker = "-" if not match[1][0].isdigit() else match[1].rstrip(").")+"."
                    blocks.append(marker+" "+escape(match[2]))
                else:
                    blocks.append(linked_text(item,page.hyperlinks))
            flush_code()
            if not page.chars:
                blocks.insert(0,"[此页没有可提取文字，需要 OCR]")
                conversion.warn("部分页面没有文字层，需要 OCR；图片不等于文字识别结果。")
            pages.append("<!-- 第 %d 页 -->\n\n%s" % (page_number,"\n\n".join(blocks)))
            original.close()
    if not has_text:
        raise ValueError("PDF 没有可提取文字，可能是扫描版；请先做 OCR")
    conversion.warn("PDF 按文字位置恢复标题、网格表格、列表、图片和双栏顺序；复杂排版、无框表格及原生公式仍需核对，不包含 OCR。")
    return "\n\n---\n\n".join(pages)+"\n"
