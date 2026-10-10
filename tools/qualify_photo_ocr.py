"""Compare a real photographed page with an independently transcribed reference.

Preserves punctuation, numbers and Markdown structure in the comparison. Only
whitespace is normalized. The reference is never passed to the OCR worker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mdreader import core
from mdreader.conversion import convert_file


def normalized(text):
    return re.sub(r"\s+", "", text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()
    base = ROOT/"artifacts/photo-ocr-qualification"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    original = hashlib.sha256(args.source.read_bytes()).hexdigest()
    package = ROOT/"plugins/packages/mdreader.document-convert-1.4.0.zip"
    ws = core.Workspace(str(folder/"workspace"))
    start = time.monotonic()
    try:
        ws.plugins.install(str(package))
        ws.plugins.enable("mdreader.document-convert")
        command = next(c["command"] for c in ws.plugins.commands() if c.get("format") == "photo-md")
        result = convert_file(ws.plugins, command, str(args.source), str(folder/"result.md"))
        actual = Path(result["path"]).read_text("utf-8")
        expected = args.expected.read_text("utf-8")
        checks = {
            "complete_text_punctuation_numbers_and_structure": normalized(actual) == normalized(expected),
            "source_unchanged": original == hashlib.sha256(args.source.read_bytes()).hexdigest(),
            "headings": re.findall(r"(?m)^#{1,6} .+$", actual) == re.findall(r"(?m)^#{1,6} .+$", expected),
            "numbered_items": re.findall(r"(?m)^\d+\. ", actual) == re.findall(r"(?m)^\d+\. ", expected),
        }
        report = {"source_name": args.source.name, "source_sha256": original,
                  "output_sha256": hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest(),
                  "expected_sha256": hashlib.sha256(args.expected.read_bytes()).hexdigest(),
                  "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                  "seconds": round(time.monotonic()-start, 3), "checks": checks,
                  "passed": all(checks.values()), "warnings": result["warnings"]}
        (folder/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        (folder/"expected.md").write_text(expected, "utf-8")
        (folder/"REPORT.md").write_text("# 实拍照片验收\n\n" +
            ("通过" if report["passed"] else "未通过") +
            "：核对完整正文、所有标点和数字、标题及编号，仅忽略空白。预期稿未传入识别程序。\n\n" +
            "[自动转换结果](result.md) · [独立校对稿](expected.md) · [详细检查](report.json)\n", "utf-8")
        print(json.dumps(report, ensure_ascii=True))
        print(folder)
        return 0 if report["passed"] else 1
    finally:
        ws.plugins.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
