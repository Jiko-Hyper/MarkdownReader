"""A01: verify real shipped exports, never user's files. Exit nonzero on defects.

Run from any directory with Python 3.12 and Pillow/pypdfium2 installed.
Each run keeps its own evidence under .testtmp/delivery-*.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mdreader import core

FIXTURES = ROOT / 'tests/fixtures/delivery'
W = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
WP = {'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
# 版式核验的硬边界，与导出插件实际使用的页面参数一致：
# PDF 是 A4 四边 48pt；Word 是 Letter 左右各 0.8in。
A4 = (595.28, 841.89)
PDF_MARGIN = 48.0
# 页脚页码画在下边距之外，只有这一条允许越出正文框。
FOOTER_TOP = 34.0
WORD_CONTENT_WIDTH = 6.9 * 72
LENGTH_TOLERANCE = 1.0
RATIO_TOLERANCE = .03
# make_assets 生成的示例图，用于判断是否被拉伸变形。
ASSET_RATIO = 480 / 240


def content_violations(boxes):
    """正文框以外的字符：截字、裁列、代码越界、公式越界都会落到这里。"""
    bad = []
    for left, bottom, right, top, char in boxes:
        if top <= FOOTER_TOP:
            continue
        if (left < PDF_MARGIN - LENGTH_TOLERANCE or right > A4[0] - PDF_MARGIN + LENGTH_TOLERANCE
                or bottom < PDF_MARGIN - LENGTH_TOLERANCE or top > A4[1] - PDF_MARGIN + LENGTH_TOLERANCE):
            bad.append({'char': char, 'box': [round(value, 1) for value in (left, bottom, right, top)]})
    return bad


def glyph_overlaps(boxes, limit=5):
    """同一页里互相压住的字符（字体回退、负缩进、行距异常都会造成）。"""
    ordered = sorted(boxes)
    found = []
    for index, (left, bottom, right, top, char) in enumerate(ordered):
        height = top - bottom
        for other in ordered[index + 1:]:
            oleft, obottom, oright, otop, ochar = other
            if oleft >= right - LENGTH_TOLERANCE:
                break
            vertical = min(top, otop) - max(bottom, obottom)
            horizontal = min(right, oright) - max(left, oleft)
            if (vertical > LENGTH_TOLERANCE and horizontal > LENGTH_TOLERANCE
                    and vertical > .5 * min(height, otop - obottom)):
                found.append({'chars': char + ochar,
                              'boxes': [[round(value, 1) for value in box]
                                        for box in ((left, bottom, right, top), other[:4])]})
                if len(found) >= limit:
                    return found
    return found


def compact(text):
    return re.sub(r'\s+', '', text)


def missing_text(expected, actual):
    """Count repeated spans too; whitespace differences are layout, not data loss."""
    actual = compact(actual)
    return [text for text, count in Counter(map(compact, expected)).items()
            if actual.count(text) < count]


def anywhere_ratio_differs(sizes, expected):
    """图片被拉伸：实际宽高比与源图不符（只有样本声明了源图比例时才判定）。"""
    if not expected:
        return False
    return any(size['ratio'] and abs(size['ratio'] - expected) / expected > RATIO_TOLERANCE
               for size in sizes)


def inspect_docx(path):
    with zipfile.ZipFile(path) as archive:
        tree = ET.fromstring(archive.read('word/document.xml'))
        numbering = ET.fromstring(archive.read('word/numbering.xml'))
        relationships = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
    abstract = {node.get('{%s}abstractNumId' % W['w']): node for node in numbering.findall('w:abstractNum', W)}
    numbers = {node.get('{%s}numId' % W['w']): node for node in numbering.findall('w:num', W)}
    counters, labels = {}, []
    for paragraph in tree.findall('.//w:p', W):
        number = paragraph.find('w:pPr/w:numPr/w:numId', W)
        if number is None:
            continue
        key = number.get('{%s}val' % W['w'])
        aid = numbers[key].find('w:abstractNumId', W).get('{%s}val' % W['w'])
        level = abstract[aid].find('w:lvl', W)
        if level.find('w:numFmt', W).get('{%s}val' % W['w']) != 'decimal':
            continue
        start = int(level.find('w:start', W).get('{%s}val' % W['w']))
        counters[key] = counters.get(key, start - 1) + 1
        labels.append(counters[key])
    tables = tree.findall('.//w:tbl', W)
    drawings = tree.findall('.//w:drawing', W)
    sizes = []
    for drawing in drawings:
        extent = drawing.find('.//wp:extent', WP)
        if extent is None:
            continue
        width = int(extent.get('cx')) / 12700.0
        height = int(extent.get('cy')) / 12700.0
        sizes.append({'width': round(width, 1), 'height': round(height, 1),
                      'ratio': width / height if height else 0.0})
    # 只有图片、没有文字的单元格不算丢失；两者都没有才是真的空了。
    empty_cells = sum(1 for cell in tree.findall('.//w:tc', W)
                      if not ''.join(node.text or '' for node in cell.findall('.//w:t', W)).strip()
                      and cell.find('.//w:drawing', W) is None)
    return {
        'text': ''.join(node.text or '' for node in tree.findall('.//w:t', W)),
        'images': len(drawings),
        'image_sizes': sizes,
        'empty_cells': empty_cells,
        'table_rows': [len(table.findall('w:tr', W)) for table in tables],
        'repeat_headers': [bool(table.find('w:tr/w:trPr/w:tblHeader', W) is not None)
                           for table in tables],
        'headings': sum(node.get('{%s}val' % W['w'], '').startswith('Heading')
                        for node in tree.findall('.//w:pStyle', W)),
        'numbered_labels': labels,
        'hyperlink_targets': [node.get('Target') for node in relationships
                              if node.get('Type', '').endswith('/hyperlink')],
    }


def inspect_pdf(path, render=True):
    import pypdfium2 as pdfium
    texts, images, outside, overlaps, pages = [], 0, [], [], []
    violations, stacked, boxes_per_page, blank = [], [], [], []
    with pdfium.PdfDocument(str(path)) as pdf:
        for number in range(len(pdf)):
            page = pdf[number]
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            width, height = page.get_size()
            image_bounds = [obj.get_bounds() for obj in page.get_objects()
                            if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
            images += len(image_bounds)
            boxes = []
            for index in range(textpage.count_chars()):
                char = textpage.get_text_range(index, 1)
                if not char.strip():
                    continue
                left, bottom, right, top = textpage.get_charbox(index)
                boxes.append((left, bottom, right, top, char))
                if left < -1 or bottom < -1 or right > width + 1 or top > height + 1:
                    outside.append({'page': number + 1, 'char': char})
                for il, ib, ir, it in image_bounds:
                    if min(right, ir) - max(left, il) > 1 and min(top, it) - max(bottom, ib) > 1:
                        overlaps.append({'page': number + 1, 'char': char})
            violations += [dict(item, page=number + 1) for item in content_violations(boxes)]
            stacked += [dict(item, page=number + 1) for item in glyph_overlaps(boxes)]
            boxes_per_page.append([{'box': [round(value, 1) for value in (il, ib, ir, it)],
                                    'width': round(ir - il, 1), 'height': round(it - ib, 1),
                                    'ratio': (ir - il) / (it - ib) if it > ib else 0.0,
                                    'outside': (il < PDF_MARGIN - LENGTH_TOLERANCE
                                                or ir > A4[0] - PDF_MARGIN + LENGTH_TOLERANCE
                                                or ib < PDF_MARGIN - LENGTH_TOLERANCE
                                                or it > A4[1] - PDF_MARGIN + LENGTH_TOLERANCE)}
                                   for il, ib, ir, it in image_bounds])
            if not textpage.get_text_range().strip() and not image_bounds:
                blank.append(number + 1)
            if render:
                target = path.with_name(path.stem + '-page-%02d.png' % (number + 1))
                bitmap = page.render(scale=1.5)
                picture = bitmap.to_pil()
                picture.save(target)
                picture.close()
                bitmap.close()
                pages.append(target.name)
            textpage.close()
            page.close()
    return {'text': '\n'.join(texts), 'images': images, 'pages': len(texts),
            'page_text': texts, 'image_boxes': boxes_per_page,
            'outside_page': outside, 'content_violations': violations,
            'glyph_overlaps': stacked, 'blank_pages': blank,
            'image_text_overlap': overlaps, 'page_images': pages}


def install_exports(ws):
    packages = {}
    for path in (ROOT / 'plugins/packages').glob('*.zip'):
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        if manifest['id'] not in ('mdreader.export-docx', 'mdreader.export-pdf'):
            continue
        version = tuple(map(int, manifest['version'].split('.')))
        old = packages.get(manifest['id'])
        if old is None or version > old[0]:
            packages[manifest['id']] = (version, path, manifest)
    evidence = []
    for pid, (_, path, manifest) in sorted(packages.items()):
        ws.plugins.install(str(path))
        ws.plugins.enable(pid)
        evidence.append({'id': pid, 'version': manifest['version'],
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if len(evidence) != 2:
        raise RuntimeError('Build the two official export packages first')
    return evidence


def make_assets(folder):
    from PIL import Image, ImageDraw
    folder.mkdir()
    picture = Image.new('RGB', (480, 240), '#edf3fa')
    draw = ImageDraw.Draw(picture)
    draw.rectangle((20, 20, 220, 220), fill='#2563a6')
    draw.ellipse((260, 20, 460, 220), fill='#499177')
    for name in ('diagram.png', '中文 图.png'):
        picture.save(folder / name)
    picture.close()
    (folder / 'broken.png').write_bytes(b'not a PNG')


def missing_table_headers(page_text, labels):
    """The long-table fixture occupies every page, including its first page."""
    return [number for number, text in enumerate(page_text, 1)
            if labels and not all(label in text for label in labels)]


def page_machine_issues(row, number):
    inspection = row.get('inspection', {})
    issues = [issue for issue in row['issues'] if ('第 %d 页' % number) in issue]
    for key, label in [('outside_page', '字符超出页面'), ('content_violations', '内容越出页边距'),
                       ('glyph_overlaps', '文字互相重叠'), ('image_text_overlap', '图片覆盖文字')]:
        if any(item['page'] == number for item in inspection.get(key, [])):
            issues.append(label)
    if number in inspection.get('blank_pages', []):
        issues.append('空白页')
    boxes = inspection.get('image_boxes', [])
    if number <= len(boxes) and any(box['outside'] for box in boxes[number - 1]):
        issues.append('图片越出页边距')
    return issues


def verify_case(api, commands, folder, case, render):
    document = folder / (case['id'] + '.md')
    original = document.read_bytes()
    info = api.loose.open_path(str(document))
    rows = []
    for ext in ('docx', 'pdf'):
        target = folder / (case['id'] + '.' + ext)
        row = {'id': case['id'], 'title': case['title'], 'format': ext,
               'issues': [], 'visual': '待验收', 'degradation': case.get('degradation', '')}
        request = {'command': commands[ext], 'doc': str(document),
                   'revision': info['revision'], 'dest': str(target), 'wait': 120}
        if 'buffer' in case:
            request['markdown'] = case['buffer']
        try:
            # Conflict alone is not a running-task cancellation or failure test.
            target.write_bytes(b'existing-output-do-not-overwrite')
            guard = api.post('/api/plugins/export', dict(request, source='buffer', confirm=True))
            if not (guard.get('conflict') or guard.get('blocked')):
                row['issues'].append('已有目标未要求确认')
            if target.read_bytes() != b'existing-output-do-not-overwrite':
                row['issues'].append('已有产物被意外覆盖')
            # The sentinel belongs to this newly allocated test directory only.
            if case.get('failure'):
                request.update(overwrite=True, confirm=True)
            else:
                target.unlink()
            result = api.post('/api/plugins/export', request)
            if result.get('needs_source'):
                if target.exists():
                    row['issues'].append('选择来源之前已生成产物')
                request['source'] = 'buffer'
                result = api.post('/api/plugins/export', request)
            if result.get('needs_confirm'):
                if target.exists():
                    row['issues'].append('预检确认之前已生成产物')
                request['confirm'] = True
                result = api.post('/api/plugins/export', request)
            row['preflight'] = result.get('preflight', {})
            row['warnings'] = result.get('warnings', [])
            if case.get('blocked'):
                kinds = [item['kind'] for item in row['preflight'].get('errors', [])]
                if not result.get('blocked') or case['blocked'] not in kinds or target.exists():
                    row['issues'].append('应阻止导出的样本未正确阻止')
                row['visual'] = '不适用：阻止导出'
            elif case.get('failure'):
                row['issues'].append('损坏附件没有报告失败')
            elif not target.is_file() or result.get('pending'):
                row['issues'].append('没有生成完整产物：' + str(result))
            else:
                inspected = inspect_docx(target) if ext == 'docx' else inspect_pdf(target, render)
                row['missing_text'] = missing_text(case['text'], inspected.pop('text'))
                if row['missing_text']:
                    row['issues'].append('正文或单元格内容缺失')
                if inspected['images'] < case.get('images', 0):
                    row['issues'].append('图片缺失：期望 %d，实际 %d' % (case['images'], inspected['images']))
                if ext == 'docx':
                    if inspected['table_rows'] != case.get('table_rows', []):
                        row['issues'].append('Word 表格行数不一致')
                    if not all(inspected['repeat_headers']):
                        row['issues'].append('Word 表头未设置跨页重复')
                    if inspected['headings'] < case.get('headings', 0):
                        row['issues'].append('Word 标题层级丢失')
                    if 'numbers' in case and inspected['numbered_labels'] != case['numbers']:
                        row['issues'].append('Word 列表起始编号或续段编号错误')
                    if any(link not in inspected['hyperlink_targets'] for link in case.get('links', [])):
                        row['issues'].append('Word 超链接目标丢失')
                    if any(size['width'] > WORD_CONTENT_WIDTH + LENGTH_TOLERANCE
                           for size in inspected['image_sizes']):
                        row['issues'].append('Word 图片超出正文宽度')
                    if anywhere_ratio_differs(inspected['image_sizes'], case.get('image_ratio')):
                        row['issues'].append('Word 图片被拉伸变形')
                    if inspected['empty_cells']:
                        row['issues'].append('Word 表格出现空单元格')
                elif inspected['outside_page']:
                    row['issues'].append('PDF 字符超出页面')
                if ext == 'pdf':
                    if inspected['content_violations']:
                        row['issues'].append('PDF 内容越出页边距')
                    if any(box['outside'] for page in inspected['image_boxes'] for box in page):
                        row['issues'].append('PDF 图片越出页边距')
                    if inspected['glyph_overlaps']:
                        row['issues'].append('PDF 文字互相重叠')
                    if inspected['blank_pages']:
                        row['issues'].append('PDF 出现空白页')
                    if any(not text.strip() for text in inspected['page_text']):
                        row['issues'].append('PDF 存在无文字页面')
                    if anywhere_ratio_differs([box for page in inspected['image_boxes'] for box in page],
                                              case.get('image_ratio')):
                        row['issues'].append('PDF 图片被拉伸变形')
                    for number in missing_table_headers(inspected['page_text'], case.get('header_labels')):
                        row['issues'].append('PDF 第 %d 页跨页表头不完整' % number)
                if ext == 'pdf' and inspected['image_text_overlap']:
                    row['issues'].append('PDF 图片覆盖文字')
                row['inspection'] = inspected
                if case.get('warning_kind') and case['warning_kind'] not in [
                        item['kind'] for item in row['preflight'].get('warnings', [])]:
                    row['issues'].append('缺少预检降级提示')
                if case.get('warning_text') and not any(case['warning_text'] in warning
                                                        for warning in row['warnings']):
                    row['issues'].append('缺少导出降级提示')
                if ext == 'pdf' and 'numbers' in case:
                    # The list sample has no other numbers. Check visible markers,
                    # including the absence of a marker on continuation paragraphs.
                    extracted = inspect_pdf(target, render=False)['text']
                    labels = [int(value) for value in re.findall(r'(?m)^\s*(\d+)\.', extracted)]
                    if labels != case['numbers']:
                        row['issues'].append('PDF 列表起始编号或续段编号错误')
        except Exception as error:
            row['exception'] = '%s: %s' % (type(error).__name__, error)
            if not case.get('failure') or not isinstance(error, ValueError) or 'UnidentifiedImageError' not in str(error):
                row['issues'].append(row['exception'])
            elif not target.exists() or target.read_bytes() != b'existing-output-do-not-overwrite':
                row['issues'].append('确认覆盖后失败破坏了已有产物')
            else:
                row['preserved_existing_output'] = True
                row['visual'] = '不适用：损坏图片失败'
        if document.read_bytes() != original:
            row['issues'].append('源文件被修改')
        row['status'] = '失败' if row['issues'] else '自动检查通过'
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-render', action='store_true', help='Skip PDF PNGs, not content checks')
    parser.add_argument('--case', action='append', help='Run specific sample ids')
    args = parser.parse_args()
    cases = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))['cases']
    if args.case:
        unknown = set(args.case) - {case['id'] for case in cases}
        if unknown:
            parser.error('Unknown cases: ' + ', '.join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in args.case]
    temp = ROOT / '.testtmp'
    temp.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='delivery-', dir=temp))
    samples = out / 'samples'
    samples.mkdir()
    for case in cases:
        shutil.copyfile(FIXTURES / (case['id'] + '.md'), samples / (case['id'] + '.md'))
    make_assets(samples / 'assets')
    ws = core.Workspace(str(out / 'workspace'))
    report = {'application': core.APP_VERSION, 'python': sys.version, 'results': []}
    try:
        report['packages'] = install_exports(ws)
        api = core.Api(ws, str(ROOT / 'webui'))
        commands = {cmd['extension']: cmd['command'] for cmd in ws.plugins.commands()}
        for case in cases:
            rows = verify_case(api, commands, samples, case, not args.no_render)
            report['results'].extend(rows)
            print(case['id'], [(row['format'], row['status'], row['issues']) for row in rows], flush=True)
            (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        ws.plugins.shutdown()
    lines = ['# A01 自动检查记录', '', '视觉状态需另行人工填写；自动通过不表示版式已验收。', '',
             '| 样本 | 格式 | 自动检查 | 问题 | 已知降级 | 视觉 |', '|---|---|---|---|---|---|']
    for row in report['results']:
        lines.append('| %s | %s | %s | %s | %s | %s |' % (row['id'], row['format'], row['status'],
                     '; '.join(row['issues']).replace('|', '/'), row['degradation'], row['visual']))
    lines += ['', '## 逐页记录（视觉验收逐页对照；PNG 与产物同目录）', '',
              '| 样本 | 格式 | 页 | 可见字符 | 图片 | 本页机器问题 |', '|---|---|---|---|---|---|']
    for row in report['results']:
        inspection = row.get('inspection') or {}
        page_text = inspection.get('page_text')
        if not page_text:
            continue
        boxes = inspection.get('image_boxes') or []
        for number, text in enumerate(page_text, 1):
            page_issues = page_machine_issues(row, number)
            lines.append('| %s | %s | %d | %d | %d | %s |' % (
                row['id'], row['format'], number, len(compact(text)), len(boxes[number - 1])
                if number <= len(boxes) else 0, '; '.join(page_issues) or '—'))
    (out / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Evidence:', out, flush=True)
    return int(any(row['issues'] for row in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
