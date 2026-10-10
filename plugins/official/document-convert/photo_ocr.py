"""Local printed-page OCR; no document-specific substitutions or external services."""
from pathlib import Path
import hashlib
import re
import statistics

from image_ocr import escape


def page_markdown(rows):
    if not rows:
        raise ValueError("照片中未识别到文字，请检查照片方向和清晰度。")
    # Detector returns quadrilaterals; use edge length rather than bounding-box
    # height, which exaggerates font size on a tilted page.
    def height(row):
        box = row["box"]
        return ((sum((box[0][i]-box[3][i])**2 for i in (0, 1)))**.5 +
                (sum((box[1][i]-box[2][i])**2 for i in (0, 1)))**.5) / 2
    normal = statistics.median(height(r) for r in rows)
    blocks = []
    kind = None
    previous = None
    headings = 0
    page_left = min(p[0] for row in rows for p in row["box"])
    page_right = max(p[0] for row in rows for p in row["box"])
    for index, row in enumerate(rows):
        text = row["text"].strip()
        if not text:
            continue
        h = height(row)
        number = re.match(r"^(\d{1,3})[、．.)]\s*(?=\D)(.+)$", text)
        chinese_number = re.match(r"^[一二三四五六七八九十]+[、．.]", text)
        current_top = min(p[1] for p in row["box"])
        previous_bottom = max(p[1] for p in previous["box"]) if previous else 0
        next_top = min(p[1] for p in rows[index+1]["box"]) if index+1 < len(rows) else current_top
        center = (min(p[0] for p in row["box"])+max(p[0] for p in row["box"]))/2
        isolated_center = (abs(center-(page_left+page_right)/2) < normal*2 and
                           current_top-previous_bottom > normal*.8 and
                           next_top-max(p[1] for p in row["box"]) > normal*.8)
        heading = (h >= normal*1.2 or h >= normal*1.1 and isolated_center) and len(text) <= 60 and not re.search(r"[，。；：？！]", text)
        gap = previous is not None and current_top-previous_bottom > normal*.9
        if heading:
            headings += 1
            blocks.append(("# " if headings == 1 else "## ") + escape(text))
            kind = "heading"
        elif number:
            blocks.append(number[1] + ". " + escape(number[2]))
            kind = "list"
        elif chinese_number or gap or kind == "heading" or not blocks:
            blocks.append(escape(text))
            kind = "paragraph"
        else:
            # CJK wraps need no inserted space; Latin word boundaries do.
            spacer = " " if re.search(r"[A-Za-z0-9]$", blocks[-1]) and re.match(r"[A-Za-z0-9]", text) else ""
            blocks[-1] += spacer + escape(text)
        previous = row
    return "\n\n".join(blocks) + "\n"


