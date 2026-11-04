"""Durable event journal; connections belong to the calling thread."""
from __future__ import annotations

from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import cv2

from .classes import RULE_LABELS


class EventStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "events.sqlite3"
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
                    camera_id TEXT NOT NULL, camera_name TEXT NOT NULL,
                    workspace TEXT NOT NULL, track_id INTEGER NOT NULL,
                    missing TEXT NOT NULL, snapshot TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS events_camera ON events(camera_id, occurred_at DESC);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.create_function("casefold", 1, lambda value: value.casefold())
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, *, occurred_at: str, camera_id: str, camera_name: str, workspace: str,
            track_id: int, missing: tuple[str, ...], frame, details: dict) -> str:
        event_id = uuid4().hex
        relative = Path("photos") / occurred_at[:10] / f"{event_id}.jpg"
        destination = self.directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise OSError("Не удалось закодировать снимок события.")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded.tobytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            with self.connection() as connection:
                connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (event_id, occurred_at, camera_id, camera_name, workspace, track_id,
                                    json.dumps(missing, ensure_ascii=False), relative.as_posix(),
                                    json.dumps(details, ensure_ascii=False)))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return event_id

    @staticmethod
    def _filters(search: str, rule: str) -> tuple[str, list]:
        where, parameters = [], []
        if search.strip():
            # instr performs a literal search, so % and _ are not wildcards.
            where.append("instr(casefold(camera_name || ' ' || workspace || ' ' || occurred_at), casefold(?)) > 0")
            parameters.append(search.strip())
        if rule:
            where.append("instr(missing, ?) > 0")
            parameters.append('"' + rule + '"')
        return (" WHERE " + " AND ".join(where) if where else ""), parameters

    def query(self, search: str = "", rule: str = "", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        where, parameters = self._filters(search, rule)
        with self.connection() as connection:
            total = connection.execute("SELECT count(*) FROM events" + where, parameters).fetchone()[0]
            rows = connection.execute("SELECT * FROM events" + where + " ORDER BY occurred_at DESC, rowid DESC LIMIT ? OFFSET ?",
                                      [*parameters, max(1, min(limit, 1000)), max(0, offset)]).fetchall()
        return [dict(row) for row in rows], total

    def snapshot_path(self, row: dict) -> Path:
        path = (self.directory / row["snapshot"]).resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise ValueError("Некорректный путь к снимку.")
        return path

    def delete(self, event_ids) -> int:
        """Delete only requested events and their photos; failed photos keep their row."""
        deleted = 0
        for event_id in dict.fromkeys(event_ids):
            with self.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                if row is None:
                    continue
                path = self.snapshot_path(row)
                photos = (self.directory / "photos").resolve()
                if not path.is_relative_to(photos) or path == photos:
                    raise ValueError("Некорректный путь к фотографии события.")
                path.unlink(missing_ok=True)
                connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
                deleted += 1
        return deleted

    def export_csv(self, destination: Path | str, search: str = "", rule: str = "") -> None:
        where, parameters = self._filters(search, rule)
        with self.connection() as connection, Path(destination).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(("ID", "Время", "Камера", "ID камеры", "Зона", "Трек", "Не обнаружено", "Фото"))
            for row in connection.execute("SELECT * FROM events" + where + " ORDER BY occurred_at DESC", parameters):
                missing = ", ".join(RULE_LABELS[key] for key in json.loads(row["missing"]))
                values = (row["id"], row["occurred_at"], row["camera_name"], row["camera_id"], row["workspace"],
                          row["track_id"], missing, str(self.snapshot_path(row)))
                writer.writerow("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                                else value for value in values)
