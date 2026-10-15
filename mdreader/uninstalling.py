"""Plan application cleanup while retaining projects and unowned user files.

Standalone stdlib entry: the installed interpreter exits before any removal.
It never reads API keys or follows a project/history reference outside the roots.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

STATE_FILES = {
    'ai-providers.json', 'ai-connection.json', 'ui-settings.json', 'workspace.json',
    'recent.json', 'folders.json', 'plugins.json', 'reading-positions.json',
    'project-history-hidden.json', '.mdreader-installations.json',
}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def child(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Invalid managed file path')
    relative = PurePosixPath(name)
    result = root.joinpath(*relative.parts)
    if not result.resolve().is_relative_to(root):
        raise ValueError('Managed path leaves its recorded root')
    return result


def plan_cleanup(program, workspace):
    program, workspace = Path(program).resolve(), Path(workspace).resolve()
    if program == workspace or program.is_relative_to(workspace) or workspace.is_relative_to(program):
        raise ValueError('Program and workspace overlap')
    payload = program / 'resources' if (program / 'resources/program-files.json').is_file() else program
    inventory = payload / 'program-files.json'
    descriptor = read_json(inventory)
    if descriptor.get('schema') != 1 or not isinstance(descriptor.get('files'), list):
        raise ValueError('Missing valid program file inventory; reinstall with the current installer')
    program_owned = {child(program, name) for name in descriptor['files']}
    retained_inventory = payload / 'installed-files.json'
    if retained_inventory.is_file():
        previous = read_json(retained_inventory)
        if previous.get('schema') != 1 or not isinstance(previous.get('files'), list):
            raise ValueError('Invalid installed file inventory')
        program_owned.update(child(program, name) for name in previous['files'])
    all_files = []
    all_dirs = {program, workspace}
    for root in (program, workspace):
        if root.exists():
            for path in root.rglob('*'):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('Linked paths cannot be cleaned')
                if path.is_file():
                    all_files.append(path)
                elif path.is_dir():
                    all_dirs.add(path)
    protected = {program / 'projects', workspace / 'projects'}
    protected.update(path.parent for path in all_files
                     if path.name == 'project.json' and path not in program_owned
                     and path.parent not in (program, workspace))

    def is_user_project(path):
        return any(path == folder or path.is_relative_to(folder) for folder in protected)

    owned = set(program_owned)
    owned.update(payload / name for name in ('installation.json', 'program-files.json',
                 'installed-files.json', 'installed-target.json', 'MDReader.log'))
    owned.update(workspace / name for name in STATE_FILES)
    # Exact component inventories remove models without deleting files added by users.
    catalog_path = payload / 'components.json'
    catalog = read_json(catalog_path).get('components', {}) if catalog_path.is_file() else {}
    for descriptor in catalog.values():
        identity = descriptor.get('content_id', descriptor.get('sha256', ''))
        if not re.fullmatch(r'[a-f0-9]{64}', identity):
            continue
        folder = workspace / 'components' / identity
        owned.update(child(folder, name) for name in descriptor.get('files', {}))
        owned.add(folder / '.installed.json')
        checksum = descriptor.get('sha256', '')
        if re.fullmatch(r'[a-f0-9]{64}', checksum):
            owned.add(workspace / 'components' / (checksum + '.download'))
    # Plugin ZIP members are the installed files; arbitrary additions stay untouched.
    packages = {}
    for archive in (payload / 'plugins/packages').glob('*.zip'):
        with zipfile.ZipFile(archive) as stream:
            meta = json.loads(stream.read('manifest.json'))
            packages[(meta.get('id'), meta.get('version'))] = [row.filename for row in stream.infolist() if not row.is_dir()]
    for marker in (workspace / 'plugins/installed').glob('*/*/manifest.json'):
        meta = read_json(marker)
        for name in packages.get((meta.get('id'), meta.get('version')), []):
            owned.add(child(marker.parent, name))
    for marker in (workspace / 'plugins/installed').glob('*/*/.mdreader-owned-files.json'):
        record = read_json(marker)
        if record.get('schema') != 1 or not isinstance(record.get('files'), list):
            raise ValueError('Invalid plugin file inventory')
        owned.update(child(marker.parent, name) for name in record['files'])
        owned.add(marker)
    for marker in (workspace / 'components').glob('*/.installed.json'):
        record = read_json(marker)
        if record.get('content_id') == marker.parent.name and isinstance(record.get('files'), list):
            owned.update(child(marker.parent, name) for name in record['files'])
            owned.add(marker)
    for path in all_files:
        if path.is_relative_to(workspace / 'plugins/tasks'):
            relative = path.relative_to(workspace / 'plugins/tasks')
            if relative.parts and re.fullmatch(r't-\d+-[a-f0-9]{6}', relative.parts[0]):
                owned.add(path)
        if path.is_relative_to(workspace / 'plugins/staging'):
            relative = path.relative_to(workspace / 'plugins/staging')
            if relative.parts and re.fullmatch(r'(?:handshake-)?[a-z0-9._-]+-\d+', relative.parts[0]):
                owned.add(path)
        if path.parent == workspace and re.fullmatch(r'\.mdreader-[a-f0-9]{12}\.tmp', path.name):
            owned.add(path)
        if path.parent == workspace / '.recovery' and re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
            owned.add(path)
        if path.parent == workspace / 'formula-cache' and re.fullmatch(r'[a-f0-9]{64}\.(?:png|json)', path.name):
            owned.add(path)
        if path.suffix == '.pyc' and path.parent.name == '__pycache__':
            source = path.parent.parent / (path.name.split('.')[0] + '.py')
            if source in owned:
                owned.add(path)
    selected = {path for path in owned if path.is_file() and not is_user_project(path)}
    return {'schema': 1, 'files': sorted(map(str, selected)),
            'directories': [str(path) for path in sorted(all_dirs, key=lambda p: len(p.parts), reverse=True)
                            if not is_user_project(path)],
            'retained_files': len(set(all_files) - selected)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    Path(args.out).write_text(json.dumps(plan_cleanup(args.program, args.workspace), ensure_ascii=False), encoding='utf-8')
