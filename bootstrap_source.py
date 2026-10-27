"""Download a pinned official source snapshot without unrelated monorepo files."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parent

def main():
    dest = ROOT / 'code_as_policies'
    dest.mkdir(exist_ok=True)
    manifest = json.loads((ROOT / 'upstream.json').read_text(encoding='utf-8'))
    for record in manifest['files']:
        name = record['file']
        path = dest / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']:
            print(f'Already verified: {name}', flush=True)
            continue
        with urllib.request.urlopen(record['url'], timeout=40) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError(f'{name}: upstream checksum mismatch.')
        if name.endswith('.ipynb'):
            if 'cells' not in json.loads(data):
                raise ValueError(f'{name}: missing notebook cells.')
        path.write_bytes(data)
        print(f'Downloaded {name}: {len(data)} bytes', flush=True)

if __name__ == '__main__':
    main()
