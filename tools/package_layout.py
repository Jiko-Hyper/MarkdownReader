"""Keep only user entry points in a packaged application's outer directory."""
from pathlib import Path

OUTER_FILES = {'MDReader.exe', '安装.exe', '卸载.exe', '使用手册.md'}


def payload(source):
    source = Path(source)
    return source / 'resources' if (source / 'resources/mdreader/core.py').is_file() else source


def organize(source):
    source = Path(source).resolve()
    if not all((source / name).is_file() for name in OUTER_FILES):
        raise ValueError('Missing user entry point')
    resources = source / 'resources'
    if resources.exists():
        raise ValueError('Output already has resources; assemble a fresh package')
    resources.mkdir()
    for path in source.iterdir():
        if path.name not in OUTER_FILES and path != resources:
            path.rename(resources / path.name)
    for name in ('安装到桌面.ps1', '更新程序.ps1', '卸载.ps1', 'Select-Folder.ps1', 'Register-FileAssociation.ps1'):
        path = resources / name
        if path.is_file():
            (resources / 'setup').mkdir(exist_ok=True)
            path.rename(resources / 'setup' / name)
    if (resources / 'LICENSE').is_file():
        (resources / 'legal').mkdir(exist_ok=True)
        (resources / 'LICENSE').rename(resources / 'legal/LICENSE')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    organize(parser.parse_args().source)
