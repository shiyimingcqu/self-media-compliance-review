import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def connection(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def migrate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        for source in sorted((Path(__file__).resolve().parents[2] / "migrations").glob("*.sql")):
            if db.execute("SELECT 1 FROM schema_migrations WHERE name=?", (source.name,)).fetchone():
                continue
            # executescript manages its own transaction: script + version marker commit atomically.
            safe_name = source.name.replace("'", "''")
            script = "BEGIN IMMEDIATE;\n" + source.read_text(encoding="utf-8")
            script += f"\nINSERT INTO schema_migrations VALUES ('{safe_name}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\nCOMMIT;"
            db.executescript(script)
