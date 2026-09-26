"""Build independently installable offline plugins for Windows Python 3.12 x64."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mdreader.plugins import build_package

VERSION = "1.0.2"
names = {"image-insert": "图片插入与缩放", "export-pdf": "Markdown 转 PDF", "export-docx": "Markdown 转 Word"}
packages = {"image-insert": ["PIL"], "export-pdf": ["PIL", "reportlab", "charset_normalizer", "markdown_it", "mdurl"],
            "export-docx": ["PIL", "docx", "lxml", "typing_extensions.py", "markdown_it", "mdurl"]}
trust_path = ROOT / "mdreader/plugin_trust.json"
trust = json.loads(trust_path.read_text(encoding="utf-8"))
outdir = ROOT / "plugins/packages"
outdir.mkdir(parents=True, exist_ok=True)
for kind, name in names.items():
    pid = "mdreader." + kind
    image = kind == "image-insert"
    capability = "editor.image_insert" if image else "export.format"
    command = {"id": "insert" if image else "export", "title": name,
               "capability": capability, "method": "insert_image" if image else "export_document"}
    if not image:
        ext = kind.split("-")[1]
        command.update(format=ext, extension=ext, magic="25504446" if ext == "pdf" else "504b0304",
                       media_type="application/pdf" if ext == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    manifest = {"id": pid, "name": name, "version": VERSION, "api_version": "1",
                "app_version_range": ">=0.2.8", "entrypoint": "plugin.py", "capabilities": [capability],
                "commands": [command], "dependencies": [], "publisher": "MDReader",
                "description": "离线插件，适用于 Windows x64 / Python 3.12；依赖随包提供。"}
    source = ROOT / "plugins/official" / kind
    (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    extra = {}
    if not image:
        extra["exporting.py"] = (ROOT / "plugins/official/common/exporting.py").read_bytes()
    for dependency in packages[kind]:
        base = ROOT / "plugins/dependencies" / dependency
        paths = [base] if base.is_file() else base.rglob("*")
        for file in paths:
            if file.is_file() and "__pycache__" not in file.parts and file.suffix not in (".pyc", ".pyo"):
                extra[file.relative_to(ROOT / "plugins/dependencies").as_posix()] = file.read_bytes()
    # Preserve dependency license notices shipped in wheel metadata.
    for file in (ROOT / "plugins/dependencies").rglob("*"):
        if file.is_file() and any(p.endswith(".dist-info") for p in file.parts) and (
                "license" in file.name.lower() or "copying" in file.name.lower()):
            extra["licenses/" + file.relative_to(ROOT / "plugins/dependencies").as_posix()] = file.read_bytes()
    target = outdir / (pid + "-" + VERSION + ".zip")
    build_package(str(source), str(target), extra=extra)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    trust["plugins"][pid] = {"publisher": "MDReader", "versions": {VERSION: digest}}
    print(target.name, target.stat().st_size, digest)
trust_path.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
import runpy
runpy.run_path(str(Path(__file__).with_name("build_conversion_plugin.py")), run_name="__main__")
