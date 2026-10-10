"""Twenty fixed, synthetic OCR fixtures; exact text comparison through the shipped worker.

This measures this corpus only, not arbitrary-image OCR accuracy. Fixtures and expected
text are generated before recognition; any failed case makes the command fail.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins/dependencies"))
from PIL import Image, ImageDraw, ImageFont, ImageOps
from mdreader import core
from mdreader.conversion import convert_file

# Only whitespace (including OCR's artificial CJK spaces) and Markdown escapes
# are normalized. Every letter, digit, punctuation mark, and line order must match.
def normalize(text):
    text = re.sub(r"(?m)^## ", "", text)
    text = re.sub(r"\\([\\`*_{}\[\]<>|#])", r"\1", text)
    return re.sub(r"\s+", "", text)


CASES = [
    ("01-english", "png", ["Reading makes ideas clear.", "Write a little every day.", "Keep your notes in one place."], {}),
    ("02-chinese", "png", ["阅读让思路更加清晰", "每天记录学习内容", "把重要的知识保存下来"], {}),
    ("03-mixed", "png", ["Markdown 文档编辑器", "支持 Windows 本地文件", "版本 2026 更新说明"], {}),
    ("04-jpeg", "jpg", ["Project planning notes", "Prepare the weekly report.", "Review the final document."], {}),
    ("05-bitmap", "bmp", ["文件管理使用说明", "打开文件夹查看内容", "新建文档后保存修改"], {}),
    ("06-tiff", "tiff", ["Meeting notes", "Discuss the project schedule.", "Confirm the next release date."], {}),
    ("07-gray", "png", ["今日学习计划", "阅读技术文档", "整理笔记和总结"], {"gray": True}),
    ("08-dark", "png", ["Dark theme reading", "Clear text on a dark page.", "Save your work before closing."], {"dark": True}),
    ("09-small", "png", ["Smaller text remains readable.", "Each paragraph contains useful information.", "The document is saved locally."], {"size": 24}),
    ("10-large", "png", ["清晰的大字阅读", "字体大小可以调整", "适合长时间查看文档"], {"size": 56}),
    ("11-heading", "png", ["Reading Guide", "Open a local document.", "Choose a comfortable font size."], {"heading": True}),
    ("12-cn-heading", "png", ["使用指南", "打开本地文档", "选择合适的字体大小"], {"heading": True}),
    ("13-ordered", "png", ["1. Open the document", "2. Review the content", "3. Save the changes"], {"ordered": True}),
    ("14-cn-ordered", "png", ["1. 打开本地文件", "2. 检查文档内容", "3. 保存所有修改"], {"ordered": True}),
    ("15-paragraph-gap", "png", ["The first paragraph introduces the topic.", "The next paragraph explains the details.", "The final paragraph records the result."], {"gap": 95}),
    ("16-bold", "png", ["Important project information", "Check every change carefully.", "Keep a copy of the original file."], {"bold": True}),
    ("17-colored", "png", ["彩色背景上的文字", "图片可以转换为文档", "识别完成后核对结果"], {"background": "#edf4ff", "foreground": "#183657"}),
    ("18-numbers", "jpeg", ["Date 2026-10-06", "Total 1234567890", "Version 1.2.0"], {}),
    ("19-transparent", "png", ["Transparent image text", "The output is an editable document.", "Images stay on this computer."], {"transparent": True}),
    ("20-margins", "tif", ["本地图片文字识别", "支持中文和英文内容", "转换结果保存为文档"], {"margin": 160}),
]


def generate(folder):
    fixtures = []
    for name, ext, lines, options in CASES:
        size = options.get("size", 38)
        margin = options.get("margin", 60)
        font_path = Path("C:/Windows/Fonts") / ("msyhbd.ttc" if options.get("bold") else "msyh.ttc")
        normal = ImageFont.truetype(str(font_path), size)
        title = ImageFont.truetype(str(font_path), 68 if options.get("heading") else size)
        fonts = [title] + [normal] * (len(lines)-1)
        gap = options.get("gap", 32)
        width = max(int(f.getlength(line)) for line, f in zip(lines, fonts)) + margin*2
        height = margin*2 + sum(f.size+gap for f in fonts)
        dark = options.get("dark")
        bg = options.get("background", "#20242b" if dark else "white")
        fg = options.get("foreground", "white" if dark else "black")
        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)
        y = margin
        for line, font in zip(lines, fonts):
            draw.text((margin, y), line, font=font, fill=fg)
            y += font.size+gap
        if options.get("gray"):
            image = ImageOps.grayscale(image)
        if options.get("transparent"):
            image = image.convert("RGBA")
            # Transparent white background, opaque glyphs.
            image.putalpha(image.convert("RGB").point(lambda v: 255-v).convert("L"))
        path = folder / (name + "." + ext)
        image.save(path, quality=85)
        expected = "\n".join(lines) + "\n"
        path.with_suffix(".expected.txt").write_text(expected, encoding="utf-8")
        fixtures.append((path, expected, options))
    return fixtures


def main():
    folder = ROOT / "artifacts/image-ocr-qualification" / time.strftime("run-%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    fixtures = generate(folder)
    workspace = core.Workspace(str(folder / "workspace"))
    rows = []
    try:
        package = ROOT / "plugins/packages/mdreader.document-convert-1.3.0.zip"
        workspace.plugins.install(str(package))
        workspace.plugins.enable("mdreader.document-convert")
        command = next(c["command"] for c in workspace.plugins.commands() if c["format"] == "image-md")
        for source, expected, options in fixtures:
            start = time.monotonic()
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            row = {"image": source.name, "sha256": original}
            try:
                target = source.with_suffix(".md")
                result = convert_file(workspace.plugins, command, str(source), str(target))
                actual = target.read_text(encoding="utf-8")
                checks = {
                    "all_text_exact": normalize(actual) == normalize(expected),
                    "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == original,
                    "nonempty_utf8_markdown": bool(actual.strip()),
                    "heading": not options.get("heading") or actual.startswith("## "),
                    "ordered_list": not options.get("ordered") or all(re.search(r"(?m)^"+str(i)+r"\. ", actual) for i in (1,2,3)),
                }
                row.update(checks=checks, passed=all(checks.values()), warnings=result["warnings"],
                           output_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
                if not row["passed"]:
                    row.update(expected=expected, actual=actual)
            except Exception as exc:
                row.update(passed=False, error=str(exc))
            row["seconds"] = round(time.monotonic()-start, 3)
            rows.append(row)
            print(source.name, "PASS" if row["passed"] else "FAIL", row.get("error", ""), flush=True)
    finally:
        workspace.plugins.shutdown()
    passed = sum(r["passed"] for r in rows)
    report = {"scope": "20 synthetic printed-text images; not a guarantee for arbitrary images",
              "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "python": sys.version,
              "passed": passed, "total": len(CASES), "pass_rate": passed / len(CASES), "cases": rows}
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = ["# 图片转 Markdown 验收报告", "", f"通过：{passed} / {len(CASES)}（{passed/len(CASES):.0%}）", "",
               "固定合成图片，通过真实插件和 Windows OCR 转换。全文核对仅忽略空白及 Markdown 转义；不是任意图片准确率保证。", "",
               "| 图片 | 结果 | 耗时（秒） |", "| --- | --- | --- |"]
    summary += [f"| [{r['image']}]({r['image']}) | {'通过' if r['passed'] else '失败'} | {r['seconds']} |" for r in rows]
    (folder / "REPORT.md").write_text("\n".join(summary)+"\n", encoding="utf-8")
    print(f"{passed}/{len(CASES)}", folder, flush=True)
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
