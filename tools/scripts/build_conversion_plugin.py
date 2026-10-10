"""Build the offline conversion plugin and register its exact digest."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mdreader.plugins import build_package, validate_manifest

source = ROOT / "plugins/official/document-convert"
manifest = {"id": "mdreader.document-convert", "name": "文档格式转换", "version": "1.4.0",
            "api_version": "1", "app_version_range": ">=0.4.11", "entrypoint": "plugin.py",
            "capabilities": ["file.convert"], "dependencies": [], "publisher": "MDReader",
            "description": "离线图片文字识别、PDF / Word 转 Markdown、Markdown 与 HTML 互转。",
            "commands": []}
for cid, title, method, ext in [
    ("scan-md", "扫描 PDF 转 Markdown（公式与原页批注）", "scan_to_md", "md"),
    ("image-md", "图片转 Markdown（离线文字识别）", "image_to_md", "md"),
    ("photo-md", "纸质照片转 Markdown（增强识别）", "photo_to_md", "md"),
    ("pdf-md", "PDF 转 Markdown", "pdf_to_md", "md"),
    ("docx-md", "Word 转 Markdown（DOCX）", "word_to_md", "md"),
    ("md-html", "Markdown 转 HTML", "md_to_html", "html"),
    ("html-md", "HTML 转 Markdown", "html_to_md", "md"),
]:
    manifest["commands"].append({"id": cid, "title": title, "method": method, "capability": "file.convert",
        "format": cid, "extension": ext, "media_type": "text/html" if ext == "html" else "text/markdown"})
validate_manifest(manifest)
(source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
deps = ROOT / "plugins/conversion-dependencies"
if not (deps / "pypdf/__init__.py").exists():
    raise SystemExit("先运行 pip install -r plugins/official/document-convert/requirements.txt --target plugins/conversion-dependencies")
extra = {p.relative_to(deps).as_posix(): p.read_bytes() for p in deps.rglob("*")
         if p.is_file() and "__pycache__" not in p.parts and "bin" not in p.relative_to(deps).parts
         and p.suffix not in (".pyc", ".pyo")}
target = ROOT / ("plugins/packages/mdreader.document-convert-" + manifest["version"] + ".zip")
build_package(str(source), str(target), extra=extra)
trust_path = ROOT / "mdreader/plugin_trust.json"
trust = json.loads(trust_path.read_text(encoding="utf-8"))
entry = trust["plugins"].setdefault(manifest["id"], {"publisher": "MDReader", "versions": {}})
entry["versions"][manifest["version"]] = hashlib.sha256(target.read_bytes()).hexdigest()
trust_path.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
print(target)
