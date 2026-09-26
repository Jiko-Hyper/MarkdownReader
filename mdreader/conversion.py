"""Host-owned file conversion: validate inputs, run a worker, publish new files only."""
from pathlib import Path
import os
import shutil
import tempfile
import uuid

from . import plugins as P

FORMATS = {
    "pdf-md": ((".pdf",), ".md"),
    "docx-md": ((".docx",), ".md"),
    "md-html": ((".md", ".markdown"), ".html"),
    "html-md": ((".html", ".htm"), ".md"),
}
MAX_INPUT = 32 * 1024 * 1024


def convert_file(store, command_name, source, destination):
    command = store.find_command(command_name)
    if command["capability"] != P.CAP_CONVERT or command.get("format") not in FORMATS:
        raise P.TaskError("请选择文档格式转换命令")
    extensions, output_ext = FORMATS[command["format"]]
    source = Path(source).resolve(strict=True)
    dest = Path(destination).absolute()
    if source.suffix.lower() not in extensions:
        raise P.TaskError("此命令只支持：" + "、".join(extensions))
    if not source.is_file() or source.stat().st_size > MAX_INPUT:
        raise P.TaskError("输入必须是 32 MB 以内的文件")
    if dest.suffix.lower() != output_ext:
        raise P.TaskError("输出文件扩展名应为 " + output_ext)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("目标已存在，请选择新的文件名：" + str(dest))
    if not dest.parent.is_dir():
        raise P.TaskError("请先创建输出目录")
    asset_name = "conversion-assets-" + uuid.uuid4().hex[:12]
    task = store.tasks.submit(command_name, input_files=[str(source)], timeout=120,
                              options={"source_dir": str(source.parent), "assets_name": asset_name})
    temporary = None
    asset_dir = None
    try:
        if not task.done.wait(125):
            store.tasks.cancel(task.id, "转换超时")
            task.done.wait(5)
            raise P.TaskError("转换超时，请尝试较小的文档")
        checked = store.tasks.result(task.id)
        if checked["extension"] != output_ext.lstrip("."):
            raise P.TaskError("转换产物扩展名不匹配")
        assets = checked.get("assets") or []
        if sum(a["bytes"] for a in assets) + checked["bytes"] > P.MAX_ARTIFACT_BYTES:
            raise P.TaskError("转换结果与图片合计超过 64 MB")
        if assets:
            folder = dest.parent / asset_name
            folder.mkdir()  # Never adopt an existing directory.
            asset_dir = folder
            for asset in assets:
                with open(asset["path"], "rb") as inp, open(folder / asset["name"], "xb") as out:
                    shutil.copyfileobj(inp, out)
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".conversion-", delete=False) as out:
            temporary = Path(out.name)
            with open(checked["path"], "rb") as inp:
                shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        # Same-volume hard link publishes atomically and refuses a racing existing target.
        os.link(temporary, dest)
        return {"path": str(dest), "warnings": checked.get("warnings", []),
                "assets": str(asset_dir) if asset_dir else ""}
    except Exception:
        if asset_dir is not None:
            shutil.rmtree(asset_dir)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if task.done.is_set():
            store.tasks.discard(task.id)


def show_conversion(owner, command):
    """Select disk input explicitly; never save or replace the current editor buffer."""
    from tkinter import filedialog, messagebox
    from .media_ui import run_job
    extensions, suffix = FORMATS[command["format"]]
    source = filedialog.askopenfilename(parent=owner.root, title=command["title"],
        filetypes=[("源文档", " ".join("*" + ext for ext in extensions))])
    if not source:
        return
    dest = filedialog.asksaveasfilename(parent=owner.root, title="保存转换结果（新文件）",
        initialdir=str(Path(source).parent), initialfile=Path(source).stem + suffix,
        defaultextension=suffix, filetypes=[("转换结果", "*" + suffix)])
    if not dest:
        return
    try:
        owner.notice("正在转换文件…")
        result = run_job(owner, lambda: convert_file(owner.ws.plugins, command["command"], source, dest))
        message = "已保存：" + result["path"]
        if result["assets"]:
            message += "\n图片目录：" + result["assets"]
        if result["warnings"]:
            message += "\n\n" + "\n".join(result["warnings"])
        owner.notice("已转换：" + result["path"])
        messagebox.showinfo("转换完成", message, parent=owner.root)
    except Exception as exc:
        owner.notice("转换失败：" + str(exc), error=True)
        messagebox.showerror("转换失败", str(exc), parent=owner.root)