def load_engine():
    try:
        import rapidocr
        from rapidocr import RapidOCR, OCRVersion, ModelType
        from rapidocr.utils.download_file import DownloadFile
    except ImportError as exc:
        raise ValueError("照片增强识别组件未安装。请使用包含离线照片识别组件的桌面测试版。") from exc
    models = Path(rapidocr.__file__).parent / "models"
    names = {"Det": "PP-OCRv6_det_small.onnx", "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
             "Rec": "PP-OCRv6_rec_small.onnx"}
    if not all((models/name).is_file() for name in names.values()):
        raise ValueError("照片识别模型不完整，请重新安装离线照片识别组件。")
    hashes = {"Det": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
              "Cls": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
              "Rec": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"}
    for stage, name in names.items():
        if hashlib.sha256((models/name).read_bytes()).hexdigest() != hashes[stage]:
            raise ValueError("照片识别模型校验失败，请重新安装离线照片识别组件。")
    # Fail closed if the library ever tries to fetch an additional resource.
    def offline_only(*args, **kwargs):
        raise ValueError("照片识别缺少本地资源；未发起网络请求。")
    DownloadFile.run = offline_only
    params = {f"{stage}.model_path": str(models/name) for stage, name in names.items()}
    params.update({"Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
                   "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
                   "Det.limit_side_len": 1280, "Global.max_side_len": 3000,
                   "Global.use_cls": False, "Global.text_score": 0.0, "Global.log_level": "error",
                   "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                   "EngineConfig.onnxruntime.inter_op_num_threads": 1})
    return RapidOCR(params=params)


def read_photo(path):
    from PIL import Image, ImageOps
    import cv2
    import numpy as np
    with Image.open(path) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("照片增强识别只支持单张图片，请先拆分多页图片。")
        if image.width*image.height > 20_000_000 or max(image.size) > 10000:
            raise ValueError("图片过大，请裁剪到 2000 万像素以内后重试。")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image).convert("RGB")
        image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def recognize(image, engine, progress):
    import cv2
    import numpy as np
    from rapidocr.utils.process_img import get_rotate_crop_image
    result = engine(image, use_cls=False)
    if result.txts is None or result.boxes is None:
        raise ValueError("照片中未识别到文字，请检查照片方向和清晰度。")
    rows = [{"text": text, "score": float(score), "box": box.tolist()}
            for text, score, box in zip(result.txts, result.scores, result.boxes)]
    if len(rows) > 300:
        raise ValueError("照片文字行数过多，请分段裁剪后重试。")
    if not rows:
        raise ValueError("照片中未识别到文字，请检查照片方向和清晰度。")
    # Adjacent boxes can overlap on the last Latin glyph (e.g. the final l
    # becomes an extra I in the next box). Re-recognize a joined physical line
    # instead of concatenating the duplicated character strings.
    joined = []
    for row in rows:
        box = np.asarray(row["box"])
        left, right = float(box[:,0].min()), float(box[:,0].max())
        top, bottom = float(box[:,1].min()), float(box[:,1].max())
        if joined:
            previous = joined[-1]
            prev = np.asarray(previous["box"])
            prev_height = float(prev[:,1].max()-prev[:,1].min())
            height = min(bottom-top, prev_height)
            gap = left-float(prev[:,0].max())
            same_baseline = abs((top+bottom)/2-float(prev[:,1].mean())) < height*.3
            if same_baseline and -height < gap < height*.8:
                x0, x1 = min(left,float(prev[:,0].min())), max(right,float(prev[:,0].max()))
                y0, y1 = min(top,float(prev[:,1].min())), max(bottom,float(prev[:,1].max()))
                previous["box"] = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
                previous["joined"] = True
                continue
        joined.append(row)
    merged = [row for row in joined if row.get("joined")]
    if merged:
        crops = [get_rotate_crop_image(image, np.asarray(row["box"], dtype=np.float32)) for row in merged]
        reread = engine.recognize_txt(crops)
        for row, text, score in zip(merged, reread.txts, reread.scores):
            row["text"], row["score"] = text, float(score)
    rows = joined
    progress("去除纸张阴影并复核文字…", 60)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Divide by a blurred paper background rather than a global threshold, so
    # fine punctuation survives uneven illumination. Reuse original detection
    # boxes; a second detector pass could silently lose/merge text lines.
    flat = cv2.divide(gray, cv2.GaussianBlur(gray, (0, 0), 18), scale=255)
    flat = cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)
    crops = [get_rotate_crop_image(flat, np.asarray(row["box"], dtype=np.float32)) for row in rows]
    refined = engine.recognize_txt(crops)
    for row, text, score in zip(rows, refined.txts, refined.scores):
        # Keep low-confidence alternatives visible in the completion warning.
        row["disagreement"] = text != row["text"]
        if text.strip() and score >= .8 and len(text) >= len(row["text"])*.8:
            row["text"], row["score"] = text, float(score)
    return rows


def convert(conversion):
    conversion.task.progress("加载本地照片识别模型…", 10)
    engine = load_engine()
    try:
        image = read_photo(conversion.source)
    except OSError as exc:
        raise ValueError("无法读取照片，请检查文件是否完整。") from exc
    conversion.task.progress("识别照片中的文字与行位置…", 30)
    rows = recognize(image, engine, conversion.task.progress)
    conversion.task.progress("还原标题、段落与编号列表…", 85)
    conversion.warn("照片已在本机识别，未上传。请对照原图核对细小标点、复选框和数字；严重模糊、手写、表格与多栏照片仍可能识别不准。")
    uncertain = [str(i+1) for i, row in enumerate(rows) if row["score"] < .95 or row.get("disagreement")]
    if uncertain:
        conversion.warn("以下原图文字行建议重点核对：" + "、".join(uncertain[:20]))
    return page_markdown(rows)
