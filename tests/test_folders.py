# -*- coding: utf-8 -*-
"""F01：直接打开用户的文件夹、在原目录新建 Markdown、外部增删自动刷新。

    python -m unittest tests.test_folders

只用临时目录：真实用户资料不参与任何创建或删除测试。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
import unittest.mock as mock

from mdreader import core
from mdreader import folders as F
from mdreader import winui


class FolderRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-folders-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "app"))
        self.user = os.path.join(self.base, "资料")
        os.makedirs(os.path.join(self.user, "子目录"), exist_ok=True)
        self.write("笔记.md", "# 笔记\n")
        self.write("子目录/深层.markdown", "# 深层\n")
        self.write("说明.txt", "纯文本\n")
        self.write("图片.png", "not an image\n")
        self.write(".hidden.md", "# 隐藏\n")
        os.makedirs(os.path.join(self.user, ".git"), exist_ok=True)
        self.write(".git/内部.md", "# 不该出现\n")

    def tearDown(self):
        self.ws.folders.watch().stop()

    def write(self, rel, text="内容\n"):
        path = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    # -- 登记 ------------------------------------------------------------
    def test_opening_a_folder_registers_it_and_lists_the_markdown_tree(self):
        info = self.ws.folders.add(self.user)
        self.assertTrue(info["id"].startswith("root-"))
        tree = self.ws.folders.scan(info["id"], force=True)
        ids = [d["id"] for d in tree["docs"]]
        self.assertEqual(set(ids), {"笔记.md", "说明.txt", "子目录/深层.markdown", ".hidden.md", ".git/内部.md"})
        self.assertIn("图片.png", [d["id"] for d in tree["files"]], "所有类型的文件都应显示")
        self.assertIn(".hidden.md", ids)
        self.assertIn(".git/内部.md", ids)
        self.assertEqual(tree["status"], "ok")
        self.assertEqual([d["id"] for d in tree["dirs"]], [".git", "子目录"])

    def test_registration_lives_in_the_app_workspace_not_the_user_folder(self):
        self.ws.folders.add(self.user)
        self.assertTrue(os.path.isfile(os.path.join(self.ws.root, "folders.json")))
        self.assertFalse(os.path.exists(os.path.join(self.user, "folders.json")))
        self.assertFalse(os.path.exists(os.path.join(self.user, ".mdreader")),
                         "不得在用户资料目录里悄悄建立管理配置")
        with open(os.path.join(self.ws.root, "folders.json"), encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored[0]["path"], os.path.abspath(self.user))

    def test_the_same_folder_opened_twice_is_one_entry(self):
        first = self.ws.folders.add(self.user)
        second = self.ws.folders.add(self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.ws.folders.roots()), 1)
        # 大小写不同的同一个路径也算同一个根目录
        self.ws.folders.add(os.path.abspath(self.user).upper())
        self.assertEqual(len(self.ws.folders.roots()), 1)

    def test_removing_a_root_never_deletes_the_folder_on_disk(self):
        info = self.ws.folders.add(self.user)
        self.assertTrue(self.ws.folders.forget(info["id"]))
        self.assertEqual(self.ws.folders.roots(), [])
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")),
                        "「从列表移除」只移除应用记录")
        with self.assertRaises(KeyError):
            self.ws.folders.require(info["id"])

    def test_identity_is_root_plus_relative_path(self):
        other = os.path.join(self.base, "另一份资料")
        os.makedirs(other, exist_ok=True)
        with open(os.path.join(other, "笔记.md"), "w", encoding="utf-8") as handle:
            handle.write("# 另一个笔记\n")
        one = self.ws.folders.add(self.user)
        two = self.ws.folders.add(other)
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual(self.ws.folders.doc_info(one["id"], "笔记.md")["id"], "笔记.md")
        self.assertEqual(self.ws.folders.doc_info(two["id"], "笔记.md")["id"], "笔记.md")
        self.assertNotEqual(self.ws.folders.doc_info(one["id"], "笔记.md")["_abs"],
                            self.ws.folders.doc_info(two["id"], "笔记.md")["_abs"])

    def test_an_unreadable_root_is_reported_without_clearing_the_tree(self):
        info = self.ws.folders.add(self.user)
        self.assertTrue(self.ws.folders.scan(info["id"], force=True)["docs"])
        moved = os.path.join(self.base, "被移走的资料")
        os.rename(self.user, moved)
        roots = self.ws.folders.roots()
        self.assertFalse(roots[0]["exists"])
        self.assertIn("不在了", roots[0]["problem"])
        with self.assertRaises(FileNotFoundError):
            self.ws.folders.scan(info["id"])
        # 恢复后树照旧，不把“暂时读不到”当成“文件全被删了”
        os.rename(moved, self.user)
        tree = self.ws.folders.scan(info["id"], force=True)
        self.assertEqual(tree["status"], "ok")
        self.assertTrue(tree["docs"])

    def test_links_that_leave_the_root_are_skipped(self):
        outside = os.path.join(self.base, "外部目录")
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, "外面的.md"), "w", encoding="utf-8") as handle:
            handle.write("# 外面\n")
        link = os.path.join(self.user, "链接目录")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("当前环境不允许建立符号链接")
        info = self.ws.folders.add(self.user)
        tree = self.ws.folders.scan(info["id"], force=True)
        self.assertNotIn("链接目录/外面的.md", [d["id"] for d in tree["docs"]],
                         "越出根目录的链接不得进入树")
        self.assertIn("链接目录", tree["skipped_links"])

    # -- 新建 ------------------------------------------------------------
    def test_new_markdown_is_created_in_the_original_folder(self):
        info = self.ws.folders.add(self.user)
        made = self.ws.folders.create_doc(info["id"], "会议记录")
        self.assertEqual(made["id"], "会议记录.md")
        self.assertTrue(os.path.isfile(os.path.join(self.user, "会议记录.md")))
        self.assertEqual(self.ws.folders.read_doc(info["id"], "会议记录.md"), "")
        # 新建后立刻能在树里看到，不需要手动刷新
        self.assertIn("会议记录.md",
                      [d["id"] for d in self.ws.folders.scan(info["id"])["docs"]])

    def test_new_markdown_keeps_the_extension_and_can_go_into_a_subdirectory(self):
        info = self.ws.folders.add(self.user)
        self.assertEqual(self.ws.folders.create_doc(info["id"], "带扩展名.markdown")["id"],
                         "带扩展名.markdown")
        self.assertEqual(self.ws.folders.create_doc(info["id"], "中文 空格")["id"], "中文 空格.md")
        nested = self.ws.folders.create_doc(info["id"], "深层新建", subdir="子目录")
        self.assertEqual(nested["id"], "子目录/深层新建.md")
        self.assertTrue(os.path.isfile(os.path.join(self.user, "子目录", "深层新建.md")))

    def test_the_plus_in_my_folders_counts_up_instead_of_refusing(self):
        """原文件夹树的「＋」用 unique=True：连点几次得到 Untitled-2/-3，不覆盖。"""
        info = self.ws.folders.add(self.user)
        first = self.ws.folders.create_doc(info["id"], "Untitled", subdir="子目录", unique=True)
        second = self.ws.folders.create_doc(info["id"], "Untitled", subdir="子目录", unique=True)
        third = self.ws.folders.create_doc(info["id"], "Untitled", subdir="子目录", unique=True)
        self.assertEqual([first["id"], second["id"], third["id"]],
                         ["子目录/Untitled.md", "子目录/Untitled-2.md", "子目录/Untitled-3.md"])

    def test_creating_without_unique_still_refuses_an_existing_name(self):
        info = self.ws.folders.add(self.user)
        for taken in ("笔记", "笔记.md"):
            with self.subTest(name=taken):
                with self.assertRaises(FileExistsError):
                    self.ws.folders.create_doc(info["id"], taken)

    def test_names_that_windows_cannot_use_are_refused_with_a_reason(self):
        info = self.ws.folders.add(self.user)
        for bad, hint in (("", "填写"), ("   ", "填写"), ("nul", "保留"),
                          ("CON.md", "保留"), ("lpt3", "保留"), ("尾巴 .md", "空格或句点"),
                          ("点结尾.", "空格或句点"), ("问号?", "不能包含"), ("a:b", "不能包含"),
                          ("星号*.md", "不能包含")):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError) as caught:
                    self.ws.folders.create_doc(info["id"], bad)
                self.assertIn(hint, str(caught.exception))

    def test_an_existing_name_is_refused_instead_of_overwritten(self):
        info = self.ws.folders.add(self.user)
        for taken in ("笔记", "笔记.md", "笔记.MD"):
            with self.subTest(name=taken):
                with self.assertRaises(FileExistsError):
                    self.ws.folders.create_doc(info["id"], taken)
        with open(os.path.join(self.user, "笔记.md"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# 笔记\n")

    def test_creation_refuses_a_target_that_appears_between_check_and_create(self):
        """检查通过、创建那一刻文件已在，也必须拒绝而不是覆盖。

        直接让“存在性检查”漏报（模拟检查与创建之间的竞争窗口），此时只有
        ``O_EXCL`` 独占创建还能拦住覆盖——去掉独占语义这条用例必须失败。
        """
        info = self.ws.folders.add(self.user)
        target = os.path.join(self.user, "抢跑.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("别人写的\n")
        with mock.patch.object(F, "_already_exists", lambda parent, filename: False):
            with self.assertRaises(FileExistsError):
                self.ws.folders.create_doc(info["id"], "抢跑")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "别人写的\n", "竞争时不得覆盖别人的文件")

    def test_creation_and_reads_cannot_leave_the_authorized_root(self):
        info = self.ws.folders.add(self.user)
        with self.assertRaises(ValueError):
            self.ws.folders.create_doc(info["id"], "越界.md", subdir="..")
        with self.assertRaises(ValueError):
            self.ws.folders.create_doc(info["id"], "越界.md", subdir=os.path.join("..", "外部"))
        with self.assertRaises(ValueError):
            self.ws.folders.doc_info(info["id"], "../外部.md")
        with self.assertRaises(KeyError):
            self.ws.folders.create_doc("root-00000000", "无授权.md")

    def test_creating_in_a_missing_subdirectory_is_refused(self):
        info = self.ws.folders.add(self.user)
        with self.assertRaises(FileNotFoundError):
            self.ws.folders.create_doc(info["id"], "没地方放.md", subdir="不存在的目录")

    def test_case_insensitive_extensions_are_part_of_the_tree(self):
        self.write("大写.MD", "# 大写\n")
        self.write("混合.MarkDown", "# 混合\n")
        info = self.ws.folders.add(self.user)
        ids = [d["id"] for d in self.ws.folders.scan(info["id"], force=True)["docs"]]
        self.assertIn("大写.MD", ids)
        self.assertIn("混合.MarkDown", ids)


class FolderWatchTests(unittest.TestCase):
    """外部增删要在两秒内被监听发现；扫描期间不冻结调用方。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-watch-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "app"))
        self.user = os.path.join(self.base, "资料")
        os.makedirs(os.path.join(self.user, "深层", "更深"), exist_ok=True)
        with open(os.path.join(self.user, "笔记.md"), "w", encoding="utf-8") as handle:
            handle.write("# 笔记\n")
        self.info = self.ws.folders.add(self.user)
        self.watch = self.ws.folders.watch()

    def tearDown(self):
        self.watch.stop()

    def test_an_external_add_is_reported_within_the_target_delay(self):
        seen = []
        done = threading.Event()

        def listener(rid):
            seen.append(rid)
            done.set()

        self.watch.add_listener(listener)
        self.watch.configure(0.2)
        self.watch.poll_once()                 # 建立基线
        self.watch.start()
        started = time.monotonic()
        with open(os.path.join(self.user, "外部新增.md"), "w", encoding="utf-8") as handle:
            handle.write("# 外部新增\n")
        self.assertTrue(done.wait(2.0), "外部新增没有在 2 秒内被发现")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(seen, [self.info["id"]])

    def test_a_change_inside_a_subdirectory_is_noticed(self):
        self.watch.poll_once()
        deep = os.path.join(self.user, "深层", "更深", "里面.md")
        with open(deep, "w", encoding="utf-8") as handle:
            handle.write("# 里面\n")
        changed = self.watch.poll_once()
        self.assertEqual(changed, [self.info["id"]])
        tree = self.ws.folders.scan(self.info["id"])
        self.assertIn("深层/更深/里面.md", [d["id"] for d in tree["docs"]])

    def test_editing_an_existing_file_changes_its_signature(self):
        self.watch.poll_once()
        time.sleep(0.02)
        with open(os.path.join(self.user, "笔记.md"), "a", encoding="utf-8") as handle:
            handle.write("追加一行\n")
        self.assertEqual(self.watch.poll_once(), [self.info["id"]])

    def test_the_baseline_scan_does_not_report_the_existing_content_as_new(self):
        self.watch.poll_once()                       # 第一圈建立基线
        self.assertEqual(self.watch.poll_once(), [], "没有变化就不该反复通知界面")

    def test_watch_stops_when_the_folder_is_removed_from_the_list(self):
        self.watch.start()
        self.assertTrue(self.watch.stats()["running"])
        self.ws.folders.forget(self.info["id"])
        time.sleep(0.05)
        self.assertEqual(self.watch.stats()["watched"], 0)
        self.watch.stop()
        self.assertFalse(self.watch.stats()["running"])


