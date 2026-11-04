"""CSV export of all events matching the current journal filters."""

import csv
import os
from pathlib import Path
import tempfile

from .violations import violation_label


def safe_cell(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def export_journal(database, token, path, search='', state='all'):
    """Replace the destination only after the complete export has succeeded."""
    destination = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8-sig', newline='') as handle:
            count = _write_rows(database, token, handle, search, state)
        os.replace(temporary, destination)
        return count
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_rows(database, token, handle, search, state):
    count, before = 0, None
    writer = csv.writer(handle, delimiter=';')
    writer.writerow(['ID', 'Время', 'Объект', 'Камера', 'Вид нарушения', 'Количество',
                     'Статус', 'Проверил', 'Время проверки', 'Комментарий'])
    while True:
        rows = database.list_events(token, search, state, before)
        for row in rows:
            values = [row['id'], row['occurred_at'].astimezone().isoformat(timespec='seconds'),
                      row['workspace_name'], row['camera_name'], violation_label(row['kind']),
                      row['object_count'], 'Просмотрено' if row['reviewed_at'] else 'Новое',
                      row['reviewer'], row['reviewed_at'].astimezone().isoformat(timespec='seconds')
                      if row['reviewed_at'] else '', row['note']]
            writer.writerow([safe_cell(value) for value in values])
        count += len(rows)
        if len(rows) < 100:
            break
        before = min(row['id'] for row in rows)
    return count
