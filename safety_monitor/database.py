"""PostgreSQL persistence, password verification and application permissions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import hmac
import os
import re
from pathlib import Path
import secrets

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import DEFAULT_SETTINGS, WorkspaceStore

ROOT = Path(__file__).resolve().parent.parent


class DatabaseError(OSError):
    pass


class AccessDenied(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations).hex()
    return f'pbkdf2_sha256${iterations}${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, count, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(count)).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == 'admin'


class Database:
    def __init__(self, *, schema: str = 'safety_monitor', **options) -> None:
        load_dotenv(ROOT / '.env')
        if re.fullmatch(r'[a-z_][a-z0-9_]*', schema) is None:
            raise ValueError('Invalid database schema')
        self.schema = schema
        self.options = options or dict(
            host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
            port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'),
            dbname=os.getenv('SAFETY_MONITOR_DB_NAME', 'safety_monitor'),
            user=os.getenv('SAFETY_MONITOR_DB_USER', 'safety_monitor'),
            password=os.getenv('SAFETY_MONITOR_DB_PASSWORD', ''),
        )
        self._sessions: dict[str, User] = {}
        self._recorders: dict[str, User] = {}
        self._initialized = False
        self._dummy_hash = hash_password(secrets.token_urlsafe(24))

    def connect(self):
        try:
            return psycopg.connect(**self.options, connect_timeout=3, row_factory=dict_row,
                                   options=f'-c search_path={self.schema},public -c statement_timeout=5000 -c lock_timeout=3000')
        except psycopg.Error:
            raise DatabaseError('PostgreSQL недоступен. Проверьте .env и выполните docker compose up -d db.') from None

    def initialize(self) -> None:
        """Migrate and seed once, atomically; never overwrite an existing config."""
        if self._initialized:
            return
        with self.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(736201)')
            conn.execute((Path(__file__).with_name('schema.sql')).read_text(encoding='utf-8'))
            for username, display_name, role in [('admin', 'Администратор', 'admin'),
                                                 ('daniyal', 'Даниял', 'inspector')]:
                if not conn.execute('SELECT id FROM users WHERE username=%s', (username,)).fetchone():
                    conn.execute('INSERT INTO users(username, display_name, role, password_hash) '
                                 'VALUES (%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING',
                                 (username, display_name, role, hash_password('12345')))
            if conn.execute('SELECT id FROM application_config WHERE id=1').fetchone() is None:
                document = dict(version=1, settings=dict(DEFAULT_SETTINGS), workspaces=[])
                conn.execute('INSERT INTO application_config(id,document) VALUES (1,%s)', (Jsonb(document),))
        self._initialized = True

    def login(self, username: str, password: str) -> str:
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM users WHERE username=%s', (username.strip().lower(),)).fetchone()
        valid = verify_password(password, row['password_hash'] if row else self._dummy_hash)
        if not row or not valid:
            raise AccessDenied('Неверный логин или пароль.')
        token = secrets.token_urlsafe(32)
        self._sessions[token] = User(**{k: row[k] for k in ('id', 'username', 'display_name', 'role')})
        return token

    def user(self, token: str, *, admin: bool = False) -> User:
        user = self._sessions.get(token)
        if user is None:
            raise AccessDenied('Войдите в аккаунт.')
        if admin and not user.is_admin:
            raise AccessDenied('Это действие доступно только администратору.')
        return user

    def logout(self, token: str) -> None:
        self._sessions.pop(token, None)

    def recording_token(self, token: str) -> str:
        user = self.user(token)
        recording_token = secrets.token_urlsafe(32)
        self._recorders[recording_token] = user
        return recording_token

    def release_recording_token(self, token: str) -> None:
        self._recorders.pop(token, None)

    def load_config(self, token: str) -> tuple[dict, int]:
        self.user(token)
        with self.connect() as conn:
            row = conn.execute('SELECT document,revision FROM application_config WHERE id=1').fetchone()
        return row['document'], row['revision']

    def save_config(self, token: str, document: dict, revision: int) -> int:
        self.user(token, admin=True)
        WorkspaceStore._decode(document)
        with self.connect() as conn:
            row = conn.execute('UPDATE application_config SET document=%s,revision=revision+1,updated_at=now() '
                               'WHERE id=1 AND revision=%s RETURNING revision', (Jsonb(document), revision)).fetchone()
            if row is None:
                raise ValueError('Объекты изменены в другом окне. Войдите заново, чтобы обновить данные.')
            return row['revision']

    def record_event(self, token: str, camera_id: str, camera_name: str, workspace_name: str,
                     count: int, snapshot: bytes, *, kind: str = 'no_helmet') -> int | None:
        if token not in self._recorders:
            self.user(token)
        if count <= 0 or not snapshot:
            raise ValueError('Для события нужны обнаружение и кадр.')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', kind):
            raise ValueError('Некорректный вид нарушения.')
        with self.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (camera_id,))
            recent = conn.execute("SELECT id FROM events WHERE camera_id=%s AND kind=%s AND occurred_at > now()-interval '30 seconds' LIMIT 1",
                                  (camera_id, kind)).fetchone()
            if recent:
                return None
            row = conn.execute('INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot,kind) '
                               'VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                               (camera_id, camera_name, workspace_name, count, snapshot, kind)).fetchone()
            return row['id']

    def list_events(self, token: str, search: str = '', state: str = 'all', before: int | None = None,
                    *, limit: int = 100) -> list[dict]:
        self.user(token)
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError('Некорректный размер страницы.')
        if state not in {'all', 'new', 'reviewed'}:
            raise ValueError('Неизвестный статус.')
        with self.connect() as conn:
            return conn.execute("SELECT e.id,e.occurred_at,e.camera_name,e.workspace_name,e.kind,e.object_count,"
                                "e.reviewed_at,e.note,u.display_name AS reviewer FROM events e "
                                "LEFT JOIN users u ON u.id=e.reviewed_by "
                                "WHERE (e.camera_name ILIKE %s OR e.workspace_name ILIKE %s) "
                                "AND (%s='all' OR (%s='new' AND e.reviewed_at IS NULL) "
                                "OR (%s='reviewed' AND e.reviewed_at IS NOT NULL)) "
                                "AND (%s::bigint IS NULL OR e.id < %s) ORDER BY e.id DESC LIMIT %s",
                                (f'%{search}%', f'%{search}%', state, state, state, before, before, limit)).fetchall()

    def event_snapshot(self, token: str, event_id: int) -> bytes:
        self.user(token)
        with self.connect() as conn:
            row = conn.execute('SELECT snapshot FROM events WHERE id=%s', (event_id,)).fetchone()
        if row is None:
            raise ValueError('Событие не найдено.')
        return bytes(row['snapshot'])

    def review_event(self, token: str, event_id: int, note: str) -> None:
        user = self.user(token)
        with self.connect() as conn:
            changed = conn.execute('UPDATE events SET reviewed_by=%s,reviewed_at=now(),note=%s '
                                   'WHERE id=%s AND reviewed_at IS NULL RETURNING id',
                                   (user.id, note[:2000], event_id)).fetchone()
            if not changed:
                raise ValueError('Событие уже просмотрено или не найдено. Обновите журнал.')

    def update_event_note(self, token: str, event_id: int, note: str) -> None:
        self.user(token)
        with self.connect() as conn:
            row = conn.execute('UPDATE events SET note=%s WHERE id=%s RETURNING id',
                               (note[:2000], event_id)).fetchone()
            if row is None:
                raise ValueError('Событие не найдено.')

    def delete_events(self, token: str, event_ids: list[int]) -> int:
        self.user(token, admin=True)
        if not event_ids or len(event_ids) > 100 or any(type(value) is not int or value <= 0 for value in event_ids):
            raise ValueError('Выберите от 1 до 100 записей для удаления.')
        with self.connect() as conn:
            return conn.execute('DELETE FROM events WHERE id=ANY(%s)', (event_ids,)).rowcount


class DatabaseWorkspaceStore(WorkspaceStore):
    """Reuse validation and rollback logic with PostgreSQL as the source of truth."""

    def __init__(self, database: Database, token: str) -> None:
        super().__init__()
        self.database, self.token = database, token
        self.can_manage = database.user(token).is_admin
        document, self.revision = database.load_config(token)
        self.workspaces, self.settings = self._decode(document)

    def save(self) -> None:
        document = dict(version=1, settings=dict(self.settings),
                        workspaces=[asdict(w) for w in self.workspaces])
        try:
            self.revision = self.database.save_config(self.token, document, self.revision)
        except psycopg.Error:
            raise DatabaseError('Не удалось сохранить изменения в PostgreSQL.') from None

    def fork(self):
        """Copy the current revision for a worker without reading or mutating Tk state."""
        draft = object.__new__(type(self))
        draft.database, draft.token = self.database, self.token
        draft.can_manage, draft.revision = self.can_manage, self.revision
        draft.workspaces, draft.settings = deepcopy(self.workspaces), dict(self.settings)
        return draft

    def adopt(self, draft):
        """Publish a committed draft on Tk's thread while retaining live camera references."""
        current = {workspace.id: workspace for workspace in self.workspaces}
        updated = []
        for source in draft.workspaces:
            workspace = current.get(source.id, source)
            cameras = {camera.id: camera for camera in workspace.cameras}
            updated_cameras = []
            for source_camera in source.cameras:
                camera = cameras.get(source_camera.id, source_camera)
                camera.__dict__.update(deepcopy(source_camera.__dict__))
                updated_cameras.append(camera)
            workspace.name, workspace.cameras = source.name, updated_cameras
            updated.append(workspace)
        self.workspaces, self.settings, self.revision = updated, dict(draft.settings), draft.revision
