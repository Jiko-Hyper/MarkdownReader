"""Render all PDF fixture pages to PNG with an independent PDFium engine for visual QA."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugins/conversion-dependencies")]
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

corpus = ROOT / "artifacts/conversion-qualification/corpus"
output = corpus.parent / "visual-check"
output.mkdir(exist_ok=True)
thumbs = []
for path in sorted(corpus.glob("*.pdf")):
    with pdfium.PdfDocument(path) as doc:
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=1.25)
            image = bitmap.to_pil().convert("RGB")
            image.save(output / (path.stem[:2] + "-page-%d.png" % (i+1)))
            image.thumbnail((300,425))
            thumb = Image.new("RGB",(320,465),"#dce0e5")
            thumb.paste(image,(10,30))
            ImageDraw.Draw(thumb).text((10,10),"PDF %s / page %d" % (path.stem[:2],i+1),fill="black")
            thumbs.append(thumb)
            bitmap.close(); page.close()
for start in range(0,len(thumbs),9):
    group = thumbs[start:start+9]
    sheet = Image.new("RGB",(960,465*((len(group)+2)//3)),"white")
    for n,thumb in enumerate(group): sheet.paste(thumb,((n%3)*320,(n//3)*465))
    sheet.save(output / ("contact-%d.png" % (start//9+1)))
print(output)
