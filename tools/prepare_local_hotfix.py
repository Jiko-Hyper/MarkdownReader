"""Prepare a narrow, hash-verified hotfix against an existing local install.

This preparation tool only reads the install. Applying the resulting archive is
an explicit, separate action, and the launcher refuses while the app is running.
"""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile


WIN_METHODS = (
    "_bind_navigation", "switch_at_selection", "_preview_selection_offsets",
    "open_user_folder",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return path.read_bytes().decode("utf-8-sig")


def find(text, name, class_name=None):
    tree = ast.parse(text)
    body = tree.body
    if class_name:
        body = next(node.body for node in body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)


def lines_of(text, node):
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return "".join(text.splitlines(keepends=True)[start - 1:node.end_lineno])


def normalize(text, old):
    newline = "\r\n" if "\r\n" in old else "\n"
    return text.replace("\r\n", "\n").replace("\n", newline)


def replace_function(old, source, name, class_name=None):
    node = find(old, name, class_name)
    replacement = normalize(lines_of(source, find(source, name, class_name)), old)
    lines = old.splitlines(keepends=True)
    first = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
    lines[first:node.end_lineno] = [replacement]
    return "".join(lines)


def shortcut_line(text, key):
    tree = ast.parse(text)
    shortcuts = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "SHORTCUTS" for t in node.targets))
    row = next(row for row in shortcuts.value.elts
               if isinstance(row, ast.Tuple) and row.elts[0].value == key)
    if row.lineno != row.end_lineno:
        raise ValueError("Shortcut entry must be one line")
    return row.lineno - 1


def add_navigation_menu(old, source):
    method = find(source, "show_more_menu", "MarkdownWindow")
    statement = next(node for node in method.body if isinstance(node, ast.Expr)
                     and isinstance(node.value, ast.Call)
                     and any(k.arg == "command" and isinstance(k.value, ast.Attribute)
                             and k.value.attr == "switch_at_selection"
                             for k in node.value.keywords))
    target = find(old, "show_more_menu", "MarkdownWindow")
    anchor = next(node for node in target.body if isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call)
                  and any(k.arg == "command" and isinstance(k.value, ast.Attribute)
                          and k.value.attr == "show_outline"
                          for k in node.value.keywords))
    lines = old.splitlines(keepends=True)
    lines.insert(anchor.lineno - 1, normalize(lines_of(source, statement), old))
    return "".join(lines)


def backport_winui(old, source, extra_methods):
    result = old
    for name in (*WIN_METHODS, *extra_methods):
        result = replace_function(result, source, name, "MarkdownWindow")
    lines = result.splitlines(keepends=True)
    source_lines = source.splitlines(keepends=True)
    lines[shortcut_line(result, "Shift+M")] = normalize(
        source_lines[shortcut_line(source, "Ctrl+Shift+E")], old)
    return add_navigation_menu("".join(lines), source)


def backport_core(old, source):
    # Preserve the old general PowerShell helper: other dialogs keep their
    # behavior. Only this folder picker needs error reporting and cancellation.
    helper = lines_of(source, find(source, "_run_ps"))
    helper = helper.replace("def _run_ps(", "def _run_folder_ps(", 1)
    result = replace_function(old, source, "_dialog_folder")
    node = find(result, "_dialog_folder")
    folder = lines_of(result, node).replace("_run_ps(", "_run_folder_ps(")
    lines = result.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [
        normalize(helper.rstrip() + "\n\n\n", old), folder,
    ]
    result = "".join(lines)
    if ast.dump(find(old, "_run_ps")) != ast.dump(find(result, "_run_ps")):
        raise ValueError("Unrelated PowerShell helper changed")
    return result


