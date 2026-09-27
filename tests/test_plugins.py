# -*- coding: utf-8 -*-
"""P01：插件机制。清单校验、受信来源、安装/启用/禁用/卸载、任务与结果校验。

    python -m unittest tests.test_plugins

样例插件包由 `plugins/samples/*` 现场打包（确定性打包，哈希可复现）；
受信清单写在临时目录里，绝不使用真实工作区或用户资料。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from mdreader import core
from mdreader import plugin_tasks as T
from mdreader import plugins as P
from mdreader import winui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "plugins", "samples")
SAMPLE_IMAGE = os.path.join(SAMPLES, "sample-image")
SAMPLE_A = os.path.join(SAMPLES, "sample-export-a")
SAMPLE_B = os.path.join(SAMPLES, "sample-export-b")
SAMPLE_DEP = os.path.join(SAMPLES, "sample-dependent")

#: 测试里自建的受信清单模板：三个正式插件占位（真实清单里已经发布并登记哈希）
RESERVED = {
    "mdreader.image-insert": {"publisher": "MDReader", "versions": {},
                              "reserved": "F03 交付（图片插入插件）"},
    "mdreader.export-pdf": {"publisher": "MDReader", "versions": {},
                            "reserved": "F07 交付（PDF 导出插件）"},
    "mdreader.export-docx": {"publisher": "MDReader", "versions": {},
                             "reserved": "F08 交付（Word 导出插件）"},
}


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def sha256(path):
    import hashlib
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)
    return path


def tiny_png(path, width=2, height=2):
    """写一张真正的最小 PNG（不依赖任何图像库）。"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes([40, 90, 160]) * width for _ in range(height))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(data)
    return path


