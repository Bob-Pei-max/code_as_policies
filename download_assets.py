"""Fetch and verify the assets pinned in assets/manifest.json."""
from pathlib import Path
import argparse
import hashlib
import json
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'assets'

def verify_archive(path: Path, record: dict) -> None:
    data = path.read_bytes()
    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError(f'{path.name}: asset checksum mismatch; expected the pinned archive.')
    if not zipfile.is_zipfile(path):
        raise ValueError(f'{path.name}: not a ZIP archive.')


def extract_archive(path: Path, destination: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Archive contains an unsafe path')
        archive.extractall(destination)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify local archives and required URDFs without downloading or extracting.')
    args = parser.parse_args()
    records = json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))
    ASSETS.mkdir(exist_ok=True)
    for record in records:
        name, file_id = record['asset'], record['drive_id']
        path = ASSETS / (name + '.zip')
        url = f'https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t'
        if not path.exists() and not args.check:
            print(f'Downloading {name} ...', flush=True)
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read()
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError(f'{name}: downloaded bytes do not match assets/manifest.json.')
            path.write_bytes(data)
        verify_archive(path, record)
        if not args.check:
            extract_archive(path, ASSETS)
        if not (ASSETS / name / (name + '.urdf')).is_file():
            raise FileNotFoundError(f'{name}: missing extracted URDF. Run download_assets.py without --check.')
        print(f'{name} ready: {path.stat().st_size} bytes', flush=True)

if __name__ == '__main__':
    main()
