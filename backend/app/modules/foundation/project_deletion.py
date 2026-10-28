"""Permanent project deletion with FK closure, shared-file protection and durable cleanup."""
import hashlib
import json
import logging
from pathlib import Path
import shutil
from uuid import uuid4

from app.core.database import connection
from app.core.errors import ApiError
from .service import now

logger = logging.getLogger(__name__)


def quoted(value):
    # Identifiers come only from SQLite's schema, never request parameters.
    return '"' + value.replace('"', '""') + '"'


def checked_path(settings, value):
    root = settings.data_dir.resolve()
    target = Path(value).resolve()
    folders = {'uploads', 'evidence', 'brief-vision', 'image-post-assets', 'video-references', 'prepared-videos', 'preview-cache'}
    if target == root or not target.is_relative_to(root) or len(target.relative_to(root).parts) < 2 or target.relative_to(root).parts[0] not in folders:
        raise ApiError(409, 'PROJECT_FILE_PATH_UNSAFE', '项目文件路径超出数据目录，请先处理路径迁移')
    return target


def cleanup_project_files(settings, project_id=None):
    with connection(settings.database_path) as db:
        rows = db.execute('SELECT path FROM project_file_cleanup' + (' WHERE project_id=?' if project_id else ''), (project_id,) if project_id else ()).fetchall()
        for row in rows:
            try:
                target = checked_path(settings, row['path'])
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
                db.execute('DELETE FROM project_file_cleanup WHERE path=?', (row['path'],))
            except (OSError, ApiError):
                logger.warning('Project file cleanup deferred; project=%s', project_id or 'startup')
        return db.execute('SELECT count(*) FROM project_file_cleanup' + (' WHERE project_id=?' if project_id else ''), (project_id,) if project_id else ()).fetchone()[0]


