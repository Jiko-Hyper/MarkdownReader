"""Offline PDF/DOCX/HTML/Markdown conversion, executed only in a plugin worker."""
import base64
import html
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import zipfile

MAX_IMAGE = 16 * 1024 * 1024
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/webp": ".webp", "image/bmp": ".bmp"}
EXT_MIME = {ext: mime for mime, ext in MIME_EXT.items()}
EXT_MIME[".jpeg"] = "image/jpeg"


def read_text(path):
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("无法识别文本编码，请先保存为 UTF-8") from exc


class Conversion:
    def __init__(self, task):
        self.task = task
        files = task.options.get("files") or []
        if len(files) != 1:
            raise ValueError("请选择一个源文档")
        self.source = Path(files[0])
        self.root = Path(task.options["source_dir"]).resolve()
        self.assets_name = task.options["assets_name"]
        self.assets = []
        self.warnings = []
        self.asset_bytes = 0

    def warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def image_data(self, src):
        parts = urlsplit(src)
        if parts.scheme == "data":
            match = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)", src)
            if not match or match[1] not in MIME_EXT:
                raise ValueError("不支持的内嵌图片")
            if len(match[2]) > MAX_IMAGE * 2:
                raise ValueError("内嵌图片过大")
            return base64.b64decode(match[2], validate=False), match[1]
        if parts.scheme or parts.netloc or src.startswith(("/", "\\")):
            raise ValueError("远程或绝对路径图片未读取")
        path = (self.root / unquote(parts.path)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("图片超出源文档目录")
        if path.suffix.lower() not in EXT_MIME:
            raise ValueError("仅支持 PNG、JPEG、GIF、WebP、BMP 图片")
        if not path.is_file() or path.stat().st_size > MAX_IMAGE:
            raise ValueError("图片不存在或超过 16 MB")
        return path.read_bytes(), EXT_MIME[path.suffix.lower()]

    def save_image(self, data, mime):
        if mime not in MIME_EXT or len(data) > MAX_IMAGE:
            raise ValueError("图片格式不支持或超过 16 MB")
        if len(self.assets) >= 500 or self.asset_bytes + len(data) > 48 * 1024 * 1024:
            raise ValueError("图片总量超过转换上限")
        name = "image-%03d%s" % (len(self.assets) + 1, MIME_EXT[mime])
        target = Path(self.task.path(name))
        target.write_bytes(data)
        self.assets.append(str(target))
        self.asset_bytes += len(data)
        return self.assets_name + "/" + name

    def clean_html(self, markup, *, embed=False, saved_images=False):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(markup, "html.parser")
        for node in list(soup.find_all(["script", "style", "iframe", "object", "embed", "head", "form"])):
            if node.parent is not None:
                node.decompose()
        for node in soup.find_all(True):
            for attr in list(node.attrs):
                if attr.lower().startswith("on") or attr.lower() in ("style", "srcset"):
                    del node[attr]
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            scheme = urlsplit(re.sub(r"[\x00-\x20]", "", href)).scheme.lower()
            if scheme and scheme not in ("https", "http", "mailto"):
                del anchor["href"]
                self.warn("已移除不安全的链接协议。")
            elif not scheme and href and not href.startswith("#"):
                self.warn("相对链接保留原写法；输出移到其他目录时请检查链接。")
        for image in soup.find_all("img"):
            src = image.get("src", "")
            if saved_images and src.startswith(self.assets_name + "/"):
                continue
            try:
                data, mime = self.image_data(src)
                if len(data) > MAX_IMAGE:
                    raise ValueError("图片超过 16 MB")
                if embed:
                    image["src"] = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
                else:
                    image["src"] = self.save_image(data, mime)
            except (ValueError, OSError) as exc:
                self.warn("部分图片未转换：" + str(exc))
                image.replace_with("[图片：" + image.get("alt", "") + "]")
        return str(soup.body or soup)

    def to_markdown(self, markup, *, saved_images=False):
        from markdownify import markdownify
        cleaned = self.clean_html(markup, saved_images=saved_images)
        return markdownify(cleaned, heading_style="ATX", bullets="-", escape_misc=True,
                          keep_inline_images_in=["td", "th"], table_infer_header=True).strip() + "\n"

    def pdf(self):
        from pypdf import PdfReader
        reader = PdfReader(self.source)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先解除密码后重试")
        if len(reader.pages) > 1000:
            raise ValueError("PDF 超过 1000 页，请拆分后转换")
        pages = []
        found = False
        for index, page in enumerate(reader.pages, 1):
            self.task.progress("提取 PDF 第 %d 页" % index, int(index / len(reader.pages) * 85))
            text = (page.extract_text() or "").strip()
            found = found or bool(text)
            if not text:
                text = "[此页没有可提取文字，可能需要 OCR]"
                self.warn("部分页面没有文字层，需要 OCR；输出保留了页码提示。")
            # Escape literal prose instead of accidentally inventing Markdown structure.
            text = re.sub(r"([\\`*_{}\[\]<>#+.!|~-])", r"\\\1", text)
            pages.append("<!-- 第 %d 页 -->\n\n%s" % (index, text))
        if not found:
            raise ValueError("PDF 没有可提取文字，可能是扫描版；请先做 OCR")
        self.warn("PDF 按文字层提取：多栏阅读顺序、表格、公式和图片需要人工核对；不包含 OCR。")
        return "\n\n---\n\n".join(pages) + "\n"

    def docx(self):
        import mammoth
        with zipfile.ZipFile(self.source) as archive:
            if len(archive.infolist()) > 5000 or sum(i.file_size for i in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Word 解压后的内容过大")
        def image_handler(image):
            try:
                with image.open() as stream:
                    data = stream.read(MAX_IMAGE + 1)
                return {"src": self.save_image(data, image.content_type)}
            except ValueError as exc:
                self.warn("部分 Word 图片未转换：" + str(exc))
                return {"src": "", "alt": "不支持的 Word 图片"}
        with self.source.open("rb") as stream:
            result = mammoth.convert_to_html(stream, convert_image=mammoth.images.img_element(image_handler))
        for message in result.messages:
            self.warn(str(message.message))
        self.warn("Word 常用标题、列表、表格与图片已转换；分页、文本框和复杂公式可能无法保留。")
        return self.to_markdown(result.value, saved_images=True)

    def markdown(self):
        from markdown_it import MarkdownIt
        engine = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
        body = self.clean_html(engine.render(read_text(self.source)), embed=True)
        title = html.escape(self.source.stem)
        return ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
                'img-src data:; style-src &#39;unsafe-inline&#39;">'
                '<title>' + title + '</title><style>'
                'body{max-width:900px;margin:40px auto;padding:0 24px;line-height:1.7;'
                'font-family:system-ui,sans-serif;color:#202124;background:#fff}'
                'img{max-width:100%}pre{padding:16px;background:#f4f5f7;overflow:auto}'
                'table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}'
                'blockquote{border-left:4px solid #ddd;margin-left:0;padding-left:16px}'
                '</style></head><body>' + body + '</body></html>\n')

    def run(self, direction):
        self.task.progress("读取源文档", 5)
        if direction == "pdf-md":
            text = self.pdf()
        elif direction == "docx-md":
            text = self.docx()
        elif direction == "html-md":
            text = self.to_markdown(read_text(self.source))
        else:
            text = self.markdown()
        if not text.strip():
            self.warn("源文档没有可转换的正文。")
        target = Path(self.task.path("result.html" if direction == "md-html" else "result.md"))
        target.write_text(text or "\n", encoding="utf-8")
        self.task.progress("转换完成", 100)
        return {"kind": "export", "path": str(target), "assets": self.assets, "warnings": self.warnings}


def pdf_to_md(task):
    return Conversion(task).run("pdf-md")


def word_to_md(task):
    return Conversion(task).run("docx-md")


def md_to_html(task):
    return Conversion(task).run("md-html")


def html_to_md(task):
    return Conversion(task).run("html-md")
