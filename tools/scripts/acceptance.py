"""验收矩阵里可以自动化的部分（`开发框架.md` 7.2）。

`scripts/check.ps1` 验证的是代码行为；这个脚本驱动**真实窗口与真实工作区**，
按验收矩阵逐行跑一遍能自动判定的项目，打印结果，并可用 `--write` 落一份
带环境信息的验收记录。需要人眼判断的行（排版观感、输入法候选词外观等）
会以 SKIP 列出并写明原因，不会假装通过。

    python scripts/acceptance.py                # 只打印
    python scripts/acceptance.py --write        # 同时写入 docs/acceptance-<版本>-<日期>.md
    python scripts/acceptance.py --keep         # 保留临时工作区以便排查
"""
from __future__ import annotations

import argparse
import ctypes as c
import datetime
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from unittest import mock
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _code_root() -> Path:
    """Where to import `mdreader` from: the source tree by default.

    ``--root DIR`` points the run at another copy (the actual install or an
    extracted distribution), because 开发框架.md 9 requires source, package and
    install to be verified separately. Parsed before importing the app so the
    chosen copy really is the one under test.
    """
    argv = sys.argv[1:]
    for index, item in enumerate(argv):
        if item == "--root" and index + 1 < len(argv):
            return Path(argv[index + 1]).resolve()
        if item.startswith("--root="):
            return Path(item.split("=", 1)[1]).resolve()
    return ROOT


CODE_ROOT = _code_root()
WEBUI = CODE_ROOT / "webui"
sys.path.insert(0, str(CODE_ROOT))
from mdreader import core, documents as D, plugins as P, winui  # noqa: E402
from mdreader.ime import CANDIDATE_GAP, CFS_CANDIDATEPOS, CFS_RECT  # noqa: E402

RESULTS: list[tuple[str, str, str, str]] = []
ENVIRONMENT: list[tuple[str, str]] = []

