"""End-to-end export samples for visual inspection, using only isolated files."""
import json
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mdreader import core
from PIL import Image, ImageDraw

out = ROOT / ".testtmp/official-plugin-review"
out.mkdir(parents=True, exist_ok=True)
photo = Image.new("RGB", (1000, 420), "#e8eef6")
draw = ImageDraw.Draw(photo)
draw.rectangle((60, 80, 400, 340), fill="#2563a6")
draw.ellipse((560, 60, 900, 380), fill="#499177")
photo.save(out / "diagram.png")
source = """# 文档转换验证

本文件用于验证 Markdown 导出后的中文、表格、图片与分页。Word 内容应可编辑，PDF 应能搜索文字。

## 格式与图片

这段包含 **粗体**、*斜体*、`inline code` 和 [链接](https://example.com)。

- 第一项：中文与 English
- 第二项：保留可编辑列表

![示例图](diagram.png "width=520")

| 项目 | 结果 | 说明 |
|---|---|---|
| 标题 | 正常 | 中文标题与层级 |
| 图片 | 等比例 | 保留原始像素 |
| 文档 | 可编辑 | 表格与正文 |

## 长文分页

"""
source += ("分页测试：每个自然段都应完整显示，不与页脚重叠，不出现截断或缺失字符。长文保持连续排版，表格具有清晰的边框。\n\n" * 20)
source += "```python\nprint('MDReader export')\n```\n"
document = out / "sample.md"
document.write_text(source, encoding="utf-8")
ws = core.Workspace(tempfile.mkdtemp(prefix="workspace-", dir=out))
api = core.Api(ws, str(ROOT / "webui"))
try:
    for file in (ROOT / "plugins/packages").glob("*.zip"):
        with zipfile.ZipFile(file) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        ws.plugins.install(str(file))
        ws.plugins.enable(manifest["id"])
    info = api.loose.open_path(str(document))
    for command in ws.plugins.commands():
        if command.get("extension") not in ("pdf", "docx"):
            continue
        target = out / ("sample." + command["extension"])
        result = api.post("/api/plugins/export", {"command": command["command"], "doc": str(document),
            "revision": info["revision"], "dest": str(target), "overwrite": True, "wait": 120})
        print(result.get("path") or result)
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(out / "sample.pdf"))
    for index in range(len(pdf)):
        pdf[index].render(scale=1.5).to_pil().save(out / ("pdf-page-%d.png" % (index + 1)))
    print("PDF pages:", len(pdf))
finally:
    ws.plugins.shutdown()
