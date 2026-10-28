import json
from pathlib import Path

import pytest

from app.core.database import connection
from test_creator_discussions import seed, objection
from test_project_management import new_project
from test_script_versions import K, task_with_rule


def remove(client, project, key=None, revision=None):
    return client.delete(f'/api/v1/projects/{project["id"]}', headers={
        'Idempotency-Key': key or K(), 'If-Match': str(project['revision'] if revision is None else revision)})


def current(client, project):
    return client.get(f'/api/v1/projects/{project["id"]}').json()['data']


def test_empty_project_deletion_preconditions_replay_and_other_project(client):
    project, other = new_project(client), new_project(client)
    assert client.delete(f'/api/v1/projects/{project["id"]}', headers={'Idempotency-Key': K()}).status_code == 428
    assert remove(client, project, revision=0).status_code == 409
    key = K()
    response = remove(client, project, key)
    assert response.status_code == 200, response.text
    assert response.json()['data'] == {'id': project['id'], 'deleted': True, 'cleanupPending': False}
    assert remove(client, project, key).json()['data'] == response.json()['data']
    assert remove(client, project).status_code == 404
    assert client.get(f'/api/v1/projects/{project["id"]}').status_code == 404
    assert all(p['id'] != project['id'] for p in client.get('/api/v1/projects?status=archived').json()['data'])
    assert current(client, other)['name'] == other['name']
    assert client.get('/api/v1/dashboard').json()['data']['activeProjects'] == 1


def add_video(client, task_id, file_id=None):
    rid, stamp = K(), '2026-10-25T10:00:00Z'
    with connection(client.app.state.settings.database_path) as db:
        db.execute('INSERT INTO evidence_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (rid, 'video', task_id, file_id, 'succeeded', 1, '{}', '{}', None, None, stamp, stamp))
    directory = client.app.state.settings.data_dir / 'evidence' / rid
    directory.mkdir(parents=True)
    (directory / 'clip.mp4').write_bytes(b'project-owned-evidence')
    return rid, directory


def test_populated_project_removes_relations_files_and_stale_cached_responses(client, monkeypatch):
    project, task, rid, rule = seed(client, monkeypatch)
    other = new_project(client)
    assert objection(client, task, 'script', rid, rule).status_code == 200
    asset = client.post('/api/v1/files', files={'file': ('script.txt', b'project upload', 'text/plain')}, data={'purpose': 'script'}, headers={'Idempotency-Key': K()}).json()['data']
    video_id, directory = add_video(client, task['id'], asset['id'])
    media_paths = []
    with connection(client.app.state.settings.database_path) as db:
        storage = Path(db.execute('SELECT storage_key FROM file_assets WHERE id=?', (asset['id'],)).fetchone()[0])
        assert storage.is_file()
        db.execute('INSERT INTO idempotency_records VALUES (?,?,?,?,?)', ('cached-review', K(), 'hash', json.dumps({'id': rid}), 'now'))
        image_id, reference_id = K(), K()
        db.execute('INSERT INTO image_post_assets VALUES (?,?,?,?,?,?,?,?,?)', (image_id, task['id'], 'image.png', 'a' * 64, 'b' * 64, 1, 1, 3, 'now'))
        db.execute('INSERT INTO video_reference_images VALUES (?,?,?,?,?,?,?,?)', (reference_id, project['id'], '参考图', 'a' * 64, 1, 1, 3, 'now'))
        for folder, filename in [('image-post-assets', image_id + '.png'), ('image-post-assets', image_id + '.source'), ('video-references', reference_id + '.png')]:
            path = client.app.state.settings.data_dir / folder / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'owned asset')
            media_paths.append(path)
    response = remove(client, current(client, project))
    assert response.status_code == 200, response.text
    assert not response.json()['data']['cleanupPending']
    assert not storage.exists() and not directory.exists()
    assert all(not path.exists() for path in media_paths)
    assert client.get(f'/api/v1/tasks/{task["id"]}/workflow').status_code == 404
    assert client.get(f'/api/v1/files/{asset["id"]}').status_code == 404
    assert current(client, other)['id'] == other['id']
    with connection(client.app.state.settings.database_path) as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        for table in ('creator_tasks', 'briefs', 'script_versions', 'workflow_script_reviews', 'review_disputes', 'review_dispute_messages', 'task_standard_sets', 'evidence_runs'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        assert not db.execute('SELECT 1 FROM idempotency_records WHERE instr(response_json,?)>0', (rid,)).fetchone()


@pytest.mark.parametrize('table', ['workflow_script_reviews', 'evidence_runs'])
def test_running_work_prevents_any_deletion(client, monkeypatch, table):
    project, task, rid, _ = seed(client, monkeypatch)
    if table == 'evidence_runs':
        rid, _ = add_video(client, task['id'])
    with connection(client.app.state.settings.database_path) as db:
        db.execute(f"UPDATE {table} SET status='processing' WHERE id=?", (rid,))
    response = remove(client, current(client, project))
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'PROJECT_HAS_RUNNING_JOBS'
    assert current(client, project)['id'] == project['id']


def test_shared_upload_is_kept_for_other_project(client, monkeypatch):
    project, task, _, _ = seed(client, monkeypatch)
    other, other_task = task_with_rule(client)
    asset = client.post('/api/v1/files', files={'file': ('shared.txt', b'shared source', 'text/plain')}, data={'purpose': 'script'}, headers={'Idempotency-Key': K()}).json()['data']
    add_video(client, task['id'], asset['id'])
    other_run, other_dir = add_video(client, other_task['id'], asset['id'])
    assert remove(client, current(client, project)).status_code == 200
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT storage_key FROM file_assets WHERE id=?', (asset['id'],)).fetchone()
        assert row and Path(row['storage_key']).is_file()
        assert db.execute('SELECT 1 FROM evidence_runs WHERE id=?', (other_run,)).fetchone()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert other_dir.is_dir() and current(client, other)['id'] == other['id']


def test_cleanup_failure_is_durable_and_retried(client, monkeypatch):
    project, task, _, _ = seed(client, monkeypatch)
    _, directory = add_video(client, task['id'])
    import app.modules.foundation.project_deletion as deletion
    original = deletion.shutil.rmtree
    monkeypatch.setattr(deletion.shutil, 'rmtree', lambda path: (_ for _ in ()).throw(PermissionError()))
    key = K()
    project = current(client, project)
    response = remove(client, project, key)
    assert response.status_code == 200 and response.json()['data']['cleanupPending']
    assert directory.exists()
    monkeypatch.setattr(deletion.shutil, 'rmtree', original)
    assert not remove(client, project, key).json()['data']['cleanupPending']
    assert not directory.exists()


def test_unsafe_upload_path_rolls_back_without_deleting_project(client, monkeypatch):
    project, task, _, _ = seed(client, monkeypatch)
    asset = client.post('/api/v1/files', files={'file': ('safe.txt', b'contents', 'text/plain')}, data={'purpose': 'script'}, headers={'Idempotency-Key': K()}).json()['data']
    add_video(client, task['id'], asset['id'])
    with connection(client.app.state.settings.database_path) as db:
        db.execute('UPDATE file_assets SET storage_key=? WHERE id=?', (str(client.app.state.settings.database_path), asset['id']))
    response = remove(client, current(client, project))
    assert response.status_code == 409 and response.json()['error']['code'] == 'PROJECT_FILE_PATH_UNSAFE'
    assert current(client, project)['id'] == project['id']
