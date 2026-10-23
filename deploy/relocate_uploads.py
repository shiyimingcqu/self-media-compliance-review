"""Offline Windows/Linux data migration. Run only while the API is stopped."""
import argparse
import hashlib
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def relocate(root: Path, apply: bool = False) -> int:
    root = root.resolve()
    database = root / 'workbench.sqlite3'
    if not database.is_file():
        raise ValueError('Database missing; import the complete data directory first')
    with closing(sqlite3.connect(database)) as db, db:
        rows = db.execute('SELECT id,extension,storage_key,byte_size,sha256 FROM file_assets').fetchall()
        updates = []
        for identity, extension, old_path, size, expected_hash in rows:
            if not re.fullmatch(r'[0-9a-f]{32}', identity) or not re.fullmatch(r'[a-z0-9]+', extension):
                raise ValueError('Invalid file identity or extension; no paths changed')
            target = (root / 'uploads' / f'{identity}.{extension}').resolve()
            if target.parent != (root / 'uploads').resolve() or not target.is_file():
                raise ValueError(f'Missing or unsafe uploaded file: {identity}; no paths changed')
            digest = hashlib.sha256()
            with target.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if target.stat().st_size != size or digest.hexdigest() != expected_hash:
                raise ValueError(f'Uploaded file integrity check failed: {identity}; no paths changed')
            if str(target) != old_path:
                updates.append((str(target), identity))
        if apply and updates:
            backup_dir = root / 'migration-backups'
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / ('before-relocate-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
            with closing(sqlite3.connect(backup)) as saved:
                db.backup(saved)
            db.executemany('UPDATE file_assets SET storage_key=? WHERE id=?', updates)
    return len(updates)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('/var/lib/review'))
    parser.add_argument('--apply', action='store_true', help='Back up the database, then update verified upload paths')
    args = parser.parse_args()
    print(f'{"Updated" if args.apply else "Would update"} {relocate(args.data_dir, args.apply)} upload paths')