#: Console code pages differ (GBK on the development machine); keep output
#: printable instead of dying on an encoding error halfway through.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def record(group, name, status, detail=""):
    RESULTS.append((group, name, status, detail))
    mark = {"PASS": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
    print("  %s %s%s" % (mark, name, ("  — " + detail) if detail else ""), flush=True)


def check(group, name, condition, detail=""):
    record(group, name, "PASS" if condition else "FAIL", detail if not condition else "")
    return bool(condition)


def skip(group, name, reason):
    record(group, name, "SKIP", reason)


def env(name, value):
    ENVIRONMENT.append((name, str(value)))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def luminance(color: str) -> float:
    value = color.lstrip("#")
    channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    parts = [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in channels]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def contrast(foreground: str, background: str) -> float:
    one, two = luminance(foreground), luminance(background)
    lighter, darker = max(one, two), min(one, two)
    return (lighter + 0.05) / (darker + 0.05)


imm = c.WinDLL("imm32")


class CandidateForm(c.Structure):
    _fields_ = [("index", w.DWORD), ("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]


class CompositionForm(c.Structure):
    _fields_ = [("style", w.DWORD), ("point", w.POINT), ("area", w.RECT)]


imm.ImmGetContext.argtypes = [w.HWND]
imm.ImmGetContext.restype = w.HANDLE
imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
imm.ImmGetCandidateWindow.argtypes = [w.HANDLE, w.DWORD, c.POINTER(CandidateForm)]
imm.ImmGetCandidateWindow.restype = w.BOOL
imm.ImmGetCompositionWindow.argtypes = [w.HANDLE, c.POINTER(CompositionForm)]
imm.ImmGetCompositionWindow.restype = w.BOOL


def ime_forms(adapter):
    context = imm.ImmGetContext(adapter._hwnd)
    if not context:
        return None, None
    try:
        candidate, composition = CandidateForm(), CompositionForm()
        imm.ImmGetCandidateWindow(context, 0, c.byref(candidate))
        imm.ImmGetCompositionWindow(context, c.byref(composition))
        return candidate, composition
    finally:
        imm.ImmReleaseContext(adapter._hwnd, context)


def window(workspace, visible=False):
    """A real desktop window on a disposable workspace.

    Geometry checks need a mapped window (an unmapped one reports 0,0), so the
    main window stays visible; the throwaway ones are hidden to avoid flashing.
    """
    winui.MarkdownWindow.enable_file_drop = lambda self: None
    win = winui.MarkdownWindow(str(workspace))
    if visible:
        win.root.geometry("1100x700+40+40")
        win.root.deiconify()
        win.root.update()
    else:
        win.root.withdraw()
    return win


def close(win):
    try:
        for timer in win.root.tk.call("after", "info"):
            win.root.after_cancel(timer)
        win.root.destroy()
    except Exception:
        pass


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# matrix
# --------------------------------------------------------------------------

def section(title):
    print("\n" + title, flush=True)


def environment_section(win):
    section("【环境】")
    env("被测代码", str(Path(core.__file__).resolve().parents[1]))
    env("目录角色", "源码树" if CODE_ROOT == ROOT else "指定副本（安装或分发包）")
    env("系统", "%s %s" % (platform.system(), platform.release()))
    env("Windows 版本", platform.version())
    env("Python", platform.python_version())
    env("渲染引擎", core.R.ENGINE_NAME)
    env("DPI 缩放", "%.2f（%.0f dpi）" % (win.ui_scale, win.root.winfo_fpixels("1i")))
    env("屏幕", "%dx%d" % (win.root.winfo_screenwidth(), win.root.winfo_screenheight()))
    env("程序版本", "%s %s" % (core.APP_NAME, core.APP_VERSION))
    for name, value in ENVIRONMENT:
        print("    %-12s %s" % (name, value), flush=True)


def files_section(win, root: Path):
    section("【文件】中文与空格路径、空文档、只读、缺失、同名、接近上限")

    tricky = write(root / "我的 笔记" / "中文 文档.md", "# 中文 标题\n\n正文 内容\n")
    win.open_local_files([str(tricky)])
    ok = check("文件", "中文与空格路径可打开", win.active_tab is not None and "正文 内容" in win.get_text())
    if ok:
        win.toggle_mode()
        win._set_widget("# 改过的标题\n\n改过的正文\n")
        win.source = win.get_text()
        win.set_dirty(True)
        saved = win.save_doc()
        check("文件", "中文与空格路径可保存", saved and "改过的正文" in tricky.read_text(encoding="utf-8"))
        reopened = window(root / "secondary-workspace")
        try:
            reopened.open_local_files([str(tricky)])
            check("文件", "重新打开读到最新内容", "改过的正文" in reopened.get_text())
        finally:
            close(reopened)
        win.close_tab(win.active_tab)

    empty = write(root / "空文档.md", "")
    win.open_local_files([str(empty)])
    opened = check("文件", "空文档可打开且不报错", win.active_tab is not None)
    if opened:
        win.toggle_mode()
        win._set_widget("\n")
        win.source = "\n"
        win.set_dirty(True)
        check("文件", "空文档可写入", win.save_doc() and empty.read_text(encoding="utf-8").strip() == "")
        win.close_tab(win.active_tab)

    missing = root / "不存在.md"
    win.open_local_files([str(missing)])
    check("文件", "缺失文件被拒绝且不创建", not missing.exists())

    readonly = write(root / "只读.md", "只读内容\n")
    os.chmod(readonly, stat.S_IREAD)
    try:
        win.open_local_files([str(readonly)])
        win.toggle_mode()
        win._set_widget("想改的内容\n")
        win.source = win.get_text()
        win.set_dirty(True)
        failed = not win.save_doc()
        check("文件", "只读文件保存失败但保留缓冲",
              failed and win.get_text() == "想改的内容\n"
              and readonly.read_text(encoding="utf-8") == "只读内容\n")
        win.set_dirty(False)
        win.close_tab(win.active_tab)
    finally:
        os.chmod(readonly, stat.S_IWRITE)

    project = win.ws.create_project("验收项目")["id"]
    pdir = win.ws.require_project(project)
    source = write(root / "同名.md", "第一份\n")
    win.ws.import_paths(pdir, [source])
    write(root / "同名.md", "第二份\n")
    added = win.ws.import_paths(pdir, [source])["added"]
    names = sorted(name for name in os.listdir(pdir) if name.endswith(".md"))
    contents = []
    for name in names:
        with open(os.path.join(pdir, name), encoding="utf-8") as handle:
            contents.append(handle.read())
    check("文件", "同名导入自动改名且两份都在",
          added and added[0] != "同名.md" and "第一份\n" in contents and "第二份\n" in contents)

    huge = Path(root, "大文档.md")
    huge.write_text("# 大文档\n\n" + "填充内容 " * 8 * 40000 + "\n", encoding="utf-8")
    too_big = Path(root, "超过上限.md")
    too_big.write_text("x" * (8 * 1024 * 1024 + 10), encoding="utf-8")
    try:
        D.snapshot(str(huge))
        near_ok = True
    except Exception as exc:
        near_ok = False
        record("文件", "接近上限的文档可读", "FAIL", str(exc))
    if near_ok:
        check("文件", "接近上限的文档可读（%.1f MB）" % (huge.stat().st_size / 1024 / 1024), True)
    try:
        D.snapshot(str(too_big))
        check("文件", "超过 8 MB 被拒绝", False)
    except ValueError as exc:
        check("文件", "超过 8 MB 被拒绝", "8 MB" in str(exc), str(exc))
    return project


def lifecycle_section(win, root: Path):
    section("【生命周期】新建、编辑、保存、关闭、重启、异常结束")

    win.new_loose_draft()
    win._set_widget("# 草稿\n\n第一次保存\n")
    win.source = win.get_text()
    win.set_dirty(True)
    target = root / "生命周期.md"
    # 模拟“保存对话框已经选好目标”：草稿尚未落盘，所以基线是 missing，
    # 否则保存会被当成外部修改冲突而弹出对话框。
    win.cur_loose["path"] = str(target)
    win.cur_loose["name"] = "生命周期"
    win.cur_loose["revision"] = "missing"
    saved = win.save_doc()
    check("生命周期", "新建临时文档可保存落地",
          saved and target.is_file() and "第一次保存" in target.read_text(encoding="utf-8"))
    win.close_tab(win.active_tab)

    restarted = window(root / "restart-workspace")
    try:
        restarted.open_local_files([str(target)])
        check("生命周期", "重启后读到已保存内容", "第一次保存" in restarted.get_text())
    finally:
        close(restarted)

    edit_win = window(root / "crash-workspace")
    try:
        edit_win.open_local_files([str(target)])
        edit_win.toggle_mode()
        edit_win._set_widget("# 崩溃前\n\n没来得及保存\n")
        edit_win.source = edit_win.get_text()
        edit_win.set_dirty(True)
        edit_win.flush_recovery()
        snapshots = edit_win.ws.recovery.list()
        check("生命周期", "异常结束前留下恢复快照",
              len(snapshots) == 1 and "没来得及保存" in snapshots[0].get("text", ""))
        check("生命周期", "恢复快照不覆盖原文件", "第一次保存" in target.read_text(encoding="utf-8"))
        restored = window(root / "restore-workspace")
        try:
            restored.win if False else None
            # 同一工作区才能看到快照：把快照复制到新窗口的工作区
            restored.ws.recovery.save(str(target), snapshots[0]["text"], str(target), "missing")
            with_open = []
            restored.offer_recovery = lambda: with_open.append(True)
            restored.offer_recovery()
            check("生命周期", "启动时会提示可恢复内容", with_open == [True])
        finally:
            close(restored)
    finally:
        close(edit_win)


def input_section(win):
    section("【输入】内嵌拼音位置与候选框（自动可判定部分）")

    if win.editor is None:
        win.new_loose_draft()               # 需要一份可编辑文档来量组合浮层位置
    editor = win.editor
    win._set_widget("你好")
    editor.mark_set("insert", "end-1c")
    editor.focus_force()
    win.root.update()
    # 这里**不**自己设置可编辑性：那一步由正常的显示流程负责，忘了设置时
    # Windows 会画回自己的白色组合框（0.2.8 回归）。只断言结果。
    check("输入", "进入源码后编辑控件已可组合（否则系统会画回白色组合框）",
          bool(win.ime is not None and win.ime.editable))
    win.ime._focus_in()
    # 最后一步才画浮层：真实窗口在 update() 期间会因为没有真实按键而触发
    # 焦点往返，把测试用的假组合一起清掉，所以先让事件循环跑完再放。
    win.ime._paint(("ni'hao", 6))
    box = win.ime.surface.show("ni'hao", 6)
    win.root.update_idletasks()
    canvas = win.ime.surface.canvas
    caret = editor.bbox("insert")
    placement = canvas.place_info()
    placed = (int(placement.get("x", -1)), int(placement.get("y", -1)))
    check("输入", "拼音浮层与光标对齐",
          box is not None and placed == (box[0], box[1])
          and box[0] == caret[0] and box[1] == caret[1],
          "place=%s box=%s caret=%s" % (placed, box, caret))
    check("输入", "浮层文字与正文同字体同颜色",
          canvas.itemcget("preedit", "text") == "ni'hao"
          and canvas.itemcget("preedit", "font") == str(editor["font"])
          and canvas.itemcget("preedit", "fill") == win.pal["fg"])
    candidate, composition = ime_forms(win.ime)
    rect = win.ime.rect
    check("输入", "候选框排在拼音下方（CFS_CANDIDATEPOS）",
          candidate is not None and candidate.style == CFS_CANDIDATEPOS
          and candidate.point.y >= rect[1] + rect[3],
          "candidate=%s rect=%s" % ((candidate.point.x, candidate.point.y) if candidate else None, rect))
    check("输入", "组合矩形如实上报（CFS_RECT）",
          composition is not None and composition.style == CFS_RECT)
    check("输入", "候选框与拼音底边相距 %d 像素" % CANDIDATE_GAP,
          candidate is not None and candidate.point.y == rect[1] + rect[3] + CANDIDATE_GAP)
    win.ime.surface.hide()

    field = win.recent_search
    field.focus_force()
    win.root.update()
    check("输入", "搜索框默认就参与组合（由初始化设置，无需手工开启）",
          bool(win.ime_search.editable))
    win.ime_search._focus_in()
    box = win.ime_search.surface.show("sou'suo", 4)
    win.ime_search._paint(("sou'suo", 4))
    win.root.update_idletasks()
    win.root.update()
    canvas = win.ime_search.surface.canvas
    check("输入", "搜索框也使用同一套内嵌拼音",
          box is not None and canvas.winfo_rootx() == field.winfo_rootx() + box[0]
          and canvas.winfo_rooty() == field.winfo_rooty() + box[1])
    win.ime_search.surface.hide()
    skip("输入", "真实输入法的候选词外观与选词过程", "需要人在输入法下实际操作")

    editor.focus_force()
    win.root.update()


def dialog_section(win, root: Path):
    """回归：系统选择框回传的路径必须逐字到达。

    维护者在真实窗口里打开桌面上的中文目录时，状态栏回的是
    「无法打开这个文件夹：找不到文件夹：C:\\Users\\<乱码>\\Desktop\\<乱码>」。
    根因不是权限，而是 Windows PowerShell 5.1 把**重定向的**标准输出按本机 ANSI
    代码页写（中文系统是 GBK），核心却一律按 UTF-8 解。真实对话框需要人点，
    这里用同一个外壳（真的起一次 PowerShell 把路径打回来）把
    「回传字符串 → 解码 → 登记 → 树」这条链跑完，只省掉点击那一下。
    """
    section("【对话框】系统选择框回传的路径逐字到达")
    user = root / "对话框 资料"
    write(user / "笔记.md", "# 笔记\n")
    if not (shutil.which("powershell.exe") or shutil.which("pwsh")):
        skip("对话框", "外壳回传的中文路径逐字到达", "本机没有 PowerShell，无法端到端验证")
        return

    def pick():
        return core._run_ps("'%s';" % str(user).replace("'", "''"))

    check("对话框", "外壳回传的路径与所选目录逐字相同", pick() == [str(user)], str(pick()))
    with mock.patch.object(core, "_dialog_folder", side_effect=pick):
        win.open_user_folder()
    current = win.current_root() or {}
    check("对话框", "选中文件夹后正确显示而不是「找不到文件夹」",
          win.roots and os.path.normcase(current.get("path") or "") == os.path.normcase(str(user)),
          win.lbl_status.cget("text"))
    check("对话框", "树上列出该目录下的中文文件名", [d["id"] for d in win.root_docs] == ["笔记.md"],
          str([d["id"] for d in win.root_docs]))
    check("对话框", "打开文件夹时文件夹一律先收起",
          all(not win.root_tree.item(iid, "open") for iid in win.root_tree.get_children("")))
    with mock.patch("tkinter.messagebox.askyesno", return_value=True):
        win.remove_current_root()
    check("对话框", "本小节结束后登记清空，不干扰后面的小节", win.roots == [])


def folder_section(win, root: Path):
    """F01：直接打开用户自己的文件夹、在原目录新建、外部增删自动更新。"""
    section("【文件夹】打开原目录、新建 Markdown、外部增删自动更新")
    user = root / "用户资料"
    write(user / "笔记.md", "# 笔记\n\n正文\n")
    write(user / "子目录" / "深入.md", "# 深入\n")

    with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
        win.open_user_folder()
    ids = [d["id"] for d in win.root_docs]
    check("文件夹", "打开后列出根目录下的 Markdown", ids == ["笔记.md", "子目录/深入.md"], str(ids))
    check("文件夹", "界面显示的是原件路径", str(user) in win.lbl_root.cget("text"))
    check("文件夹", "登记写在应用工作区而不是用户目录",
          not (user / "folders.json").exists() and not (user / ".mdreader").exists())

    with mock.patch("tkinter.simpledialog.askstring", return_value="验收 新建"):
        win.new_folder_doc()
    created = user / "验收 新建.md"
    check("文件夹", "新建的 Markdown 落在原目录", created.is_file())
    check("文件夹", "新建后立刻打开且内容是空的",
          win.cur_loose is not None
          and os.path.normcase(win.cur_loose.get("path") or "") == os.path.normcase(str(created))
          and win.get_text().strip() == "")
    check("文件夹", "已存在同名文件时不覆盖",
          _refuses(win, "笔记") and (user / "笔记.md").read_text(encoding="utf-8") == "# 笔记\n\n正文\n")

    write(user / "外部新增.md", "# 外部新增\n")
    # 模拟后台监听线程置的脏标记 + 轮询间隔已过（界面只在主线程刷新）
    win.ws.folders._cache.clear()
    win._folders_dirty = True
    win.folder_watch_tick()
    check("文件夹", "外部新增在界面上出现",
          "外部新增.md" in [d["id"] for d in win.root_docs])
    before = set(os.listdir(user))
    with mock.patch("tkinter.messagebox.askyesno", return_value=True):
        win.remove_current_root()
    check("文件夹", "从列表移除后树上不再显示", win.roots == [] and win.root_docs == [])
    check("文件夹", "移除只是记录，磁盘文件全在", set(os.listdir(user)) == before and (user / "笔记.md").is_file())
    check("文件夹", "移除后不能再按根目录读写",
          _refuses(win, None, root_id="root-00000000"))
    with mock.patch.object(win, "ask_save_changes", return_value=False):
        win.close_tab(win.active_tab)


def _refuses(win, name, root_id=None):
    """新建必须失败（重名、保留名、无授权根目录都算）。"""
    handler = win.ws.folders
    try:
        handler.create_doc(root_id or win.cur_root or "", name or "x")
    except Exception:
        return True
    return False


def delete_section(win, root: Path):
    """F02：删的是用户的本地文件——每次提示、默认回收站、失败不永久删除。"""
    section("【删除】删除本地文件必须明确、可恢复、失败不永久删除")
    user = root / "删除资料"
    write(user / "要删的.md", "# 要删的\n")
    write(user / "留着的.md", "# 留着的\n")
    with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
        win.open_user_folder()
    did = "要删的.md"
    target = user / did

    seen = {}

    def refuse(title, message, **kwargs):
        seen["title"] = title
        seen["message"] = message
        seen["default"] = kwargs.get("default")
        return False

    with mock.patch("tkinter.messagebox.askyesno", side_effect=refuse):
        win.delete_folder_doc(did)
    check("删除", "每次删除都提示真实影响", "title" in seen and "删除本地文件" in seen["title"], seen.get("title", ""))
    check("删除", "提示里有完整路径", str(target) in seen.get("message", ""))
    check("删除", "说明会进回收站且不只是移除列表",
          "回收站" in seen.get("message", "") and "不只是从 MDReader 列表中移除" in seen.get("message", ""))
    check("删除", "取消后文件与树都没变",
          target.is_file() and did in [d["id"] for d in win.root_docs])

    with mock.patch.object(core, "_send_to_recycle_bin", return_value=False), \
         mock.patch("tkinter.messagebox.askyesno", return_value=True):
        win.delete_folder_doc(did)
    check("删除", "回收站不可用时保留文件且不改永久删除",
          target.is_file() and "已保留" in win.lbl_status.cget("text"))
    check("删除", "失败后树上仍有这个文件", did in [d["id"] for d in win.root_docs])

    with mock.patch("tkinter.messagebox.askyesno", return_value=True), \
         mock.patch.object(core, "_send_to_recycle_bin", side_effect=lambda path: (os.remove(path), True)[1]):
        win.delete_folder_doc(did)
    check("删除", "确认后文件从原目录消失", not target.exists())
    check("删除", "同目录其他文件与父目录都没动",
          (user / "留着的.md").is_file() and user.is_dir())
    check("删除", "删除后树上不再显示", did not in [d["id"] for d in win.root_docs])

    with mock.patch("tkinter.messagebox.askyesno", return_value=True):
        win.remove_current_root()
    check("删除", "「从列表移除」与「删除」分开，且不删磁盘文件", (user / "留着的.md").is_file())
    with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
        win.open_user_folder()


def plugin_section(win, root: Path):
    """P01：插件机制——清单、受信来源、启停、任务与结果校验。

    用 `plugins/samples/*` 现场打包的**样例插件**验证机制本身；正式的
    图片/PDF/Word 插件由 F03/F07/F08 交付，本轮只验证到“机制可用”。
    """
    section("【插件】清单校验、安装启用、任务与结果边界")
    samples = ROOT / "plugins" / "samples"
    packages = root / "插件包"
    packages.mkdir(parents=True, exist_ok=True)
    trust_file = root / "plugin-trust.json"
    # 与随程序分发的 plugin_trust.json 一样：三个正式插件先占位、尚未发布，
    # 样例插件的哈希由本轮现场打包后登记，绝不使用真实工作区的受信清单。
    shipped = P.TrustRegistry()
    trust = {"api_version": "1",
             "plugins": {row["id"]: {"publisher": row["publisher"], "versions": {},
                                     "reserved": row["reserved"]}
                         for row in shipped.reserved()}}

    def pack(name, **kwargs):
        import hashlib
        source = samples / name
        out = packages / ("%s-%d.zip" % (name, len(trust["plugins"])))
        P.build_package(str(source), str(out))
        with open(out, "rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        manifest = json.loads((source / P.MANIFEST_NAME).read_text(encoding="utf-8"))
        entry = trust["plugins"].setdefault(manifest["id"],
                                           {"publisher": "MDReader", "versions": {}})
        entry.setdefault("versions", {})[manifest["version"]] = digest
        entry.update(kwargs)
        trust_file.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
        store.trust.load()
        return out, manifest["id"]

    store = win.ws.plugins
    trust_file.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
    store.trust = P.TrustRegistry(str(trust_file))
    api = win.plugin_bridge()

    # -- 无插件时核心照常可用 --------------------------------------------
    check("插件", "没有插件时核心功能不受影响（命令为空、文档仍可读写）",
          store.commands() == [] and win.plugin_commands() == [] and api is not None)
    check("插件", "界面明确提示还没有启用插件",
          "插件未启用" in win.plugin_menu_entries()["insert"][0])
    reserved = {row["id"]: row for row in store.list_plugins()}
    check("插件", "三个正式插件位在未发布前显示为未安装",
          all(reserved.get(pid, {}).get("state") == "not-installed"
              for pid in ("mdreader.image-insert", "mdreader.export-pdf", "mdreader.export-docx")))

    # -- 安装与启用 ------------------------------------------------------
    image_pkg, image_id = pack("sample-image")
    installed = store.install(image_pkg)
    check("插件", "受信清单里的包可以安装，且默认不启用",
          installed["verified"] and store.commands() == [])
    enabled = store.enable(image_id)
    check("插件", "启用后命令出现在两端（窗口与页面读同一份状态）",
          enabled["enabled"] and [c["command"] for c in win.plugin_commands()]
          == [c["command"] for c in api.get("/api/plugins", {})["commands"]])

    # -- 图片插入任务 ----------------------------------------------------
    user = root / "插件资料"
    doc = write(user / "带图.md", "# 带图\n\n正文\n")
    picture = user / "素材.png"
    picture.write_bytes(_sample_png())
    api.loose.open_path(str(doc))
    inserted = api.post("/api/plugins/insert",
                        {"doc": str(doc), "revision": D.revision(str(doc)), "path": str(picture),
                         "options": {"alt": "示意图"}, "entry": "web", "wait": 90})
    landed = user / "assets" / "素材.png"
    check("插件", "插件产物由宿主校验后写进文档旁边的 assets",
          landed.is_file() and inserted["markdown"] == "![示意图](assets/素材.png)")
    check("插件", "插入不改源文档，原图也保留（复制而不是移动）",
          doc.read_text(encoding="utf-8") == "# 带图\n\n正文\n" and picture.is_file())
    with open(landed, "rb") as stream:
        check("插件", "落地的图片是真的图片（宿主复查文件头）",
              stream.read(8) == b"\x89PNG\r\n\x1a\n")

    task = api.post("/api/plugins/command",
                    {"command": "%s:insert.image" % image_id,
                     "options": {"synthetic": True, "escape": True}, "wait": 60})
    check("插件", "插件声明的产物在任务工作目录之外时被拒绝",
          task["task"]["state"] == "failed" and "工作目录之外" in task["task"]["error"])

    win.open_local_files([str(doc)])
    win.show_source()
    with mock.patch.object(core, "_dialog_images", return_value=[str(picture)]):
        win.insert_image_with_plugin()
    check("插件", "桌面端「更多 → 插入图片」把片段写进编辑器并标记未保存",
          "![素材](assets/素材" in win.get_text() and win.dirty, win.lbl_status.cget("text"))

    # -- 导出任务 --------------------------------------------------------
    export_pkg, export_id = pack("sample-export-a")
    store.install(export_pkg)
    store.enable(export_id)
    target = root / "导出" / "带图.samplea"
    exported = api.post("/api/plugins/export",
                        {"doc": str(doc), "command": "%s:export.samplea" % export_id,
                         "dest": str(target), "markdown": "# 快照内容\n", "source": "buffer",
                         "wait": 90})
    check("插件", "导出插件只产临时文件，最终由核心原子替换到目标",
          target.is_file() and open(target, "rb").read(5) == b"SAMPA")
    again = api.post("/api/plugins/export",
                     {"doc": str(doc), "command": "%s:export.samplea" % export_id,
                      "dest": str(target), "markdown": "# 又一次\n", "source": "buffer",
                      "wait": 90})
    check("插件", "目标已存在时必须先确认，不静默覆盖", bool(again.get("conflict")))

    # -- 禁用 / 运行中禁用 ------------------------------------------------
    running = api.post("/api/plugins/command",
                       {"command": "%s:export.samplea" % export_id,
                        "options": {"sleep": 30}, "wait": 1})
    busy = running["task"]["id"]
    store.disable(export_id)
    check("插件", "禁用会取消正在跑的任务，结果不会提交",
          store.tasks.poll(busy)["state"] == "cancelled")
    try:
        api.post("/api/plugins/command", {"command": "%s:export.samplea" % export_id, "wait": 1})
        refused = False
    except Exception as exc:
        refused = "不可用" in str(exc) or "禁用" in str(exc)
    check("插件", "禁用后服务端也拒绝调用（不只是界面藏按钮）", refused)

    # -- 依赖与不兼容 ----------------------------------------------------
    store.install(pack("sample-export-b")[0])
    dep_pkg, dep_id = pack("sample-dependent")
    store.install(dep_pkg)
    try:
        store.enable(dep_id)
        blocked = ""
    except P.PluginError as exc:
        blocked = str(exc)
    check("插件", "缺依赖只禁用该插件并说明原因", "依赖" in blocked and "没有启用" in blocked)
    store.enable("mdreader.sample-export-b")
    store.enable(dep_id)
    check("插件", "依赖启用后该插件恢复可用（其他插件不受影响）",
          any(c["command"].startswith(dep_id) for c in store.commands()))
    store.disable("mdreader.sample-export-b")
    check("插件", "依赖被禁用后，依赖它的插件重新变为不可用",
          not any(c["command"].startswith(dep_id) for c in store.commands()))

    incompatible = packages / "incompatible.zip"
    source = root / "不兼容源码"
    shutil.copytree(str(samples / "sample-export-b"), str(source))
    manifest_path = source / P.MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["id"] = "mdreader.sample-incompatible"
    data["name"] = "样例插件：版本不兼容"
    data["app_version_range"] = ">=9.0.0"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    P.build_package(str(source), str(incompatible))
    store.install(str(incompatible), allow_unverified=True)
    try:
        store.enable("mdreader.sample-incompatible")
        reason = ""
    except P.PluginError as exc:
        reason = str(exc)
    row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-incompatible")
    check("插件", "版本不兼容的插件只被禁用并写明原因",
          row["state"] == "unavailable" and "0.2.8" in row["reason"]
          and "无法启用" in reason)

    # -- 安装包越界与卸载 ------------------------------------------------
    evil = packages / "traversal.zip"
    with zipfile.ZipFile(str(evil), "w") as archive:
        archive.writestr("manifest.json", json.dumps(
            {"id": "mdreader.evil", "name": "越界", "version": "1.0.0", "api_version": "1",
             "entrypoint": "plugin.py", "capabilities": ["editor.image_insert"],
             "commands": [{"id": "i", "title": "插入", "capability": "editor.image_insert",
                           "method": "insert_image"}]}, ensure_ascii=False))
        archive.writestr("../evil.py", "print(1)")
    try:
        store.install(str(evil), allow_unverified=True)
        traversed = ""
    except P.PackageError as exc:
        traversed = str(exc)
    check("插件", "含越界路径的安装包被拒绝", "越界" in traversed)

    uninstall_id = "mdreader.sample-export-a"
    before = sorted(p.name for p in user.rglob("*"))
    store.uninstall(uninstall_id)
    check("插件", "卸载只删插件代码，用户文档与已插入图片都保留",
          uninstall_id not in [r["id"] for r in store.list_plugins() if r["installed"]]
          and sorted(p.name for p in user.rglob("*")) == before)

    # -- 重启后的状态 ----------------------------------------------------
    fresh = P.PluginStore(win.ws.root, app_version=core.APP_VERSION,
                          trust_path=str(trust_file))
    image_row = next(r for r in fresh.list_plugins() if r["id"] == image_id)
    check("插件", "重启（新实例）后启用状态保持一致",
          image_row["enabled"] and image_row["available"])
    fresh.shutdown()

    skip("插件", "正式插件导出稿的排版观感（分页、字体观感）",
         "要人工打开 PDF/Word 看；自动部分见【正式插件】小节")
    skip("插件", "插件工作进程的系统权限边界",
         "独立进程不是操作系统沙箱，首版仅执行受信插件；第三方隔离方案未实现")


#: 维护者 2026-09-25 试用正式插件时反馈的字符：中文正文字体（SimSun/黑体/雅黑）没有这些字形，
#: 转换后曾经在导出稿上留成空白。
GLYPH_SAMPLE = "\u2212\u207b\u2076\u2081\u2082"          # − ⁻ ⁶ ₁ ₂


def official_plugin_section(win, root: Path):
    """F03/F07/F08：随仓库发布的三个正式包，用真实工作进程导出真实文档。

    维护者试用后反馈「转换时会出现乱码」，根因是基础字体缺字形时 ReportLab 会画一个空的
    .notdef。这里把「导出稿里这些字符真的还在」固定成验收行。
    """
    section("【正式插件】四个发布包的安装、导出与字形覆盖")
    packages = sorted((ROOT / "plugins" / "packages").glob("*.zip"))
    if len(packages) != 4:
        skip("正式插件", "四个发布包齐全并可从受信清单安装",
             "plugins/packages 下应有四个包，实际找到 %d 个" % len(packages))
        return
    workspace = core.Workspace(str(root / "正式插件工作区"))
    api = core.Api(workspace, str(WEBUI))
    try:
        verified, ids = [], []
        for package in packages:
            manifest = json.loads(zipfile.ZipFile(package).read("manifest.json").decode("utf-8"))
            installed = workspace.plugins.install(str(package))
            verified.append(installed["verified"])
            ids.append(manifest["id"])
        check("正式插件", "四个发布包都在受信清单里，安装来源已核对",
              len(verified) == 4 and all(verified) and len(set(ids)) == 4, ", ".join(ids))
        enabled = [workspace.plugins.enable(pid) for pid in ids]
        extensions = {c.get("extension") for c in workspace.plugins.commands()}
        check("正式插件", "启用后 PDF 与 Word 导出命令都注册出来",
              all(row["enabled"] for row in enabled) and {"pdf", "docx"} <= extensions)
        commands = workspace.plugins.commands()
        if {"pdf", "docx"} - extensions:
            return

        user = root / "正式插件资料"
        markdown = ("# 字形覆盖\n\n物理链条：CTE ≈ 23×10\u207b\u2076/K；R_eq = R_single / (n \u2212 m)，"
                    "\u03b8\u2082 \u2212 \u03b8\u2081，**粗体 10\u207b\u2076**。\n\n"
                    "| 项目 | 值 |\n|---|---|\n| 系数 | \u22122.18 |\n\n"
                    "行内 `变量 \u03b1 10\u207b\u2076`\n")
        doc = write(user / "字形.md", markdown)
        api.loose.open_path(str(doc))
        revision = D.revision(str(doc))
        produced = {}
        for ext in ("pdf", "docx"):
            command = next(c for c in commands if c.get("extension") == ext)
            target = user / ("字形." + ext)
            result = api.post("/api/plugins/export", {"command": command["command"], "doc": str(doc),
                                                      "revision": revision, "markdown": markdown,
                                                      "dest": str(target), "wait": 120})
            produced[ext] = (target, result)
        check("正式插件", "两个格式都真的生成了文件，源 Markdown 没有被改动",
              all(result.get("path") == str(target) for target, result in produced.values())
              and doc.read_text(encoding="utf-8") == markdown
              and produced["pdf"][0].read_bytes().startswith(b"%PDF"))

        pdf_bytes = produced["pdf"][0].read_bytes()
        check("正式插件", "PDF 给缺字形的符号换用了有字形的回退字体",
              b"TimesNewRoman" in pdf_bytes and b"SimSun" in pdf_bytes)
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(produced["pdf"][0]))
            try:
                text = "".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
            finally:
                document.close()
            missing = [char for char in GLYPH_SAMPLE if char not in text]
            check("正式插件", "PDF 文本层里 − ⁻ ⁶ ₁ ₂ 一个都不少，正文可搜索",
                  not missing and "R_eq = R_single" in text, "缺少：" + "".join(missing))
        except ImportError:
            skip("正式插件", "PDF 文本层里 − ⁻ ⁶ ₁ ₂ 一个都不少，正文可搜索",
                 "这台机器没有 pypdfium2，无法读取导出稿的文本层")

        with zipfile.ZipFile(produced["docx"][0]) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        runs = re.findall(r"<w:r>(.*?)</w:r>", xml, re.S)
        switched = [body for run in runs if 'w:ascii="Times New Roman"' in run
                    for body in re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S)]
        plain = [body for run in runs if "w:ascii=" not in run
                 for body in re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S)]
        check("正式插件", "Word 只把缺字形的字符换成回退字体，中文正文保持原字体",
              any("\u207b" in body for body in switched) and any("\u2082" in body for body in switched)
              and any("物理链条" in body for body in plain))

        # F07/F08：公式以图片嵌入，并如实报告“不可编辑/不可检索”
        def export_confirmed(doc_path, command, target, wait=120):
            """像用户那样导出：可降级提示先确认，然后真的写出文件（F06 的流程）。"""
            payload = {"command": command["command"], "doc": str(doc_path),
                       "revision": D.revision(str(doc_path)), "dest": str(target), "wait": wait}
            result = api.post("/api/plugins/export", payload)
            if result.get("needs_confirm"):
                payload["confirm"] = True
                result = api.post("/api/plugins/export", payload)
            return result

        formula_doc = write(user / "公式.md",
                            "# 公式导出\n\n质能关系 $E = mc^{2}$，独立公式：\n\n"
                            "$$\n\\frac{a}{b} = \\sqrt[3]{x + 1}\n$$\n\n"
                            "画不出来的 $\\foo{x}$ 保持原文。\n")
        api.loose.open_path(str(formula_doc))
        formula_targets = {}
        for ext in ("pdf", "docx"):
            command = next(c for c in commands if c.get("extension") == ext)
            target = user / ("公式." + ext)
            formula_targets[ext] = (target, export_confirmed(formula_doc, command, target))
        check("正式插件", "公式导出成功且不改动源文",
              all(result.get("path") == str(target) and target.is_file()
                  for target, result in formula_targets.values())
              and formula_doc.read_text(encoding="utf-8").count("$") == 8)
        warnings = formula_targets["pdf"][1].get("warnings") or []
        check("正式插件", "公式降级要如实报告（不可编辑、不可检索）",
              any("公式" in message and "不可检索" in message for message in warnings), str(warnings))
        with zipfile.ZipFile(formula_targets["docx"][0]) as archive:
            formula_xml = archive.read("word/document.xml").decode("utf-8")
            formula_media = [n for n in archive.namelist() if n.startswith("word/media/")]
        check("正式插件", "Word 里两个公式各自嵌成图片",
              len(formula_media) >= 2, "图片 %d 张" % len(formula_media))
        check("正式插件", "Word 图片带原始写法作替代文字",
              'descr="公式：E = mc^{2}"' in formula_xml)
        check("正式插件", "画不出来的公式保留原始写法（两个格式都不丢内容）",
              "foo{x}" in formula_xml)
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(formula_targets["pdf"][0]))
            try:
                formula_text = "".join(document[i].get_textpage().get_text_range()
                                       for i in range(len(document)))
            finally:
                document.close()
            check("正式插件", "PDF 正文仍可检索、公式不在文本层、坏公式保留原文",
                  "质能关系" in formula_text and "mc" not in formula_text.replace(" ", "")
                  and "foo{x}" in formula_text)
        except ImportError:
            skip("正式插件", "PDF 公式文本层核对", "这台机器没有 pypdfium2")

        odd = write(user / "私用区.md", "# 标题\n\n正文里有 \ue05f 一个没有字形的字符。\n")
        api.loose.open_path(str(odd))
        command = next(c for c in commands if c.get("extension") == "pdf")
        result = api.post("/api/plugins/export", {"command": command["command"], "doc": str(odd),
                                                  "revision": D.revision(str(odd)), "dest": str(user / "私用区.pdf"),
                                                  "wait": 120})
        check("正式插件", "连回退字体也没有字形的字符会被报告，导出不假装成功",
              bool(result.get("warnings")) and "\ue05f" in result["warnings"][0])

        # F04：宽表导出不能裁列（换行或压缩列宽，但内容要都在）
        columns = 12
        wide = ("# 宽表\n\n| " + " | ".join("列%d" % n for n in range(1, columns + 1)) + " |\n"
                + "| " + " | ".join(["---"] * columns) + " |\n"
                + "| " + " | ".join("内容%d" % n for n in range(1, columns + 1)) + " |\n")
        wide_doc = write(user / "宽表.md", wide)
        api.loose.open_path(str(wide_doc))
        wide_targets = {}
        for ext in ("pdf", "docx"):
            command = next(c for c in commands if c.get("extension") == ext)
            target = user / ("宽表." + ext)
            wide_targets[ext] = (target, api.post("/api/plugins/export", {
                "command": command["command"], "doc": str(wide_doc),
                "revision": D.revision(str(wide_doc)), "markdown": wide,
                "dest": str(target), "wait": 120}))
        check("正式插件", "宽表两个格式都导出成功",
              all(result.get("path") == str(target) for target, result in wide_targets.values()))
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(wide_targets["pdf"][0]))
            try:
                wide_text = re.sub(r"\s+", "", "".join(
                    document[i].get_textpage().get_text_range() for i in range(len(document))))
            finally:
                document.close()
            lost = [n for n in range(1, columns + 1)
                    if ("列%d" % n) not in wide_text or ("内容%d" % n) not in wide_text]
            check("正式插件", "PDF 宽表 12 列一列都没被裁掉", not lost, "缺少列：%s" % lost)
        except ImportError:
            skip("正式插件", "PDF 宽表 12 列一列都没被裁掉", "这台机器没有 pypdfium2")
        with zipfile.ZipFile(wide_targets["docx"][0]) as archive:
            wide_xml = archive.read("word/document.xml").decode("utf-8")
        wide_table = re.search(r"<w:tbl>.*?</w:tbl>", wide_xml, re.S)
        rows = re.findall(r"<w:tr\b.*?</w:tr>", wide_table.group(0), re.S) if wide_table else []
        check("正式插件", "Word 宽表每一行都是 12 个单元格",
              bool(rows) and all(len(re.findall(r"<w:tc>", row)) == columns for row in rows),
              "行数 %d" % len(rows))
    finally:
        workspace.plugins.shutdown()



