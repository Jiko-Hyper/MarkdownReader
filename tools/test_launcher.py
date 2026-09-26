"""Run the compiled launcher with a harmless argument-capture entry point."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="mdreader-launcher-") as temp:
        package = Path(temp) / "中文 space"
        runtime = package / "runtime"
        runtime.mkdir(parents=True)
        shutil.copy2(root / "build/launcher/MDReader.exe", package)
        for source in (root / "build/runtime").iterdir():
            if source.is_file() and source.suffix in {".exe", ".dll", ".zip", "._pth"}:
                shutil.copy2(source, runtime)
        (package / "main.py").write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path(__file__).with_suffix('.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "sys.exit(7 if '--fail' in sys.argv else 0)\n", encoding="utf-8")
        cases = [[], ["--version"], ["--open", str(package / "笔记 --console.md")],
                 ["--workspace", str(package / "数据 空间"), "--port", "0"], ["--fail"]]
        for args in cases:
            result = subprocess.run([str(package / "MDReader.exe"), "--console", *args], timeout=20)
            actual = json.loads((package / "main.json").read_text(encoding="utf-8"))
            assert actual == args, (actual, args)
            assert result.returncode == (7 if args == ["--fail"] else 0), result.returncode
    print("PASS: launcher arguments, Unicode/spaces, console substring, child exit status")


if __name__ == "__main__":
    main()
