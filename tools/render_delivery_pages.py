"""Render Word-produced PDF evidence without marking visual review as passed."""
import argparse
import json
from pathlib import Path
import pypdfium2 as pdfium


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    report = json.loads((args.evidence / 'report.json').read_text(encoding='utf-8'))
    records = []
    for row in report['results']:
        if row['format'] != 'docx' or not row.get('inspection'):
            continue
        path = args.evidence / 'word-pages' / (row['id'] + '.pdf')
        with pdfium.PdfDocument(str(path)) as pdf:
            for index in range(len(pdf)):
                page = pdf[index]
                bitmap = page.render(scale=1.5)
                picture = bitmap.to_pil()
                target = path.with_name(path.stem + '-page-%02d.png' % (index + 1))
                picture.save(target)
                picture.close()
                bitmap.close()
                page.close()
                records.append({'id': row['id'], 'page': index + 1,
                                'image': str(target.relative_to(args.evidence)), 'visual': '待验收'})
    (args.evidence / 'word-pages.json').write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Word preview pages:', len(records))


if __name__ == '__main__':
    main()
