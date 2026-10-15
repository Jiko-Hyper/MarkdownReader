"""Trim dependencies the offline scan runtime never loads, without losing a capability.

Evidence behind every entry (2026-10-10, MDReader 0.4.12):

* ``tools/qualify_scan_ocr.py`` and a direct ``mineru.parser.parse`` run were executed in
  this runtime with ``MDREADER_IMPORT_LOG`` set; ``Lib/site-packages/sitecustomize.py``
  records every top-level module imported by every process taking part (MinerU renders
  pages in worker processes).  The packages below were imported by none of them.
* They are also unreachable by design: the scan command accepts PDF only
  (`mdreader.document-convert` manifest magic), the host forces offline model lookup
  (``MINERU_MODEL_SOURCE=local``, ``HF_HUB_OFFLINE=1``, ``MODELSCOPE_OFFLINE=1``), the
  desktop app never starts MinerU's gradio web UI, and DOCX conversion uses the plugin's
  own mammoth under the main runtime, not this one.
* Packages whose need could depend on an input we did not exercise (math rendering for
  detected equations, format detection, terminal width) are deliberately kept.

Usage::

    python tools/prune_scan_runtime.py --runtime build/scan-runtime-portable --check
    python tools/prune_scan_runtime.py --runtime build/scan-runtime-portable --apply
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

#: name -> why it can go
REMOVALS = {
    'gradio': 'MinerU 的网页演示界面，桌面端从不启动',
    'gradio_client': 'gradio 客户端，同上',
    'hf_gradio': 'gradio 的 HuggingFace 主题，同上',
    'groovy': 'gradio 的 Python→JS 转译器，同上',
    'safehttpx': 'gradio 的 HTTP 封装，同上',
    'httpx_retries': 'gradio 的重试封装，同上',
    'opentelemetry': 'gradio 遥测',
    'watchfiles': 'gradio 热重载',
    'starlette': 'gradio 的 ASGI 框架',
    'uvicorn': 'gradio 的 ASGI 服务器',
    'fastapi': 'gradio 的 Web 框架',
    'aiosqlite': 'gradio 的会话数据库',
    'python_multipart': 'gradio 的表单解析',
    'multipart': 'gradio 的表单解析',
    'orjson': 'gradio 的快速 JSON',
    'tomlkit': 'gradio 的配置读写',
    'semantic_version': 'gradio 的版本比较',
    'jinja2': 'gradio 的模板引擎',
    'markupsafe': 'jinja2 的转义库',
    'tinycss2': 'gradio 的 CSS 解析',
    'webencodings': 'tinycss2 的编码表',
    'nh3': 'gradio 的 HTML 清洗',
    'pydub': 'gradio 的音频处理',
    'shellingham': 'typer 的 shell 探测',
    'typer': 'MinerU 命令行入口（桌面端直接调用库）',
    'mammoth': 'DOCX 转换在主运行时的插件依赖里，本运行时用不到',
    'modelscope': '模型下载（宿主强制离线，模型随包提供）',
    'modelscope_hub': '模型下载，同上',
    'hf_xet': 'HuggingFace 传输后端，同样离线',
    'pandas': 'Office/表格分析；扫描命令只接受 PDF',
    'pandas.libs': 'pandas 的本地库',
    'pytz': 'pandas 的时区数据',
    'tzdata': 'pandas 的时区库',
    'openpyxl': 'xlsx 读写（扫描命令只接受 PDF）',
    'xlsxwriter': 'xlsx 写出，同上',
    'et_xmlfile': 'openpyxl 的 XML 写入',
    'docx': 'python-docx；DOCX 转换走插件自带 mammoth',
    'pptx': 'python-pptx；没有 pptx 转换命令',
    'olefile': '旧 Office 复合文档读取',
    'jieba': '中文分词，只有 Office/文本解析路径才用',
    'pip': '安装工具，运行时不使用',
    'setuptools': '打包工具，运行时不使用',
    'pypdfium2_cli': 'pypdfium2 命令行，运行时不使用',
    'reportlab': 'PDF 写出；导出插件自带一份',
}


def package_version(folder: Path) -> str:
    import importlib.metadata as metadata
    try:
        return metadata.distribution(folder.name).version
    except Exception:
        return ''


def dist_infos(site: Path, name: str):
    """这个包可能登记的 dist-info 目录（- 与 _ 两种写法都可能出现，去重）。"""
    found = set()
    for pattern in {name, name.replace('-', '_'), name.replace('_', '-')}:
        found.update(site.glob('%s-*.dist-info' % pattern))
    return sorted(found)


def folder_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='真的删除；默认只报告')
    parser.add_argument('--check', action='store_true', help='只列出会删什么')
    args = parser.parse_args(argv)
    site = args.runtime / 'Lib/site-packages'
    if not (site / 'mineru').is_dir():
        raise SystemExit('Not a scan runtime: %s' % site)
    removed, total = [], 0
    for name, reason in sorted(REMOVALS.items()):
        folder = site / name
        if not folder.is_dir():
            continue
        size = folder_size(folder)
        infos = [item for item in dist_infos(site, name) if item.is_dir()]
        entry = {'name': name, 'version': package_version(folder), 'bytes': size,
                 'dist_info': [item.name for item in infos], 'reason': reason}
        removed.append(entry)
        total += size
        if args.apply:
            shutil.rmtree(folder)
            for item in infos:
                shutil.rmtree(item)
    print('%s %d 个包，%.1f MB（解压后）' % ('已删除' if args.apply else '将删除',
                                            len(removed), total / 2 ** 20))
    for entry in removed:
        print('  %8.2f MB  %-20s %s' % (entry['bytes'] / 2 ** 20, entry['name'], entry['reason']))
    if args.apply:
        target = args.runtime / 'removed-packages.json'
        target.write_text(json.dumps({'removed': removed}, ensure_ascii=False, indent=2),
                          encoding='utf-8')
        print('清单已写入 %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
