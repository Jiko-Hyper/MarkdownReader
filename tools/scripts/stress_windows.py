"""Same-process lifecycle stress; both IME and shell drop bridges are enabled."""
import gc
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdreader.winui import MarkdownWindow

with tempfile.TemporaryDirectory(prefix='mdreader-stress-') as root:
    for index in range(int(sys.argv[1]) if len(sys.argv) > 1 else 100):
        window = MarkdownWindow(root)
        window.root.withdraw()
        assert window.ime.ok, window.ime.error
        assert window._drop.ok
        window.root.update()
        window.root.destroy()
        del window
        gc.collect()
        if (index + 1) % 10 == 0:
            print('cycles:', index + 1, flush=True)
