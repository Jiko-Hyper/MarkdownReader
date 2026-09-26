# -*- coding: utf-8 -*-
"""A01–A15 关键验收矩阵（任务书 §9）：逐条执行并记录。

    python scripts/acceptance_matrix.py                  # 只打印
    python scripts/acceptance_matrix.py --write          # 写 docs/acceptance-matrix-<版本>-<日期>.md
    python scripts/acceptance_matrix.py --root DIR       # 对安装副本或分发包执行
    python scripts/acceptance_matrix.py --only A05,A08   # 只跑某几条
    python scripts/acceptance_matrix.py --keep           # 保留临时工作区以便排查

与 `scripts/acceptance.py` 的分工：那边按**功能小节**跑全部自动项（文件、文件夹、删除、
插件、表格、公式、导出……）；这里按任务书 §9 的 **15 条关键场景**逐条执行，只断言每条
“通过条件”里能自动判定的部分。需要人眼确认的部分（真实回收站还原、输入法候选词外观、
Word/PDF 排版观感、断网后浏览器渲染）、以及两项历史缺陷（E06 换主题丢位置、E07 侧栏焦点）
以 SKIP 列出并写明原因——SKIP 不是通过，记录里也不会写成通过。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
#: 本脚本自己的参数（`acceptance` 会在导入时改写用法，见下）。
ORIGINAL_ARGV = list(sys.argv[1:])

# `acceptance` 在导入时按 sys.argv 决定被测代码根目录（--root）。为了不让它被
# 我们自己的 `--write/--label` 干扰，这里只把 --root 交给它，其余参数自己留着
# （早先直接截断 sys.argv 导致 `--write` 被吞掉，安装副本那次跑完没有写记录）。
for _index, _item in enumerate(ORIGINAL_ARGV):
    if _item == "--root" and _index + 1 < len(ORIGINAL_ARGV):
        sys.argv = [sys.argv[0], "--root", ORIGINAL_ARGV[_index + 1]]
        break
    if _item.startswith("--root="):
        sys.argv = [sys.argv[0], "--root=" + _item.split("=", 1)[1]]
        break
sys.path.insert(0, str(HERE))
import acceptance as A  # noqa: E402

core, D, P, winui = A.core, A.D, A.P, A.winui
from mdreader import formula as FX  # noqa: E402
from mdreader import media  # noqa: E402
from mdreader import render as RD  # noqa: E402
from mdreader import tables as TB  # noqa: E402

section, check, skip, record, write = A.section, A.check, A.skip, A.record, A.write


# --------------------------------------------------------------------------
# 共用小工具
# --------------------------------------------------------------------------

def official_packages():
    """随仓库发布的正式插件包，按 id 索引。"""
    found = {}
    for package in sorted((ROOT / "plugins" / "packages").glob("*.zip")):
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        found[manifest["id"]] = package
    return found


def plugin_workspace(root: Path, name: str, ids=("mdreader.image-insert",)):
    """自己的临时工作区 + 装入并启用的正式插件；返回 (workspace, api, 失败原因)。"""
    workspace = core.Workspace(str(root / name))
    packages = official_packages()
    missing = [pid for pid in ids if pid not in packages]
    if missing:
        return workspace, None, "缺插件包：%s" % "、".join(missing)
    for pid in ids:
        workspace.plugins.install(str(packages[pid]))
        workspace.plugins.enable(pid)
    return workspace, core.Api(workspace, str(A.WEBUI)), ""


def own_window(root: Path, name: str):
    return A.window(root / name)


def sample_store(root: Path, name: str, ids):
    """用样例插件现场打包 + 本地受信清单（不碰真实工作区的受信文件）。"""
    workspace = core.Workspace(str(root / name))
    samples = ROOT / "plugins" / "samples"
    packages = root / (name + " 包")
    packages.mkdir(parents=True, exist_ok=True)
    trust_file = root / (name + " trust.json")
    trust = {"api_version": "1", "plugins": {}}
    trust_file.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
    store = workspace.plugins
    store.trust = P.TrustRegistry(str(trust_file))
    import hashlib
    installed = []
    for sample in ids:
        out = packages / ("%s.zip" % sample)
        P.build_package(str(samples / sample), str(out))
        manifest = json.loads((samples / sample / P.MANIFEST_NAME).read_text(encoding="utf-8"))
        trust["plugins"].setdefault(manifest["id"], {"publisher": "MDReader", "versions": {}})
        trust["plugins"][manifest["id"]].setdefault("versions", {})[
            manifest["version"]] = hashlib.sha256(out.read_bytes()).hexdigest()
        trust_file.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
        store.trust.load()
        store.install(str(out))
        store.enable(manifest["id"])
        installed.append((sample, manifest["id"], packages))
    return workspace, store, trust_file, packages, installed


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S))


def cleanup(workspace):
    try:
        workspace.plugins.shutdown()
    except Exception:
        pass


# --------------------------------------------------------------------------
# A01–A15
# --------------------------------------------------------------------------

def row_a01(root: Path):
    section("A01 原文件夹新建、子目录、重名与只读")
    user = root / "A01 用户目录"
    write(user / "笔记.md", "# 笔记\n\n正文\n")
    (user / "子目录").mkdir(parents=True, exist_ok=True)
    win = own_window(root, "A01 工作区")
    try:
        with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
            win.open_user_folder()
        rid = win.cur_root or ""
        check("A01", "打开原文件夹后拿到授权根目录", bool(rid) and user.is_dir(), rid)
        made = win.ws.folders.create_doc(rid, "新的.md", content="# 新的\n")
        check("A01", "新建的 Markdown 确实落在选定目录",
              (user / "新的.md").is_file(), made.get("id", ""))
        sub = win.ws.folders.create_doc(rid, "子目录 里的.md", subdir="子目录")
        check("A01", "子目录里也能新建（文件名含空格）",
              (user / "子目录" / "子目录 里的.md").is_file(), sub.get("id", ""))
        conflict = ""
        try:
            win.ws.folders.create_doc(rid, "笔记.md", content="覆盖试试\n")
        except Exception as exc:                        # noqa: BLE001 - 报告拒绝原因
            conflict = str(exc)
        check("A01", "重名被拒绝且原件逐字符未变",
              bool(conflict) and (user / "笔记.md").read_text(encoding="utf-8") == "# 笔记\n\n正文\n",
              conflict)
        docs = sorted(p.relative_to(user).as_posix() for p in user.rglob("*.md"))
        check("A01", "没有暗中导入副本（目录里就是这三份）",
              docs == ["子目录/子目录 里的.md", "新的.md", "笔记.md"], str(docs))

        readonly = user / "只读.md"
        write(readonly, "只读内容\n")
        os.chmod(readonly, stat.S_IREAD)
        try:
            win.open_local_files([str(readonly)])
            win.toggle_mode()
            win._set_widget("想改的内容\n")
            win.source = win.get_text()
            win.set_dirty(True)
            failed = not win.save_doc()
            check("A01", "只读文件保存失败、缓冲保留、磁盘内容未变",
                  failed and win.get_text() == "想改的内容\n"
                  and readonly.read_text(encoding="utf-8") == "只读内容\n",
                  win.lbl_status.cget("text"))
            win.set_dirty(False)
        finally:
            os.chmod(readonly, stat.S_IWRITE)
    finally:
        A.close(win)


def row_a02(root: Path):
    section("A02 外部批量增删、原子替换、根目录掉线（记录实际延迟）")
    user = root / "A02 用户目录"
    for index in range(3):
        write(user / ("原本 %d.md" % index), "# 原本 %d\n" % index)
    win = own_window(root, "A02 工作区")
    try:
        with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
            win.open_user_folder()

        for index in range(5):
            write(user / ("外部新增 %d.md" % index), "# 外部新增 %d\n" % index)
        started = time.perf_counter()
        win.ws.folders._cache.clear()
        win._folders_dirty = True
        win.folder_watch_tick()
        added = sorted(d["id"] for d in win.root_docs)
        latency = time.perf_counter() - started
        check("A02", "外部批量新增后树最终一致（刷新延迟 %.0f ms）",
              len([item for item in added if item.startswith("外部新增")]) == 5 and latency < 3.0,
              "树里 %d 项，用时 %.0f ms" % (len(added), latency * 1000))

        for index in range(2):
            os.remove(user / ("外部新增 %d.md" % index))
        win.ws.folders._cache.clear()
        win._folders_dirty = True
        win.folder_watch_tick()
        now = sorted(d["id"] for d in win.root_docs)
        check("A02", "外部批量删除后树里不再有已删文件",
              not any(name.startswith(("外部新增 0", "外部新增 1")) for name in now),
              "、".join(now[:4]))

        target = user / "原本 0.md"
        scratch = user / "原本 0.md.tmp"
        write(scratch, "# 原子替换后的内容\n")
        os.replace(scratch, target)
        win.ws.folders._cache.clear()
        win._folders_dirty = True
        win.folder_watch_tick()
        check("A02", "外部原子替换后读到的是新内容",
              "原子替换后的内容" in win.ws.folders.read_doc(win.cur_root, "原本 0.md"))
        check("A02", "临时文件不残留", not scratch.exists())

        away = root / "A02 掉线的目录"
        registered = user
        os.rename(user, away)
        win.ws.folders._cache.clear()
        win._folders_dirty = True
        win.folder_watch_tick()
        from mdreader import folders as FO
        status = FO._status_of(str(registered))
        check("A02", "根目录掉线后不清库（登记仍在，状态是 missing）",
              bool(win.roots) and status == "missing",
              "状态 %s，登记 %d 个" % (status, len(win.roots)))

        loose = write(root / "A02 掉线时仍可编辑.md", "# 掉线时\n\n仍可编辑\n")
        win.open_local_files([str(loose)])
        win.toggle_mode()
        win._set_widget("掉线期间照常编辑\n")
        win.source = win.get_text()
        win.set_dirty(True)
        check("A02", "根目录失联时界面不冻结（别的文档照常编辑）",
              win.get_text() == "掉线期间照常编辑\n")
        win.set_dirty(False)
        os.rename(away, user)
        win.ws.folders._cache.clear()
        win._folders_dirty = True
        win.folder_watch_tick()
        tree = win.ws.folders.scan(win.cur_root, force=True)
        ids = [item["id"] for item in tree.get("docs", [])]
        check("A02", "目录回来后树恢复", "原本 0.md" in ids, "、".join(ids[:4]))
    finally:
        A.close(win)


def row_a03(root: Path):
    section("A03 删除取消/确认/回收站失败/未保存编辑")
    user = root / "A03 用户目录"
    write(user / "要删的.md", "# 要删的\n")
    write(user / "留着的.md", "# 留着的\n")
    bin_dir = root / "A03 模拟回收站"
    bin_dir.mkdir(parents=True, exist_ok=True)

    def fake_recycle(path):
        """把文件挪出原目录（真实回收站会弹系统确认框，自动脚本不驱动它）。"""
        shutil.move(str(path), str(bin_dir / os.path.basename(str(path))))
        return True

    win = own_window(root, "A03 工作区")
    try:
        with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
            win.open_user_folder()
        did = "要删的.md"
        target = user / did
        seen = {}

        def refuse(title, message, **kwargs):
            seen.update(title=title, message=message, default=kwargs.get("default"))
            return False

        with mock.patch("tkinter.messagebox.askyesno", side_effect=refuse):
            win.delete_folder_doc(did)
        check("A03", "取消时文件仍在、树未变",
              target.is_file() and did in [d["id"] for d in win.root_docs])
        check("A03", "提示写明真实影响（完整路径 + 进回收站）",
              str(target) in seen.get("message", "") and "回收站" in seen.get("message", "")
              and "删除本地文件" in seen.get("title", ""), seen.get("title", ""))

        with mock.patch.object(core, "_send_to_recycle_bin", return_value=False), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True):
            win.delete_folder_doc(did)
        check("A03", "回收站失败时保留文件、不改成永久删除",
              target.is_file() and "已保留" in win.lbl_status.cget("text"))

        with mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch.object(core, "_send_to_recycle_bin", side_effect=fake_recycle):
            win.delete_folder_doc(did)                  # 走到「回收站成功」这条分支
        check("A03", "确认后文件离开原目录（回收站调用成功）",
              not target.exists() and (bin_dir / did).is_file()
              and (user / "留着的.md").is_file() and user.is_dir(),
              "同目录其他文件与父目录都没动")
        skip("A03", "真实回收站里能不能还原回来",
             "自动脚本不驱动系统回收站：真实 SHFileOperation 会弹系统确认框（无人值守会卡住），"
             "还原也要人工在资源管理器里做；本轮这一项以模拟的回收站分支通过，真机还原未验")

        dirty = write(user / "有未保存修改.md", "# 有未保存修改\n")
        win.open_local_files([str(dirty)])
        win.toggle_mode()
        win._set_widget("改了但没保存\n")
        win.source = win.get_text()
        win.set_dirty(True)
        unsaved_prompt = {}

        def cancel_delete(title, message, **kwargs):
            unsaved_prompt.update(title=title, message=message)
            return None                                 # 「取消：什么都不做」

        with mock.patch("tkinter.messagebox.askyesnocancel", side_effect=cancel_delete):
            win.delete_folder_doc("有未保存修改.md")
        check("A03", "有未保存编辑时给出三选一提示（另存 / 放弃 / 取消）",
              "未保存" in unsaved_prompt.get("message", "")
              and "另存" in unsaved_prompt.get("message", "")
              and "删除本地文件" in unsaved_prompt.get("title", ""),
              (unsaved_prompt.get("message") or "")[:60])
        check("A03", "取消删除后未保存内容仍在编辑区",
              dirty.is_file() and win.get_text() == "改了但没保存\n")

        with mock.patch("tkinter.messagebox.askyesnocancel", return_value=False), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch.object(core, "_send_to_recycle_bin", side_effect=fake_recycle):
            win.delete_folder_doc("有未保存修改.md")
        tab = win._loose_tab_for(str(dirty))
        check("A03", "选择「放弃修改并继续」后文件被删除，缓冲变成「原文件已删除」",
              (not dirty.exists()) and (bin_dir / "有未保存修改.md").is_file()
              and bool((tab or {}).get("loose", {}).get("deleted")),
              "标签状态 %s" % ((tab or {}).get("loose", {}).get("deleted")))
        win.set_dirty(False)
    finally:
        A.close(win)


def row_a04(root: Path):
    section("A04 双端打开后单端删除，再保存和重启")
    user = root / "A04 用户目录"
    doc = write(user / "双端.md", "# 双端\n\n原始内容\n")
    first = own_window(root, "A04 工作区一")
    second = own_window(root, "A04 工作区二")
    try:
        first.open_local_files([str(doc)])
        second.open_local_files([str(doc)])
        check("A04", "两端都能打开同一个文件",
              first.active_tab is not None and second.active_tab is not None)

        with mock.patch.object(core, "_dialog_folder", return_value=[str(user)]):
            first.open_user_folder()
        # 这个标签就在前台：删除前会走「有未保存修改吗」的三选一，这里按「继续删除」应答
        with mock.patch.object(first, "_confirm_unsaved_before_delete", return_value=True), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch.object(core, "_send_to_recycle_bin",
                               side_effect=lambda path: (os.remove(path), True)[1]):
            first.delete_folder_doc("双端.md")
        check("A04", "一端删除后磁盘文件消失", not doc.exists())

        second.toggle_mode()
        second._set_widget("另一端没保存的修改\n")
        second.source = second.get_text()
        second.set_dirty(True)
        conflicts = []
        second.resolve_conflict = lambda conflict: conflicts.append(conflict) or False
        saved = second.save_doc()
        check("A04", "另一端保存不会悄悄重建已删文件，而是让用户另存",
              (not saved) and (not doc.exists()) and second.get_text() == "另一端没保存的修改\n"
              and "另存" in second.lbl_status.cget("text"),
              second.lbl_status.cget("text"))
        second.set_dirty(False)

        third = own_window(root, "A04 工作区三")
        try:
            third.open_local_files([str(doc)])
            check("A04", "重启（新实例）后已删文件没有被自动复活",
                  not doc.exists()
                  and not any((tab.get("loose") or {}).get("path") == str(doc)
                              for tab in third.tabs))
        finally:
            A.close(third)
    finally:
        A.close(first)
        A.close(second)


def row_a05(root: Path):
    section("A05 中文空格图片路径、附件落位、两端显示、截图粘贴与撤销")
    user = root / "A05 资料"
    folder = user / "中文 目录"
    doc = write(folder / "图纸 笔记.md", "# 图纸\n\n正文\n")
    picture = folder / "照片 一.png"
    picture.write_bytes(A._sample_png())
    workspace, api, problem = plugin_workspace(root, "A05 工作区", ("mdreader.image-insert",))
    if api is None:
        skip("A05", "正式图片插入插件可用", problem)
        return
    try:
        api.loose.open_path(str(doc))
        inserted = api.post("/api/plugins/insert", {
            "doc": str(doc), "revision": D.revision(str(doc)), "path": str(picture),
            "options": {"alt": "照片", "width": 720}, "entry": "web", "wait": 90})
        landed = sorted((folder / "assets").glob("*.png"))
        check("A05", "中文/空格路径下附件落进文档旁边的 assets",
              len(landed) == 1 and landed[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
              landed[0].name if landed else "没有附件")
        match = re.match(r"!\[[^\]]*\]\(([^)\s]+)", inserted.get("markdown", ""))
        relative = match.group(1) if match else ""
        check("A05", "原图保留、正文写的是相对链接",
              picture.is_file() and relative.startswith("assets/")
              and str(picture) not in inserted.get("markdown", ""),
              inserted.get("markdown", ""))
        resolved = media.local_image(relative, str(doc), str(user))
        check("A05", "相对链接能解析回真实文件", os.path.isfile(resolved), resolved)

        served = api.get("/api/localfile", {"doc": str(doc), "p": relative})
        check("A05", "网页端取到的是图片本身（不是替代文字）",
              served.ctype == "image/png" and bytes(served.body[:8]) == b"\x89PNG\r\n\x1a\n",
              served.ctype)

        write(doc, "# 图纸\n\n" + inserted["markdown"] + "\n")
        win = own_window(root, "A05 展示")
        try:
            win.root.deiconify()
            win.root.geometry("1000x700")
            win.root.update()
            win.open_local_files([str(doc)])
            win.root.update()
            images = [child for child in win.preview.winfo_children() if hasattr(child, "image")]
            check("A05", "桌面预览显示真实图片（不是 alt 文字）",
                  len(images) == 1 and images[0].image.width() > 0,
                  "图片控件 %d 个" % len(images))

            win.toggle_mode()
            win._set_widget("")
            win.source = ""
            win.plugin_commands = (lambda capability="":
                                   (workspace.plugins.commands()
                                    if capability == core.PL.CAP_IMAGE_INSERT else []))
            win._plugin_bridge = api
            with mock.patch.object(media, "clipboard_image", return_value=str(picture)), \
                 mock.patch("mdreader.media_ui.choose_width", return_value=240):
                win.insert_clipboard_image()
            check("A05", "截图粘贴入口把图片插到光标处",
                  "![图片](assets/" in win.get_text(), win.get_text().strip()[:50])
            win.text.edit_undo()
            check("A05", "撤销只撤销正文链接，附件不删",
                  "![图片](assets/" not in win.get_text()
                  and len(sorted((folder / "assets").glob("*.png"))) == 2,
                  "附件 %d 个" % len(sorted((folder / "assets").glob("*.png"))))
            win.set_dirty(False)
        finally:
            A.close(win)
    finally:
        cleanup(workspace)
    skip("A05", "拖放与粘贴的真实鼠标/键盘观感",
         "需要人工从资源管理器拖一张图、按一次 Ctrl+Shift+V（自动部分见【图片】小节）")


def row_a06(root: Path):
    section("A06 表格转义、宽表、TSV、结构修改")
    model = TB.build(2, 2, header=True,
                     fills=[["名称", "数量"], ["甲", "1"], ["乙 | 二", "2"]])
    rendered = TB.render(model["header"], model["rows"], model["aligns"])
    check("A06", "插入表格生成标准管道表格且内容齐全",
          rendered.startswith("| 名称 | 数量 |") and r"| 乙 \| 二 | 2 |" in rendered,
          rendered.splitlines()[0])
    back = TB.read(rendered + "\n", line=0)
    check("A06", "读回时转义还原成原始文本",
          back["ok"] and back["table"]["rows"][1][0] == "乙 | 二",
          json.dumps(back["table"]["rows"], ensure_ascii=False))

    wide = TB.build(12, 1, header=True,
                    fills=[["列%d" % n for n in range(12)], [str(n) for n in range(12)]])
    wide_text = TB.render(wide["header"], wide["rows"], wide["aligns"])
    cells = TB.split_row(wide_text.splitlines()[2])[0]
    check("A06", "12 列宽表一列不丢也不串行",
          len(cells) == 12 and cells[11].strip() == "11" and cells[0].strip() == "0",
          "%d 个单元格，末列「%s」" % (len(cells), cells[11].strip() if len(cells) > 11 else ""))
    page = RD.render_markdown(wide_text + "\n")[0]
    cells_html = len(re.findall(r"<td[ >]", page)) + len(re.findall(r"<th[ >]", page))
    check("A06", "渲染后的宽表 12 列都在（不裁列）",
          len(re.findall(r"<td[ >]", page)) == 12 and len(re.findall(r"<th[ >]", page)) == 12,
          "单元格 %d 个（td=%d th=%d）" % (cells_html, len(re.findall(r"<td[ >]", page)),
                                          len(re.findall(r"<th[ >]", page))))

    tsv = TB.parse_tsv("名称\t数量\n甲\t1\n乙\t2\n")
    check("A06", "规则 TSV 解析成 2 列 3 行", tsv["ok"] and tsv["columns"] == 2,
          "；".join(tsv.get("warnings") or []))
    irregular = TB.parse_tsv("名称\t数量\n甲\n乙\t2\t多一列\n")
    check("A06", "不规则 TSV 给出提示而不是静默错列",
          bool(irregular.get("warnings")) or not irregular.get("ok"),
          "；".join(irregular.get("warnings") or []))
    quoted = TB.parse_tsv('名称\t备注\n甲\t"带"引号"和\t制表符"\n')
    check("A06", "含引号/内嵌制表符的复杂内容有明确说明",
          bool(quoted.get("warnings")) or not quoted.get("ok"),
          "；".join(quoted.get("warnings") or []))

    table_md = "| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n"
    grown = TB.operate(table_md, "insert_row", line=0, index=1, values=["乙", "2"])
    check("A06", "结构修改（加一行）只改表格块",
          grown.get("ok") and "| 乙 | 2 |" in grown.get("text", "")
          and grown["text"].startswith("| 名称 | 数量 |"),
          (grown.get("reason") or "")[:40])
    bad = TB.operate(table_md, "set_cell", line=0, row=0, column=0, value="两行\n内容")
    check("A06", "不支持的多行单元格被拒绝且不改正文", bad.get("ok") is False,
          (bad.get("reason") or "")[:40])

    win = own_window(root, "A06 工作区")
    try:
        doc = write(root / "A06" / "表格.md", table_md)
        win.open_local_files([str(doc)])
        win.toggle_mode()
        win._set_widget(table_md)
        win.source = win.get_text()
        win.editor.edit_separator()
        separators = win.editor.cget("autoseparators")
        win.editor.configure(autoseparators=False)
        try:
            win.editor.delete("1.0", "end-1c")
            win.editor.insert("1.0", grown["text"])
        finally:
            win.editor.configure(autoseparators=separators)
        win.editor.edit_separator()
        win.source = win.get_text()
        win.set_dirty(True)
        inserted_ok = "| 乙 | 2 |" in win.get_text()
        win.text.edit_undo()
        check("A06", "结构修改一次撤销就还原", inserted_ok and win.get_text() == table_md,
              win.get_text().strip()[:40])
        win.set_dirty(False)
    finally:
        A.close(win)
    skip("A06", "表格弹层在真实中文输入法下的输入体验",
         "需要人工敲一次中文（自动部分见 tests/test_table_ui.py）")


def row_a07(root: Path):
    section("A07 公式、普通美元、代码、错误宏与大输入")
    money = "价格是 $5 与 $6 元，还有孤立 $ 符号。\n"
    money_hits = FX.scan(money)
    check("A07", "金额文本与孤立美元符号不会被当成公式（都带原因退回文本）",
          all(hit.get("error") for hit in money_hits) and bool(money_hits),
          "；".join((hit.get("error") or "")[:20] for hit in money_hits))
    code = "```\n$x = 1$\n```\n行内代码 `$y = 2$`\n"
    check("A07", "代码块与行内代码里的 $ 不参与", FX.scan(code) == [])
    inline = FX.scan("质能关系 $E = mc^{2}$ 与独立块：\n\n$$\n\\frac{a}{b}\n$$\n")
    check("A07", "行内与独立公式都能识别",
          len(inline) == 2 and [hit["display"] for hit in inline] == [False, True],
          json.dumps([hit["display"] for hit in inline]))
    check("A07", "未闭合的 $ 保留文本并给出原因",
          bool(FX.scan("未闭合 $x + 1\n")[0].get("error")))

    unsafe = FX.validate("\\write18{calc}\n\\input{/etc/passwd}\n")
    check("A07", "危险宏/文件读取被拒绝（不执行、有原因）",
          not unsafe["ok"] and bool(unsafe["reason"]), unsafe["reason"][:50])
    broken = FX.to_png("\\frac{1}{", size=16)
    check("A07", "语法错误的公式画不出来但不抛异常", broken.get("ok") is False,
          broken.get("reason", "")[:40])
    check("A07", "超过字符上限的公式被拒绝",
          FX.validate("x" * (FX.MAX_TEX_CHARS + 10))["ok"] is False)

    page = RD.render_markdown("正文 $E = mc^{2}$ 与 `$x$`\n\n<script>alert(1)</script>\n",
                              formula=lambda tex, display: {"ok": True,
                                                            "src": "/api/formula?tex=x",
                                                            "width": 10, "height": 10})[0]
    check("A07", "公式走单独通道，正文里的脚本仍被转义",
          "formula-img" in page and "<script>" not in page and "&lt;script&gt;" in page,
          "formula-img=%s script=%s" % ("formula-img" in page, "<script>" in page))
    broken = RD.render_markdown("坏公式 $\\foo{x}$\n",
                                formula=lambda tex, display: {"ok": False,
                                                              "reason": "不认识的命令"})[0]
    check("A07", "画不出来的公式保留原始表达式与原因",
          "foo" in broken and "formula-error" in broken, broken[-120:])

    directory = str(root / "A07 公式缓存")
    picked = FX.cached(directory, "E = mc^{2}", size=16, display=False)
    check("A07", "离线渲染产出 PNG 并写进缓存目录",
          picked["ok"] and Path(picked["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          os.path.basename(picked["path"]))
    again = FX.cached(directory, "E = mc^{2}", size=16, display=False)
    check("A07", "同一表达式第二次命中缓存（同一个文件）",
          again["path"] == picked["path"] and again["cached"] is True)
    dark = FX.cached(directory, "E = mc^{2}", size=16, display=False, theme="dark",
                     color="#dce1e8", background="#22262d")
    check("A07", "换主题是另一张图（缓存键含主题）", dark["path"] != picked["path"])
    skip("A07", "公式在真实窗口里的排版观感",
         "需要人眼看（与 KaTeX 的差距、长公式换行、三种主题下的接缝）；见【公式】小节")


def row_a08(root: Path):
    section("A08 混排长文：两端可读、PDF 不截断、DOCX 结构可编辑")
    ids = ("mdreader.export-pdf", "mdreader.export-docx")
    workspace, api, problem = plugin_workspace(root, "A08 工作区", ids)
    if api is None:
        skip("A08", "PDF / Word 导出插件可用", problem)
        return
    try:
        pid = workspace.create_project("A08 项目", with_readme=False)["id"]
        pdir = Path(workspace.require_project(pid))
        (pdir / "assets").mkdir(parents=True, exist_ok=True)
        (pdir / "assets" / "示意图.png").write_bytes(A._sample_png())
        lines = ["# 混排长文 标题 \U0001f4d8", "", "开头段落，含中文、Emoji \u2705 与行内 `代码`。", "",
                 "![示意图](assets/示意图.png \"width=320\")", "",
                 "```python", "def hello():", "    return '世界'", "```", "",
                 "| " + " | ".join("列%d" % n for n in range(12)) + " |",
                 "| " + " | ".join(["---"] * 12) + " |",
                 "| " + " | ".join("值%d" % n for n in range(12)) + " |", "",
                 "行内公式 $E = mc^{2}$，下面是独立公式：", "", "$$", "\\frac{a}{b}", "$$", ""]
        lines += ["第 %d 段正文，用来把文档撑到多页。" % n for n in range(1, 120)]
        lines += ["", "结尾标记 A08-TAIL", ""]
        markdown = "\n".join(lines)
        doc = pdir / "混排.md"
        doc.write_text(markdown, encoding="utf-8")

        rendered = api.get("/api/doc", {"pid": pid, "doc": "混排.md"})
        html = rendered.get("html") or ""
        check("A08", "网页入口把标题、代码、表格都渲染出来",
              "混排长文" in html and "def hello" in html and "列11" in html and "<table" in html,
              "HTML %d 字" % len(html))

        win = own_window(root, "A08 展示")
        try:
            win.root.deiconify()
            win.root.geometry("1100x760")
            win.root.update()
            win.open_local_files([str(doc)])
            win.root.update()
            shown = win.preview.get("1.0", "end")
            images = [child for child in win.preview.winfo_children() if hasattr(child, "image")]
            frames = [child for child in win.preview.winfo_children()
                      if getattr(child, "_restyle_table", None) is not None]
            labels = [label.cget("text") for label in (frames[0].winfo_children() if frames else [])]
            missed = [marker for marker in ("混排长文", "def hello") if marker not in shown]
            check("A08", "桌面预览显示标题、代码、宽表与图片/公式",
                  not missed and "列11" in labels and len(images) >= 2,
                  "缺少 %s；表格单元格 %d 个，图 %d 张" % ("、".join(missed) or "无",
                                                          len(labels), len(images)))
        finally:
            A.close(win)

        commands = {row["extension"]: row["command"] for row in workspace.plugins.commands()}
        produced = {}
        for ext in ("pdf", "docx"):
            target = pdir / ("混排." + ext)
            result = api.post("/api/plugins/export", {
                "pid": pid, "doc": "混排.md", "command": commands[ext],
                "dest": str(target), "wait": 300})
            produced[ext] = (target, result)
        check("A08", "两个格式都真的生成了文件，源文逐字符未变",
              all(result.get("path") == str(target) for target, result in produced.values())
              and doc.read_text(encoding="utf-8") == markdown
              and produced["pdf"][0].read_bytes().startswith(b"%PDF"))

        pdf = produced["pdf"][0]
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(pdf))
            try:
                pages = len(document)
                text = "".join(document[i].get_textpage().get_text_range() for i in range(pages))
            finally:
                document.close()
            check("A08", "PDF 是多页且结尾标记还在（分页没截断）",
                  pages >= 2 and "A08-TAIL" in text and "混排长文" in text,
                  "%d 页，文本 %d 字" % (pages, len(text)))
            check("A08", "PDF 正文可检索（含中文与代码）",
                  "def hello" in text and "结尾标记" in text)
        except ImportError:
            skip("A08", "PDF 分页与正文可检索", "这台机器没有 pypdfium2，无法读导出稿的文本层")

        with zipfile.ZipFile(produced["docx"][0]) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
        check("A08", "DOCX 标题用的是标题样式（结构可编辑）",
              'w:val="Heading1"' in xml or 'w:val="Heading 1"' in xml)
        check("A08", "DOCX 里有真表格与嵌入图片",
              "<w:tbl>" in xml and "<w:drawing>" in xml and bool(media_files),
              "媒体 %d 个" % len(media_files))
        check("A08", "DOCX 正文完整（结尾标记在）", "A08-TAIL" in xml)
        skip("A08", "Word 里公式是否可编辑（OMML）",
             "本轮没有实现 OMML：Word 里公式是图片并带原始写法替代文字；PDF 里不可检索")
        skip("A08", "分页观感与表格跨页效果", "需要人工用真实 Word / WPS / PDF 阅读器打开看")
    finally:
        cleanup(workspace)


def row_a09(root: Path):
    section("A09 导出未保存文本、附件变化与原文件同路径")
    work = root / "A09 资料"
    asset = work / "assets" / "图.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(A._sample_png())
    doc = write(work / "导出.md", "# 导出\n\n![图](assets/图.png)\n\n磁盘正文\n")
    workspace, api, problem = plugin_workspace(root, "A09 工作区", ("mdreader.export-docx",))
    if api is None:
        skip("A09", "DOCX 导出插件可用", problem)
        return
    try:
        api.loose.open_path(str(doc))
        command = workspace.plugins.commands()[0]["command"]
        buffer_text = "# 导出\n\n![图](assets/图.png)\n\n缓冲区正文（没保存）\n"
        plan = api.post("/api/export/check", {"doc": str(doc), "revision": D.revision(str(doc)),
                                              "markdown": buffer_text,
                                              "dest": str(work / "检查.docx")})
        check("A09", "未保存时预检要求先选来源（默认当前编辑内容）",
              plan["needs_source"] is True and plan["source"] == "buffer", plan["source"])
        check("A09", "预检只算不写", sorted(os.listdir(str(work))) == ["assets", "导出.md"],
              "、".join(sorted(os.listdir(str(work)))))

        for source, want, avoid in (("disk", "磁盘正文", "缓冲区"),
                                    ("buffer", "缓冲区正文", None)):
            label = "磁盘已保存版本" if source == "disk" else "当前编辑内容"
            target = work / ("来源-%s.docx" % source)
            api.post("/api/plugins/export", {
                "doc": str(doc), "revision": D.revision(str(doc)), "command": command,
                "markdown": buffer_text, "source": source, "dest": str(target), "wait": 300})
            text = docx_text(target) if target.is_file() else ""
            check("A09", "选「%s」时导出稿里的正文就是那一份" % label,
                  want in text and (avoid is None or avoid not in text),
                  "导出稿含「%s」" % want)
        check("A09", "导出不改源文（磁盘上还是磁盘那一份）",
              doc.read_text(encoding="utf-8").endswith("磁盘正文\n"))

        refused = ""
        try:
            api.post("/api/export/check", {"doc": str(doc), "revision": D.revision(str(doc)),
                                           "dest": str(doc)})
        except ValueError as exc:
            refused = str(exc)
        check("A09", "导出目标不能是源文档本身", bool(refused), refused[:40])

        asset.unlink()
        missing = api.post("/api/export/check", {"doc": str(doc), "revision": D.revision(str(doc)),
                                                 "command": command,
                                                 "dest": str(work / "缺图.docx")})
        messages = "；".join(item.get("message", "") for item in missing["preflight"]["errors"])
        check("A09", "附件被删掉后预检拦住导出，并指出缺的是哪一张",
              missing["preflight"]["blocked"] and "assets/图.png" in messages,
              messages or "预检没有报错")

        asset.write_bytes(b"not an image any more")
        before = sorted(item.name for item in work.iterdir())
        failed = None
        try:
            failed = api.post("/api/plugins/export", {
                "doc": str(doc), "revision": D.revision(str(doc)), "command": command,
                "dest": str(work / "坏图.docx"), "wait": 120})
        except Exception as exc:                        # noqa: BLE001 - 失败原因要能看懂
            failed = {"error": str(exc)}
        check("A09", "附件内容被改坏（文件还在）时导出失败并给出原因，不留半成品",
              bool(failed) and not (work / "坏图.docx").exists()
              and sorted(item.name for item in work.iterdir()) == before,
              str(failed.get("error", ""))[:70] if failed else "没有报错")
    finally:
        cleanup(workspace)


def row_a10(root: Path):
    section("A10 同名导出、确认后外部变化、取消、磁盘写入失败")
    work = root / "A10 资料"
    doc = write(work / "文稿.md", "# 文稿\n\n正文\n")
    target = work / "文稿.docx"
    target.write_bytes(b"OLD-DOCX")
    workspace, api, problem = plugin_workspace(root, "A10 工作区", ("mdreader.export-docx",))
    if api is None:
        skip("A10", "DOCX 导出插件可用", problem)
        return
    try:
        api.loose.open_path(str(doc))
        command = workspace.plugins.commands()[0]["command"]
        payload = {"doc": str(doc), "revision": D.revision(str(doc)), "command": command,
                   "dest": str(target), "wait": 300}
        first = api.post("/api/plugins/export", dict(payload))
        check("A10", "目标已存在且没确认时不静默覆盖",
              first.get("conflict") is True and "已存在" in first.get("error", "")
              and target.read_bytes() == b"OLD-DOCX", first.get("error", "")[:50])

        seen = D.revision(str(target))
        confirmed = api.post("/api/plugins/export", dict(payload, overwrite=seen))
        check("A10", "用户确认（对着看过的修订）后写出新文件",
              confirmed.get("path") == str(target) and target.read_bytes()[:2] == b"PK",
              str(confirmed.get("error", ""))[:40])

        target.write_bytes(b"CHANGED-OUTSIDE")
        again = api.post("/api/plugins/export", dict(payload, overwrite=seen))
        check("A10", "确认之后目标又被外部改过，必须重新确认",
              again.get("conflict") is True and target.read_bytes() == b"CHANGED-OUTSIDE",
              str(again.get("error", ""))[:50])
        forced = api.post("/api/plugins/export", dict(payload, overwrite=D.revision(str(target))))
        check("A10", "重新确认后按新内容覆盖", forced.get("path") == str(target)
              and target.read_bytes()[:2] == b"PK")

        cancel_dest = work / "取消.docx"
        api.post("/api/export/check", {"doc": str(doc), "revision": D.revision(str(doc)),
                                       "dest": str(cancel_dest)})
        check("A10", "走完预检就取消时目标没有被创建", not cancel_dest.exists())

        blocked = work / "占用.docx"
        blocked.mkdir()
        failed = None
        try:
            failed = api.post("/api/plugins/export", {
                "doc": str(doc), "revision": D.revision(str(doc)), "command": command,
                "dest": str(blocked), "overwrite": True, "wait": 300})
        except Exception as exc:                        # noqa: BLE001 - 失败原因要能看懂
            failed = {"error": str(exc)}
        leftovers = [item.name for item in work.iterdir() if item.name.startswith(".mdreader")]
        check("A10", "目标写不进去时报错、原样保留、不留可误认的半成品",
              bool(failed) and blocked.is_dir() and list(blocked.iterdir()) == []
              and not leftovers and not (work / "占用.docx.mdreader").exists(),
              (str(failed.get("error", ""))[:50] if failed else "没有报错"))
    finally:
        cleanup(workspace)


def row_a11(root: Path):
    section("A11 断网、关闭 MDReader 后打开导出文件")
    work = root / "A11 资料"
    asset = work / "assets" / "图.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(A._sample_png())
    workspace = core.Workspace(str(root / "A11 工作区"))
    try:
        pid = workspace.create_project("A11 项目", with_readme=False)["id"]
        pdir = Path(workspace.require_project(pid))
        (pdir / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(asset), str(pdir / "assets" / "图.png"))
        (pdir / "自包含.md").write_text(
            "# 自包含\n\n![图](assets/图.png)\n\n公式 $a^{2}+b^{2}=c^{2}$\n\n[外链](https://example.com)\n",
            encoding="utf-8")
        dest = work / "自包含.html"
        out = workspace.export_doc_html(pdir, "自包含.md", str(dest), pid=pid)
        page = Path(out).read_text(encoding="utf-8")
        check("A11", "导出稿是自包含 HTML（不依赖本地服务）",
              page.startswith("<!doctype html") and "/api/" not in page)
        check("A11", "图片与公式都内嵌成 data URI",
              page.count("data:image/png;base64,") >= 2,
              "%d 处内嵌" % page.count("data:image/png;base64,"))
        check("A11", "正文没有指向开发机的绝对路径",
              "D:\\AIWORKSPACE" not in page and "file:///" not in page)
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
        check("A11", "只有用户自己写的外链，没有 CDN / 本地服务依赖",
              all("example.com" in url for url in external),
              "外链：%s" % ("、".join(external[:3]) or "无"))
        skip("A11", "断网后双击打开导出文件",
             "需要人工断网并用浏览器打开确认渲染（自动部分只证明资源已内嵌、无本地服务依赖）")
    finally:
        cleanup(workspace)


def row_a12(root: Path):
    section("A12 输入法、三主题同步、查找目录、阅读位置、撤销和替换（含 E06 复验）")
    work = root / "A12 资料"
    lines = ["# 长文标题"] + ["第 %d 行正文，用来撑出滚动空间。" % n for n in range(1, 900)]
    doc = write(work / "长文.md", "\n".join(lines) + "\n")
    win = own_window(root, "A12 工作区")
    try:
        win.root.deiconify()
        win.root.geometry("900x620")
        win.root.update()
        win.open_local_files([str(doc)])
        win.root.update()
        check("A12", "每个编辑控件都有输入法适配项", len(win._imes) > 0, "%d 个" % len(win._imes))

        win.toggle_mode()
        try:
            win.ime.surface.show("zheng'wen", 9)
            win.editor.mark_set("insert", "1.0")
            blocked = not win.format_selection("bold")
        finally:
            win.ime._focus_out()
        check("A12", "输入法组合中工具栏不往缓冲区里插标记", blocked,
              win.get_text().splitlines()[0][:20])

        win.set_theme("dark", persist=True)
        win.root.update()
        settings = core.read_ui_settings(win.ws.root)
        api = core.Api(win.ws, str(A.WEBUI))
        page_settings = api.get("/api/settings", {})
        check("A12", "主题写进工作区设置，两端读到同一个值（dark）",
              settings.get("theme") == "dark" and page_settings.get("theme") == "dark",
              "本地 %s / 网页 %s" % (settings.get("theme"), page_settings.get("theme")))

        win.toggle_mode()
        win.preview.yview_moveto(0.6)
        win.root.update()
        before = str(win.preview.index("@0,0"))
        win.set_theme("light", persist=False)
        win.root.update()
        after = str(win.preview.index("@0,0"))
        delta = abs(int(before.split(".")[0]) - int(after.split(".")[0]))
        check("A12", "预览换主题后顶部行号基本不变（E06 复验）", delta <= 3,
              "换主题前第 %s 行 → 换后第 %s 行（相差 %d 行）" % (before, after, delta))
        skip("A12", "E06 的收尾方式是否符合任务书要求",
             "现状用分数位置 yview_moveto 恢复（任务书 §E06 明确不建议），也没有与 E01 拆开单独验收；"
             "现象已不复现，但 E06 仍记为「未按计划完成」，等维护者决定是否重做")

        pid = win.ws.create_project("A12 查找", with_readme=False)["id"]
        pdir = win.ws.require_project(pid)
        win.ws.create_doc(pdir, "一", content="# 一\n\n这里有关键词 检索词\n")
        win.ws.create_doc(pdir, "二", content="# 二\n\n也有 检索词 出现\n")
        hits = win.ws.search(pdir, "检索词")
        check("A12", "目录查找能在多篇文档里找到命中并给出位置",
              len(hits) >= 2 and all(hit.get("name") for hit in hits),
              "%d 处命中：%s" % (len(hits), "、".join(hit.get("name", "") for hit in hits[:3])))

        win.open_local_files([str(doc)])
        win.toggle_mode()
        win._set_widget("重点内容\n")
        win.source = win.get_text()
        win.editor.mark_set("insert", "1.0")
        win.editor.tag_remove("sel", "1.0", "end")
        win.editor.tag_add("sel", "1.0", "1.2")
        win.format_selection("bold")
        bolded = win.get_text() == "**重点**内容\n"
        win.toggle_mode()
        win.toggle_mode()
        win.text.edit_undo()
        check("A12", "工具栏规则生效且切换视图后撤销仍然有效（E01 不回归）",
              bolded and win.get_text() == "重点内容\n", win.get_text().strip())
        win.set_dirty(False)
        skip("A12", "查找替换（历史 E03）", "本阶段未实现（历史排期在 0.2.10），不冒充已交付")
        skip("A12", "收起侧栏后焦点落在隐藏搜索框（历史 E07）",
             "既有缺陷，记录在 docs/NEXT_ITERATION.md §E07；本阶段未排期，未擅自移除")
        skip("A12", "输入法候选词外观", "需要人工敲一次中文看候选框位置（自动部分见【输入】小节）")
    finally:
        A.close(win)


def row_a13(root: Path):
    section("A13 无插件、单插件、组合启用、禁用卸载与重启")
    work = root / "A13 资料"
    doc = write(work / "核心可用.md", "# 核心可用\n\n正文\n")
    workspace = core.Workspace(str(root / "A13 工作区"))
    api = core.Api(workspace, str(A.WEBUI))
    try:
        store = workspace.plugins
        api.loose.open_path(str(doc))
        loose = api.get("/api/loose", {"doc": str(doc)})
        check("A13", "一个插件都没有时核心照常读写文档",
              store.commands() == [] and "核心可用" in json.dumps(loose, ensure_ascii=False))
        refused = ""
        try:
            api.post("/api/plugins/insert", {"doc": str(doc), "revision": D.revision(str(doc)),
                                             "path": str(doc), "wait": 30})
        except Exception as exc:                        # noqa: BLE001 - 拒绝原因要能看懂
            refused = str(exc)
        check("A13", "无插件时插图请求被明确拒绝（不是假装成功）", bool(refused), refused[:50])

        packages = official_packages()
        store.install(str(packages["mdreader.image-insert"]))
        store.enable("mdreader.image-insert")
        check("A13", "只启用图片插件时只注册图片命令",
              [row["capability"] for row in store.commands()] == [core.PL.CAP_IMAGE_INSERT],
              str([row["capability"] for row in store.commands()]))
        for pid in ("mdreader.export-pdf", "mdreader.export-docx"):
            store.install(str(packages[pid]))
            store.enable(pid)
        extensions = sorted(row.get("extension") for row in store.commands())
        check("A13", "三个插件组合启用后命令齐全（pdf + docx + 插图）",
              extensions == ["", "docx", "pdf"], str(extensions))

        store.disable("mdreader.image-insert")
        check("A13", "禁用后服务端立刻不再注册该命令",
              not any(row["capability"] == core.PL.CAP_IMAGE_INSERT for row in store.commands()))
        before = sorted(item.name for item in work.rglob("*"))
        store.uninstall("mdreader.export-pdf")
        check("A13", "卸载只删插件代码，用户文档与已插入图片保留",
              "mdreader.export-pdf" not in [row["id"] for row in store.list_plugins()
                                            if row["installed"]]
              and sorted(item.name for item in work.rglob("*")) == before)

        fresh = P.PluginStore(workspace.root, app_version=core.APP_VERSION)
        enabled = {row["id"]: row["enabled"] for row in fresh.list_plugins() if row["installed"]}
        check("A13", "重启（新实例）后启用状态保持一致",
              enabled.get("mdreader.export-docx") is True
              and enabled.get("mdreader.image-insert") is False,
              json.dumps(enabled, ensure_ascii=False))
        fresh.shutdown()
    finally:
        cleanup(workspace)


def row_a14(root: Path):
    section("A14 不兼容、缺依赖、插件异常、超时及执行中禁用")
    workspace, store, trust_file, packages, _ = sample_store(
        root, "A14 工作区", ("sample-image", "sample-export-a", "sample-export-b"))
    try:
        # 依赖：sample-dependent 需要 sample-export-b，先禁用它
        dep_workspace, dep_store, dep_trust, dep_packages, dep_rows = sample_store(
            root, "A14 依赖", ("sample-export-b", "sample-dependent"))
        dep_store.disable("mdreader.sample-export-b")
        reason = ""
        try:
            dep_store.enable("mdreader.sample-dependent")
        except P.PluginError as exc:
            reason = str(exc)
        row = next(item for item in dep_store.list_plugins()
                   if item["id"] == "mdreader.sample-dependent")
        check("A14", "缺依赖的插件只被禁用并写明原因",
              row["state"] == "unavailable" and "依赖" in (row.get("reason") or "") and bool(reason),
              (row.get("reason") or "")[:60])
        dep_store.enable("mdreader.sample-export-b")
        dep_store.enable("mdreader.sample-dependent")
        check("A14", "依赖启用后该插件恢复可用（其他不受影响）",
              any(item["command"].startswith("mdreader.sample-dependent")
                  for item in dep_store.commands()))
        cleanup(dep_workspace)

        failed = store.tasks.submit("mdreader.sample-image:insert.image",
                                    options={"synthetic": True, "fail": True}, wait=30)
        check("A14", "插件自己报错时任务失败，其他命令照常可用",
              store.tasks.poll(failed.id)["state"] == "failed"
              and any(item["capability"] == core.PL.CAP_EXPORT for item in store.commands()),
              (store.tasks.poll(failed.id).get("error") or "")[:50])

        slow = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"sleep": 30}, wait=1)
        store.disable("mdreader.sample-export-a")
        check("A14", "执行中禁用会取消任务，结果不提交",
              store.tasks.poll(slow.id)["state"] == "cancelled")
        store.enable("mdreader.sample-export-a")

        timed = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                   options={"sleep": 30}, timeout=2, wait=40)
        state = store.tasks.poll(timed.id)["state"]
        check("A14", "超时的任务被结束而不是一直挂着",
              state in ("timeout", "failed", "cancelled"), "任务状态 %s" % state)
        other = store.tasks.submit("mdreader.sample-image:insert.image",
                                   options={"synthetic": True}, wait=30)
        check("A14", "别的插件不受影响（任务照常完成）",
              store.tasks.poll(other.id)["state"] == "done")
        incompatible = root / "A14 不兼容源码"
        shutil.copytree(str(ROOT / "plugins" / "samples" / "sample-export-b"), str(incompatible))
        manifest_path = incompatible / P.MANIFEST_NAME
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["id"] = "mdreader.sample-incompatible"
        data["name"] = "样例插件：版本不兼容"
        data["app_version_range"] = ">=9.0.0"
        manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = packages / "incompatible.zip"
        P.build_package(str(incompatible), str(out))
        import hashlib
        trust = json.loads(trust_file.read_text(encoding="utf-8"))
        trust["plugins"]["mdreader.sample-incompatible"] = {
            "publisher": "MDReader",
            "versions": {"1.0.0": hashlib.sha256(out.read_bytes()).hexdigest()}}
        trust_file.write_text(json.dumps(trust, ensure_ascii=False, indent=2), encoding="utf-8")
        store.trust.load()
        store.install(str(out))
        blocked = ""
        try:
            store.enable("mdreader.sample-incompatible")
        except P.PluginError as exc:
            blocked = str(exc)
        row = next(item for item in store.list_plugins() if item["id"] == "mdreader.sample-incompatible")
        check("A14", "版本不兼容的插件只被禁用并写明原因",
              row["state"] == "unavailable" and "0.2.8" in (row.get("reason") or "")
              and "无法启用" in blocked, (row.get("reason") or "")[:60])
    finally:
        cleanup(workspace)


def row_a15(root: Path):
    section("A15 过期提交与越界输入（换文档、改正文、安装包、结果路径）")
    work = root / "A15 资料"
    doc = write(work / "提交目标.md", "# 提交目标\n\n正文\n")
    workspace, store, trust_file, packages, _ = sample_store(root, "A15 工作区", ("sample-image",))
    try:
        task = store.tasks.submit("mdreader.sample-image:insert.image", options={"synthetic": True},
                                  doc=str(doc), revision=D.revision(str(doc)), wait=30)
        stale = ""
        try:
            store.tasks.result(task.id, now_revision="改过的修订")
        except P.StaleResult as exc:
            stale = str(exc)
        check("A15", "正文改过之后，旧任务的结果被拒绝提交", bool(stale), stale[:50])
        cross = ""
        try:
            store.tasks.commit_assets(task.id, str(work / "assets"),
                                      now_doc=str(work / "另一篇.md"))
        except P.StaleResult as exc:
            cross = str(exc)
        check("A15", "插到别的文档上会被拒绝（不串文档）", bool(cross), cross[:50])

        outside = store.tasks.submit("mdreader.sample-image:insert.image",
                                     options={"synthetic": True, "escape": True}, wait=30)
        view = store.tasks.poll(outside.id)
        check("A15", "插件声明的产物在工作目录之外时被拒绝",
              view["state"] == "failed" and "工作目录之外" in (view.get("error") or ""),
              (view.get("error") or "")[:50])

        evil = packages / "traversal.zip"
        with zipfile.ZipFile(str(evil), "w") as archive:
            archive.writestr("manifest.json", json.dumps(
                {"id": "mdreader.evil", "name": "越界", "version": "1.0.0", "api_version": "1",
                 "entrypoint": "plugin.py", "capabilities": ["editor.image_insert"],
                 "commands": [{"id": "i", "title": "插入", "capability": "editor.image_insert",
                               "method": "insert_image"}]}, ensure_ascii=False))
            archive.writestr("../evil.py", "print(1)")
        traversed = ""
        try:
            store.install(str(evil), allow_unverified=True)
        except P.PackageError as exc:
            traversed = str(exc)
        check("A15", "含越界路径的安装包被拒绝", "越界" in traversed, traversed[:50])

        write(work / "另一篇.md", "# 另一篇\n\n别的正文\n")
        check("A15", "被拒绝的提交没有改动任何文档",
              doc.read_text(encoding="utf-8") == "# 提交目标\n\n正文\n"
              and (work / "另一篇.md").read_text(encoding="utf-8") == "# 另一篇\n\n别的正文\n"
              and not (work / "assets").exists())
    finally:
        cleanup(workspace)


ROWS = (("A01", row_a01), ("A02", row_a02), ("A03", row_a03), ("A04", row_a04),
        ("A05", row_a05), ("A06", row_a06), ("A07", row_a07), ("A08", row_a08),
        ("A09", row_a09), ("A10", row_a10), ("A11", row_a11), ("A12", row_a12),
        ("A13", row_a13), ("A14", row_a14), ("A15", row_a15))


def write_report(label="") -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    suffix = ("-" + label) if label else ""
    target = ROOT / "docs" / ("acceptance-matrix-%s%s-%s.md"
                              % (core.APP_VERSION, suffix, datetime.date.today().isoformat()))
    lines = ["# A01–A15 关键验收矩阵执行记录 %s（%s）" % (core.APP_VERSION, stamp), "",
             "由 `scripts/acceptance_matrix.py --write` 生成，逐条对应 `docs/NEXT_AUTHORING.md` §9 的",
             "关键验收矩阵。只包含能自动判定的部分；需要人眼确认的行以 SKIP 列出并写明原因，",
             "SKIP 不算通过。功能小节的完整自动项见同目录的 `acceptance-<版本>-<日期>.md`。", "",
             "## 环境", ""]
    lines += ["- %s：%s" % (name, value) for name, value in A.ENVIRONMENT]
    current = None
    for group, name, status, detail in A.RESULTS:
        if group != current:
            lines += ["", "## %s" % group, ""]
            current = group
        lines.append("- [%s] %s%s" % ("x" if status == "PASS" else (" " if status == "FAIL" else "-"),
                                      name, ("（%s）" % detail) if detail else ""))
    lines += ["", "## 人工确认来源", "",
              "本矩阵里 SKIP 的人工项与两项历史缺陷（E06/E07）都汇总在",
              "`docs/acceptance-manual-notes.md`；请在那一份文件里写结论，不要改这份自动记录。", ""]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description="MDReader A01–A15 关键验收矩阵")
    parser.add_argument("--write", action="store_true", help="写入 docs/acceptance-matrix-<版本>-<日期>.md")
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--root", default="", help="改用另一份代码（安装目录或解压后的分发包）")
    parser.add_argument("--label", default="", help="写入记录时的后缀，例如 installed / package")
    parser.add_argument("--only", default="", help="只跑某几条，例如 A05,A08")
    args = parser.parse_args(ORIGINAL_ARGV if argv is None else argv)

    wanted = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    temporary = tempfile.TemporaryDirectory(prefix="mdreader-amatrix-")
    root = Path(temporary.name)
    win = A.window(root / "workspace", visible=True)
    code = 0
    try:
        A.environment_section(win)
        for name, row in ROWS:
            if wanted and name not in wanted:
                continue
            row(root)
        code = A.summary(started)
        if args.write:
            print("\n矩阵记录已写入：%s" % write_report(args.label))
    finally:
        A.close(win)
        if args.keep:
            print("\n临时工作区保留在：%s" % root)
        else:
            temporary.cleanup()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
