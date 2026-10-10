"""Offline Windows OCR. Image content is data, never executable instructions."""
import json
import os
from pathlib import Path
import re
import statistics
import subprocess


def prepare_image(source, target, crisp_target):
    from PIL import Image, ImageOps
    with Image.open(source) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("暂不支持多页或动态图片，请拆分为单张图片后重试。")
        if image.width * image.height > 20_000_000 or max(image.size) > 10000:
            raise ValueError("图片过大，请裁剪到 2000 万像素以内后重试。")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, image).convert("L")
        # Normalize dark-theme screenshots before OCR, using the border background.
        border = [image.getpixel((x, y)) for x, y in (
            (0, 0), (image.width-1, 0), (0, image.height-1),
            (image.width-1, image.height-1), (image.width//2, 0))]
        if statistics.median(border) < 128:
            image = ImageOps.invert(image)
        image = ImageOps.autocontrast(image)
        scale = min(2.0, 2500 / max(image.size))
        image = image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale))), Image.Resampling.LANCZOS)
        image.save(target, "PNG")
        # Crisp foreground prevents thin CJK strokes being segmented as separate radicals.
        image = image.point(lambda value: 255 if value > 160 else 0)
        image.save(crisp_target, "PNG")


def readable_text(text):
    # Windows Chinese OCR inserts artificial spaces between ideographs.
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff，。！？、；：])", "", text)
    text = re.sub(r"(?<=[，。！？、；：])\s+(?=[\u3400-\u9fff])", "", text)
    return text.strip()


def escape(text):
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", text)


def markdown_from_lines(lines):
    rows = []
    for line in lines:
        words = line.get("words") or []
        text = readable_text(line.get("text", ""))
        if not text or not words:
            continue
        top = min(float(w["y"]) for w in words)
        bottom = max(float(w["y"]) + float(w["height"]) for w in words)
        rows.append((text, top, bottom, statistics.median(float(w["height"]) for w in words)))
    if not rows:
        raise ValueError("图片中未识别到文字。请使用清晰、正向的文字图片。")
    normal = statistics.median(r[3] for r in rows)
    blocks = []
    previous_bottom = None
    for text, top, bottom, height in rows:
        bullet = re.match(r"^[•●▪·]\s*(.+)$", text)
        numbered = re.match(r"^(\d{1,3})\s*[.．、)·](?:\s+|(?=[\u3400-\u9fff]))(.+)$", text)
        dash = re.match(r"^-\s+(.+)$", text)
        heading = len(rows) >= 3 and height >= normal * 1.4 and len(text) <= 100
        if heading:
            rendered = "## " + escape(text)
        elif bullet or dash:
            rendered = "- " + escape((bullet or dash)[1])
        elif numbered:
            rendered = numbered[1] + ". " + escape(numbered[2])
        else:
            rendered = escape(text)
        gap = previous_bottom is not None and top - previous_bottom > normal * 1.1
        separator = "\n\n" if heading or gap or (blocks and blocks[-1].startswith("## ")) else "  \n"
        if blocks:
            blocks.append(separator)
        blocks.append(rendered)
        previous_bottom = bottom
    return "".join(blocks).strip() + "\n"


def convert(conversion):
    if os.name != "nt":
        raise ValueError("图片文字识别目前需要 Windows 10 / 11。")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_file():
        raise ValueError("未找到 Windows 自带的文字识别运行环境。")
    conversion.task.progress("正在离线识别图片文字…", 20)
    result_path = Path(conversion.task.path("ocr-result.json"))
    normalized_path = Path(conversion.task.path("ocr-input.png"))
    crisp_path = Path(conversion.task.path("ocr-crisp.png"))
    try:
        prepare_image(conversion.source, normalized_path, crisp_path)
    except OSError as exc:
        raise ValueError("无法读取这张图片，请检查图片文件是否完整。") from exc
    try:
        result = subprocess.run([
            str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(Path(__file__).with_name("image_ocr.ps1")),
            "-InputPath", str(normalized_path.resolve()), "-OutputPath", str(result_path),
            "-CrispPath", str(crisp_path.resolve()),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("图片文字识别超时，请裁剪图片后重试。") from exc
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace")
        if "OCR_LANGUAGE_MISSING" in diagnostic:
            raise ValueError("未安装文字识别语言。请在 Windows 语言设置中安装对应语言的文字识别功能。")
        if "OCR_IMAGE_TOO_LARGE" in diagnostic:
            raise ValueError("图片尺寸超过 Windows 文字识别上限，请缩小或分段裁剪后重试。")
        if "OCR_MULTIPLE_FRAMES" in diagnostic:
            raise ValueError("暂不支持多页或动态图片，请拆分为单张图片后重试。")
        raise ValueError("无法识别这张图片，请确认文件有效并使用 PNG、JPEG、BMP 或单页 TIFF。")
    data = json.loads(result_path.read_text(encoding="utf-8"))
    conversion.warn("图片已在本机识别，未上传。识别结果请核对；手写、公式、复杂表格与多栏排版可能无法准确还原。")
    if not data.get("chinese_available"):
        conversion.warn("当前识别语言：" + data.get("language", "未知") + "；中文识别需要安装 Windows 中文文字识别语言。")
    return markdown_from_lines(data.get("lines") or [])
