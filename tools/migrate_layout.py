"""Remove inventoried legacy program files, retaining projects and unknown files."""
import json
from pathlib import Path, PurePosixPath
import shutil


def migrate(program, previous_files=None):
    program = Path(program).resolve()
    entries = list(program.rglob('*'))
    if any(path.is_symlink() or not path.resolve().is_relative_to(program) for path in entries):
        raise ValueError('Linked paths cannot be migrated')
    def child(name):
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))):
            raise ValueError('Invalid legacy inventory path')
        path = program / name
        if path.is_symlink() or not path.resolve().is_relative_to(program):
            raise ValueError('Legacy inventory crosses program directory')
        return path
    owned = set()
    if previous_files is not None:
        if not isinstance(previous_files, list):
            raise ValueError('Invalid previous file inventory')
        owned.update(child(name) for name in previous_files)
    else:
        for name in ('program-files.json', 'installed-files.json'):
            record = program / name
            if record.is_file():
                descriptor = json.loads(record.read_text(encoding='utf-8-sig'))
                if descriptor.get('schema') != 1 or not isinstance(descriptor.get('files'), list):
                    raise ValueError('Invalid legacy inventory')
                owned.update(child(name) for name in descriptor['files'])
    if not owned:
        raise ValueError('Missing legacy ownership records')
    owned.update(program / name for name in ('program-files.json', 'installed-files.json', 'installation.json', 'installed-target.json', 'MDReader.log'))
    new = json.loads((program / 'resources/program-files.json').read_text(encoding='utf-8-sig'))
    current = {child(name) for name in new['files']}
    current.update(program / 'resources' / name for name in ('installation.json', 'installed-files.json', 'installed-target.json', 'MDReader.log'))
    protected = {program / 'projects'}
    protected.update(path.parent for path in program.rglob('project.json') if path not in owned)
    def is_project(path):
        return any(path == folder or path.is_relative_to(folder) for folder in protected)
    moves = {}
    retained_components = set()
    for path in owned - current:
        name = path.relative_to(program).as_posix()
        if name.startswith(('resources/scan-runtime/', 'resources/runtime/photo_ocr_runtime/')) and path.is_file():
            retained_components.add(path)
        if not is_project(path) and name.startswith(('scan-runtime/', 'runtime/photo_ocr_runtime/')) and path.is_file():
            target = child('resources/' + name)
            if target.exists():
                raise ValueError('Recognition target already exists; retained legacy files')
            moves[path] = target
    for path in program.rglob('*.pyc'):
        if path.parent.name == '__pycache__' and path.parent.parent / (path.name.split('.')[0] + '.py') in owned:
            owned.add(path)
    for path, target in moves.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    for path in owned - current - retained_components:
        if not is_project(path) and path.is_file():
            path.unlink()
    obsolete_directories = set()
    for path in owned - current - retained_components:
        parent = path.parent
        while parent != program:
            obsolete_directories.add(parent)
            parent = parent.parent
    directories = sorted((p for p in obsolete_directories if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for path in directories:
        if path.is_symlink() or not path.resolve().is_relative_to(program):
            raise ValueError('Linked directory retained')
        if not is_project(path) and not any(path.iterdir()):
            path.rmdir()
    return sorted(path.relative_to(program).as_posix() for path in set(moves.values()) | retained_components)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program', type=Path, required=True)
    parser.add_argument('--previous-manifest', type=Path)
    args = parser.parse_args()
    previous = json.loads(args.previous_manifest.read_text(encoding='utf-8-sig')) if args.previous_manifest else None
    print(json.dumps(migrate(args.program, previous)))
