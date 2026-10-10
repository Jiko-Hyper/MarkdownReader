"""Build a relocatable offline scan runtime from embedded Python and tested deps."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=ROOT/'.testtmp/scan-venv')
    parser.add_argument('--models', type=Path, default=ROOT/'.testtmp/physics-mineru-home/models')
    parser.add_argument('--embed', type=Path, default=ROOT/'build/dl/python-3.12.9-embed-amd64.zip')
    parser.add_argument('--out', type=Path, default=ROOT/'build/scan-runtime-portable')
    args = parser.parse_args()
    site = args.env/'Lib/site-packages'
    packages = {d.metadata['Name']: d.version for d in importlib.metadata.distributions(path=[str(site)])}
    if packages.get('mineru') != '4.0.10' or not args.models.is_dir():
        raise SystemExit('Need MinerU 4.0.10 dependencies and downloaded offline models.')
    dest = args.out.resolve()
    if dest.exists():
        raise SystemExit('Output exists; specify a fresh --out to avoid stale dependencies.')
    dest.mkdir(parents=True)
    with zipfile.ZipFile(args.embed) as archive:
        archive.extractall(dest)
    shutil.copytree(site, dest/'Lib/site-packages', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (dest/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\nimport site\n', encoding='utf-8')
    home = dest/'models-home'
    shutil.copytree(args.models, home/'models', ignore=shutil.ignore_patterns('.cache', '.locks', '.git', '.mdl', '.msc'))
    # MinerU derives base_dir from MINERU_HOME at startup, including after relocation.
    config = {'model': {'source': 'local', 'small_backend': 'onnx', 'vlm': {'engine': 'llama-cpp', 'server_url': ''}}}
    (home/'config.yaml').write_text(json.dumps(config), encoding='utf-8')
    manifest = {}
    for path in sorted((home/'models').rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                manifest[path.relative_to(dest).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (dest/'component-manifest.json').write_text(json.dumps({'packages': packages, 'models': manifest}, indent=2), encoding='utf-8')
    print(dest)


if __name__ == '__main__':
    main()
