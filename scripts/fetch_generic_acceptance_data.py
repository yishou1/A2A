"""Download small, attributed UCI datasets for non-operational acceptance tests."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

SOURCES = {
    'iris': {
        'url': 'https://archive.ics.uci.edu/static/public/53/iris.zip',
        'page': 'https://archive.ics.uci.edu/dataset/53/iris',
        'doi': 'https://doi.org/10.24432/C56C76',
        'citation': 'Fisher, R. (1936). Iris [Dataset]. UCI Machine Learning Repository.',
        'members': ['iris.data', 'iris.names'], 'rows': 150, 'features': 4,
    },
    'wine': {
        'url': 'https://archive.ics.uci.edu/static/public/109/wine.zip',
        'page': 'https://archive.ics.uci.edu/dataset/109/wine',
        'doi': 'https://doi.org/10.24432/C5PC7J',
        'citation': 'Aeberhard, S. & Forina, M. (1992). Wine [Dataset]. UCI Machine Learning Repository.',
        'members': ['wine.data', 'wine.names'], 'rows': 178, 'features': 13,
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'acceptance/public_data')
    parser.add_argument('--refresh', action='store_true', help='Explicitly allow replacing an existing source manifest')
    args = parser.parse_args()
    manifest_path = args.output / 'manifest.json'
    if manifest_path.exists() and not args.refresh:
        raise SystemExit('Manifest already exists; benchmark runs are offline. Use --refresh only for an intentional data update.')
    manifest = {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
                'purpose': 'General mathematical software validation only; no operational domain evaluation.', 'datasets': {}}
    for name, source in SOURCES.items():
        with urlopen(source['url'], timeout=60) as response:
            archive = response.read(2_000_001)
        if len(archive) > 2_000_000:
            raise RuntimeError('Unexpectedly large dataset archive')
        record = {key: value for key, value in source.items() if key != 'members'}
        record.update(license='CC-BY-4.0', license_url='https://creativecommons.org/licenses/by/4.0/',
                      archive_sha256=hashlib.sha256(archive).hexdigest(), files={})
        with ZipFile(BytesIO(archive)) as bundle:
            for member in source['members']:
                # Extract only explicitly allowlisted flat files, preserving source bytes.
                info = bundle.getinfo(member)
                if info.file_size > 2_000_000:
                    raise RuntimeError(f'Unexpected dataset member size: {member}')
                data = bundle.read(member)
                target = args.output / name / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                record['files'][member] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        manifest['datasets'][name] = record
        print(f'Downloaded {name}: {source["rows"]} rows, original bytes preserved')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