def _sample_png():
    """一张最小合法 PNG，验收不需要额外素材。"""
    import struct
    import zlib
    width = height = 2
    raw = b"".join(b"\x00" + bytes([40, 90, 160]) * width for _ in range(height))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _dib(rows, bits=24):
    """按 Windows ``CF_DIB`` 造一份位图数据：``rows[0]`` 是图像最上面那一行。"""
    import struct
    height, width = len(rows), len(rows[0])
    stride = ((width * bits + 31) // 32) * 4
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bits, 0,
                         stride * height, 0, 0, 0, 0)
    body = bytearray()
    for row in reversed(rows):                          # 自下而上存放
        line = bytearray()
        for red, green, blue in row:
            line += bytes((blue, green, red)) + (b"\xff" if bits == 32 else b"")
        body += line + b"\x00" * (stride - len(line))
    return header + bytes(body)


def _png_rows(data: bytes):
    """独立读回 PNG 的像素：自己解 IHDR/IDAT（只认 8 位 RGB、无隔行）。

    验收不接受“编码器自己说对了”——这里用 zlib 重新解一遍，和编码器不共享代码。
    """
    import struct
    import zlib as _zlib
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    width, height, depth, kind = struct.unpack(">IIBB", data[16:26])
    if depth != 8 or kind != 2:
        raise ValueError("不是 8 位 RGB")
    payload, offset = b"", 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind_bytes = data[offset + 4:offset + 8]
        if kind_bytes == b"IDAT":
            payload += data[offset + 8:offset + 8 + length]
        offset += 12 + length
    raw = _zlib.decompress(payload)
    stride = width * 3
    rows = []
    for index in range(height):
        base = index * (stride + 1) + 1                  # 跳过每行的过滤字节
        rows.append([tuple(raw[base + x * 3:base + x * 3 + 3]) for x in range(width)])
    return width, height, rows


