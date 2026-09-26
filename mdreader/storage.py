"""Filesystem primitives shared by workspace operations; no UI dependencies."""
from __future__ import annotations

import os
import uuid

# Names of our own temporary files. They start with a dot so an interrupted
# write is visually obvious, and they never collide with a user's own
# "<file>.md.tmp" recovery file (the regression tests pin that behaviour).
_TEMP_PREFIX = ".mdreader-"
_TEMP_SUFFIX = ".tmp"


def safe_join(root: str, *parts: str) -> str:
    """Resolve links/junctions and reject paths outside the requested root."""
    root_abs = os.path.realpath(os.path.abspath(root))
    target = os.path.realpath(os.path.join(
        root_abs, *[p.replace("/", os.sep).replace("\\", os.sep) for p in parts if p]))
    if not is_within(root_abs, target):
        raise ValueError("路径越界，已拒绝：%s" % target)
    return target


def is_within(root: str, target: str) -> bool:
    """True when ``target`` is ``root`` itself or lives under it.

    Both sides are resolved with :func:`os.path.realpath`, so a symlink or a
    Windows junction pointing outside the root is reported as outside.
    """
    root_abs = os.path.realpath(os.path.abspath(root))
    target_abs = os.path.realpath(os.path.abspath(target))
    try:
        return os.path.normcase(os.path.commonpath([root_abs, target_abs])) == os.path.normcase(root_abs)
    except ValueError:      # different drives on Windows
        return False


def _make_temporary(parent: str, name: str) -> "tuple[int, str]":
    """Create a fresh sibling temp file and return ``(fd, path)``.

    Uses :func:`os.open` with ``O_CREAT | O_EXCL`` and a random name rather than
    ``tempfile.mkstemp``: ``mkstemp`` silently retries in a loop when a creation
    attempt fails, which turns an unwritable directory into an apparent hang.
    Here a failed attempt is either retried a bounded number of times or raised.
    """
    for _ in range(8):
        candidate = os.path.join(parent, "%s%s%s" % (_TEMP_PREFIX, uuid.uuid4().hex[:12], _TEMP_SUFFIX))
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        return fd, candidate
    raise FileExistsError("无法在 %s 下创建临时文件" % parent)


def atomic_write(path: str, text: str | bytes) -> None:
    """Write via a unique sibling, so failed/concurrent writes cannot truncate data.

    The original file is only touched by the final :func:`os.replace`. If
    anything fails — including the replace itself — the temporary file is
    removed and the original content is left untouched, and the exception
    propagates so callers can report it.
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)

    fd, temporary = _make_temporary(parent, os.path.basename(path))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text if isinstance(text, bytes) else text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
