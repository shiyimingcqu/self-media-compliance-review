"""Preserve original call duration when a report is later repaired or human-reviewed."""
from datetime import datetime, timezone

TABLES = {'standard_recommendations', 'workflow_script_reviews', 'image_post_reviews',
          'evidence_runs', 'brief_visual_runs', 'brief_facts_runs', 'brief_project_imports'}


def stamp_duration(db, table, identity, result):
    if table not in TABLES:
        raise ValueError('Unknown timed operation')
    row = db.execute(f'SELECT created_at FROM {table} WHERE id=?', (identity,)).fetchone()
    if row:
        start = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        result['durationMs'] = max(0, round((datetime.now(timezone.utc) - start).total_seconds() * 1000))