def launcher(install, checksum):
    install_literal = str(install).replace("'", "''")
    return f'''param([switch]$CheckOnly, [switch]$NoPause)
$ErrorActionPreference = 'Stop'
$install = '{install_literal}'
$archive = Join-Path $PSScriptRoot 'MDReader-0.4.12-local-hotfix-update.zip'
$expectedArchiveHash = '{checksum}'
try {{
    if (-not (Test-Path -LiteralPath (Join-Path $install 'MDReader.exe')) -or
        -not (Test-Path -LiteralPath (Join-Path $install 'runtime\\python.exe'))) {{
        throw '原 MDReader 程序目录不存在，请勿移动本更新入口。'
    }}
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedArchiveHash) {{
        throw '修复包校验失败，未修改程序。'
    }}
    $running = @(Get-CimInstance Win32_Process | Where-Object {{
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($install+'\\', [StringComparison]::OrdinalIgnoreCase)
    }})
    if ($running.Count) {{
        throw 'MDReader 仍在运行。请先保存文档，并关闭所有 MDReader 窗口，再双击本入口。'
    }}
    if ($CheckOnly) {{
        Write-Host '修复包完整，MDReader 已关闭。检查完成，尚未更新。' -ForegroundColor Green
        return
    }}
    & (Join-Path $install 'runtime\\python.exe') (Join-Path $PSScriptRoot 'apply_update.py') --install $install --archive $archive
    if ($LASTEXITCODE -ne 0) {{ throw '修复未完成，请查看上方原因；原文件备份和回滚由更新工具保留。' }}
    Write-Host '修复完成。请使用原桌面 MDReader 图标打开程序。' -ForegroundColor Green
}} catch {{
    Write-Host ('未更新：' + $_.Exception.Message) -ForegroundColor Red
    if ($CheckOnly) {{ throw }}
}} finally {{
    if (-not $NoPause -and -not $CheckOnly) {{ Read-Host '按 Enter 关闭窗口' | Out-Null }}
}}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extra-win-method", action="append", default=[])
    args = parser.parse_args()
    source, install, out = args.source.resolve(), args.install.resolve(), args.out.resolve()
    if out.is_relative_to(install) or install.is_relative_to(out):
        raise ValueError("Output and install must be separate")
    if out.exists():
        raise ValueError("Use a new output directory; preparation never overwrites artifacts")
    originals = {}
    payloads = {}

    for relative, transform in [
        ("mdreader/winui.py", lambda a, b: backport_winui(a, b, args.extra_win_method)),
        ("mdreader/core.py", backport_core),
    ]:
        originals[relative] = (install / relative).read_bytes()
        result = transform(originals[relative].decode("utf-8-sig"), read(source / relative))
        compile(result, relative, "exec")
        payloads[relative] = result.encode("utf-8")
    installed_core = originals["mdreader/core.py"].decode("utf-8-sig")
    version = re.search(r'APP_VERSION = "([^"]+)"', installed_core).group(1)
    if version != "0.4.12" or f'APP_VERSION = "{version}"' not in payloads["mdreader/core.py"].decode("utf-8"):
        raise ValueError("This local backport must preserve the 0.4.12 program baseline")

    # navigation.py had no intervening changes in this checkout. Require every
    # old top-level function to survive verbatim in AST terms before accepting
    # the new mapping implementation and its helpers.
    relative = "mdreader/navigation.py"
    originals[relative] = (install / relative).read_bytes()
    old_nav, new_nav = originals[relative].decode("utf-8-sig"), read(source / relative)
    find(new_nav, "map_markdown_selection")
    for node in ast.parse(old_nav).body:
        if isinstance(node, ast.FunctionDef):
            if ast.dump(node) != ast.dump(find(new_nav, node.name)):
                raise ValueError("Existing navigation helper changed: " + node.name)
    compile(new_nav, relative, "exec")
    payloads[relative] = normalize(new_nav, old_nav).encode("utf-8")

    relative = "Select-Folder.ps1"
    originals[relative] = (install / relative).read_bytes() if (install / relative).exists() else None
    payloads[relative] = (source / "release/app/Select-Folder.ps1").read_bytes()
    if b"ShowFiles" not in payloads[relative]:
        raise ValueError("Folder browser fix is not ready")

    # All input validation above finishes before preparing any output.
    out.mkdir(parents=True)
    stage, backup = out / "staging", out / "original-program-files"
    stage.mkdir()
    shutil.copytree(install / "mdreader", stage / "mdreader", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(install / "main.py", stage / "main.py")
    shutil.copy2(install / "MDReader.exe", stage / "MDReader.exe")
    changes = {}
    for relative, data in payloads.items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        before = originals[relative]
        if before is not None:
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)
        changes[relative] = {"before": digest(before) if before is not None else None,
                             "sha256": digest(data)}
    manifest = {"schema": 1, "base_version": version, "version": version,
                "hotfix": "local-navigation-folder-20261014", "files": changes}
    archive = out / "MDReader-0.4.12-local-hotfix-update.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as package:
        for relative, data in payloads.items():
            package.writestr(relative, data)
        package.writestr("update-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as package:
        if package.testzip():
            raise ValueError("Patch archive validation failed")
    checksum = digest(archive.read_bytes())
    archive.with_suffix(".zip.sha256").write_text(checksum + "  " + archive.name + "\n", encoding="ascii")
    (out / "update-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(source / "tools/apply_update.py", out / "apply_update.py")
    (out / "应用修复.ps1").write_text(launcher(install, checksum), encoding="utf-8-sig")
    (out / "应用修复.cmd").write_text('@echo off\r\npowershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0应用修复.ps1"\r\n', encoding="utf-8-sig")
    diffs = []
    for relative, after in payloads.items():
        before = originals[relative] or b""
        diffs.extend(difflib.unified_diff(before.decode("utf-8-sig").splitlines(keepends=True),
                                         after.decode("utf-8-sig").splitlines(keepends=True),
                                         fromfile="installed/" + relative, tofile="hotfix/" + relative))
    (out / "program-changes.diff").write_text("".join(diffs), encoding="utf-8")
    (out / "修复说明.txt").write_text(
        "本地修复：预览与源码定位、打开文件夹时显示内容。\n"
        "保留正式版 0.4.12 基线，仅替换清单中的 4 个程序文件。\n"
        "请保存文档并关闭所有 MDReader 窗口，再双击 应用修复.ps1 的桌面入口。\n"
        "更新会验证旧文件与修复包，备份原程序文件；失败会自动回滚。\n"
        "文档、配置、运行时和识别组件不参与更新。\n"
        "目标：" + str(install) + "\n", encoding="utf-8-sig")
    print(json.dumps({"out": str(out), "archive": str(archive), "sha256": checksum,
                      "files": list(payloads), "install_modified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