def delete_project(settings, project_id, revision, key):
    if not key.strip():
        raise ApiError(400, 'VALIDATION_ERROR', 'Idempotency-Key 不能为空白')
    scope = f'local-reviewer:demo:project:{project_id}:delete'
    fingerprint = hashlib.sha256(json.dumps([project_id, revision]).encode()).hexdigest()
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT * FROM idempotency_records WHERE scope=? AND key=?', (scope, key)).fetchone()
        if prior:
            if prior['request_hash'] != fingerprint:
                raise ApiError(409, 'IDEMPOTENCY_CONFLICT', '相同幂等键不能用于不同内容')
            result = json.loads(prior['response_json'])
        else:
            row = db.execute("SELECT * FROM projects WHERE id=? AND mode='demo'", (project_id,)).fetchone()
            if not row:
                raise ApiError(404, 'RESOURCE_NOT_FOUND', '项目不存在')
            if revision is None:
                raise ApiError(428, 'PRECONDITION_REQUIRED', '删除项目必须携带 If-Match revision')
            try:
                expected = int(revision)
            except ValueError:
                raise ApiError(400, 'VALIDATION_ERROR', 'If-Match 必须是整数 revision') from None
            if expected != row['revision']:
                raise ApiError(409, 'REVISION_CONFLICT', '项目已更新，请刷新后重新操作')

            tables = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            foreign_keys = {table: list(db.execute(f'PRAGMA foreign_key_list({quoted(table)})')) for table in tables}
            db.execute('CREATE TEMP TABLE project_delete_rows (table_name TEXT,row_id INTEGER,PRIMARY KEY(table_name,row_id))')
            db.execute("INSERT INTO project_delete_rows SELECT 'projects',rowid FROM projects WHERE id=?", (project_id,))
            # Walk incoming references only: brands, settings and other projects are not owners' children.
            changed = True
            while changed:
                changed = False
                for table, keys in foreign_keys.items():
                    for fk in keys:
                        parent, source, dest = fk['table'], fk['from'], fk['to'] or 'id'
                        count = db.execute(f'INSERT OR IGNORE INTO project_delete_rows SELECT ?,c.rowid FROM {quoted(table)} c JOIN {quoted(parent)} p ON c.{quoted(source)}=p.{quoted(dest)} JOIN project_delete_rows d ON d.table_name=? AND d.row_id=p.rowid', (table, parent)).rowcount
                        changed = changed or count > 0
            selected = {table: list(db.execute(f'SELECT t.rowid AS _row_id,t.* FROM {quoted(table)} t JOIN project_delete_rows d ON d.table_name=? AND d.row_id=t.rowid', (table,))) for table in tables}
            for table, rows in selected.items():
                for item in rows:
                    if 'project_id' in item.keys() and item['project_id'] not in (None, project_id):
                        raise ApiError(409, 'PROJECT_SHARED_DEPENDENCY', '资料被其他项目关联，请先解除跨项目关联')
                    if 'brief_id' in item.keys() and item['brief_id']:
                        owner = db.execute('SELECT project_id FROM briefs WHERE id=?', (item['brief_id'],)).fetchone()
                        if owner and owner['project_id'] != project_id:
                            raise ApiError(409, 'PROJECT_SHARED_DEPENDENCY', '规则被其他项目关联，请先解除跨项目关联')
                    if 'status' in item.keys() and item['status'] in ('queued', 'processing', 'vision', 'facts', 'parsing'):
                        raise ApiError(409, 'PROJECT_HAS_RUNNING_JOBS', '项目有审核或解析正在运行，请完成后再删除')

            # Only delete file assets whose remaining references all belong to this deletion.
            file_ids = set()
            for table, rows in selected.items():
                for fk in foreign_keys[table]:
                    if fk['table'] == 'file_assets':
                        file_ids.update(item[fk['from']] for item in rows if item[fk['from']])
            files = []
            for file_id in file_ids:
                shared = False
                for table, keys in foreign_keys.items():
                    for fk in keys:
                        if fk['table'] == 'file_assets' and db.execute(f'SELECT 1 FROM {quoted(table)} t WHERE t.{quoted(fk["from"])}=? AND NOT EXISTS(SELECT 1 FROM project_delete_rows d WHERE d.table_name=? AND d.row_id=t.rowid) LIMIT 1', (file_id, table)).fetchone():
                            shared = True
                if not shared:
                    asset = db.execute('SELECT rowid AS _row_id,* FROM file_assets WHERE id=?', (file_id,)).fetchone()
                    if asset:
                        files.append(asset)
                        db.execute("INSERT INTO project_delete_rows VALUES ('file_assets',?)", (asset['_row_id'],))

            paths = []
            for table, folder in [('evidence_runs', 'evidence'), ('brief_visual_runs', 'brief-vision')]:
                paths.extend(settings.data_dir / folder / item['id'] for item in selected[table])
            for item in selected['image_post_assets']:
                paths.extend(settings.data_dir / 'image-post-assets' / (item['id'] + suffix) for suffix in ('.png', '.source'))
            paths.extend(settings.data_dir / 'video-references' / (item['id'] + '.png') for item in selected['video_reference_images'])
            for asset in files:
                expected_path = settings.uploads_dir / (asset['id'] + ('.' + asset['extension'] if asset['extension'] else ''))
                if Path(asset['storage_key']).resolve() != expected_path.resolve():
                    raise ApiError(409, 'PROJECT_FILE_PATH_UNSAFE', '上传文件路径与资产标识不符，请先处理路径迁移')
                paths.extend([Path(asset['storage_key']), settings.data_dir / 'prepared-videos' / asset['id']])
                if not db.execute('SELECT 1 FROM file_assets f WHERE sha256=? AND NOT EXISTS(SELECT 1 FROM project_delete_rows d WHERE d.table_name=\'file_assets\' AND d.row_id=f.rowid)', (asset['sha256'],)).fetchone():
                    paths.extend((settings.data_dir / 'preview-cache').glob(f'*/{asset["sha256"]}-*'))
            paths = {str(checked_path(settings, path)) for path in paths}
            stamp = now()
            db.executemany('INSERT OR IGNORE INTO project_file_cleanup VALUES (?,?,?)', [(path, project_id, stamp) for path in paths])
            # Remove stale response caches, which could otherwise replay deleted resources.
            identities = {item['id'] for rows in selected.values() for item in rows if 'id' in item.keys()}
            identities.update(asset['id'] for asset in files)
            for identity in identities:
                db.execute('DELETE FROM audit_events WHERE entity_id=?', (identity,))
                db.execute('DELETE FROM idempotency_records WHERE instr(scope,?)>0 OR instr(response_json,?)>0', (identity, identity))
            db.execute('PRAGMA defer_foreign_keys=ON')
            for table in tables:
                db.execute(f'DELETE FROM {quoted(table)} WHERE rowid IN (SELECT row_id FROM project_delete_rows WHERE table_name=?)', (table,))
            result = {'id': project_id, 'deleted': True}
            db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, 'local-reviewer', 'project', project_id, 'deleted', 'demo', '{}', stamp))
            db.execute('INSERT INTO idempotency_records VALUES (?,?,?,?,?)', (scope, key, fingerprint, json.dumps(result), stamp))
    result['cleanupPending'] = bool(cleanup_project_files(settings, project_id))
    return result