class FolderApiTests(unittest.TestCase):
    """两个入口共用同一套规则：服务端也校验根目录与命令。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-folderapi-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "app"))
        self.user = os.path.join(self.base, "资料")
        os.makedirs(self.user, exist_ok=True)
        with open(os.path.join(self.user, "正文.md"), "w", encoding="utf-8") as handle:
            handle.write("# 标题\n\n![图](图片.png)\n\n[另一篇](另一篇.md)\n")
        with open(os.path.join(self.user, "另一篇.md"), "w", encoding="utf-8") as handle:
            handle.write("# 另一篇\n")
        with open(os.path.join(self.user, "图片.png"), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.api = core.Api(self.ws, os.path.join(self.base, "webui"))
        self.rid = self.api.post("/api/folder/open", {"path": self.user})["folder"]["id"]

    def tearDown(self):
        self.ws.folders.watch().stop()

    def test_state_lists_registered_roots_for_both_entries(self):
        state = self.api.get("/api/state", {})
        self.assertEqual([r["id"] for r in state["root_folders"]], [self.rid])

    def test_folder_endpoint_returns_the_tree_without_absolute_paths(self):
        data = self.api.get("/api/folder", {"root": self.rid})
        self.assertEqual([d["id"] for d in data["docs"]], ["另一篇.md", "正文.md"])
        for doc in data["docs"]:
            self.assertNotIn("_abs", doc, "界面拿到的树里不带服务器绝对路径字段")
        self.assertEqual(data["status"], "ok")

    def test_document_endpoint_renders_and_points_resources_at_the_api(self):
        data = self.api.get("/api/folder/doc", {"root": self.rid, "doc": "正文.md"})
        self.assertEqual(data["kind"], "folder")
        # 没有 front matter 时用文件名，不用正文里的一级标题
        self.assertEqual(data["title"], "正文")
        self.assertIn("/api/localfile?root=", data["html"])
        self.assertIn(urllib.parse.quote("图片.png"), data["html"], "相对路径按根目录解析")
        self.assertIn("data-folder-doc", data["html"], "同级文档链接指向授权接口")
        # 网页端删除要带着“我看到的那一版修订”，所以渲染结果必须给出它
        self.assertEqual(data["info"]["revision"], core.D.revision(os.path.join(self.user, "正文.md")))

    def test_front_matter_title_wins_when_it_is_present(self):
        with open(os.path.join(self.user, "带前置.md"), "w", encoding="utf-8") as handle:
            handle.write("---\ntitle: 前置标题\n---\n\n# 正文标题\n")
        data = self.api.get("/api/folder/doc", {"root": self.rid, "doc": "带前置.md"})
        self.assertEqual(data["title"], "前置标题")

    def test_images_and_documents_outside_the_root_are_not_served(self):
        outside = os.path.join(self.base, "外面.png")
        with open(outside, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(ValueError):
            self.api.get("/api/localfile", {"root": self.rid, "p": "../外面.png"})
        with self.assertRaises(ValueError):
            self.api.get("/api/folder/doc", {"root": self.rid, "doc": "../外面.md"})

    def test_an_unregistered_root_is_refused(self):
        with self.assertRaises(KeyError):
            self.api.get("/api/folder", {"root": "root-deadbeef"})
        with self.assertRaises(KeyError):
            self.api.post("/api/folder/create", {"root": "root-deadbeef", "name": "偷偷新建"})

    def test_create_through_the_api_reports_the_real_path_and_never_overwrites(self):
        made = self.api.post("/api/folder/create", {"root": self.rid, "name": "新的一篇"})
        self.assertEqual(made["doc"]["id"], "新的一篇.md")
        self.assertEqual(made["path"], os.path.join(self.user, "新的一篇.md"))
        with self.assertRaises(FileExistsError):
            self.api.post("/api/folder/create", {"root": self.rid, "name": "新的一篇"})

    def test_remove_through_the_api_keeps_every_file(self):
        self.api.post("/api/folder/remove", {"root": self.rid})
        self.assertEqual(self.api.get("/api/state", {})["root_folders"], [])
        self.assertTrue(os.path.isfile(os.path.join(self.user, "正文.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "图片.png")))


class FolderDesktopTests(unittest.TestCase):
    """桌面窗口：打开原目录 → 新建 Markdown → 外部增删自动刷新。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-folderwin-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.user = os.path.join(self.base, "我的资料")
        os.makedirs(os.path.join(self.user, "子目录"), exist_ok=True)
        self.write("笔记.md", "# 笔记\n\n第一行\n")
        self.write("子目录/深入.md", "# 深入\n")
        self._dialog = mock.patch.object(core, "_dialog_folder", side_effect=self.dialog_result)
        self._dialog.start()
        self.addCleanup(self._dialog.stop)
        self._drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        self._drop.start()
        self.addCleanup(self._drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.base, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)

    def dialog_result(self):
        """What the picker returns. Subclasses can route this through PowerShell."""
        return [self.user]

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def tearDown(self):
        try:
            self.win.ws.folders.watch().stop()
        except Exception:
            pass

    def write(self, rel, text="内容\n"):
        path = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def tree_labels(self):
        out = []

        def walk(parent):
            for iid in self.win.root_tree.get_children(parent):
                out.append((iid, self.win.root_tree.item(iid, "text")))
                walk(iid)

        walk("")
        return out

    def force_rescan(self):
        """让下一眼真的重新走一遍目录（等价于轮询间隔已过）。"""
        self.win.ws.folders._cache.clear()
        self.win._folder_signature = None

    def tick(self, dirty=False):
        """走一次真正的轮询回调（计时器句柄由窗口创建）。

        ``dirty=True`` 模拟后台监听线程发现变化后置的标记；界面只在主线程上刷新。
        先清掉管理器的缓存节流，等价于「轮询间隔已经过去」。
        """
        self.win.ws.folders._cache.clear()
        self.win._folder_tick = "test"
        self.win._folders_dirty = dirty
        self.win.folder_watch_tick()

    def test_the_watcher_only_signals_and_never_touches_widgets(self):
        """后台线程只置标记；任何控件操作都必须发生在 Tk 主线程上。"""
        w = self.win
        w.open_user_folder()
        calls = []
        original = w.refresh_folder_tree
        w.refresh_folder_tree = lambda force=False: calls.append(force)
        try:
            w._mark_folders_dirty("root-x")          # 相当于监听线程的回调
            self.assertEqual(calls, [], "监听回调本身不得刷新界面")
            self.assertTrue(w._folders_dirty)
            self.tick(dirty=True)
            self.assertEqual(calls, [False], "刷新发生在主线程的轮询里")
            self.assertFalse(w._folders_dirty)
        finally:
            w.refresh_folder_tree = original

    def test_opening_a_folder_shows_its_markdown_tree(self):
        w = self.win
        self.assertEqual(w.roots, [])
        w.open_user_folder()
        self.assertEqual(len(w.roots), 1)
        self.assertEqual(w.cur_root, w.roots[0]["id"])
        self.assertEqual([d["id"] for d in w.root_docs], ["笔记.md", "子目录/深入.md"])
        labels = [text for _iid, text in self.tree_labels()]
        iids = [iid for iid, _text in self.tree_labels()]
        self.assertIn("📄 笔记.md", labels)
        self.assertIn("📁 子目录", labels)
        self.assertIn("f:笔记.md", iids)
        self.assertIn("f:子目录/深入.md", iids)
        self.assertIn(os.path.join(self.user), w.lbl_root.cget("text"))
        self.assertIn("2 个文件", w.lbl_root.cget("text"))

    def test_opening_a_file_from_the_tree_uses_the_original_path(self):
        w = self.win
        w.open_user_folder()
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        self.assertEqual(len(w.tabs), 1)
        self.assertEqual(os.path.normcase(w.cur_loose["path"]),
                         os.path.normcase(os.path.join(self.user, "笔记.md")))
        self.assertIn("第一行", w.get_text())

    def test_an_external_change_appears_in_the_tree_through_the_watch_tick(self):
        w = self.win
        w.open_user_folder()
        self.write("外部新增.md", "# 外部新增\n")
        self.tick(dirty=True)
        self.assertIn("外部新增.md", [d["id"] for d in w.root_docs])
        self.assertIn("f:外部新增.md", [iid for iid, _text in self.tree_labels()])

    def test_an_external_delete_is_reflected_and_the_reading_tab_survives(self):
        w = self.win
        w.open_user_folder()
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        os.remove(os.path.join(self.user, "笔记.md"))
        self.tick(dirty=True)
        self.assertNotIn("笔记.md", [d["id"] for d in w.root_docs])
        self.assertEqual(len(w.tabs), 1, "已打开标签保留，未保存内容不因外部删除而丢失")

    def test_new_markdown_is_created_in_the_original_directory_and_opened(self):
        w = self.win
        w.open_user_folder()
        w.root_tree.selection_set("f:笔记.md")
        with mock.patch("tkinter.simpledialog.askstring", return_value="会议记录"):
            w.new_folder_doc()
        target = os.path.join(self.user, "会议记录.md")
        self.assertTrue(os.path.isfile(target), "新建文件必须落在用户选的目录里")
        self.assertIn("会议记录.md", [d["id"] for d in w.root_docs])
        self.assertEqual(os.path.normcase(w.cur_loose["path"]), os.path.normcase(target))

    def test_new_markdown_can_go_into_the_selected_subdirectory(self):
        w = self.win
        w.open_user_folder()
        w.root_tree.selection_set("r:子目录")
        with mock.patch("tkinter.simpledialog.askstring", return_value="子目录新建"):
            w.new_folder_doc()
        self.assertTrue(os.path.isfile(os.path.join(self.user, "子目录", "子目录新建.md")))

    def test_a_name_that_already_exists_is_reported_and_nothing_is_overwritten(self):
        w = self.win
        w.open_user_folder()
        with mock.patch("tkinter.simpledialog.askstring", return_value="笔记"):
            w.new_folder_doc()
        with open(os.path.join(self.user, "笔记.md"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# 笔记\n\n第一行\n")
        self.assertIn("同名", w.lbl_status.cget("text"))

    def test_removing_the_folder_from_the_list_keeps_every_file(self):
        w = self.win
        w.open_user_folder()
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            w.remove_current_root()
        self.assertEqual(w.roots, [])
        self.assertEqual(w.root_docs, [])
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "子目录", "深入.md")))

    def test_declining_the_removal_keeps_the_registration(self):
        w = self.win
        w.open_user_folder()
        with mock.patch("tkinter.messagebox.askyesno", return_value=False):
            w.remove_current_root()
        self.assertEqual(len(w.roots), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")))

    def test_an_unreachable_folder_is_reported_without_clearing_the_tree(self):
        w = self.win
        w.open_user_folder()
        moved = os.path.join(self.base, "被移走")
        os.rename(self.user, moved)
        try:
            w._folder_signature = None
            w.refresh_folder_tree(force=True)
            self.assertIn("读不到", w.lbl_root.cget("text"))
        finally:
            os.rename(moved, self.user)
        w._folder_signature = None
        w.refresh_folder_tree(force=True)
        self.assertIn("2 个文件", w.lbl_root.cget("text"))
        self.assertEqual([d["id"] for d in w.root_docs], ["笔记.md", "子目录/深入.md"])

    def test_renaming_updates_the_tree_and_the_open_tab_identity(self):
        w = self.win
        w.open_user_folder()
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        with mock.patch("tkinter.simpledialog.askstring", return_value="改过的名字"):
            w.rename_folder_doc("笔记.md")
        self.assertFalse(os.path.exists(os.path.join(self.user, "笔记.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "改过的名字.md")))
        self.assertIn("改过的名字.md", [d["id"] for d in w.root_docs])
        self.assertEqual(os.path.normcase(w.cur_loose["path"]),
                         os.path.normcase(os.path.join(self.user, "改过的名字.md")),
                         "标签要跟着换身份，否则保存会写回旧路径")

    def test_the_folder_tick_does_not_redraw_an_unchanged_tree(self):
        w = self.win
        w.open_user_folder()
        baseline = w._folder_signature
        self.tick()
        self.assertEqual(w._folder_signature, baseline)
        self.assertIsNotNone(baseline)

    def test_a_folder_registered_by_the_other_entry_shows_up_here(self):
        """(网页) 那一端新开的文件夹，桌面端在下一个轮询周期也要看到。"""
        w = self.win
        self.assertEqual(w.roots, [])
        second = os.path.join(self.base, "网页开的")
        os.makedirs(second, exist_ok=True)
        with open(os.path.join(second, "另一份.md"), "w", encoding="utf-8") as handle:
            handle.write("# 另一份\n")
        w.ws.folders.add(second)          # 相当于另一个入口完成了登记
        self.tick()
        self.assertEqual(len(w.roots), 1)
        self.assertIn("另一份.md", [d["id"] for d in w.root_docs])

    def test_a_folder_removed_by_the_other_entry_disappears_here(self):
        w = self.win
        w.open_user_folder()
        w.ws.folders.forget(w.cur_root)
        self.tick()
        self.assertEqual(w.roots, [])
        self.assertEqual(w.root_docs, [])
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")),
                        "另一入口移除登记也不得删除磁盘文件")


class FolderDialogEncodingTests(unittest.TestCase):
    """回归：文件夹选择框回传的路径必须完好地穿过 PowerShell 这一跳。

    维护者在真实窗口里打开桌面上的中文目录时，状态栏回的是
    「无法打开这个文件夹：找不到文件夹：C:\\Users\\<乱码>\\Desktop\\<乱码>」。
    原因不是权限也不是路径错：Windows PowerShell 5.1 把**重定向的**标准输出按本机
    ANSI 代码页（中文系统是 GBK）写，而核心原先一律按 UTF-8 解。中文目录名于是
    变成 U+FFFD 乱码，磁盘上当然没有这个目录——「没有要求」的文件夹也打不开。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-dialog-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.user = os.path.join(self.base, "用户目录", "桌面 资料")
        os.makedirs(os.path.join(self.user, "子目录"), exist_ok=True)
        self.write("笔记.md", "# 笔记\n")
        self.write("子目录/深入.md", "# 深入\n")

    def write(self, rel, text="内容\n"):
        path = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    @staticmethod
    def has_powershell():
        return bool(shutil.which("powershell.exe") or shutil.which("pwsh"))

    def test_utf8_and_local_code_page_results_both_decode_without_mojibake(self):
        sample = "C:\\Users\\简\\Desktop\\笔记"
        self.assertEqual(core._decode_ps_output(sample.encode("utf-8")), sample)
        self.assertEqual(core._decode_ps_output(b"\xef\xbb\xbf" + sample.encode("utf-8")), sample)
        if os.name == "nt":
            # 旧实现只认 UTF-8 且带 errors="replace"：ANSI 字节会变成 U+FFFD
            try:
                ansi = sample.encode("mbcs")
            except UnicodeEncodeError:
                ansi = None                 # 本机 ANSI 代码页装不下这些字，无从验证
            if ansi is not None:
                decoded = core._decode_ps_output(ansi)
                self.assertEqual(decoded, sample)
                self.assertNotIn("\ufffd", decoded)

    def test_the_shell_round_trip_keeps_a_chinese_path_intact(self):
        if not self.has_powershell():
            self.skipTest("本机没有 PowerShell，无法验证对话框回传路径")
        out = core._run_ps("'%s';" % self.user.replace("'", "''"))
        self.assertEqual(out, [self.user], "回传的路径必须与选中目录逐字相同")
        self.assertTrue(os.path.isdir(out[0]), "解出来的字符串必须是磁盘上真实存在的目录")

    def test_a_folder_registered_from_the_shell_result_is_scanned_and_listed(self):
        if not self.has_powershell():
            self.skipTest("本机没有 PowerShell，无法验证对话框回传路径")
        ws = core.Workspace(os.path.join(self.base, "workspace"))
        self.addCleanup(ws.folders.watch().stop)
        paths = core._run_ps("'%s';" % self.user.replace("'", "''"))
        self.assertTrue(paths, "外壳没有回传路径")
        info = ws.folders.add(paths[0])      # 这一行就是旧的失败点
        self.assertEqual(info["path"], os.path.abspath(self.user))
        tree = ws.folders.scan(info["id"], force=True)
        self.assertEqual([d["id"] for d in tree["files"]], ["笔记.md", "子目录/深入.md"])


class FolderDialogRoundTripTests(FolderDesktopTests):
    """维护者那条路径的端到端回归：真实 PowerShell 回传 → 登记 → 树。

    ``_dialog_folder`` 换成真的起一次 PowerShell 把路径打回来，与用户点「打开文件夹」
    在系统对话框里选中同一个目录只差一次点击。
    """

    def dialog_result(self):
        return core._run_ps("'%s';" % self.user.replace("'", "''"))

    def test_opening_a_chinese_folder_displays_its_files_with_folders_collapsed(self):
        if not FolderDialogEncodingTests.has_powershell():
            self.skipTest("本机没有 PowerShell，无法端到端验证对话框回传路径")
        w = self.win
        w.open_user_folder()
        self.assertEqual([r["path"] for r in w.roots], [os.path.abspath(self.user)])
        self.assertTrue(w.cur_root, "打开后应选中这个根目录")
        self.assertEqual([d["id"] for d in w.root_docs], ["笔记.md", "子目录/深入.md"])
        labels = [text for _iid, text in self.tree_labels()]
        self.assertIn("📄 笔记.md", labels)
        self.assertIn("📁 子目录", labels)
        self.assertIn(os.path.join(self.user), w.lbl_root.cget("text"))
        for iid, _text in self.tree_labels():
            if iid.startswith("r:"):
                self.assertFalse(w.root_tree.item(iid, "open"), "打开文件夹时一律先收起")
        # 选中的文件夹就是新建目标；没有选中文件夹时落在根目录
        w.root_tree.selection_set("r:子目录")
        self.assertEqual(w._selected_root_dir(), "子目录")
        with mock.patch("tkinter.simpledialog.askstring", return_value="子目录新建"):
            w.new_folder_doc()
        self.assertTrue(os.path.isfile(os.path.join(self.user, "子目录", "子目录新建.md")))
        w.root_tree.selection_remove(*w.root_tree.selection())
        with mock.patch("tkinter.simpledialog.askstring", return_value="根目录新建"):
            w.new_folder_doc()
        self.assertTrue(os.path.isfile(os.path.join(self.user, "根目录新建.md")))


class UntitledPlusTests(unittest.TestCase):
    """目录行末尾的「＋」：直接在该目录生成 Untitled.md，不问名称。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-plus-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "workspace"))
        self.pid = self.ws.create_project("项目", with_readme=False)["id"]
        self.pdir = self.ws.require_project(self.pid)
        os.makedirs(os.path.join(self.pdir, "docs"), exist_ok=True)

    def test_the_plus_creates_an_untitled_document_in_that_directory(self):
        made = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        self.assertEqual(made["id"], "docs/Untitled.md")
        self.assertTrue(os.path.isfile(os.path.join(self.pdir, "docs", "Untitled.md")))
        self.assertIn("Untitled", self.ws.read_doc(self.pdir, made["id"]))

    def test_repeated_clicks_never_overwrite_an_earlier_draft(self):
        first = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        second = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        third = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        self.assertEqual([first["id"], second["id"], third["id"]],
                         ["docs/Untitled.md", "docs/Untitled-2.md", "docs/Untitled-3.md"])
        self.assertEqual(len(os.listdir(os.path.join(self.pdir, "docs"))), 3)

    def test_creation_is_exclusive_even_if_the_directory_listing_is_stale(self):
        """树上的目录内容可能过期；重名判断必须以真正创建的结果为准。"""
        target = os.path.join(self.pdir, "docs", "Untitled.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("别人写的\n")
        made = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        self.assertEqual(made["id"], "docs/Untitled-2.md")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "别人写的\n", "不得覆盖已存在的文件")

    def test_a_run_of_existing_names_keeps_counting(self):
        for name in ("Untitled.md", "Untitled-2.md", "Untitled-4.md"):
            with open(os.path.join(self.pdir, "docs", name), "w", encoding="utf-8") as handle:
                handle.write("x\n")
        made = self.ws.create_doc(self.pdir, "Untitled", subdir="docs")
        self.assertEqual(made["id"], "docs/Untitled-3.md")


class UntitledPlusWindowTests(unittest.TestCase):
    """桌面窗口：点项目树里目录行的「＋」就在那个目录新建并打开。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-pluswin-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self._drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        self._drop.start()
        self.addCleanup(self._drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.base, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)
        self.pid = self.win.ws.create_project("项目", with_readme=False)["id"]
        self.pdir = self.win.ws.require_project(self.pid)
        os.makedirs(os.path.join(self.pdir, "docs"), exist_ok=True)
        self.win.cur_pid = self.pid
        self.win.load_project(self.pid, keep_doc=False)

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def folder_row(self, subdir):
        for iid in self.win.tree.get_children(""):
            if self.win._project_nodes.get(iid) == subdir:
                return iid
        self.fail("找不到目录行：%s" % subdir)

    def test_the_folder_row_shows_a_plus(self):
        row = self.folder_row("docs")
        self.assertIn("＋", self.win.tree.item(row, "text"))
        self.assertIn("plus", self.win.tree.item(row, "tags"))

    def test_clicking_the_plus_creates_and_opens_untitled(self):
        w = self.win
        w.tree.focus(self.folder_row("docs"))
        w.on_plus_click()
        target = os.path.join(self.pdir, "docs", "Untitled.md")
        self.assertTrue(os.path.isfile(target))
        self.assertEqual(w.cur_doc["id"], "docs/Untitled.md")
        self.assertIn("Untitled", w.get_text(), "新建后应当已经打开并显示内容")
        self.assertEqual(w.tabs[-1]["doc"]["id"], "docs/Untitled.md")

    def test_two_clicks_make_two_documents(self):
        w = self.win
        w.tree.focus(self.folder_row("docs"))
        w.on_plus_click()
        w.load_project(self.pid, keep_doc=True)
        w.tree.focus(self.folder_row("docs"))
        w.on_plus_click()
        names = sorted(os.listdir(os.path.join(self.pdir, "docs")))
        self.assertEqual(names, ["Untitled-2.md", "Untitled.md"])

    def test_the_plus_never_fires_for_a_document_row(self):
        w = self.win
        w.new_untitled_in("docs")
        w.load_project(self.pid, keep_doc=True)
        doc_row = "d:docs/Untitled.md"
        self.assertTrue(w.tree.exists(doc_row))
        w.tree.focus(doc_row)
        before = sorted(os.listdir(os.path.join(self.pdir, "docs")))
        self.assertNotIn("plus", w.tree.item(doc_row, "tags"), "文件行不该带「＋」")
        w.on_plus_click()
        self.assertEqual(sorted(os.listdir(os.path.join(self.pdir, "docs"))), before)


class FolderDeleteTests(unittest.TestCase):
    """F02：删除用户的本地文件——默认回收站、失败不永久删除、缓冲不被偷偷写回。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-del-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "app"))
        self.user = os.path.join(self.base, "资料")
        os.makedirs(self.user, exist_ok=True)
        self.write("笔记.md", "# 笔记\n")
        self.write("留着.md", "# 留着\n")
        self.info = self.ws.folders.add(self.user)

    def tearDown(self):
        self.ws.folders.watch().stop()

    def write(self, rel, text="内容\n"):
        path = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_delete_goes_to_the_recycle_bin_and_leaves_other_files_alone(self):
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle) as recycle:
            removed = self.ws.folders.remove_doc(self.info["id"], "笔记.md")
        recycle.assert_called_once()
        self.assertTrue(removed["deleted"])
        self.assertEqual(removed["file"], "笔记.md")
        self.assertFalse(os.path.exists(os.path.join(self.user, "笔记.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "留着.md")), "其他文档不得被删")
        self.assertTrue(os.path.isdir(self.user), "不得删除用户根目录")
        self.assertNotIn("笔记.md", [d["id"] for d in self.ws.folders.scan(self.info["id"], force=True)["docs"]])

    def test_a_failed_recycle_bin_keeps_the_file_and_never_purges_it(self):
        with mock.patch.object(F.FolderRoots, "_to_recycle", return_value=False):
            with self.assertRaises(PermissionError) as caught:
                self.ws.folders.remove_doc(self.info["id"], "笔记.md")
        self.assertIn("已保留", str(caught.exception))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")),
                        "回收站不可用时必须保留文件，不得改为永久删除")

    def test_a_file_replaced_after_confirmation_is_not_deleted(self):
        before = F_FOLDER_REVISION(self.user, "笔记.md")
        self.write("笔记.md", "# 确认之后被人改过了\n")      # 外部替换
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle):
            with self.assertRaises(core.D.ConflictError):
                self.ws.folders.remove_doc(self.info["id"], "笔记.md", expected_revision=before)
        self.assertTrue(os.path.isfile(os.path.join(self.user, "笔记.md")))
        with open(os.path.join(self.user, "笔记.md"), encoding="utf-8") as handle:
            self.assertIn("被人改过", handle.read())

    def test_deleting_something_that_is_already_gone_is_reported_not_guessed(self):
        os.remove(os.path.join(self.user, "笔记.md"))
        with self.assertRaises(KeyError):
            self.ws.folders.remove_doc(self.info["id"], "笔记.md")

    def test_delete_cannot_leave_the_authorized_root(self):
        with self.assertRaises(ValueError):
            self.ws.folders.remove_doc(self.info["id"], "../留着.md")
        self.assertTrue(os.path.isfile(os.path.join(self.user, "留着.md")))


def F_FOLDER_REVISION(folder, name):
    return core.D.revision(os.path.join(folder, name))


def fake_recycle(target):
    """假回收站：真的把文件移走（Windows 的回收站会把文件移走），并回答成功。

    只 mock「怎么移走」，不 mock「是否移走」——否则删除的成功路径就没被验证。
    """
    os.remove(target)
    return True


class FolderDeleteApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-delapi-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws = core.Workspace(os.path.join(self.base, "app"))
        self.user = os.path.join(self.base, "资料")
        os.makedirs(self.user, exist_ok=True)
        self.path = os.path.join(self.user, "正文.md")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("# 正文\n")
        self.api = core.Api(self.ws, os.path.join(self.base, "webui"))
        self.rid = self.api.post("/api/folder/open", {"path": self.user})["folder"]["id"]

    def tearDown(self):
        self.ws.folders.watch().stop()

    def test_the_api_deletes_to_the_recycle_bin_and_returns_the_new_tree(self):
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle):
            res = self.api.post("/api/folder/delete",
                                {"root": self.rid, "doc": "正文.md", "recycle": True,
                                 "expected": core.D.revision(self.path)})
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed"]["file"], "正文.md")
        self.assertEqual(res["docs"], [])
        self.assertFalse(os.path.exists(self.path))

    def test_a_stale_revision_is_refused_with_a_conflict(self):
        stale = core.D.revision(self.path)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("# 换过了\n")
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle):
            with self.assertRaises(core.D.ConflictError):
                self.api.post("/api/folder/delete",
                              {"root": self.rid, "doc": "正文.md", "expected": stale})
        self.assertTrue(os.path.exists(self.path))

    def test_an_unregistered_root_cannot_delete_anything(self):
        with self.assertRaises(KeyError):
            self.api.post("/api/folder/delete",
                          {"root": "root-00000000", "doc": "正文.md", "expected": "x"})
        self.assertTrue(os.path.exists(self.path))


