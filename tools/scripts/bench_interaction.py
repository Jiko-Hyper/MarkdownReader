"""Reading-interaction benchmark: what one wheel notch and one zoom tick cost.

The 0.2.8 shape of this script measured a 180 KB document with eight zoom ticks
and reported ~19 ms, which hid the real problem: the cost was proportional to
the *number of links*, not to this small corpus, so a 1 MB article spent seconds
per tick (docs/DEVELOPMENT.md §3.10).

Everything here is disposable: the corpus is generated in a temporary directory
and deleted afterwards, and no user workspace is touched.

    python scripts/bench_interaction.py [--mb 0.2,1.0] [--rounds 3]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdreader import winui  # noqa: E402

PARAGRAPH = (
    "## 小节 %d\n\n"
    "这是一段普通的正文，用于填充文档内容，包含中文、English words 和数字 12345。\n"
    "它带有 **粗体**、*斜体*、`行内代码` 以及 [链接](https://example.com)。\n\n"
    "- 列表项一\n- 列表项二\n  - 嵌套项\n\n"
    "> 一段引用文字。\n\n"
)


def repeat_to_size(block: str, target_bytes: int) -> str:
    unit = max(1, len(block.encode("utf-8")))
    return block * max(1, int(target_bytes / unit))


def build_document(path: Path, megabytes: float) -> None:
    body = "".join(PARAGRAPH % part for part in range(4))
    path.write_text("# 长文\n\n" + repeat_to_size(body, int(megabytes * 1024 * 1024)),
                    encoding="utf-8")


def settle(window, seconds: float = 0.5) -> None:
    """Run the event loop until the pending zoom/scroll job has drained."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        window.root.update()
        wheel = getattr(window, "wheel", None)
        if getattr(window, "_zoom_job", None) is None and (wheel is None or wheel.job is None):
            return
        time.sleep(0.002)


def measure(window, megabytes: float) -> dict:
    document = Path(window._bench_tmp, "long.md")
    build_document(document, megabytes)
    window.open_local_files([str(document)])
    window.root.update()
    window.preview.yview_moveto(0.3)
    window.root.update()
    result = {
        "mb": megabytes,
        "documents": int(float(window.preview.index("end-1c"))),
        "tables": len(window.preview.winfo_children()),
    }

    # Zoom first, on a freshly opened document: Tk does its first full layout
    # during the first size change, and doing that before any scrolling keeps
    # the two sizes comparable.
    window.preview.yview_moveto(0.3)
    window.root.update()
    ticks = []
    for _ in range(4):
        started = time.perf_counter()
        window.on_zoom(SimpleNamespace(delta=120))
        window.root.update()
        settle(window)
        ticks.append((time.perf_counter() - started) * 1000)
    result["zoom_tick_ms"] = round(statistics.median(ticks), 2)
    result["zoom_tick_max_ms"] = round(max(ticks), 2)

    # A six-tick gesture, the way a reader actually changes the text size.
    started = time.perf_counter()
    for index in range(6):
        window.on_zoom(SimpleNamespace(delta=120 if index % 2 == 0 else -120))
        window.root.update()
        time.sleep(0.025)
        window.root.update()
    settle(window)
    result["zoom_gesture_ms"] = round((time.perf_counter() - started) * 1000, 1)

    # One wheel notch, fed at a human pace so each sample is one event plus its
    # share of repaint.  The handler cost and the applied scroll are separate:
    # see the gesture_handler_ms metric below.
    samples = []
    for _ in range(14):
        started = time.perf_counter()
        window.preview.event_generate("<MouseWheel>", delta=-120)
        window.root.update()
        samples.append((time.perf_counter() - started) * 1000)
        time.sleep(0.03)
        window.root.update()
    result["wheel_event_ms"] = round(statistics.median(samples), 2)
    result["wheel_event_max_ms"] = round(max(samples), 2)

    # A whole gesture: ten notches at 20 Hz, then the coalesced applies drain.
    started = time.perf_counter()
    handler = 0.0
    for _ in range(10):
        tick = time.perf_counter()
        window.preview.event_generate("<MouseWheel>", delta=-120)
        window.root.update()
        handler += time.perf_counter() - tick
        time.sleep(0.02)
        window.root.update()
    settle(window)
    result["gesture_wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    result["gesture_handler_ms"] = round(handler * 1000, 2)

    # Theme switch while reading (rebuilds the preview in place).
    started = time.perf_counter()
    window.set_theme("dark" if window.theme != "dark" else "light")
    window.root.update()
    result["theme_switch_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MDReader reading-interaction benchmark")
    parser.add_argument("--mb", default="0.2,1.0",
                        help="document sizes in MB, comma separated (default 0.2,1.0)")
    parser.add_argument("--rounds", type=int, default=3, help="rounds per size")
    args = parser.parse_args(argv)
    sizes = [float(part) for part in str(args.mb).split(",") if part.strip()]

    import tkinter

    print("Python:", sys.version.split()[0], "| Tk:", tkinter.TkVersion)
    for megabytes in sizes:
        runs = []
        for _ in range(max(1, args.rounds)):
            with tempfile.TemporaryDirectory(prefix="mdreader-interaction-") as tmp:
                with patch.object(winui.MarkdownWindow, "enable_file_drop"):
                    window = winui.MarkdownWindow(str(Path(tmp, "workspace")))
                window._bench_tmp = tmp
                try:
                    window.root.geometry("1100x760+20+20")
                    window.root.update()
                    runs.append(measure(window, megabytes))
                finally:
                    window.ws.folders.watch().stop()
                    for job in window.root.tk.call("after", "info"):
                        window.root.after_cancel(job)
                    window.root.destroy()
        summary = {key: round(statistics.median(run[key] for run in runs), 2)
                   for key in runs[0] if key != "mb"}
        summary["mb"] = megabytes
        print(json.dumps(summary, ensure_ascii=False))
        if os.environ.get("MDREADER_BENCH_VERBOSE"):
            for run in runs:
                print("   ", json.dumps(run, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
