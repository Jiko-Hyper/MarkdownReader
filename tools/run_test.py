"""Launch the current checkout using its bundled development runtime."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "build" / "runtime"
os.environ["TCL_LIBRARY"] = str(RUNTIME / "tcl" / "tcl8.6")
os.environ["TK_LIBRARY"] = str(RUNTIME / "tcl" / "tk8.6")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.argv[0] = str(ROOT / "main.py")
if __name__ == "__main__":
    runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
