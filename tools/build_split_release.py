"""Produce core, content-addressed OCR components, and a small incremental update."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
if __package__:
    from .package_layout import payload
else:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from package_layout import payload


def is_component(name):
    if name.startswith('resources/'):
        name = name[len('resources/'):]
    return name.startswith(('scan-runtime/', 'runtime/photo_ocr_runtime/'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def files(folder):
    return {p.relative_to(folder).as_posix(): p for p in sorted(folder.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')
            and p.name != 'photo-instructions.expected.md'}


def write_inventory(source, entries=None):
    entries = files(source) if entries is None else entries
    target = payload(source) / 'program-files.json'
    name = target.relative_to(source).as_posix()
    target.write_text(json.dumps({'schema': 1, 'files': sorted(set(entries) | {name})}, ensure_ascii=False), encoding='utf-8')
    return target


def archive(target, entries, extra=None):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, path in entries.items():
            z.write(path, name)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    with zipfile.ZipFile(target) as z:
        if z.testzip():
            raise RuntimeError('Archive checksum check failed')
    digest = sha(target)
    target.with_suffix('.zip.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii')
    print(f'{target.name}: {target.stat().st_size / 1e6:.2f} MB', flush=True)
    return digest


def snapshot(source):
    version = re.search(r'APP_VERSION = "([^"]+)"', (payload(source)/'mdreader/core.py').read_text('utf-8')).group(1)
    return {'version': version, 'files': {name: sha(path) for name, path in files(source).items()
                                        if not is_component(name)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--core-only', action='store_true', help='Reuse the verified component catalog without repacking OCR models')
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--base-url', default='https://github.com/Jiko-Hyper/MarkdownReader/releases/download')
    args = parser.parse_args()
    source = args.source.resolve()
    if args.inventory_only:
        write_inventory(source)
        return
    if args.snapshot:
        args.snapshot.write_text(json.dumps(snapshot(source), ensure_ascii=False, indent=2), encoding='utf-8')
        return
    args.out.mkdir(parents=True, exist_ok=True)
    current = snapshot(source)
    app = payload(source)
    version = current['version']
    catalog = {'schema': 1, 'app_version': version, 'components': {}}
    previous_path = args.out/'components.json'
    previous = json.loads(previous_path.read_text('utf-8'))['components'] if previous_path.is_file() else {}
    if args.core_only:
        if not all(name in previous for name in ('photo-ocr', 'scan-engine', 'scan-models')):
            raise RuntimeError('Core-only build requires the existing verified components.json catalog')
        catalog['components'] = previous
    components = [
            ('photo-ocr', app/'runtime/photo_ocr_runtime', ()),
            ('scan-engine', app/'scan-runtime', ('models-home/',)),
            ('scan-models', app/'scan-runtime/models-home', ())]
    for name, folder, excludes in ([] if args.core_only else components):
        if not folder.is_dir():
            raise RuntimeError('Missing verified offline component: ' + str(folder))
        entries = {key: path for key, path in files(folder).items() if not key.startswith(excludes)}
        hashes = {key: sha(path) for key, path in entries.items()}
        identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        filename = f'MDReader-{name}-{identity[:16]}-win64.zip'
        target = args.out / filename
        # Component filenames depend on their contents, independently of app releases.
        checksum = target.with_suffix('.zip.sha256')
        if target.is_file() and checksum.is_file() and sha(target) == checksum.read_text('ascii').split()[0]:
            digest = sha(target)
            print(f'{target.name}: reused verified component', flush=True)
        else:
            digest = archive(target, entries)
        old = previous.get(name, {})
        url = old.get('url') if old.get('sha256') == digest else None
        catalog['components'][name] = {'sha256': digest, 'content_id': identity, 'bytes': target.stat().st_size,
            'unpacked_bytes': sum(path.stat().st_size for path in entries.values()), 'files': hashes,
            'url': url or f'{args.base_url}/v{version}/{filename}'}
    (app/'components.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.out/'components.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    core_entries = {name: path for name, path in files(source).items()
                    if not is_component(name)}
    inventory = write_inventory(source, core_entries)
    core_entries[inventory.relative_to(source).as_posix()] = inventory
    archive(args.out/f'MarkdownReader-{version}-core-win64.zip', core_entries)
    current = snapshot(source)
    (args.out/f'MarkdownReader-{version}-program-manifest.json').write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.base:
        base = json.loads(args.base.read_text('utf-8'))
        changed = {name: path for name, path in core_entries.items() if base['files'].get(name) != current['files'][name]}
        # The extracted patch must carry its own current updater. An older
        # installed updater cannot validate newly supported program filenames.
        if (app/'setup/更新程序.ps1').is_file():
            bootstrap = ('安装.exe', 'resources/setup/更新程序.ps1', 'resources/setup/Select-Folder.ps1', 'resources/tools/apply_update.py')
        elif app != source:
            bootstrap = ('安装.exe', 'resources/更新程序.ps1', 'resources/Select-Folder.ps1', 'resources/tools/apply_update.py')
        else:
            bootstrap = ('更新程序.cmd', '更新程序.ps1', 'Select-Folder.ps1', 'tools/apply_update.py')
        for name in bootstrap:
            if name in core_entries:
                changed[name] = core_entries[name]
        runtime_changes = [name for name in changed if name.startswith(('runtime/', 'resources/runtime/'))]
        if runtime_changes:
            raise RuntimeError('Runtime changed; use the core installer for this release: ' + str(runtime_changes))
        manifest = {'schema': 1, 'base_version': base['version'], 'version': version,
                    'files': {name: {'sha256': current['files'][name], 'before': base['files'].get(name)} for name in changed}}
        archive(args.out/f'MarkdownReader-{base["version"]}-to-{version}-update.zip', changed,
                {'update-manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2)})


if __name__ == '__main__':
    main()
