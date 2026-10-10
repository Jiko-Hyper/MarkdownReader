"""Prepare pinned offline photo OCR dependencies and copy them into the desktop runtime.

Use a full Python 3.12 interpreter. --install downloads packages from pip's configured
index; recognition itself never downloads models or sends images over the network.
"""
import argparse
import hashlib
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins/photo-ocr-dependencies"
HASHES = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}


def verify(folder):
    versions = {d.metadata["Name"].lower().replace("_", "-"): d.version
                for d in importlib.metadata.distributions(path=[str(folder)])}
    for requirement in (ROOT/"tools/photo_ocr_requirements.txt").read_text("utf-8").splitlines():
        if not requirement or requirement.startswith("#"):
            continue
        name, version = requirement.split("==")
        if versions.get(name.lower().replace("_", "-")) != version:
            raise RuntimeError("Missing or wrong OCR dependency: " + requirement)
    for name, expected in HASHES.items():
        model = folder/"rapidocr/models"/name
        if not model.is_file() or hashlib.sha256(model.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Missing or invalid OCR model: " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--runtime", type=Path, default=ROOT/"build/runtime")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("The desktop OCR runtime requires Python 3.12")
    if args.install:
        subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(SOURCE),
                        "-r", str(ROOT/"tools/photo_ocr_requirements.txt")], check=True)
    verify(SOURCE)
    if not (args.runtime/"python.exe").is_file():
        raise RuntimeError("Prepare the desktop Python runtime first")
    destination = args.runtime/"photo_ocr_runtime"
    shutil.copytree(SOURCE, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "bin"))
    verify(destination)
    print("Offline photo OCR ready:", destination)


if __name__ == "__main__":
    main()