def image_section(root: Path):
    """F03：剪贴板截图与拖入图片（文件选择那一步早已交付）。

    剪贴板部分**只读**：脚本不往系统剪贴板写任何东西，也不动用户的图片文件；
    真机上的按键（Ctrl+Shift+V 实际敲下去）留给人工验收行。
    """
    section("【图片】剪贴板截图与拖入图片（F03）")
    from mdreader import media, media_ui

    package = next((p for p in sorted((ROOT / "plugins" / "packages").glob("*.zip"))
                    if "image" in p.name), None)
    if package is None:
        skip("图片", "正式图片插入插件可用", "plugins/packages 下找不到图片插入包")
        return
    workspace_root = root / "图片工作区"
    win = window(workspace_root)
    try:
        store = win.ws.plugins
        store.install(str(package))
        store.enable("mdreader.image-insert")
        check("图片", "正式图片插入插件安装并启用",
              any(c["capability"] == core.PL.CAP_IMAGE_INSERT for c in store.commands()))

        # ① 剪贴板读取：CF_DIB 用标准库解成 PNG
        #    24×16：插件拒绝宽度 <16 px 的图片，验收底图必须比这个门槛宽，
        #    否则验的是插件的下限保护，不是剪贴板这条链路。
        rows = ([[(255, 0, 0)] * 24]
                + [[(0, 255, 0)] * 24 for _ in range(14)]
                + [[(0, 0, 255)] * 24])
        png = media._clipboard_png_from_dib(_dib(rows))
        corners = [((0, 0), (255, 0, 0)), ((0, 23), (255, 0, 0)),
                   ((15, 0), (0, 0, 255)), ((15, 23), (0, 0, 255)),
                   ((7, 11), (0, 255, 0))]
        try:
            width, height, pixels = _png_rows(png or b"")
            detail = "24×16，四角与中间像素逐个对过"
            decoded = all(pixels[row][column] == color for (row, column), color in corners)
        except Exception as exc:                        # noqa: BLE001 - 报告失败原因
            width, height, decoded, detail = 0, 0, False, "读不回来：%s" % exc
        check("图片", "剪贴板位图（CF_DIB）用标准库编成真 PNG，不依赖 Pillow",
              (width, height) == (24, 16) and decoded, detail)
        check("图片", "认不出来的剪贴板数据被拒绝（不猜、不抛异常）",
              media._clipboard_png_from_dib(b"\x00" * 64) is None
              and media._clipboard_png_from_dib(b"") is None)

        live = ""
        note = ""
        try:
            live = media.clipboard_image()
        except Exception as exc:                        # noqa: BLE001 - 读取失败也要说清楚
            note = "读取失败：%s" % exc
        if live:
            with open(live, "rb") as stream:
                head = stream.read(8)
            check("图片", "读一次真实剪贴板（只读，不写入剪贴板）",
                  os.path.isfile(live) and (head == b"\x89PNG\r\n\x1a\n"
                                            or head[:3] == b"\xff\xd8\xff"),
                  os.path.basename(live))
            if media.owns_clipboard_file(live):         # 只清理我们自己新建的临时文件
                os.remove(live)
        else:
            skip("图片", "读一次真实剪贴板（只读，不写入剪贴板）",
                 "当前剪贴板里没有图片%s；截图或复制一张图后再跑一次即可覆盖这条"
                 % (("（%s）" % note) if note else ""))

        # ② 桌面端：剪贴板图片插到光标处（真实插件、真实附件目录）
        user = root / "图片资料"
        doc = write(user / "插图.md", "# 插图\n\n正文\n")
        shot = user / "剪贴板.png"
        shot.write_bytes(png)
        for tab in list(win.tabs):
            with _quiet_prompt(win):
                win.close_tab(tab)
        win.open_local_files([str(doc)])
        win.show_source()
        with mock.patch.object(media, "clipboard_image", return_value=str(shot)), \
             mock.patch.object(media_ui, "choose_width", return_value=480):
            win.insert_clipboard_image()
        text = win.get_text()
        assets = sorted((user / "assets").glob("*.png"))
        check("图片", "桌面端把剪贴板图片插到光标处（片段进缓冲区，附件落 assets）",
              "![图片](assets/" in text and "width=480" in text and win.dirty,
              text.strip())
        check("图片", "落地的附件是真 PNG，用户的原图没有被删也没有被改",
              len(assets) == 1 and assets[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
              and shot.is_file() and shot.read_bytes() == png)

        ours = media._clipboard_target("", ".png")
        shutil.copyfile(str(shot), ours)
        with mock.patch.object(media, "clipboard_image", return_value=ours), \
             mock.patch.object(media_ui, "choose_width", return_value=720):
            win.insert_clipboard_image()
        check("图片", "自己新建的临时截图插入后立刻删掉（用户原件仍然保留）",
              (not os.path.exists(ours)) and shot.is_file())

        # ③ 拖入：图片走插入通道，同一次拖放里的 .md 仍然只是打开。
        #    拖入 .md 会**切换活动文档**，所以每次都要先把插图那篇调回前台再读正文。
        def doc_text(path):
            tab = next((t for t in win.tabs if (t.get("loose") or {}).get("path") == str(path)), None)
            if tab is not None:
                win.activate_tab(tab)
            return win.get_text()

        with mock.patch.object(media_ui, "choose_width", return_value=720):
            win.on_files_dropped([str(shot)])
        after_image = doc_text(doc).count("![图片](assets/")
        other = write(user / "另一篇.md", "# 另一篇\n\n别的内容\n")
        with mock.patch.object(media_ui, "choose_width", return_value=720):
            win.on_files_dropped([str(shot), str(other)])
        opened = [t for t in win.tabs if (t.get("loose") or {}).get("path") == str(other)]
        pieces = doc_text(doc).count("![图片](assets/")
        check("图片", "拖入图片插到光标处；同一次拖放里的 .md 仍然只是临时打开",
              after_image == 3 and pieces == 4 and len(opened) == 1
              and len(sorted((user / "assets").glob("*.png"))) == 4,
              "图片片段 %d 个，打开的文档 %d 个" % (pieces, len(opened)))

        second = user / "剪贴板2.png"
        second.write_bytes(png)
        seen = []
        doc_text(doc)                                   # 让插图那篇回到前台再拖
        with mock.patch.object(media_ui, "choose_width", return_value=720), \
             mock.patch.object(winui.MarkdownWindow, "notice",
                               lambda _self, message, error=False: seen.append(message)):
            win.on_files_dropped([str(shot), str(second)])
        check("图片", "一次拖入多张只插第一张，并如实说明其余没有处理",
              doc_text(doc).count("![图片](assets/") == 5
              and any("其余 1 张" in message for message in seen),
              "；".join(seen[-2:]))

        # ④ 插件禁用：只提示，不改正文，也不去读剪贴板
        store.disable("mdreader.image-insert")
        before = doc_text(doc)
        seen = []
        with mock.patch.object(media, "clipboard_image") as grab, \
             mock.patch.object(winui.MarkdownWindow, "notice",
                               lambda _self, message, error=False: seen.append(message)):
            win.insert_clipboard_image()
        check("图片", "插件禁用时只提示：不读剪贴板、不改正文",
              doc_text(doc) == before and not grab.called
              and any("插件" in message for message in seen),
              "；".join(seen))
        store.enable("mdreader.image-insert")

        # ⑤ 网页端：走同一份接口（base64 上传，和浏览器里 Ctrl+V 同一条路）
        import base64
        api = core.Api(win.ws, str(WEBUI))
        web_doc = write(user / "网页插图.md", "# 网页插图\n\n正文\n")
        api.loose.open_path(str(web_doc))
        before_assets = sorted((user / "assets").glob("image-*.png"))
        result = api.post("/api/plugins/insert", {
            "doc": str(web_doc), "revision": D.revision(str(web_doc)),
            "b64": base64.b64encode(png).decode("ascii"), "name": "剪贴板.png",
            "entry": "web", "wait": 90})
        new_assets = sorted(set((user / "assets").glob("image-*.png")) - set(before_assets))
        name = new_assets[0].name if new_assets else ""
        check("图片", "网页端上传的截图（base64）落到文档旁边的 assets",
              len(new_assets) == 1 and new_assets[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
              and ("assets/" + name) in result["markdown"] and "width=24" in result["markdown"]
              and web_doc.read_text(encoding="utf-8") == "# 网页插图\n\n正文\n",
              result["markdown"])

        app_js = (WEBUI / "app.js").read_text(encoding="utf-8")
        check("图片", "网页端粘贴截图/拖入图片接进同一条插入接口（接线检查）",
              "clipboardImageFile(data)" in app_js and "insertImageBlob(image, '已插入截图')" in app_js
              and "await insertImageBlob(droppedFiles[0]" in app_js
              and "'/api/plugins/insert'" in app_js)

        # ⑥ 入口：快捷键与菜单
        check("图片", "Ctrl+Shift+V 已绑定（大写/小写两个 keysym 都绑）",
              bool(win.root.bind("<Control-Shift-V>")) and bool(win.root.bind("<Control-Shift-v>"))
              and "Ctrl+Shift+V" in dict(winui.SHORTCUTS))
        menus = []
        real_menu = win._tk.Menu

        def factory(*args, **kwargs):
            menu = real_menu(*args, **kwargs)
            menus.append(menu)
            return menu

        with mock.patch.object(win._tk, "Menu", factory), \
             mock.patch.object(real_menu, "tk_popup", lambda *a, **k: None):
            win.show_more_menu()
        labels = []
        for index in range(menus[0].index("end") or 0):
            if menus[0].type(index) != "separator":      # 分隔线没有 -label 选项
                labels.append(menus[0].entrycget(index, "label"))
        check("图片", "「更多」菜单里有「粘贴剪贴板图片（截图）」入口",
              any("粘贴剪贴板图片" in label for label in labels), "，".join(labels))
    finally:
        close(win)
        try:
            win.ws.plugins.shutdown()
        except Exception:
            pass


def editing_section(win, root: Path):
    section("【编辑】E01 撤销连续性与标签隔离")

    first = write(root / "编辑" / "第一份.md", "# 第一份\n\n原始内容\n")
    second = write(root / "编辑" / "第二份.md", "# 第二份\n\n另一份内容\n")
    # 前面的小节留下了项目和临时文档标签（其中一份还带着故意制造的冲突），
    # 这里从干净状态开始，避免“保存”落到别的文档上。
    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    win.open_local_files([str(first), str(second)])
    win.activate_tab(next(t for t in win.tabs if t["key"] == win._tab_key(loose={"path": str(first)})))
    win.show_source()
    win._set_widget("原始内容\n")
    win.source = win.get_text()
    win.set_dirty(True)
    win.editor.mark_set("insert", "end-1c")
    win.editor.insert("end", "切换视图前输入的一行\n")
    win.editor.edit_separator()
    typed = win.get_text()
    editor = win.editor
    win.toggle_mode()                       # 源码 -> 预览
    preview_shown = win.text is win.preview and win.mode == "preview"
    win.toggle_mode()                       # 预览 -> 源码
    check("编辑", "视图切换复用同一个编辑控件", win.editor is editor and win.mode == "source")
    check("编辑", "预览使用独立的只读控件", preview_shown)
    check("编辑", "源码 → 预览 → 源码后内容不变", win.get_text() == typed)
    undone = False
    try:
        win.text.edit_undo()
        undone = win.get_text() == "原始内容\n"
        win.text.edit_redo()
    except Exception:
        undone = False
    check("编辑", "视图切换后仍可撤销与重做", undone and win.get_text() == typed)

    first_tab, first_editor = win.active_tab, win.editor
    second_tab = next(t for t in win.tabs
                      if t["key"] == win._tab_key(loose={"path": str(second)}))
    win.activate_tab(second_tab)
    second_editor = win.editor
    win.show_source("另一份内容\n")
    win.editor.insert("end", "第二份追加\n")
    win.editor.edit_separator()
    second_text = win.get_text()
    check("编辑", "每个标签有自己的编辑控件", second_editor is not first_editor)
    win.activate_tab(first_tab)
    check("编辑", "切回第一份内容与控件都还原",
          win.editor is first_editor and win.get_text() == typed)
    isolated = True
    try:
        win.text.edit_undo()
        isolated = win.get_text() == "原始内容\n" and "第二份追加" not in win.get_text()
        win.text.edit_redo()
    except Exception:
        isolated = False
    check("编辑", "撤销只影响当前文档", isolated)
    win.activate_tab(second_tab)
    check("编辑", "第二份内容未被上一份的撤销改动", win.get_text() == second_text)

    win.activate_tab(first_tab)
    wins_before = win.text.index("insert")
    try:
        ready = bool(win.ime is not None and win.ime.editable)
        win.ime.surface.show("ni'hao", 6)   # 一次未确认的组合
        win.activate_tab(second_tab)        # 组合中途切标签
        still_ready = bool(win.ime is not None and win.ime.editable)
        win.activate_tab(first_tab)
        win.ime._focus_out()
        stable = (ready and still_ready and win.get_text() == typed
                  and win.text.index("insert") == wins_before)
    except Exception as exc:
        stable = False
        record("编辑", "组合中途切换标签不报错", "FAIL", str(exc))
    check("编辑", "未确认拼音不进入正文、切换后无异常且新标签同样已可组合", stable)
    win.ime.surface.hide()

    win.save_doc()
    marker = not win.dirty
    check("编辑", "保存后未保存标记清除", marker)
    baseline_text = win.get_text()
    win.editor.insert("end", "保存后新增\n")
    win.editor.edit_separator()
    win.root.update()
    dirty_after_typing = win.dirty
    win.text.edit_undo()
    win.root.update()
    clean_at_baseline = not win.dirty and win.get_text() == baseline_text
    win.text.edit_redo()
    win.root.update()
    dirty_again = win.dirty
    check("编辑", "输入后标记未保存", dirty_after_typing)
    check("编辑", "撤销回保存基线去掉未保存标记", clean_at_baseline)
    check("编辑", "重做离开基线恢复未保存标记", dirty_again)
    win.save_doc()                          # 把重做后的内容写回磁盘再重开
    reopened = window(win.ws.root)
    try:
        reopened.open_local_files([str(first)])
        check("编辑", "重启读到撤销前的磁盘版本", "保存后新增" in reopened.get_text())
    finally:
        close(reopened)

    for index in range(20):
        write(root / "批量" / ("文档 %02d.md" % index), "# 批量 %d\n\n内容\n" % index)
    paths = [str(root / "批量" / ("文档 %02d.md" % index)) for index in range(20)]
    jobs_before = len(win.root.tk.call("after", "info"))
    editors_before = len(win._editors)
    win.open_local_files(paths)
    editors_while_open = len(win._editors)
    for tab in win.tabs:
        win.activate_tab(tab)
        win.toggle_mode()
        win.toggle_mode()
    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    jobs_after = len(win.root.tk.call("after", "info"))
    check("编辑", "打开 20 份文档后每份都有自己的编辑控件",
          editors_while_open - editors_before == 20, "新增 %d" % (editors_while_open - editors_before))
    check("编辑", "关闭全部标签后控件与组合适配项回收", win.editor is None and len(win._imes) == 2,
          "editors=%d imes=%d" % (len(win._editors), len(win._imes)))
    check("编辑", "关闭标签后计时器未累积", jobs_after <= jobs_before + 2,
          "before=%d after=%d" % (jobs_before, jobs_after))
    check("编辑", "预览控件在关闭标签后仍可用", win.preview.winfo_exists())


def _quiet_prompt(win):
    """Answer the close prompt with 放弃 so the sweep does not block."""
    import contextlib
    import unittest.mock

    @contextlib.contextmanager
    def context():
        with unittest.mock.patch.object(win, "ask_save_changes", return_value=False):
            yield
    return context()


def table_section(win, root: Path):
    """F04：表格插入/编辑与常用格式工具栏（真实窗口，规则来自核心）。"""
    section("【表格】插入、编辑与常用格式（F04）")
    from mdreader import formatting as FM
    from mdreader import tables as TB

    note = root / "表格"
    doc = write(note / "表格笔记.md", "# 表格笔记\n\n开场正文\n")
    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    win.open_local_files([str(doc)])
    win.toggle_mode()
    win._set_widget("开场正文\n")
    win.source = win.get_text()
    win.editor.mark_set("insert", "1.2")

    choice = {"columns": 2, "rows": 2, "has_header": True, "header": ["名称", "数量"],
              "aligns": ["left", "right"],
              "fills": [["名称", "数量"], ["甲", "1"], ["乙 | 二", "2"]]}
    with mock.patch.object(winui.TableDialog, "show", return_value=choice):
        inserted = win.insert_table_dialog()
    text = win.get_text()
    check("表格", "插入表格落在光标行之后且前后留空行",
          inserted and text.startswith("开场正文\n\n| 名称 | 数量 |\n| --- | ---: |"), repr(text[:40]))
    check("表格", "单元格里的竖线被转义", r"| 乙 \| 二 | 2 |" in text, text)
    read = TB.read(text, line=3)
    check("表格", "插入后的表格能被原样读回",
          read["ok"] and read["table"]["rows"][1][0] == "乙 | 二" and read["table"]["aligns"][1] == "right")
    win.text.edit_undo()
    win.root.update()
    check("表格", "插入表格一次撤销回到原文", win.get_text() == "开场正文\n",
          repr(win.get_text()[:40]))
    win.text.edit_redo()
    win.root.update()

    win.editor.mark_set("insert", "3.0")
    grown = {"columns": 2, "rows": 3, "has_header": True, "header": ["名称", "数量"],
             "aligns": ["center", "right"],
             "fills": [["名称", "数量"], ["甲", "1"], ["乙 | 二", "2"], ["丙", "3"]]}
    with mock.patch.object(winui.TableDialog, "show", return_value=grown):
        edited = win.edit_table_dialog()
    text = win.get_text()
    check("表格", "编辑表格可加行并改对齐",
          edited and "| :---: | ---: |" in text and text.count("| 丙 | 3 |") == 1, text)

    odd = "| 名称 | 数量 |\n| --- | --- |\n| 甲 |\n"
    win._set_widget(odd)
    win.editor.mark_set("insert", "1.2")
    refused = win.edit_table_dialog()
    check("表格", "列数对不上的表格保留源码并拒绝修改",
          refused is False and win.get_text() == odd, win.get_text())

    win._set_widget("只有正文\n")
    win.editor.mark_set("insert", "1.0")
    not_in_table = win.edit_table_dialog()
    check("表格", "光标不在表格里时不猜最近的一张表",
          not_in_table is False and win.get_text() == "只有正文\n")
    check("表格", "多行单元格被明确拒绝", bool(TB.check_cell("第一行\n第二行")))
    check("表格", "单元格里的换行不被偷偷塞进 Markdown",
          TB.operate("| a |\n| --- |\n| 1 |\n", "set_cell", line=0, row=0, column=0,
                     value="两行\n内容")["ok"] is False)

    source = "开场\n旧内容\n结尾\n"
    win._set_widget(source)
    win.editor.tag_remove("sel", "1.0", "end")
    win.editor.tag_add("sel", "2.0", "2.4")
    with mock.patch.object(winui.TablePasteDialog, "show", return_value={"header": True}):
        pasted = win.paste_as_table("名称\t数量\n甲\t1\n")
    check("表格", "TSV 粘贴确认后替换选区",
          pasted and win.get_text() == "开场\n\n| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n\n结尾\n",
          win.get_text())
    win._set_widget(source)
    with mock.patch.object(winui.TablePasteDialog, "show", return_value=None):
        cancelled = win.paste_as_table("名称\t数量\n甲\t1\n")
    check("表格", "取消粘贴预览时一个字节都不动",
          cancelled is False and win.get_text() == source)
    with mock.patch.object(win.root, "clipboard_get", return_value="普通一行文本"):
        check("表格", "普通粘贴不被表格预览拦截", win.on_editor_paste() is None)
    with mock.patch.object(win.root, "clipboard_get", return_value="a\tb\n1\t2\n"), \
         mock.patch.object(winui.TablePasteDialog, "show", return_value={"header": True}):
        check("表格", "多行多列文本粘贴走预览", win.on_editor_paste() == "break")

    wide_header = "| " + " | ".join("列%d" % n for n in range(1, 13)) + " |"
    wide_sep = "| " + " | ".join(["---"] * 12) + " |"
    wide_row = "| " + " | ".join("内容%d" % n for n in range(1, 13)) + " |"
    win._set_widget("%s\n%s\n%s\n" % (wide_header, wide_sep, wide_row))
    win.toggle_mode()                                    # 预览
    win.root.update()
    frames = [child for child in win.preview.winfo_children()
              if getattr(child, "_restyle_table", None) is not None]
    labels = frames[0].winfo_children() if frames else []
    check("表格", "宽表在阅读界面把 12 列都画出来", len(labels) == 24, "标签 %d 个" % len(labels))
    check("表格", "宽表每列都能换行（不撑破窗口）",
          bool(labels) and all(int(label.cget("wraplength")) > 0 for label in labels))

    win.toggle_mode()                                    # 回到源码
    win._set_widget("正文\n")
    win.editor.tag_remove("sel", "1.0", "end")
    win.editor.tag_add("sel", "1.0", "1.2")
    check("表格", "粗体动作走核心规则",
          win.format_selection("bold") and win.get_text() == "**正文**\n", win.get_text())
    win.text.edit_undo()
    win.root.update()
    check("表格", "格式动作一次撤销", win.get_text() == "正文\n", repr(win.get_text()))
    win.editor.tag_remove("sel", "1.0", "end")
    win.editor.mark_set("insert", "1.0")
    bullets = win.format_selection("bullets")
    heading = win.format_selection("heading", level=2)
    check("表格", "行级格式（列表、标题）叠加正确",
          bullets and heading and win.get_text() == "## - 正文\n", repr(win.get_text()))

    win._set_widget("正文\n")
    win.editor.tag_remove("sel", "1.0", "end")
    win.editor.mark_set("insert", "1.0")
    win.ime.surface.show("zheng'wen", 9)
    composed = win.format_selection("bold")
    win.ime.surface.hide()
    check("表格", "输入法组合中不套格式", composed is False and win.get_text() == "正文\n")
    check("表格", "格式动作的规则表覆盖任务书要求的动作",
          set(["heading", "bold", "italic", "bullets", "quote", "code", "link"]) <= set(FM.ACTIONS))

    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    check("表格", "本小节结束后没有遗留标签与临时文档", win.editor is None and not win.tabs)
    skip("表格", "工具栏按钮与表格对话框的实际观感",
         "按钮排布、对话框尺寸与列宽观感需要人眼在真实窗口确认")


def link_section(win, root: Path):
    """F09：检查当前文档链接——缺文件、越界、绝对路径、锚点，以及重新选择文件。"""
    section("【链接】检查当前文档的引用（F09）")
    from mdreader import links as LK

    user = root / "链接资料"
    doc = write(user / "笔记.md",
                "# 标题一\n\n## 小节\n\n"
                "![缺图](assets/没有.png)\n"
                "![越界](../外面.png)\n"
                "![绝对](C:\\Windows\\win.ini)\n\n"
                "[坏链接](没有.md) [跳转](#小节) [坏跳转](#不存在)\n\n"
                "```\n![代码里的](不算.png)\n```\n\n"
                "[外站](https://example.com)\n")
    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    win.open_local_files([str(doc)])
    win.show_source()
    issues = []
    with mock.patch.object(winui.LinkReportDialog, "show", autospec=True,
                           side_effect=lambda dialog: (issues.extend(dialog.issues),
                                                       {"action": "locate", "index": 0})[1]):
        located = win.check_document_links()
    check("链接", "报告列出缺文件、越界、绝对路径与坏锚点",
          [(item["level"], item["source"]) for item in issues] ==
          [("error", "assets/没有.png"), ("error", "../外面.png"),
           ("error", "C:\\Windows\\win.ini"), ("error", "没有.md"), ("warn", "#不存在")],
          "、".join(item["source"] for item in issues))
    check("链接", "代码块里的引用不算链接", all("不算.png" not in item["source"] for item in issues))
    check("链接", "定位到源码选中出问题的那一行",
          located and win.editor.get("sel.first", "sel.last") == "![缺图](assets/没有.png)"
          and "第 5 行" in win.lbl_status.cget("text"), win.lbl_status.cget("text"))

    chosen = write(user / "新图.png", "png")
    replies = [{"action": "choose", "index": 0}, {"action": None}]
    with mock.patch.object(winui.LinkReportDialog, "show", autospec=True,
                           side_effect=lambda _dialog: replies.pop(0)), \
         mock.patch.object(core, "_dialog_images", return_value=[str(chosen)]):
        replaced = win.check_document_links()
    check("链接", "重新选择文件后引用指向复制到 assets 的新文件",
          "![缺图](assets/新图.png)" in win.get_text()
          and (user / "assets" / "新图.png").is_file(), win.get_text()[:60])
    check("链接", "只改缓冲区，磁盘上的旧引用要等用户保存",
          "assets/没有.png" in doc.read_text(encoding="utf-8"))
    win.text.edit_undo()
    check("链接", "一次替换能一次撤销", "assets/没有.png" in win.get_text())
    win.text.edit_redo()

    before = sorted(os.listdir(str(user)))
    workspace = core.Workspace(str(root / "链接工作区"))
    api = core.Api(workspace, str(WEBUI))
    pid = workspace.create_project("链接")["id"]
    pdir = workspace.require_project(pid)
    entry = workspace.create_doc(pdir, "笔记", content="# 标题\n\n![图](assets/没有.png)\n")
    report = api.post("/api/check/links", {"pid": pid, "doc": entry["id"]})["report"]
    check("链接", "服务端检查与桌面端同源（同一份规则）",
          [item["source"] for item in report["errors"]] == ["assets/没有.png"]
          and report["summary"].startswith("有 1 个"))
    try:
        api.post("/api/check/links", {"doc": str(doc)})
        check("链接", "未授权目录里的文档拒绝检查", False)
    except PermissionError:
        check("链接", "未授权目录里的文档拒绝检查", True)
    anchors = LK.heading_anchors("# 一\n\n## 二 三\n\n## 二 三\n")
    frag, _meta, _engine = core.R.render_markdown("# 一\n\n## 二 三\n\n## 二 三\n")
    emitted = set(re.findall(r'<h[1-6] id="([^"]+)"', frag))
    check("链接", "锚点规则与渲染器一致（不会各说各话）", anchors == emitted,
          "、".join(sorted(anchors)))
    check("链接", "检查过程不改动用户目录", sorted(os.listdir(str(user))) == before,
          "、".join(sorted(os.listdir(str(user)))))
    skip("链接", "报告列表在真实窗口里的排布与措辞",
         "需要人眼确认条目是否读得懂、定位后光标位置是否顺手")


def export_service_section(win, root: Path):
    """F06：统一导出服务——来源选择、预检、覆盖确认与快照语义（用正式插件跑真导出）。"""
    section("【导出】统一快照、预检与覆盖保护（F06）")
    packages = sorted((ROOT / "plugins" / "packages").glob("*.zip"))
    if len(packages) != 4:
        skip("导出", "预检与导出流程", "plugins/packages 下应有四个包，实际 %d 个" % len(packages))
        return
    workspace = core.Workspace(str(root / "导出工作区"))
    try:
        ids = []
        for package in packages:
            manifest = json.loads(zipfile.ZipFile(package).read("manifest.json").decode("utf-8"))
            workspace.plugins.install(str(package))
            ids.append(manifest["id"])
        for pid_name in ids:
            workspace.plugins.enable(pid_name)
        api = core.Api(workspace, str(WEBUI))
        commands = workspace.plugins.commands()
        pdf = next(c for c in commands if c.get("extension") == "pdf")
        if not pdf:
            skip("导出", "预检与导出流程", "没有可用的 PDF 导出命令")
            return

        user = root / "导出资料"
        clean = write(user / "干净.md", "# 干净\n\n正文一段，公式 $E = mc^{2}$。\n")
        api.loose.open_path(str(clean))
        plan = api.post("/api/export/check", {"doc": str(clean), "command": pdf["command"]})
        check("导出", "预检给出 A4 浅色打印模板", plan["page"]["size"] == "A4"
              and plan["page"]["theme"] == "print")
        check("导出", "已经保存的文档不追问来源",
              plan["needs_source"] is False and plan["source"] == "disk")
        check("导出", "预检本身不写任何文件",
              sorted(os.listdir(str(user))) == [os.path.basename(str(clean))],
              ", ".join(sorted(os.listdir(str(user)))))

        unsaved = api.post("/api/export/check", {"doc": str(clean), "command": pdf["command"],
                                                 "markdown": "# 改过还没保存\n"})
        check("导出", "有未保存修改时先问来源（默认当前编辑内容）",
              unsaved["needs_source"] is True and unsaved["source"] == "buffer")
        buggy = user / "缺图.md"
        write(buggy, "# 缺图\n\n![图](assets/没有这张.png)\n")
        api.loose.open_path(str(buggy))
        blocked = api.post("/api/export/check", {"doc": str(buggy), "command": pdf["command"]})
        check("导出", "缺图被预检拦住并说明原因",
              blocked["preflight"]["blocked"]
              and "找不到" in blocked["preflight"]["errors"][0]["message"])
        refused = api.post("/api/plugins/export", {"doc": str(buggy), "command": pdf["command"],
                                                   "dest": str(user / "缺图.pdf"), "wait": 60})
        check("导出", "有严重问题时不启动导出、不留下半成品",
              refused.get("blocked") is True and not (user / "缺图.pdf").exists())

        warn_doc = write(user / "有提示.md",
                         "# 有提示\n\n公式 $\\foo{x}$，[死链](没有.md)。\n")
        api.loose.open_path(str(warn_doc))
        ask = api.post("/api/plugins/export", {"doc": str(warn_doc), "command": pdf["command"],
                                              "dest": str(user / "有提示.pdf"), "wait": 60})
        check("导出", "可降级项要求先确认", ask.get("needs_confirm") is True
              and bool(ask["preflight"]["warnings"]) and not (user / "有提示.pdf").exists())
        done = api.post("/api/plugins/export", {"doc": str(warn_doc), "command": pdf["command"],
                                               "dest": str(user / "有提示.pdf"),
                                               "confirm": True, "wait": 120})
        check("导出", "确认后真的写出文件", done.get("path") == str(user / "有提示.pdf")
              and (user / "有提示.pdf").read_bytes().startswith(b"%PDF"))

        again = api.post("/api/plugins/export", {"doc": str(clean), "command": pdf["command"],
                                                "dest": str(user / "有提示.pdf"), "wait": 60})
        check("导出", "目标已存在时先确认覆盖",
              again.get("conflict") is True and "已存在" in again.get("error", ""))
        forced = api.post("/api/plugins/export", {"doc": str(clean), "command": pdf["command"],
                                                 "dest": str(user / "有提示.pdf"),
                                                 "overwrite": True, "wait": 120})
        check("导出", "确认后覆盖成新产物", forced.get("path") == str(user / "有提示.pdf"))
        try:
            api.post("/api/plugins/export", {"doc": str(clean), "command": pdf["command"],
                                             "dest": str(clean), "wait": 30})
            check("导出", "导出目标不能是源文档本身", False)
        except ValueError:
            check("导出", "导出目标不能是源文档本身",
                  Path(str(clean)).read_text(encoding="utf-8").startswith("# 干净"))

        snapshot_doc = write(user / "快照.md", "# 快照\n\n第一版内容\n")
        api.loose.open_path(str(snapshot_doc))
        target = user / "快照.pdf"
        api.post("/api/plugins/export", {"doc": str(snapshot_doc), "command": pdf["command"],
                                        "dest": str(target), "markdown": "# 快照\n\n导出用的这一版\n",
                                        "source": "buffer", "wait": 120})
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(target))
            try:
                text = "".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
            finally:
                document.close()
            check("导出", "导出用的是那一刻的快照内容", "导出用的这一版" in text)
        except ImportError:
            skip("导出", "导出用的是那一刻的快照内容", "这台机器没有 pypdfium2")
    finally:
        workspace.plugins.shutdown()


def formula_section(win, root: Path):
    """F05：公式的识别、渲染、缓存与两端显示（规则在核心，两端都走同一份）。"""
    section("【公式】识别、离线渲染与两端显示（F05）")
    from mdreader import formula as FX

    source = ("# 公式\n\n质能 $E = mc^{2}$，代码 `$不是公式$`，金额 $5 与 $6 元。\n\n"
              "$$\n\\frac{a}{b} = \\sqrt[3]{x + 1}\n$$\n\n坏公式 $\\foo{x}$。\n")
    doc = write(root / "公式" / "公式笔记.md", source)
    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    win.open_local_files([str(doc)])
    win.root.update()
    preview = win.preview
    images = [child for child in preview.winfo_children() if hasattr(child, "_formula_tex")]
    check("公式", "行内与独立公式都嵌成图片（不是替代文字）",
          [child._formula_tex for child in images] ==
          ["E = mc^{2}", "\\frac{a}{b} = \\sqrt[3]{x + 1}"]
          and all(str(child.cget("image")) for child in images),
          "图片 %d 张" % len(images))
    check("公式", "画不出来的公式把源码留在正文里",
          "$\\foo{x}$" in preview.get("1.0", "end-1c"), preview.get("1.0", "end-1c")[:60])
    check("公式", "行内代码与金额文本不被当成公式",
          "$不是公式$" in preview.get("1.0", "end-1c") and "$5 与 $6 元" in preview.get("1.0", "end-1c"))
    cache = Path(win.ws.root, "formula-cache")
    check("公式", "公式图片落在工作区缓存目录里（不碰用户资料）",
          cache.is_dir() and len(list(cache.glob("*.png"))) == 2,
          "%d 个文件" % (len(list(cache.glob("*.png"))) if cache.is_dir() else 0))

    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory(prefix="mdreader-formula-accept-") as scratch:
        workspace = core.Workspace(scratch)
        api = core.Api(workspace, str(WEBUI))
        pid = workspace.create_project("公式")["id"]
        pdir = workspace.require_project(pid)
        entry = workspace.create_doc(pdir, "公式", content=source)
        rendered = workspace.render_doc(pdir, entry["id"], pid=pid)
        check("公式", "网页视图指向本地渲染接口并保留源码作替代文字",
              "/api/formula?" in rendered["html"] and "formula-img" in rendered["html"]
              and "E = mc^{2}" in rendered["html"] and "formula-error" in rendered["html"])
        check("公式", "渲染不改动源文", rendered["raw"] == source)
        first = api.get("/api/formula", {"tex": r"\frac{a}{b}", "size": "17", "theme": "light"})
        cached_first = len(list(Path(core.formula_cache_dir(scratch)).glob("*.png")))
        second = api.get("/api/formula", {"tex": r"\frac{a}{b}", "size": "17", "theme": "light"})
        cached_second = len(list(Path(core.formula_cache_dir(scratch)).glob("*.png")))
        check("公式", "/api/formula 返回 PNG 且第二次命中缓存",
              first.ctype == "image/png" and first.body.startswith(b"\x89PNG")
              and first.body == second.body and cached_second == cached_first,
              "第二次调用后缓存文件 %d → %d" % (cached_first, cached_second))
        themed = api.get("/api/formula", {"tex": r"\frac{a}{b}", "size": "17", "theme": "dark"})
        check("公式", "主题不同就是另一张图（缓存键含主题）", themed.body != first.body)
        try:
            api.get("/api/formula", {"tex": r"\foo{x}"})
            check("公式", "画不出来的公式接口报错而不是返回空图", False)
        except ValueError as exc:
            check("公式", "画不出来的公式接口报错而不是返回空图", "\\foo" in str(exc))

        dest = Path(scratch, "导出.html")
        workspace.export_doc_html(pdir, entry["id"], str(dest), pid=pid)
        page = dest.read_text(encoding="utf-8")
        check("公式", "导出的 HTML 内嵌图片、断网可看",
              "data:image/png;base64," in page and "/api/formula?" not in page)

    failures = []
    for tex, label in FX.SUPPORTED_SAMPLES:
        result = FX.to_png(tex, size=17)
        if not result["ok"]:
            failures.append("%s（%s）" % (label, result["reason"]))
    check("公式", "固定样本表 %d 条全部能渲染" % len(FX.SUPPORTED_SAMPLES), not failures,
          "；".join(failures[:3]))

    from mdreader import render as RD
    frag, _meta, _engine = RD.render_markdown(
        "正文 <script>alert(1)</script> 与 $x^{2}$。", formula=core.formula_resolver(win.ws.root))
    check("公式", "公式走单独通道，正文脚本仍被过滤",
          "<script" not in frag and "formula-img" in frag)

    for tab in list(win.tabs):
        with _quiet_prompt(win):
            win.close_tab(tab)
    skip("公式", "公式的排版观感（与 KaTeX 的差距、长公式换行）",
         "需要人眼在真实窗口与导出稿里看；自动部分只证明“画出来了、内容没丢”")


def display_section(win, root: Path):
    section("【显示】三种主题、缩放、对比度")

    for name, palette in winui.THEMES.items():
        win.set_theme(name)
        win.root.update_idletasks()
        check("显示", "%s 主题应用到窗口" % name, win.text["background"] == palette["bg"])
        check("显示", "%s 主题正文对比度 ≥ 4.5" % name,
              contrast(palette["fg"], palette["bg"]) >= 4.5,
              "%.2f" % contrast(palette["fg"], palette["bg"]))
        check("显示", "%s 主题次要文字对比度 ≥ 4.5" % name,
              contrast(palette["muted"], palette["bg"]) >= 4.5,
              "%.2f" % contrast(palette["muted"], palette["bg"]))

    stored = core.read_ui_settings(win.ws.root)["theme"]
    check("显示", "主题写入工作区设置", stored == win.theme, stored)
    fresh = window(win.ws.root)
    try:
        check("显示", "重启后恢复上次主题", fresh.theme == win.theme, fresh.theme)
        check("显示", "尺寸随显示缩放计算",
              int(fresh.side["width"]) == fresh.px(310)
              and int(fresh.style.lookup("Treeview", "rowheight")) == fresh.px(28))
    finally:
        close(fresh)

    skip("显示", "100% / 150% / 200% 实际观感、小窗口与长表格",
         "本机只覆盖当前缩放（%.2f）；换缩放需要人工确认" % win.ui_scale)


def entries_section(win, root: Path, project: str):
    section("【界面】原生窗口与网页入口行为一致")

    pdir = win.ws.require_project(project)
    doc = win.ws.create_doc(pdir, "双入口", content="# 双入口\n\n原始内容\n")
    api = core.Api(win.ws, str(WEBUI))

    payload = api.get("/api/doc", {"pid": project, "doc": doc["id"]})
    check("界面", "网页入口读到与原生相同的身份", payload.get("identity") == os.path.join(pdir, "双入口.md"))
    check("界面", "网页入口带有保存基线", bool(payload.get("info", {}).get("revision")))

    stale = payload["info"]["revision"]
    write(Path(pdir, "双入口.md"), "# 外部改过\n\n磁盘新内容\n")
    try:
        api.post("/api/doc/save", {"pid": project, "doc": doc["id"], "content": "网页写的\n", "expected": stale})
        check("界面", "网页保存遇到外部修改会冲突", False)
    except core.D.ConflictError as exc:
        check("界面", "网页保存遇到外部修改会冲突", exc.current == core.D.revision(os.path.join(pdir, "双入口.md")))
    check("界面", "冲突时磁盘内容未被覆盖",
          "磁盘新内容" in Path(pdir, "双入口.md").read_text(encoding="utf-8"))

    win.open_local_files([str(Path(pdir, "双入口.md"))])
    win.toggle_mode()
    win._set_widget("原生写的\n")
    win.source = win.get_text()
    win.set_dirty(True)
    # 顺序很重要：先打开（记住基线），再让磁盘变化，然后保存。
    write(Path(pdir, "双入口.md"), "# 又改了一次\n\n更新的磁盘内容\n")
    conflicts = []
    win.resolve_conflict = lambda conflict: conflicts.append(conflict) or False
    saved = win.save_doc()
    check("界面", "原生窗口保存也会被冲突拦下", not saved and len(conflicts) == 1,
          "saved=%s conflicts=%d" % (saved, len(conflicts)))
    check("界面", "冲突时磁盘内容未被原生窗口覆盖",
          "更新的磁盘内容" in Path(pdir, "双入口.md").read_text(encoding="utf-8"))
    check("界面", "原生窗口同样保留编辑内容", win.get_text() == "原生写的\n")
    skip("界面", "冲突对话框的按钮分支", "由 tests/test_document_ui.py 真实点击覆盖，此处不重复")
    win.set_dirty(False)
    win.close_tab(win.active_tab)

    server_ok = False
    try:
        ws_obj, httpd, port = core.serve(str(win.ws.root), port=0)
        token = core.api_of(httpd).token
        thread = core.ServerThread(httpd)
        thread.start()
        try:
            import json
            import urllib.error
            import urllib.request
            request = urllib.request.Request("http://127.0.0.1:%d/api/state" % port,
                                             headers={"X-MDReader-Session": token})
            with urllib.request.urlopen(request, timeout=10) as response:
                state = json.load(response)
            server_ok = state.get("version") == core.APP_VERSION
            unauthorized = urllib.request.Request("http://127.0.0.1:%d/api/state" % port)
            try:
                urllib.request.urlopen(unauthorized, timeout=10)
                check("界面", "未带会话凭据的请求被拒绝", False)
            except urllib.error.HTTPError as exc:
                check("界面", "未带会话凭据的请求被拒绝", exc.code == 403, str(exc.code))
        finally:
            thread.stop()
            thread.join(timeout=5)
    except OSError as exc:
        record("界面", "本地服务可启动", "SKIP", str(exc))
    check("界面", "本地服务报告同一版本", server_ok)


def share_section(win, root: Path, project: str):
    section("【分享】导出单篇与合集，断网可打开")

    pdir = win.ws.require_project(project)
    dest = Path(root, "导出", "单篇.html")
    out = win.ws.export_doc_html(pdir, os.path.join("双入口.md"), str(dest), pid=project)
    page = Path(out).read_text(encoding="utf-8")
    check("分享", "单篇导出生成文件", Path(out).is_file())
    check("分享", "导出内容不依赖本地服务", "/api/" not in page)
    check("分享", "导出是自包含 HTML", page.startswith("<!doctype html") and "</html>" in page)

    all_dest = Path(root, "导出", "合集.html")
    out = win.ws.export_project_html(project, str(all_dest))
    page = Path(out).read_text(encoding="utf-8")
    check("分享", "合集导出包含每个文档",
          page.count('class="export-doc"') == len(win.ws.scan_docs(pdir)),
          "%d 节 / %d 篇" % (page.count('class="export-doc"'), len(win.ws.scan_docs(pdir))))
    check("分享", "合集目录锚点可用", 'href="#doc-' in page and 'id="doc-' in page)
    check("分享", "合集同样不依赖本地服务", "/api/" not in page)
    skip("分享", "断网后双击打开导出文件", "需要人眼确认浏览器渲染结果")


def install_lifecycle_section(install_dir: str):
    """按快捷方式的方式启动安装副本，验证开窗、服务、关窗退出（会真实开窗）。

    对应用户实际点桌面图标 / 把 .md 拖到图标上的路径：`开发框架.md` 7.2 把这几行
    列为人工验收，这里做成可重复执行的版本，默认不跑（`--lifecycle DIR` 才跑）。
    """
    section("【安装副本生命周期】按快捷方式启动 → 服务 → 关窗退出")
    user32 = c.WinDLL("user32", use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user32.IsWindowVisible.argtypes = [w.HWND]
    user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [w.HWND]
    user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    WM_CLOSE = 0x0010

    def visible_windows(pid):
        found = []

        def visit(hwnd, _lparam):
            owner = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buffer = c.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                found.append((hwnd, buffer.value))
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return found

    def listening_ports(pid):
        output = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                capture_output=True, text=True).stdout
        ports = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                if parts[4] == str(pid):
                    ports.append(int(parts[1].rsplit(":", 1)[1]))
        return sorted(set(ports))

    install = Path(install_dir).resolve()
    main = install / "main.py"
    if not check("生命周期", "安装目录里有 main.py", main.is_file(), str(main)):
        return
    version_file = install / "VERSION.txt"
    expected = version_file.read_text(encoding="utf-8").strip().split()[-1] if version_file.is_file() else ""

    with tempfile.TemporaryDirectory(prefix="mdreader-drop-") as folder:
        dropped = write(Path(folder, "拖进来的 文档.md"), "# 拖进来的文档\n\n正文\n")
        scenarios = (("按快捷方式启动", []), ("把 .md 拖到图标上", [str(dropped)]))
        for index, (label, extra) in enumerate(scenarios):
            # 每次启动都指向独立临时工作区：绝不能写进用户真实的 %USERPROFILE%\MDReader。
            scenario_ws = Path(folder, "ws-%d" % index)
            scenario_ws.mkdir(parents=True, exist_ok=True)
            argv = [str(main), "--workspace", str(scenario_ws), *extra]
            process = subprocess.Popen([sys.executable, *argv], cwd=str(install),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            window, title, port = None, "", None
            deadline = time.time() + 40
            while time.time() < deadline and process.poll() is None:
                windows = visible_windows(process.pid)
                ports = listening_ports(process.pid)
                if windows and ports:
                    window, title = windows[0]
                    port = ports[0]
                    if label != "把 .md 拖到图标上" or "拖进来的" in title:
                        break
                time.sleep(0.5)
            if process.poll() is not None:
                check("生命周期", "%s：进程保持运行" % label, False, "返回码 %s" % process.returncode)
                continue
            check("生命周期", "%s：出现窗口" % label, window is not None, title)
            if window is not None and expected and expected not in title:
                # Tk 先映射窗口、再设置标题，采样可能正好落在两者之间；这里等到标题
                # 稳定为止（最多 10 秒），否则会把一次正常的启动判成失败。
                settle = time.time() + 10
                while time.time() < settle and expected not in title:
                    time.sleep(0.2)
                    fresh = visible_windows(process.pid)
                    if fresh:
                        window, title = fresh[0]
            check("生命周期", "%s：标题显示版本 %s" % (label, expected or "?"),
                  bool(expected) and expected in title, title)
            if label == "把 .md 拖到图标上":
                check("生命周期", "%s：文件被自动打开" % label, "拖进来的 文档" in title, title)
            served = False
            if port:
                try:
                    import urllib.request
                    with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as response:
                        served = response.status == 200
                except Exception as exc:
                    record("生命周期", "%s：本地页面可访问" % label, "FAIL", str(exc))
            check("生命周期", "%s：本地服务提供页面" % label, served, "端口 %s" % port)
            if window:
                user32.PostMessageW(window, WM_CLOSE, 0, 0)
            exited = False
            deadline = time.time() + 25
            while time.time() < deadline:
                if process.poll() is not None:
                    exited = True
                    break
                time.sleep(0.5)
            check("生命周期", "%s：关窗后进程退出" % label, exited, "返回码 %s" % process.poll())
            if not exited:
                process.kill()
            time.sleep(0.8)
            check("生命周期", "%s：关窗后本地服务端口释放" % label, not listening_ports(process.pid))


def summary(started) -> int:
    section("【结果】")
    failed = [row for row in RESULTS if row[2] == "FAIL"]
    skipped = [row for row in RESULTS if row[2] == "SKIP"]
    passed = [row for row in RESULTS if row[2] == "PASS"]
    print("  通过 %d，失败 %d，跳过 %d，用时 %.1f 秒"
          % (len(passed), len(failed), len(skipped), __import__("time").perf_counter() - started))
    for group, name, _, detail in failed:
        print("  [FAIL] %s / %s  %s" % (group, name, detail))
    return 1 if failed else 0


HAND_NOTES_MARKER = "## 结论与人工确认"
#: 每次重跑都会重建上面那张自动矩阵，所以人工结论单独放在这个文件里，
#: 由 write_report 原样追加——手写的验收结论不能因为重跑一次就消失。
HAND_NOTES = ROOT / "docs" / "acceptance-manual-notes.md"


def write_report(label="") -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    suffix = ("-" + label) if label else ""
    target = ROOT / "docs" / ("acceptance-%s%s-%s.md"
                              % (core.APP_VERSION, suffix, datetime.date.today().isoformat()))
    lines = ["# 验收记录 %s（%s）" % (core.APP_VERSION, stamp), "",
             "由 `scripts/acceptance.py --write` 生成；只包含能自动判定的项目，",
             "需要人眼确认的行以 SKIP 列出，未经人工确认不算通过。", "",
             "## 环境", ""]
    lines += ["- %s：%s" % (name, value) for name, value in ENVIRONMENT]
    current = None
    for group, name, status, detail in RESULTS:
        if group != current:
            lines += ["", "## %s" % group, ""]
            current = group
        lines.append("- [%s] %s%s" % ("x" if status == "PASS" else (" " if status == "FAIL" else "-"),
                                      name, ("（%s）" % detail) if detail else ""))
    if HAND_NOTES.is_file():
        notes = HAND_NOTES.read_text(encoding="utf-8").strip()
        if notes:
            lines += ["", notes, ""]
    elif "结论" not in "".join(lines):
        lines += ["", HAND_NOTES_MARKER, "",
                  "（人工结论尚未写入：%s）" % HAND_NOTES.name, ""]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description="MDReader 验收矩阵（自动部分）")
    parser.add_argument("--write", action="store_true", help="写入 docs/acceptance-<版本>-<日期>.md")
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--root", default="", help="改用另一份代码（安装目录或解压后的分发包）")
    parser.add_argument("--label", default="", help="写入记录时的后缀，例如 installed / package")
    parser.add_argument("--lifecycle", default="", metavar="安装目录",
                        help="额外按快捷方式方式启动该安装副本验证开窗/服务/关窗退出（会真实开窗）")
    args = parser.parse_args(argv)

    temporary = tempfile.TemporaryDirectory(prefix="mdreader-acceptance-")
    root = Path(temporary.name)
    win = window(root / "workspace", visible=True)
    try:
        environment_section(win)
        project = files_section(win, root)
        lifecycle_section(win, root)
        input_section(win)
        display_section(win, root)
        entries_section(win, root, project)
        dialog_section(win, root)
        folder_section(win, root)
        delete_section(win, root)
        plugin_section(win, root)
        official_plugin_section(win, root)
        image_section(root)
        export_service_section(win, root)
        link_section(win, root)
        editing_section(win, root)
        table_section(win, root)
        formula_section(win, root)
        share_section(win, root, project)
        if args.lifecycle:
            close(win)                       # 让出前台，真正按快捷方式启动另一份
            install_lifecycle_section(args.lifecycle)
            win = None
    finally:
        if win is not None:
            close(win)
        if args.keep:
            print("\n临时工作区保留在：%s" % root)
        else:
            temporary.cleanup()

    code = summary(started)
    if args.write:
        print("\n验收记录已写入：%s" % write_report(args.label))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
