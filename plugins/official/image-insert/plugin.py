import re
import uuid


def insert_image(task):
    from PIL import Image, ImageOps
    source = task.options.get("image")
    if not source:
        raise ValueError("请先选择图片")
    task.progress("读取图片", 15)
    with Image.open(source) as opened:
        picture = ImageOps.exif_transpose(opened)
        picture.load()
        width = int(task.options.get("width") or min(picture.width, 720))
        if not 16 <= width <= 4096:
            raise ValueError("图片显示宽度应为 16 到 4096 像素")
        target = task.path("image-" + uuid.uuid4().hex[:12] + ".png")
        picture.convert("RGBA" if "A" in picture.getbands() else "RGB").save(target, "PNG")
    name = target.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    alt = re.sub(r'[\[\]\\\r\n]', " ", str(task.options.get("alt") or "图片"))
    task.progress("图片已准备", 100)
    return {"kind": "image-insert", "markdown": '![%s](assets/%s "width=%d")' % (alt, name, width),
            "assets": [{"path": target}], "note": "保留原始像素，通过显示宽度等比例缩放"}