class PluginTestCase(unittest.TestCase):
    """公共夹具：临时工作区、临时受信清单、现场打的插件包。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdreader-plugins-")
        self.addCleanup(self.temp.cleanup)
        self.base = self.temp.name
        self.ws_root = os.path.join(self.base, "app")
        self.packages = os.path.join(self.base, "packages")
        self.trust_file = os.path.join(self.base, "plugin_trust.json")
        self.trust = {"api_version": "1", "plugins": json.loads(json.dumps(RESERVED))}
        self._counter = 0
        self.stores = []

    # -- 夹具 -------------------------------------------------------------
    def write_trust(self):
        write_text(self.trust_file, json.dumps(self.trust, ensure_ascii=False, indent=2))
        for store in self.stores:          # 受信清单改了，已经建好的仓库要重新读
            store.trust.load()
        return self.trust_file

    def store(self, **kwargs):
        if not os.path.exists(self.trust_file):
            self.write_trust()
        kwargs.setdefault("app_version", "0.3.0")
        kwargs.setdefault("trust_path", self.trust_file)
        kwargs.setdefault("python", sys.executable)
        kwargs.setdefault("task_timeout", 25.0)
        store = P.PluginStore(self.ws_root, **kwargs)
        self.stores.append(store)
        self.addCleanup(self._shutdown, store)
        return store

    @staticmethod
    def _shutdown(store):
        try:
            store.shutdown()
            store.cleanup()
        except Exception:
            pass

    def copy_sample(self, sample):
        self._counter += 1
        folder = os.path.join(self.base, "src", "%s-%d" % (os.path.basename(sample), self._counter))
        shutil.copytree(sample, folder)
        return folder

    def package(self, sample, *, version=None, manifest=None, trust=True, extra=None,
                replace=None, publisher="MDReader", allow_verify=True, broken=False):
        """打包一个样例插件；默认同时登记受信哈希。返回包路径。"""
        folder = self.copy_sample(sample)
        manifest_path = os.path.join(folder, P.MANIFEST_NAME)
        if version or manifest or broken:
            data = read_json(manifest_path)
            if version:
                data["version"] = version
            if manifest:
                data.update(manifest)
            if broken == "json":
                write_text(manifest_path, "{ not json")
            elif broken == "delete":
                os.remove(manifest_path)
            else:
                write_text(manifest_path, json.dumps(data, ensure_ascii=False, indent=2))
        for rel, payload in (replace or {}).items():
            target = os.path.join(folder, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as stream:
                stream.write(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
        os.makedirs(self.packages, exist_ok=True)
        pid = read_json(os.path.join(folder, P.MANIFEST_NAME))["id"] if not broken else "broken"
        out = os.path.join(self.packages, "%s-%d.zip" % (pid, self._counter))
        P.build_package(folder, out, extra=extra)
        if trust and not broken and allow_verify:
            version = read_json(os.path.join(folder, P.MANIFEST_NAME))["version"]
            entry = self.trust["plugins"].setdefault(
                pid, {"publisher": publisher, "versions": {}})
            entry.setdefault("versions", {})[version] = sha256(out)
            if publisher:
                entry["publisher"] = publisher
            self.write_trust()
        return out

    def raw_zip(self, name, entries):
        """手工造包：``entries`` 是 ``(名字, 内容, unix 模式)``。"""
        os.makedirs(self.packages, exist_ok=True)
        path = os.path.join(self.packages, name)
        with zipfile.ZipFile(path, "w") as zf:
            for item in entries:
                member, payload = item[0], item[1]
                mode = item[2] if len(item) > 2 else 0o644
                info = zipfile.ZipInfo(member)
                info.external_attr = mode << 16
                zf.writestr(info, payload)
        return path

    def installed_ids(self, store):
        return [row["id"] for row in store.list_plugins() if row["installed"]]

    def enable_sample(self, store, sample=SAMPLE_IMAGE, **kwargs):
        path = self.package(sample, **kwargs)
        store.install(path)
        pid = read_json(os.path.join(self.copy_sample(sample), P.MANIFEST_NAME))["id"]
        store.enable(pid)
        return pid


# ==========================================================================
# 版本范围
# ==========================================================================

class VersionRangeTests(unittest.TestCase):
    def test_plain_version_is_exact(self):
        self.assertTrue(P.version_in_range("1.2.0", "1.2.0"))
        self.assertFalse(P.version_in_range("1.2.1", "1.2.0"))

    def test_open_ranges(self):
        self.assertTrue(P.version_in_range("0.3.0", ">=0.3.0,<0.4.0"))
        self.assertFalse(P.version_in_range("0.4.0", ">=0.3.0,<0.4.0"))
        self.assertFalse(P.version_in_range("0.2.9", ">=0.3.0,<0.4.0"))

    def test_empty_or_star_means_no_limit(self):
        self.assertTrue(P.version_in_range("0.2.8", ""))
        self.assertTrue(P.version_in_range("0.2.8", "*"))

    def test_longer_versions_compare_numerically_not_as_text(self):
        self.assertTrue(P.version_in_range("0.10.0", ">=0.9.0"))
        self.assertFalse(P.version_in_range("0.9.0", ">=0.10.0"))

    def test_unparsable_clause_refuses_instead_of_allowing(self):
        self.assertFalse(P.version_in_range("1.0.0", "latest"))
        self.assertFalse(P.version_in_range("1.0.0", ">=1.0,oops"))


# ==========================================================================
# 清单校验
# ==========================================================================

class ManifestTests(PluginTestCase):
    def manifest(self, **overrides):
        base = {
            "id": "mdreader.demo", "name": "演示", "version": "1.0.0", "api_version": "1",
            "app_version_range": ">=0.2.0", "entrypoint": "plugin.py",
            "capabilities": [P.CAP_EXPORT],
            "commands": [{"id": "export.demo", "title": "演示导出", "capability": P.CAP_EXPORT,
                          "method": "export", "format": "demo", "extension": "demo",
                          "media_type": "application/x-demo"}],
        }
        base.update(overrides)
        return base

    def test_minimal_manifest_is_accepted_and_normalised(self):
        got = P.validate_manifest(self.manifest())
        self.assertEqual(got["id"], "mdreader.demo")
        self.assertEqual(got["dependencies"], [])
        self.assertEqual(got["description"], "")
        self.assertEqual(got["commands"][0]["max_bytes"], P.MAX_ARTIFACT_BYTES)

    def test_unknown_manifest_key_is_refused(self):
        with self.assertRaises(P.ManifestError) as caught:
            P.validate_manifest(self.manifest(html="<b>hi</b>"))
        self.assertIn("未允许的字段", str(caught.exception))

    def test_unknown_command_key_is_refused(self):
        command = dict(self.manifest()["commands"][0], script="alert(1)")
        with self.assertRaises(P.ManifestError) as caught:
            P.validate_manifest(self.manifest(commands=[command]))
        self.assertIn("script", str(caught.exception))

    def test_id_must_be_namespaced_and_lowercase(self):
        for bad in ("demo", "MDReader.Demo", "mdreader..demo", "mdreader demo"):
            with self.subTest(bad=bad):
                with self.assertRaises(P.ManifestError):
                    P.validate_manifest(self.manifest(id=bad))

    def test_missing_pieces_are_refused(self):
        for field in ("id", "name", "version", "api_version", "entrypoint"):
            with self.subTest(field=field):
                data = self.manifest()
                data.pop(field)
                with self.assertRaises(P.ManifestError):
                    P.validate_manifest(data)

    def test_version_must_be_numeric(self):
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(version="1.0-beta"))

    def test_unsupported_api_version_is_refused(self):
        with self.assertRaises(P.ManifestError) as caught:
            P.validate_manifest(self.manifest(api_version="99"))
        self.assertIn("不支持 api_version", str(caught.exception))

    def test_entrypoint_cannot_escape_the_package(self):
        for bad in ("../evil.py", "/abs/evil.py", "C:/evil.py", "sub/../../evil.py",
                    "plugin.txt", "sub/./plugin.py"):
            with self.subTest(bad=bad):
                with self.assertRaises(P.ManifestError):
                    P.validate_manifest(self.manifest(entrypoint=bad))

    def test_unknown_capability_is_refused(self):
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(capabilities=["editor.anything"],
                                              commands=[]))

    def test_command_must_use_a_declared_capability(self):
        with self.assertRaises(P.ManifestError) as caught:
            P.validate_manifest(self.manifest(
                capabilities=[P.CAP_IMAGE_INSERT],
                commands=[{"id": "x", "title": "x", "capability": P.CAP_EXPORT,
                           "method": "export", "format": "demo", "extension": "demo",
                           "media_type": "application/x-demo"}]))
        self.assertIn("没声明的能力", str(caught.exception))

    def test_duplicate_command_id_is_refused(self):
        command = self.manifest()["commands"][0]
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(commands=[command, dict(command)]))

    def test_method_cannot_be_a_dunder_name(self):
        command = dict(self.manifest()["commands"][0], method="__init__")
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(commands=[command]))

    def test_export_command_needs_format_extension_and_media_type(self):
        for missing in ("format", "extension", "media_type"):
            with self.subTest(missing=missing):
                command = self.manifest()["commands"][0]
                command.pop(missing)
                with self.assertRaises(P.ManifestError):
                    P.validate_manifest(self.manifest(commands=[command]))

    def test_image_command_must_not_declare_export_fields(self):
        command = {"id": "i", "title": "插入", "capability": P.CAP_IMAGE_INSERT,
                   "method": "insert_image", "format": "png"}
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(capabilities=[P.CAP_IMAGE_INSERT],
                                              commands=[command]))

    def test_magic_must_be_even_hex(self):
        command = dict(self.manifest()["commands"][0], magic="abc")
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(commands=[command]))

    def test_oversized_max_bytes_is_refused(self):
        command = dict(self.manifest()["commands"][0], max_bytes=P.MAX_ARTIFACT_BYTES * 4)
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(commands=[command]))

    def test_title_length_is_bounded(self):
        command = dict(self.manifest()["commands"][0], title="长" * 100)
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(commands=[command]))

    def test_dependency_on_itself_is_refused(self):
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(dependencies=["mdreader.demo"]))

    def test_control_characters_in_text_are_refused(self):
        with self.assertRaises(P.ManifestError):
            P.validate_manifest(self.manifest(name="演示\x07"))


# ==========================================================================
# 安装包安全
# ==========================================================================

class PackageSafetyTests(PluginTestCase):
    def manifest_bytes(self, **overrides):
        data = {
            "id": "mdreader.evil", "name": "越界包", "version": "1.0.0", "api_version": "1",
            "entrypoint": "plugin.py", "capabilities": [P.CAP_IMAGE_INSERT],
            "commands": [{"id": "i", "title": "插入", "capability": P.CAP_IMAGE_INSERT,
                          "method": "insert_image"}],
        }
        data.update(overrides)
        return json.dumps(data).encode("utf-8")

    def test_parent_traversal_is_refused(self):
        path = self.raw_zip("traversal.zip", [
            ("manifest.json", self.manifest_bytes()),
            ("../evil.py", b"print(1)"),
            ("plugin.py", b"def insert_image(task): pass"),
        ])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("越界路径", str(caught.exception))

    def test_absolute_path_is_refused(self):
        path = self.raw_zip("absolute.zip", [
            ("manifest.json", self.manifest_bytes()),
            ("C:/Windows/evil.py", b"x"),
        ])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("绝对路径", str(caught.exception))

    def test_symlink_member_is_refused(self):
        path = self.raw_zip("link.zip", [
            ("manifest.json", self.manifest_bytes()),
            ("plugin.py", b"pass", 0o120777),
        ])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("符号链接", str(caught.exception))

    def test_encrypted_member_is_refused(self):
        info = zipfile.ZipInfo("plugin.py")
        info.flag_bits |= 0x1
        with self.assertRaises(P.PackageError) as caught:
            P._check_member(info, seen=set(), total=[0], count=[0])
        self.assertIn("加密", str(caught.exception))

    def test_entry_count_limit(self):
        entries = [("manifest.json", self.manifest_bytes()), ("plugin.py", b"pass")]
        entries += [("extra/%d.py" % i, b"x") for i in range(6)]
        path = self.raw_zip("many.zip", entries)
        with mock.patch.object(P, "MAX_ENTRIES", 4):
            with self.assertRaises(P.PackageError) as caught:
                self.store().install(path, allow_unverified=True)
        self.assertIn("文件数超过上限", str(caught.exception))

    def test_unpacked_size_limit(self):
        path = self.raw_zip("big.zip", [
            ("manifest.json", self.manifest_bytes()),
            ("plugin.py", b"x" * 5000),
        ])
        with mock.patch.object(P, "MAX_UNPACKED_BYTES", 1000):
            with self.assertRaises(P.PackageError) as caught:
                self.store().install(path, allow_unverified=True)
        self.assertIn("解包后超过上限", str(caught.exception))

    def test_archive_size_limit(self):
        path = self.raw_zip("huge.zip", [("manifest.json", self.manifest_bytes())])
        with mock.patch.object(P, "MAX_ARCHIVE_BYTES", 10):
            with self.assertRaises(P.PackageError) as caught:
                self.store().install(path, allow_unverified=True)
        self.assertIn("超过上限", str(caught.exception))

    def test_missing_manifest_is_refused(self):
        path = self.raw_zip("nomanifest.zip", [("plugin.py", b"pass")])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("缺少 manifest.json", str(caught.exception))

    def test_broken_manifest_json_is_refused(self):
        path = self.raw_zip("badjson.zip", [
            ("manifest.json", b"{ not json"), ("plugin.py", b"pass")])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("UTF-8 JSON", str(caught.exception))

    def test_entrypoint_missing_from_archive_is_refused(self):
        path = self.raw_zip("noentry.zip", [
            ("manifest.json", self.manifest_bytes(entrypoint="other.py")),
            ("plugin.py", b"pass")])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("入口文件不在包里", str(caught.exception))

    def test_non_zip_is_refused(self):
        path = write_text(os.path.join(self.packages, "notzip.zip"), "hello")
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("zip", str(caught.exception))

    def test_missing_file_is_refused(self):
        with self.assertRaises(P.PackageError):
            self.store().install(os.path.join(self.packages, "nope.zip"))

    def test_duplicate_member_name_is_refused(self):
        path = self.raw_zip("dup.zip", [
            ("manifest.json", self.manifest_bytes()),
            ("plugin.py", b"pass"),
            ("plugin.py", b"pass again")])
        with self.assertRaises(P.PackageError) as caught:
            self.store().install(path, allow_unverified=True)
        self.assertIn("重复路径", str(caught.exception))

    def test_failed_install_leaves_no_staging_leftovers(self):
        path = self.raw_zip("noentry2.zip", [
            ("manifest.json", self.manifest_bytes(entrypoint="other.py")),
            ("plugin.py", b"pass")])
        store = self.store()
        with self.assertRaises(P.PackageError):
            store.install(path, allow_unverified=True)
        staging = os.path.join(store.staging_dir)
        self.assertEqual(os.listdir(staging) if os.path.isdir(staging) else [], [])


# ==========================================================================
# 受信来源
# ==========================================================================

class TrustTests(PluginTestCase):
    def test_unknown_plugin_id_is_refused(self):
        path = self.package(SAMPLE_A, trust=False)
        with self.assertRaises(P.TrustError) as caught:
            self.store().install(path)
        self.assertIn("不在受信清单里", str(caught.exception))

    def test_hash_mismatch_is_refused(self):
        path = self.package(SAMPLE_A, trust=False)
        self.trust["plugins"]["mdreader.sample-export-a"] = {
            "publisher": "MDReader", "versions": {"1.0.0": "0" * 64}}
        self.write_trust()
        with self.assertRaises(P.TrustError) as caught:
            self.store().install(path)
        self.assertIn("哈希不一致", str(caught.exception))

    def test_reserved_but_unreleased_plugin_cannot_be_installed(self):
        path = self.package(SAMPLE_A, trust=False)
        self.trust["plugins"]["mdreader.sample-export-a"] = {
            "publisher": "MDReader", "versions": {}, "reserved": "以后交付"}
        self.write_trust()
        with self.assertRaises(P.TrustError) as caught:
            self.store().install(path)
        self.assertIn("还没有发布", str(caught.exception))

    def test_shipped_trust_registry_matches_the_official_packages(self):
        registry = P.TrustRegistry()          # 随程序分发的那一份
        ids = {row["id"] for row in registry.reserved()}
        self.assertLessEqual({"mdreader.image-insert", "mdreader.export-pdf",
                              "mdreader.export-docx"}, ids)
        released = {row["id"] for row in registry.reserved() if row["released"]}
        self.assertEqual(released, {"mdreader.image-insert", "mdreader.export-pdf",
                                    "mdreader.export-docx", "mdreader.document-convert"})
        packages = sorted(glob.glob(os.path.join(ROOT, "plugins", "packages", "*.zip")))
        self.assertEqual(len(packages), len(released), packages)
        for package in packages:
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read(P.MANIFEST_NAME).decode("utf-8"))
            self.assertTrue(registry.lookup(manifest["id"], sha256(package))[0],
                            "%s 与受信清单的哈希对不上" % os.path.basename(package))

    def test_verified_install_records_source_and_hash(self):
        path = self.package(SAMPLE_A)
        store = self.store()
        store.install(path)
        row = store.list_plugins()[0]
        self.assertEqual(row["source"], "trusted")
        self.assertEqual(len(row["sha256"]), 64)

    def test_unverified_install_requires_an_explicit_decision(self):
        path = self.package(SAMPLE_A, trust=False)
        store = self.store()
        result = store.install(path, allow_unverified=True)
        self.assertFalse(result["verified"])
        row = store.list_plugins()[0]
        self.assertEqual(row["source"], "manual")
        self.assertIn("未核对", result["note"])

    def test_installed_by_hand_package_without_trust_file_reports_the_problem(self):
        store = self.store(trust_path=os.path.join(self.base, "missing.json"))
        self.assertIn("找不到受信清单", store.trust.problem)
        self.assertEqual(store.list_plugins(), [])


# ==========================================================================
# 安装 / 启用 / 禁用 / 卸载 / 重启
# ==========================================================================

class LifecycleTests(PluginTestCase):
    def test_install_then_enable_registers_commands(self):
        path = self.package(SAMPLE_IMAGE)
        store = self.store()
        store.install(path)
        self.assertEqual(store.commands(), [], "安装后默认不启用")
        store.enable("mdreader.sample-image")
        commands = store.commands()
        self.assertEqual([c["command"] for c in commands], ["mdreader.sample-image:insert.image"])
        self.assertEqual(commands[0]["capability"], P.CAP_IMAGE_INSERT)

    def test_enabled_state_survives_a_restart(self):
        path = self.package(SAMPLE_A)
        first = self.store()
        first.install(path)
        first.enable("mdreader.sample-export-a")
        second = self.store()
        row = next(r for r in second.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertTrue(row["enabled"])
        self.assertTrue(row["available"])
        self.assertEqual(len(second.commands()), 1)

    def test_disabled_plugin_stays_disabled_after_restart(self):
        path = self.package(SAMPLE_A)
        store = self.store()
        store.install(path)
        store.enable("mdreader.sample-export-a")
        store.disable("mdreader.sample-export-a")
        again = self.store()
        row = next(r for r in again.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertFalse(row["enabled"])
        self.assertTrue(row["user_disabled"])
        self.assertEqual(again.commands(), [])

    def test_installing_the_same_version_twice_is_refused(self):
        path = self.package(SAMPLE_A)
        store = self.store()
        store.install(path)
        with self.assertRaises(P.PackageError) as caught:
            store.install(path)
        self.assertIn("已经安装过", str(caught.exception))

    def test_upgrade_keeps_the_previous_version_for_rollback(self):
        old = self.package(SAMPLE_A, version="1.0.0")
        new = self.package(SAMPLE_A, version="1.1.0")
        store = self.store()
        store.install(old)
        store.enable("mdreader.sample-export-a")
        result = store.install(new)
        self.assertTrue(result["replaced"])
        self.assertTrue(os.path.isdir(result["previous"]["path"]))
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertEqual(row["version"], "1.1.0")
        rolled = store.rollback("mdreader.sample-export-a")
        self.assertEqual(rolled["version"], "1.0.0")
        self.assertFalse(rolled["enabled"], "回退后需要用户重新启用，不自动复活")

    def test_incompatible_upgrade_is_refused_and_previous_version_kept(self):
        old = self.package(SAMPLE_A, version="1.0.0")
        new = self.package(SAMPLE_A, version="2.0.0", manifest={"app_version_range": ">=9.0.0"})
        store = self.store()
        store.install(old)
        store.enable("mdreader.sample-export-a")
        with self.assertRaises(P.PluginError) as caught:
            store.install(new)
        self.assertIn("保留上一可用版本", str(caught.exception))
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertEqual(row["version"], "1.0.0")
        self.assertTrue(row["enabled"])
        self.assertTrue(row["available"])

    def test_incompatible_app_range_disables_only_that_plugin(self):
        path = self.package(SAMPLE_A, manifest={"app_version_range": ">=9.0.0"})
        store = self.store()
        store.install(path)
        with self.assertRaises(P.PluginError) as caught:
            store.enable("mdreader.sample-export-a")
        self.assertIn("当前是 0.3.0", str(caught.exception))
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertEqual(row["state"], "unavailable")
        self.assertFalse(row["available"])
        self.assertIn("版本 >=9.0.0", row["reason"])
        self.assertIn("当前是 0.3.0", row["reason"])

    def test_broken_entrypoint_fails_activation_and_keeps_it_disabled(self):
        path = self.package(SAMPLE_A,
                            replace={"plugin.py": b"def export(task)\n    pass\n"})
        store = self.store()
        store.install(path)
        with self.assertRaises(P.PluginError) as caught:
            store.enable("mdreader.sample-export-a")
        self.assertIn("无法启用", str(caught.exception))
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertFalse(row["enabled"])
        self.assertIn("启动检查失败", row["last_error"])

    def test_failed_activation_rolls_back_to_the_previous_version(self):
        old = self.package(SAMPLE_A, version="1.0.0")
        store = self.store()
        store.install(old)
        store.enable("mdreader.sample-export-a")
        broken = self.package(SAMPLE_A, version="1.1.0",
                              replace={"plugin.py": b"import missing_module_for_test\n"})
        store.install(broken)
        result = store.enable("mdreader.sample-export-a")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["version"], "1.0.0")
        self.assertTrue(result["enabled"], "回退到上一可用版本后应保持原来的启用状态")
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertEqual(row["version"], "1.0.0")
        self.assertIn("已回退", row["last_error"])

    def test_rollback_does_not_revive_a_plugin_the_user_disabled(self):
        old = self.package(SAMPLE_A, version="1.0.0")
        store = self.store()
        store.install(old)
        store.disable("mdreader.sample-export-a")
        new = self.package(SAMPLE_A, version="1.1.0")
        store.install(new)
        rolled = store.rollback("mdreader.sample-export-a")
        self.assertFalse(rolled["enabled"])
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-export-a")
        self.assertTrue(row["user_disabled"])
        self.assertFalse(row["available"])

    def test_uninstall_removes_only_plugin_code_and_cache(self):
        store = self.store()
        self.enable_sample(store, SAMPLE_A)
        user_doc = write_text(os.path.join(self.base, "用户资料", "笔记.md"), "# 笔记\n")
        user_image = write_text(os.path.join(self.base, "用户资料", "assets", "图.png"), "png")
        exported = write_text(os.path.join(self.base, "用户资料", "导出.samplea"), "x")
        cache = os.path.join(store.cache_dir, "mdreader.sample-export-a")
        write_text(os.path.join(cache, "cache.bin"), "cached")
        result = store.uninstall("mdreader.sample-export-a")
        self.assertTrue(result["removed"])
        self.assertFalse(os.path.isdir(os.path.join(store.installed_dir, "mdreader.sample-export-a")))
        self.assertFalse(os.path.isdir(cache), "插件专属缓存要被清掉")
        self.assertTrue(os.path.isfile(user_doc))
        self.assertTrue(os.path.isfile(user_image))
        self.assertTrue(os.path.isfile(exported))
        self.assertEqual(self.installed_ids(store), [])

    def test_uninstall_of_a_missing_plugin_is_refused(self):
        with self.assertRaises(P.PluginError):
            self.store().uninstall("mdreader.nothing")

    def test_uninstall_never_touches_the_other_installed_plugins(self):
        store = self.store()
        for sample in (SAMPLE_A, SAMPLE_B):
            store.install(self.package(sample))
        other = store.entry("mdreader.sample-export-b")["path"]
        user_doc = write_text(os.path.join(self.base, "用户资料", "另一篇.md"), "# 另一篇\n")
        store.uninstall("mdreader.sample-export-a")
        self.assertTrue(os.path.isdir(other), "卸载一个插件不能连累别的插件")
        self.assertEqual(self.installed_ids(store), ["mdreader.sample-export-b"])
        self.assertTrue(os.path.isfile(user_doc))
        self.assertTrue(os.path.isdir(store.plugins_dir))

    def test_broken_manifest_on_disk_is_reported_without_raising(self):
        store = self.store()
        self.enable_sample(store, SAMPLE_A)
        folder = store.entry("mdreader.sample-export-a")["path"]
        write_text(os.path.join(folder, P.MANIFEST_NAME), "{ broken")
        rows = store.list_plugins()
        row = next(r for r in rows if r["id"] == "mdreader.sample-export-a")
        self.assertEqual(row["state"], "broken")
        self.assertIn("清单损坏", row["reason"])
        self.assertEqual(store.commands(), [], "清单坏了就不能再提供命令")

    def test_corrupt_state_file_is_treated_as_nothing_installed(self):
        write_text(os.path.join(self.ws_root, P.STATE_FILE), "{ not json")
        store = self.store()
        self.assertEqual(self.installed_ids(store), [], "状态文件坏了就当没装过任何插件")
        self.assertEqual(store.commands(), [])
        self.assertIn("无法读取", store.state_problem)

    def test_reserved_plugins_are_listed_as_not_installed(self):
        store = self.store()
        rows = {row["id"]: row for row in store.list_plugins()}
        self.assertIn("mdreader.export-pdf", rows)
        self.assertEqual(rows["mdreader.export-pdf"]["state"], "not-installed")
        self.assertFalse(rows["mdreader.export-pdf"]["available"])

    def test_three_plugins_can_be_enabled_at_once(self):
        store = self.store()
        for sample in (SAMPLE_IMAGE, SAMPLE_A, SAMPLE_B):
            path = self.package(sample)
            store.install(path)
        for pid in ("mdreader.sample-image", "mdreader.sample-export-a",
                    "mdreader.sample-export-b"):
            store.enable(pid)
        self.assertEqual(len(store.commands()), 3)
        self.assertEqual(len(store.tasks.active()), 0)

    def test_disabling_one_plugin_does_not_disturb_the_others(self):
        store = self.store()
        for sample in (SAMPLE_A, SAMPLE_B):
            store.install(self.package(sample))
        store.enable("mdreader.sample-export-a")
        store.enable("mdreader.sample-export-b")
        store.disable("mdreader.sample-export-a")
        commands = [c["command"] for c in store.commands()]
        self.assertEqual(commands, ["mdreader.sample-export-b:export.sampleb"])


# ==========================================================================
# 依赖
# ==========================================================================

class DependencyTests(PluginTestCase):
    def test_missing_dependency_blocks_enabling_with_a_reason(self):
        store = self.store()
        store.install(self.package(SAMPLE_DEP))
        with self.assertRaises(P.PluginError) as caught:
            store.enable("mdreader.sample-dependent")
        self.assertIn("缺少依赖插件", str(caught.exception))
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-dependent")
        self.assertEqual(row["state"], "unavailable")
        self.assertEqual(row["dependencies"][0]["id"], "mdreader.sample-export-b")
        self.assertFalse(row["dependencies"][0]["installed"])

    def test_dependency_must_be_enabled_not_merely_installed(self):
        store = self.store()
        store.install(self.package(SAMPLE_B))
        store.install(self.package(SAMPLE_DEP))
        with self.assertRaises(P.PluginError) as caught:
            store.enable("mdreader.sample-dependent")
        self.assertIn("没有启用", str(caught.exception))

    def test_enabling_the_dependency_unblocks_the_dependent(self):
        store = self.store()
        store.install(self.package(SAMPLE_B))
        store.install(self.package(SAMPLE_DEP))
        store.enable("mdreader.sample-export-b")
        store.enable("mdreader.sample-dependent")
        self.assertEqual(sorted(c["command"] for c in store.commands()),
                         ["mdreader.sample-dependent:export.dependent",
                          "mdreader.sample-export-b:export.sampleb"])

    def test_disabling_the_dependency_makes_the_dependent_unavailable(self):
        store = self.store()
        store.install(self.package(SAMPLE_B))
        store.install(self.package(SAMPLE_DEP))
        store.enable("mdreader.sample-export-b")
        store.enable("mdreader.sample-dependent")
        store.disable("mdreader.sample-export-b")
        commands = [c["command"] for c in store.commands()]
        self.assertNotIn("mdreader.sample-dependent:export.dependent", commands)
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-dependent")
        self.assertFalse(row["available"])
        with self.assertRaises(P.PluginError):
            store.find_command("mdreader.sample-dependent:export.dependent")

    def test_uninstalling_the_dependency_keeps_the_dependent_disabled(self):
        store = self.store()
        store.install(self.package(SAMPLE_B))
        store.install(self.package(SAMPLE_DEP))
        store.enable("mdreader.sample-export-b")
        store.enable("mdreader.sample-dependent")
        store.uninstall("mdreader.sample-export-b")
        row = next(r for r in store.list_plugins() if r["id"] == "mdreader.sample-dependent")
        self.assertFalse(row["available"])
        self.assertEqual(store.commands(), [])


# ==========================================================================
# 任务：独立工作进程、进度、取消、超时、结果校验
# ==========================================================================

class TaskTests(PluginTestCase):
    def image_store(self):
        store = self.store()
        store.install(self.package(SAMPLE_IMAGE))
        store.enable("mdreader.sample-image")
        return store

    def export_store(self, sample=SAMPLE_A, pid="mdreader.sample-export-a"):
        store = self.store()
        store.install(self.package(sample))
        store.enable(pid)
        return store

    def test_image_task_runs_in_a_separate_process_and_returns_a_plan(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "alt": "示意图"},
                                  doc="/tmp/doc.md", revision="rev-1", entry="desktop",
                                  wait=30)
        self.assertEqual(task.state, "done", task.error)
        checked = store.tasks.result(task.id)
        self.assertEqual(checked["markdown"], "![示意图](assets/synthetic.png)")
        self.assertEqual(len(checked["assets"]), 1)
        self.assertTrue(checked["assets"][0]["path"].startswith(os.path.abspath(task.workdir)))

    def test_task_reports_progress_and_binds_document_identity(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, doc="/tmp/甲.md",
                                  revision="rev-9", entry="web", wait=30)
        view = store.tasks.poll(task.id)
        self.assertEqual(view["doc"], "/tmp/甲.md")
        self.assertEqual(view["revision"], "rev-9")
        self.assertEqual(view["entry"], "web")
        self.assertEqual(view["percent"], 100)

    def test_commit_assets_copies_into_the_document_folder(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, doc="/tmp/甲.md",
                                  revision="rev-1", entry="desktop", wait=30)
        target = os.path.join(self.base, "用户资料", "assets")
        result = store.tasks.commit_assets(task.id, target, now_doc="/tmp/甲.md",
                                           now_revision="rev-1")
        self.assertTrue(os.path.isfile(result["assets"][0]["path"]))
        self.assertTrue(result["assets"][0]["path"].startswith(os.path.abspath(target)))
        self.assertIn("assets/", result["markdown"])

    def test_commit_assets_never_overwrites_an_existing_file(self):
        store = self.image_store()
        target = os.path.join(self.base, "用户资料", "assets")
        write_text(os.path.join(target, "synthetic.png"), "old content")
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, wait=30)
        result = store.tasks.commit_assets(task.id, target)
        self.assertEqual(os.path.basename(result["assets"][0]["path"]), "synthetic-2.png")
        with open(os.path.join(target, "synthetic.png"), encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "old content")

    def test_a_changed_revision_discards_the_result(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, doc="/tmp/甲.md",
                                  revision="rev-1", wait=30)
        with self.assertRaises(P.StaleResult):
            store.tasks.result(task.id, now_revision="rev-2")
        with self.assertRaises(P.StaleResult):
            store.tasks.commit_assets(task.id, os.path.join(self.base, "out"),
                                      now_doc="/tmp/乙.md")

    def test_result_outside_the_work_directory_is_refused(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "escape": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("工作目录之外", task.error)

    def test_result_with_the_wrong_file_type_is_refused(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "bad_magic": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("不像", task.error)

    def test_missing_asset_list_is_refused(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "no_asset": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("没有给出要写入的图片", task.error)

    def test_plugin_exception_only_fails_that_task(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "fail": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("样例插件按要求失败", task.error)
        # 宿主还能继续跑下一个任务
        again = store.tasks.submit("mdreader.sample-image:insert.image",
                                   options={"synthetic": True}, wait=30)
        self.assertEqual(again.state, "done", again.error)

    def test_timeout_kills_the_worker_and_keeps_the_host_usable(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True, "hang": True},
                                  timeout=2.0, wait=30)
        self.assertEqual(task.state, "timeout")
        self.assertIn("超时", task.error)
        self.assertIsNone(task.process, "超时后工作进程必须已经结束")
        self.assertEqual(store.tasks.active(), [])

    def test_cancelling_a_running_task_refuses_the_result(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"slow": 0.1, "sleep": 20}, wait=1.0)
        self.assertEqual(task.state, "running")
        store.tasks.cancel(task.id, "用户取消")
        view = store.tasks.poll(task.id)
        self.assertEqual(view["state"], "cancelled")
        with self.assertRaises(P.TaskError) as caught:
            store.tasks.result(task.id)
        self.assertIn("取消", str(caught.exception))

    def test_disabling_a_plugin_cancels_its_running_task(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"sleep": 20}, wait=1.0)
        self.assertEqual(task.state, "running")
        result = store.disable("mdreader.sample-export-a")
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(store.tasks.poll(task.id)["state"], "cancelled")
        with self.assertRaises(P.TaskError):
            store.tasks.result(task.id)

    def test_export_task_returns_a_validated_artifact(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  markdown="# 标题\n\n正文\n", wait=30)
        self.assertEqual(task.state, "done", task.error)
        checked = store.tasks.result(task.id)
        self.assertEqual(checked["extension"], "samplea")
        with open(checked["path"], "rb") as stream:
            self.assertTrue(stream.read(6), "产物应该有内容")

    def test_commit_export_replaces_the_target_atomically(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  markdown="# 标题\n", wait=30)
        dest = os.path.join(self.base, "导出", "文档.samplea")
        result = store.tasks.commit_export(task.id, dest)
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(result["path"], os.path.abspath(dest))
        self.assertEqual(result["extension"], "samplea")
        leftovers = [name for name in os.listdir(os.path.dirname(dest))
                     if name.startswith(".mdreader-plugin-")]
        self.assertEqual(leftovers, [], "不得残留半成品临时文件")

    def test_commit_export_refuses_a_wrong_extension_and_bad_magic(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"bad_magic": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("与清单声明的格式不符", task.error)

    def test_oversized_artifact_is_refused_by_the_declared_limit(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"oversize": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("超过清单声明的上限", task.error)

    def test_artifact_outside_the_work_directory_is_refused(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"escape": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("工作目录之外", task.error)

    def test_plugin_returning_no_path_is_refused(self):
        store = self.export_store()
        task = store.tasks.submit("mdreader.sample-export-a:export.samplea",
                                  options={"no_output": True}, wait=30)
        self.assertEqual(task.state, "failed")
        self.assertIn("没有给出产物路径", task.error)

    def test_file_input_is_copied_into_the_work_directory(self):
        store = self.image_store()
        source = write_text(os.path.join(self.base, "图.png"), "x")
        with open(source, "wb") as stream:      # 用真实 PNG 头
            stream.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={}, input_files=[source], wait=30)
        self.assertEqual(task.state, "done", task.error)
        checked = store.tasks.result(task.id)
        self.assertTrue(os.path.isfile(checked["assets"][0]["path"]))

    def test_unknown_command_is_refused_before_any_process_starts(self):
        store = self.image_store()
        with self.assertRaises(P.PluginError):
            store.tasks.submit("mdreader.sample-image:nope", wait=1)
        with self.assertRaises(P.PluginError):
            store.tasks.submit("mdreader.sample-export-b:export.sampleb", wait=1)

    def test_state_and_core_module_stay_free_of_plugin_imports(self):
        store = self.store()
        store.install(self.package(SAMPLE_IMAGE))
        store.enable("mdreader.sample-image")
        store.list_plugins()
        loaded = [name for name in sys.modules if name.startswith("mdreader_plugin_")]
        self.assertEqual(loaded, [], "发现插件不能把插件代码导进宿主进程")

    def test_cleanup_finished_removes_task_work_directories(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, wait=30)
        self.assertTrue(os.path.isdir(task.workdir))
        store.tasks.cleanup_finished()
        self.assertFalse(os.path.isdir(task.workdir))
        with self.assertRaises(P.TaskError):
            store.tasks.result(task.id)

    def test_task_work_directories_live_under_the_workspace(self):
        store = self.image_store()
        task = store.tasks.submit("mdreader.sample-image:insert.image",
                                  options={"synthetic": True}, wait=30)
        self.assertTrue(P.is_within(os.path.join(self.ws_root, "plugins"), task.workdir))
        self.assertFalse(os.path.isdir(os.path.join(self.base, "用户资料")))


# ==========================================================================
# 服务端接口：两端共用同一批能力，禁用后服务端也拒绝
# ==========================================================================

class PluginApiTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.api = core.Api(core.Workspace(self.ws_root), self.ws_root)
        self.ws = self.api.ws
        self.ws.plugins.trust = P.TrustRegistry(self.trust_file)
        self.stores.append(self.ws.plugins)
        self.write_trust()

    def install(self, sample=SAMPLE_A, **kwargs):
        path = self.package(sample, **kwargs)
        return self.api.post("/api/plugins/install", {"path": path})

    def loose_doc(self, name="笔记.md", text="# 笔记\n\n正文\n"):
        path = write_text(os.path.join(self.base, "用户资料", name), text)
        self.api.loose.open_path(path)
        return path

    def test_status_lists_reserved_plugins_and_no_commands(self):
        state = self.api.get("/api/plugins", {})
        self.assertEqual(state["api_version"], P.PLUGIN_API_VERSION)
        self.assertEqual(state["commands"], [])
        self.assertIn("mdreader.export-pdf", [row["id"] for row in state["plugins"]])
        self.assertIn("path", state["trust"])

    def test_install_via_path_then_enable_registers_the_command(self):
        result = self.install(SAMPLE_IMAGE)
        row = next(r for r in result["plugins"] if r["id"] == "mdreader.sample-image")
        self.assertTrue(row["installed"])
        self.assertFalse(row["enabled"], "安装后由用户决定是否启用")
        self.assertEqual(result["commands"], [])
        after = self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-image", "enabled": True})
        self.assertEqual([c["command"] for c in after["commands"]],
                         ["mdreader.sample-image:insert.image"])

    def test_install_via_upload_leaves_no_incoming_file(self):
        path = self.package(SAMPLE_A)
        import base64
        with open(path, "rb") as stream:
            payload = base64.b64encode(stream.read()).decode("ascii")
        result = self.api.post("/api/plugins/install", {"b64": payload, "name": "sample.zip"})
        self.assertTrue(result["result"]["verified"])
        incoming = os.path.join(self.ws.plugins.plugins_dir, "incoming")
        self.assertEqual(os.listdir(incoming) if os.path.isdir(incoming) else [], [])

    def test_untrusted_package_is_refused_and_unverified_requires_a_decision(self):
        path = self.package(SAMPLE_A, trust=False)
        with self.assertRaises(P.TrustError):
            self.api.post("/api/plugins/install", {"path": path})
        accepted = self.api.post("/api/plugins/install", {"path": path, "allow_unverified": True})
        self.assertFalse(accepted["result"]["verified"])
        self.assertIn("未核对", accepted["result"]["note"])

    def test_disabled_plugin_command_is_refused_by_the_server(self):
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": False})
        with self.assertRaises(P.PluginError):
            self.api.post("/api/plugins/command",
                          {"command": "mdreader.sample-export-a:export.samplea", "wait": 1})

    def test_task_endpoint_reports_progress_and_can_be_cancelled(self):
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        started = self.api.post("/api/plugins/command",
                                {"command": "mdreader.sample-export-a:export.samplea",
                                 "options": {"sleep": 20}, "wait": 1})
        self.assertTrue(started["pending"])
        task_id = started["task"]["id"]
        polled = self.api.get("/api/plugins/task", {"id": task_id, "wait": 1})
        self.assertIn(polled["task"]["state"], ("queued", "running"))
        cancelled = self.api.post("/api/plugins/task/cancel", {"id": task_id})
        self.assertEqual(cancelled["task"]["state"], "cancelled")

    def test_image_insert_writes_the_asset_next_to_the_document(self):
        self.install(SAMPLE_IMAGE)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-image", "enabled": True})
        doc = self.loose_doc()
        picture = tiny_png(os.path.join(self.base, "素材", "示意图.png"))
        result = self.api.post("/api/plugins/insert",
                               {"doc": doc, "revision": core.D.revision(doc),
                                "path": picture, "options": {"alt": "示意图"},
                                "entry": "web", "wait": 60})
        self.assertFalse(result["pending"])
        self.assertEqual(result["markdown"], "![示意图](assets/示意图.png)")
        landed = os.path.join(os.path.dirname(doc), "assets", "示意图.png")
        self.assertTrue(os.path.isfile(landed))
        self.assertTrue(os.path.isfile(picture), "原图是复制不是移动")
        self.assertTrue(os.path.isfile(doc), "源文档不能被改写")
        with open(doc, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "# 笔记\n\n正文\n")

    def test_image_insert_accepts_an_uploaded_image(self):
        self.install(SAMPLE_IMAGE)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-image", "enabled": True})
        doc = self.loose_doc()
        import base64
        with open(tiny_png(os.path.join(self.base, "素材", "贴图.png")), "rb") as stream:
            payload = base64.b64encode(stream.read()).decode("ascii")
        result = self.api.post("/api/plugins/insert",
                               {"doc": doc, "b64": payload, "name": "贴图.png", "wait": 60})
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(doc), "assets", "贴图.png")))
        self.assertIn("assets/贴图.png", result["markdown"])

    def test_image_insert_is_refused_for_an_unauthorized_document(self):
        self.install(SAMPLE_IMAGE)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-image", "enabled": True})
        outside = write_text(os.path.join(self.base, "别处", "文件.md"), "# 外面\n")
        with self.assertRaises(PermissionError):
            self.api.post("/api/plugins/insert",
                          {"doc": outside, "path": tiny_png(os.path.join(self.base, "素材", "a.png")),
                           "wait": 10})

    def test_image_insert_refuses_a_changed_revision(self):
        self.install(SAMPLE_IMAGE)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-image", "enabled": True})
        doc = self.loose_doc()
        picture = tiny_png(os.path.join(self.base, "素材", "b.png"))
        with self.assertRaises(P.StaleResult):
            self.api.post("/api/plugins/insert",
                          {"doc": doc, "revision": "不一样的修订", "path": picture, "wait": 60})

    def test_export_writes_the_target_and_guards_overwrite(self):
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        doc = self.loose_doc()
        dest = os.path.join(self.base, "导出", "笔记.samplea")
        # F06：缓冲区与磁盘不一致时，必须先明确选“导出哪一份内容”
        first = self.api.post("/api/plugins/export",
                              {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                               "dest": dest, "markdown": "# 当前编辑内容\n",
                               "source": "buffer", "wait": 60})
        self.assertTrue(os.path.isfile(first["path"]))
        with open(first["path"], "rb") as stream:
            self.assertIn(b"# ", stream.read())
        again = self.api.post("/api/plugins/export",
                              {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                               "dest": dest, "markdown": "# 又一次\n",
                               "source": "buffer", "wait": 60})
        self.assertTrue(again.get("conflict"), "目标已存在时必须先确认")
        forced = self.api.post("/api/plugins/export",
                               {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                                "dest": dest, "markdown": "# 覆盖后的内容\n",
                                "source": "buffer", "overwrite": True, "wait": 60})
        self.assertTrue(forced["ok"])
        with open(dest, "rb") as stream:
            self.assertIn("覆盖后的内容".encode("utf-8"), stream.read())

    def test_export_refuses_a_document_whose_preflight_found_an_error(self):
        """F06：严重问题（缺图）直接拒绝导出，也不留下半成品。"""
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        doc = self.loose_doc(text="# 缺图\n\n![图](assets/没有这张.png)\n")
        dest = os.path.join(self.base, "导出", "缺图.samplea")
        result = self.api.post("/api/plugins/export",
                               {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                                "dest": dest, "wait": 60})
        self.assertTrue(result.get("blocked"), result)
        self.assertIn("找不到", result["preflight"]["errors"][0]["message"])
        self.assertFalse(os.path.exists(dest))

    def test_degradable_preflight_items_need_a_confirmation_first(self):
        """F06：可降级的问题（死链、写不出来的公式、过宽表格）要先确认才导出。"""
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        doc = self.loose_doc(text="# 有提示\n\n[死链](没有这篇.md) 与公式 $\\foo{x}$。\n")
        dest = os.path.join(self.base, "导出", "有提示.samplea")
        ask = self.api.post("/api/plugins/export",
                            {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                             "dest": dest, "wait": 60})
        self.assertTrue(ask.get("needs_confirm"), ask)
        self.assertTrue(ask["preflight"]["warnings"])
        self.assertFalse(os.path.exists(dest), "确认之前不能写出文件")
        done = self.api.post("/api/plugins/export",
                             {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                              "dest": dest, "confirm": True, "wait": 60})
        self.assertTrue(done.get("ok"), done)
        self.assertTrue(os.path.isfile(dest))

    def test_export_asks_which_copy_to_use_when_there_are_unsaved_edits(self):
        """F06：未保存时不许猜——先让用户选当前编辑内容还是磁盘版本。"""
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        doc = self.loose_doc()
        dest = os.path.join(self.base, "导出", "两份.samplea")
        ask = self.api.post("/api/plugins/export",
                            {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                             "dest": dest, "markdown": "# 改过还没保存\n", "wait": 60})
        self.assertTrue(ask.get("needs_source"), ask)
        self.assertFalse(os.path.exists(dest), "没选来源之前不能写出文件")
        chosen = self.api.post("/api/plugins/export",
                               {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                                "dest": dest, "markdown": "# 改过还没保存\n",
                                "source": "disk", "wait": 60})
        self.assertTrue(chosen["ok"])
        with open(dest, "rb") as stream:
            self.assertNotIn("改过还没保存".encode("utf-8"), stream.read(),
                             "选了磁盘版本就不该把缓冲区写出去")

    def test_export_refuses_the_source_document_as_target(self):
        self.install(SAMPLE_A)
        self.api.post("/api/plugins/toggle", {"id": "mdreader.sample-export-a", "enabled": True})
        doc = self.loose_doc()
        with self.assertRaises(ValueError):
            self.api.post("/api/plugins/export",
                          {"doc": doc, "command": "mdreader.sample-export-a:export.samplea",
                           "dest": doc, "wait": 10})

    def test_uninstall_through_the_api_keeps_user_files(self):
        self.install(SAMPLE_A)
        doc = self.loose_doc()
        asset = write_text(os.path.join(os.path.dirname(doc), "assets", "已插入.png"), "png")
        removed = self.api.post("/api/plugins/remove", {"id": "mdreader.sample-export-a"})
        self.assertTrue(removed["result"]["removed"])
        self.assertEqual(removed["commands"], [])
        self.assertTrue(os.path.isfile(os.path.join(doc)))
        self.assertTrue(os.path.isfile(asset))

    def test_state_endpoint_does_not_expose_plugin_internals(self):
        self.install(SAMPLE_A)
        state = self.api.get("/api/state", {})
        self.assertNotIn("plugins", state, "状态接口仍只报核心状态；插件状态走 /api/plugins")


# ==========================================================================
# 桌面窗口：菜单入口、插图、导出、关闭时清理
# ==========================================================================

class PluginDesktopTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.win = None

    def window(self):
        """建一个隐藏的真实窗口，并把它的仓库接到临时受信清单上。"""
        from mdreader import winui
        self._drop = mock.patch.object(winui.MarkdownWindow, "enable_file_drop")
        self._drop.start()
        self.addCleanup(self._drop.stop)
        win = winui.MarkdownWindow(self.ws_root)
        win.root.withdraw()
        win.ws.plugins.trust = P.TrustRegistry(self.trust_file)
        self.stores.append(win.ws.plugins)
        self.write_trust()

        def destroy():
            try:
                for timer in win.root.tk.call("after", "info"):
                    win.root.after_cancel(timer)
                win.root.destroy()
            except Exception:
                pass          # 测试自己已经关过窗口（关窗清理用例）
        self.addCleanup(destroy)
        self.win = win
        return win

    def enable(self, sample):
        win = self.win
        win.ws.plugins.install(self.package(sample))
        pid = read_json(os.path.join(SAMPLES, os.path.basename(sample), P.MANIFEST_NAME))["id"]
        win.ws.plugins.enable(pid)
        return pid

    def open_document(self, name="笔记.md", text="# 笔记\n\n正文\n"):
        path = write_text(os.path.join(self.base, "用户资料", name), text)
        self.win.open_local_files([path])
        self.win.show_source()
        return path

    def test_menu_entries_follow_the_enabled_plugins(self):
        win = self.window()
        entries = win.plugin_menu_entries()
        self.assertIn("插件未启用", entries["insert"][0])
        self.assertEqual(entries["exports"], [])
        self.assertIn("插件管理", entries["manage"][0])

        self.enable(SAMPLE_A)
        self.enable(SAMPLE_B)
        labels = [label for label, _ in win.plugin_menu_entries()["exports"]]
        self.assertEqual(len(labels), 2)
        self.assertTrue(any(".samplea" in label for label in labels))
        self.assertTrue(any(".sampleb" in label for label in labels))

        self.enable(SAMPLE_IMAGE)
        self.assertIn("样例插件", win.plugin_menu_entries()["insert"][0])
        win.ws.plugins.disable("mdreader.sample-image")
        self.assertIn("插件未启用", win.plugin_menu_entries()["insert"][0])

    def test_insert_image_lands_next_to_the_document_and_is_one_undo_step(self):
        win = self.window()
        self.enable(SAMPLE_IMAGE)
        doc = self.open_document()
        picture = tiny_png(os.path.join(self.base, "素材", "插图.png"))
        with mock.patch.object(core, "_dialog_images", return_value=[picture]):
            win.insert_image_with_plugin()
        landed = os.path.join(os.path.dirname(doc), "assets", "插图.png")
        self.assertTrue(os.path.isfile(landed))
        self.assertIn("![插图](assets/插图.png)", win.get_text())
        self.assertTrue(win.dirty, "插入后应标记为未保存（不自动保存）")
        win.text.edit_undo()
        self.assertNotIn("![插图]", win.get_text(), "一次撤销就撤掉这次插入")

        # 取消选择框：一个字节都不动
        before = win.get_text()
        with mock.patch.object(core, "_dialog_images", return_value=[]):
            win.insert_image_with_plugin()
        self.assertEqual(win.get_text(), before)

    def test_insert_image_is_refused_without_a_saved_document_or_plugin(self):
        win = self.window()
        self.open_document()
        with mock.patch.object(core, "_dialog_images") as picker:
            win.insert_image_with_plugin()
        self.assertFalse(picker.called, "没有启用的插件时不该弹图片选择框")
        self.assertIn("插件管理", win.lbl_status.cget("text"))

        self.enable(SAMPLE_IMAGE)
        win.new_loose_draft()
        with mock.patch.object(core, "_dialog_images", return_value=["x.png"]) as picker:
            win.insert_image_with_plugin()
        self.assertFalse(picker.called, "文档没有磁盘文件时不该弹图片选择框")
        self.assertIn("请先保存", win.lbl_status.cget("text"))

    def test_export_writes_the_chosen_target(self):
        win = self.window()
        self.enable(SAMPLE_A)
        doc = self.open_document()
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        dest = os.path.join(self.base, "导出", "笔记.samplea")
        with mock.patch.object(core, "_dialog_save", return_value=dest):
            win.plugin_export_document(command)
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(os.path.basename(dest), win.lbl_status.cget("text"))
        self.assertTrue(os.path.isfile(doc), "导出不动源文档")

        # 有未保存编辑时先问“导出哪一份内容”，选当前编辑内容时导出的是缓冲
        win.text.insert("end", "\n只存在于缓冲里的一行\n")
        win.source = win.get_text()
        win.set_dirty(True)
        dest2 = os.path.join(self.base, "导出", "缓冲.samplea")
        with mock.patch.object(core, "_dialog_save", return_value=dest2), \
             mock.patch.object(winui.ExportSourceDialog, "show", return_value="buffer"):
            win.plugin_export_document(command)
        with open(dest2, "rb") as stream:
            self.assertIn("只存在于缓冲里的一行".encode("utf-8"), stream.read())
        with open(doc, encoding="utf-8") as stream:
            self.assertNotIn("只存在于缓冲里的一行", stream.read())

        # 取消保存框：不启动任务
        with mock.patch.object(core, "_dialog_save", return_value=""), \
             mock.patch.object(win.ws.plugins.tasks, "submit") as submit:
            win.plugin_export_document(command)
        self.assertFalse(submit.called)

    def test_unsaved_document_can_export_the_disk_copy_or_be_cancelled(self):
        """F06：来源的选择权在用户手里——选磁盘版本就导出旧内容，取消就什么都不做。"""
        win = self.window()
        self.enable(SAMPLE_A)
        doc = self.open_document(text="# 磁盘上的版本\n\n旧正文\n")
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        win.text.insert("end", "\n只在缓冲里\n")
        win.source = win.get_text()
        win.set_dirty(True)

        dest = os.path.join(self.base, "导出", "磁盘版.samplea")
        with mock.patch.object(core, "_dialog_save", return_value=dest), \
             mock.patch.object(winui.ExportSourceDialog, "show", return_value="disk"):
            win.plugin_export_document(command)
        with open(dest, "rb") as stream:
            body = stream.read()
        self.assertIn("磁盘上的版本".encode("utf-8"), body)
        self.assertNotIn("只在缓冲里".encode("utf-8"), body)

        cancelled = os.path.join(self.base, "导出", "取消.samplea")
        with mock.patch.object(core, "_dialog_save", return_value=cancelled), \
             mock.patch.object(winui.ExportSourceDialog, "show", return_value=None):
            win.plugin_export_document(command)
        self.assertFalse(os.path.exists(cancelled), "取消来源选择后不能写出文件")
        self.assertIn("取消", win.lbl_status.cget("text"))

    def test_preflight_blocks_a_missing_image_and_explains_it(self):
        """F06：缺图是严重问题——报告里说清楚，且不启动导出任务。"""
        win = self.window()
        self.enable(SAMPLE_A)
        self.open_document(text="# 有缺图\n\n![图](assets/没有这张.png)\n")
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        dest = os.path.join(self.base, "导出", "缺图.samplea")
        shown = {}

        def capture(_self):
            shown["text"] = _self.window.children
            return False

        with mock.patch.object(core, "_dialog_save", return_value=dest), \
             mock.patch.object(win.ws.plugins.tasks, "submit") as submit, \
             mock.patch.object(winui.ExportReportDialog, "show", autospec=True,
                               side_effect=capture) as report:
            win.plugin_export_document(command)
        self.assertTrue(report.called, "缺图时必须给出预检报告")
        self.assertFalse(submit.called, "有严重问题时不能启动导出")
        self.assertFalse(os.path.exists(dest))

    def test_existing_target_is_confirmed_and_cancelling_keeps_it(self):
        win = self.window()
        self.enable(SAMPLE_A)
        self.open_document()
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        dest = write_text(os.path.join(self.base, "导出", "已存在.samplea"), "旧内容")
        with mock.patch.object(core, "_dialog_save", return_value=dest), \
             mock.patch("tkinter.messagebox.askyesno", return_value=False) as asked:
            win.plugin_export_document(command)
        self.assertTrue(asked.called, "目标已存在时必须先问一次")
        with open(dest, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "旧内容")

        with mock.patch.object(core, "_dialog_save", return_value=dest), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True):
            win.plugin_export_document(command)
        with open(dest, "rb") as stream:
            self.assertIn(b"SAMPA", stream.read(), "确认后覆盖成新产物")

    def test_export_failure_keeps_the_original_target(self):
        win = self.window()
        self.enable(SAMPLE_A)
        self.open_document()
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        dest = write_text(os.path.join(self.base, "导出", "目标.samplea"), "原始内容")
        with mock.patch.object(core, "_dialog_save", return_value=dest), \
             mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch.object(win.ws.plugins.tasks, "commit_export",
                               side_effect=P.TaskError("写盘失败")):
            win.plugin_export_document(command)
        with open(dest, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "原始内容")
        self.assertIn("未改动", win.lbl_status.cget("text"))

    def test_export_cancel_button_preserves_the_existing_target(self):
        win = self.window()
        self.enable(SAMPLE_A)
        self.open_document()
        command = win.plugin_commands(core.PL.CAP_EXPORT)[0]
        dest = write_text(os.path.join(self.base, 'cancel.samplea'), 'original')
        clicked = []
        def click_cancel():
            def search(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == 'Button' and child.cget('text') == '取消导出':
                        child.invoke()
                        clicked.append(True)
                        return True
                    if search(child):
                        return True
                return False
            if not search(win.root):
                win.root.after(5, click_cancel)
        win.root.after(5, click_cancel)
        self.assertFalse(win._plugin_export_call({'command': command['command'],
            **win._plugin_document_context(), 'dest': dest, 'overwrite': True, 'confirm': True}, dest))
        self.assertTrue(clicked)
        self.assertIn('已取消导出', win.lbl_status.cget('text'))
        with open(dest, encoding='utf-8') as stream:
            self.assertEqual(stream.read(), 'original')

    def test_plugin_dialog_lists_plugins_and_toggles_them(self):
        from mdreader.plugin_ui import PluginDialog
        win = self.window()
        self.enable(SAMPLE_A)
        dialog = PluginDialog(win)
        dialog.build()
        try:
            rows = [dialog.tree.item(iid, "values") for iid in dialog.tree.get_children()]
            names = [row[0] for row in rows]
            self.assertTrue(any("样例插件" in name for name in names))
            self.assertTrue(any(row[0] == "mdreader.export-pdf" for row in rows),
                            "未发布的正式插件也要显示占位与原因")
            self.assertTrue([row for row in rows if row[2] == "未安装"])
            index = [row["id"] for row in dialog.rows].index("mdreader.sample-export-a")
            dialog.tree.selection_set(str(index))
            dialog.refresh_detail()
            self.assertEqual(str(dialog.buttons["禁用"]["state"]), "normal")
            self.assertEqual(str(dialog.buttons["启用"]["state"]), "disabled")

            path = self.package(SAMPLE_B)
            with mock.patch.object(core, "_dialog_plugin_package", return_value=[path]):
                dialog.install()
            self.assertIn("mdreader.sample-export-b", [row["id"] for row in dialog.rows])
            dialog.tree.selection_set(str([row["id"] for row in dialog.rows]
                                          .index("mdreader.sample-export-b")))
            dialog.enable()
            self.assertTrue(win.plugin_commands(core.PL.CAP_EXPORT))
            with mock.patch("tkinter.messagebox.askyesno", return_value=True):
                dialog.uninstall()
            self.assertNotIn("mdreader.sample-export-b",
                             [row["id"] for row in dialog.rows if row["installed"]])
        finally:
            dialog.close()

    def test_disabling_or_closing_stops_running_plugin_tasks(self):
        win = self.window()
        self.enable(SAMPLE_A)
        task = win.ws.plugins.tasks.submit("mdreader.sample-export-a:export.samplea",
                                           options={"sleep": 20}, wait=1.0)
        self.assertEqual(task.state, "running")
        win.ws.plugins.disable("mdreader.sample-export-a")
        self.assertEqual(win.ws.plugins.tasks.poll(task.id)["state"], "cancelled")

        win.ws.plugins.enable("mdreader.sample-export-a")
        hanging = win.ws.plugins.tasks.submit("mdreader.sample-export-a:export.samplea",
                                              options={"hang": True}, wait=1.0)
        self.assertEqual(hanging.state, "running")
        win.root.destroy()
        deadline = time.time() + 15
        while time.time() < deadline and hanging.state == "running":
            time.sleep(0.2)
        self.assertNotEqual(hanging.state, "running", "关窗后不能留下插件工作进程")
        self.assertIsNone(hanging.process)


class HostIsolationTests(PluginTestCase):
    def test_workspace_starts_without_any_plugin(self):
        ws = core.Workspace(self.ws_root)
        self.assertEqual(ws.plugins.commands(), [])
        self.assertEqual(ws.plugins.status()["plugins"][0]["state"], "not-installed")

    def test_core_can_still_write_documents_with_no_plugin_installed(self):
        ws = core.Workspace(self.ws_root)
        pdir = ws.require_project(ws.create_project("普通项目")["id"])
        info = ws.create_doc(pdir, "笔记")
        self.assertTrue(os.path.isfile(info["_abs"]))
        self.assertEqual(ws.plugins.commands(), [])

    def test_package_build_is_deterministic(self):
        first = self.package(SAMPLE_A)
        second = self.package(SAMPLE_A)
        self.assertEqual({sha256(first), sha256(second)}, {sha256(first)},
                         "同一份源码必须得到同一个哈希")

    def test_version_key_ignores_suffixes(self):
        self.assertEqual(P.version_key("1.2.3"), (1, 2, 3, 0))
        self.assertEqual(P.version_key("1.2.3-beta"), (1, 2, 3, 0))


if __name__ == "__main__":
    unittest.main()