class FolderDeleteWindowTests(unittest.TestCase):
    """桌面端：删除必须确认；取消与失败都不删；删完缓冲只能另存。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-delwin-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.user = os.path.join(self.base, "我的资料")
        os.makedirs(self.user, exist_ok=True)
        with open(os.path.join(self.user, "笔记.md"), "w", encoding="utf-8") as handle:
            handle.write("# 笔记\n\n第一行\n")
        self._dialog = mock.patch.object(core, "_dialog_folder", return_value=[self.user])
        self._dialog.start()
        self.addCleanup(self._dialog.stop)
        self._drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        self._drop.start()
        self.addCleanup(self._drop.stop)
        self.win = winui.MarkdownWindow(os.path.join(self.base, "workspace"))
        self.win.root.withdraw()
        self.addCleanup(self.destroy_window, self.win.root)
        self.win.open_user_folder()
        self.path = os.path.join(self.user, "笔记.md")

    def tearDown(self):
        try:
            self.win.ws.folders.watch().stop()
        except Exception:
            pass

    @staticmethod
    def destroy_window(root):
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()

    def test_cancelling_the_confirmation_keeps_the_file(self):
        with mock.patch("tkinter.messagebox.askyesno", return_value=False) as asked:
            self.win.delete_folder_doc("笔记.md")
        self.assertTrue(asked.called, "每次删除都必须提示")
        self.assertTrue(os.path.isfile(self.path))

    def test_the_confirmation_names_the_file_and_says_it_is_the_local_file(self):
        seen = {}

        def capture(title, message, **kwargs):
            seen["title"] = title
            seen["message"] = message
            seen["default"] = kwargs.get("default")
            return False

        with mock.patch("tkinter.messagebox.askyesno", side_effect=capture):
            self.win.delete_folder_doc("笔记.md")
        self.assertIn("删除本地文件", seen["title"])
        self.assertIn("笔记.md", seen["message"])
        self.assertIn(self.path, seen["message"], "要给出完整路径")
        self.assertIn("回收站", seen["message"])
        self.assertIn("不只是从 MDReader 列表中移除", seen["message"])

    def test_confirming_moves_it_to_the_recycle_bin_and_updates_the_tree(self):
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.win.delete_folder_doc("笔记.md")
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual([d["id"] for d in self.win.root_docs], [])
        self.assertTrue(os.path.isdir(self.user))

    def test_a_failed_recycle_bin_keeps_the_file_and_says_why(self):
        with mock.patch.object(F.FolderRoots, "_to_recycle", return_value=False), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.win.delete_folder_doc("笔记.md")
        self.assertTrue(os.path.isfile(self.path), "失败必须保留文件")
        self.assertIn("已保留", self.win.lbl_status.cget("text"))

    def test_an_open_unsaved_document_is_asked_before_deleting(self):
        w = self.win
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        w.toggle_mode()                       # 进源码
        w._set_widget("# 笔记\n\n改过但没保存\n")
        w.source = w.get_text()
        w.set_dirty(True)
        with mock.patch("tkinter.messagebox.askyesnocancel", return_value=None) as asked, \
             mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle) as recycle:
            w.delete_folder_doc("笔记.md")
        self.assertTrue(asked.called, "有未保存修改时必须先问")
        self.assertFalse(recycle.called, "取消后不得删除")
        self.assertTrue(os.path.isfile(self.path))
        self.assertIn("改过但没保存", w.get_text(), "缓冲必须保留")

    def test_discarding_unsaved_edits_asks_once_more_and_then_deletes(self):
        w = self.win
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        w.toggle_mode()
        w._set_widget("# 改过\n")
        w.source = w.get_text()
        w.set_dirty(True)
        seen = []

        def second(title, message, **kwargs):
            seen.append(message)
            return True

        with mock.patch("tkinter.messagebox.askyesnocancel", return_value=False), \
             mock.patch("tkinter.messagebox.askyesno", side_effect=second), \
             mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle):
            w.delete_folder_doc("笔记.md")
        self.assertTrue(seen, "放弃修改时要在最终确认里再说一次")
        self.assertIn("未保存的修改也会一并丢弃", seen[-1])
        self.assertFalse(os.path.exists(self.path))

    def test_saving_first_then_deleting_keeps_the_saved_copy_elsewhere(self):
        w = self.win
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        w.toggle_mode()
        w._set_widget("# 另存后再删\n")
        w.source = w.get_text()
        w.set_dirty(True)
        elsewhere = os.path.join(self.base, "别处.md")
        with mock.patch("tkinter.messagebox.askyesnocancel", return_value=True), \
             mock.patch.object(w, "_ask_save_path", return_value=elsewhere), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle):
            w.delete_folder_doc("笔记.md")
        self.assertTrue(os.path.isfile(elsewhere), "先另存的内容必须留住")
        self.assertFalse(os.path.exists(self.path), "原文件按确认被删除")

    def test_a_deleted_document_is_not_silently_recreated_by_saving(self):
        w = self.win
        w.root_tree.selection_set("f:笔记.md")
        w.on_folder_activate()
        w.toggle_mode()
        w._set_widget("# 删掉之后还想保存\n")
        w.source = w.get_text()
        w.set_dirty(True)
        # 「否」= 放弃修改并继续；再确认一次删除。不做这一步会弹真实的系统对话框。
        with mock.patch.object(F.FolderRoots, "_to_recycle", side_effect=fake_recycle), \
             mock.patch("tkinter.messagebox.askyesnocancel", return_value=False), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True):
            w.delete_folder_doc("笔记.md")
        self.assertFalse(os.path.exists(self.path))
        with mock.patch.object(w, "save_as", return_value=False) as save_as:
            self.assertFalse(w.save_doc())
        self.assertTrue(save_as.called, "已被删除的文档只能另存")
        self.assertFalse(os.path.exists(self.path), "不得因保存而悄悄建回原路径")
        # 之后按保存会给出“原文件已被删除，只能另存”的明确说法
        self.assertIn("原文件已被删除", w.lbl_status.cget("text"))
        self.assertIn("另存", w.lbl_status.cget("text"))

    def test_removing_the_folder_from_the_list_still_deletes_nothing(self):
        w = self.win
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            w.remove_current_root()
        self.assertTrue(os.path.isfile(self.path), "「从列表移除」与「删除」必须分清")


if __name__ == "__main__":
    unittest.main(verbosity=2)
