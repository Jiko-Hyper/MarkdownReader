"""Create and validate a ZIP64 release, preserving hidden dependency metadata."""
import hashlib
from pathlib import Path
import sys
import zipfile


def main():
    source, target = map(lambda arg: Path(arg).resolve(), sys.argv[1:])
    if not source.is_dir() or target.is_relative_to(source):
        raise ValueError('Archive must be outside the source directory')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                archive.write(path, path.relative_to(source).as_posix())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Archive integrity check failed')
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    target.with_suffix('.zip.sha256').write_text(f'{digest}  {target.name}\n', encoding='ascii')
    print(f'{target.name}: {target.stat().st_size} bytes; SHA256 {digest}')


if __name__ == '__main__':
    main()
