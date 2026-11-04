"""PostgreSQL persistence, password verification and application permissions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import hmac
from io import BytesIO
import os
import re
from pathlib import Path
import secrets

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import DEFAULT_SETTINGS, PERSONAL_SETTINGS, WorkspaceStore

ROOT = Path(__file__).resolve().parent.parent
_UNCHANGED_PHOTO = object()


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

    @staticmethod
    def _config_row(conn, *, write=False):
        # Access checks share the config lock with mutations, so revocation and
        # a protected operation have a single, ordered transaction boundary.
        return conn.execute('SELECT document,revision FROM application_config WHERE id=1 '
                            + ('FOR UPDATE' if write else 'FOR SHARE')).fetchone()

    @staticmethod
    def _visible_workspaces(user: User, document: dict) -> list[dict]:
        return [workspace for workspace in document['workspaces']
                if user.is_admin or user.id in workspace.get('inspector_ids', [])]

    @classmethod
    def _visible_ids(cls, user: User, document: dict) -> list[str]:
        return [workspace['id'] for workspace in cls._visible_workspaces(user, document)]

    @staticmethod
    def _preferences(conn, user_id: int) -> dict:
        row = conn.execute('SELECT document FROM user_preferences WHERE user_id=%s', (user_id,)).fetchone()
        return row['document'] if row else {}

    @staticmethod
    def _write_preferences(conn, user_id: int, values: dict) -> None:
        if values:
            conn.execute('INSERT INTO user_preferences(user_id,document) VALUES (%s,%s) '
                         'ON CONFLICT (user_id) DO UPDATE SET document=user_preferences.document || '
                         'EXCLUDED.document,updated_at=now()', (user_id, Jsonb(values)))

    @staticmethod
    def _validate_memberships(conn, document: dict) -> None:
        inspector_ids = {value for workspace in document['workspaces']
                         for value in workspace.get('inspector_ids', [])}
        worker_ids = {value for workspace in document['workspaces']
                      for value in workspace.get('worker_ids', [])}
        inspectors = {row['id'] for row in conn.execute(
            "SELECT id FROM users WHERE role='inspector' AND id=ANY(%s::bigint[])",
            (list(inspector_ids),)).fetchall()}
        workers = {row['id'] for row in conn.execute(
            'SELECT id FROM workers WHERE id=ANY(%s::integer[])', (list(worker_ids),)).fetchall()}
        if inspector_ids != inspectors:
            raise ValueError('Выберите существующих инспекторов.')
        if worker_ids != workers:
            raise ValueError('Рабочий уже удалён. Обновите список и повторите выбор.')

    def load_config(self, token: str) -> tuple[dict, int]:
        user = self.user(token)
        with self.connect() as conn:
            row = self._config_row(conn)
            document = deepcopy(row['document'])
            document['settings'] = {**DEFAULT_SETTINGS, **document.get('settings', {}),
                                    **{key: value for key, value in self._preferences(conn, user.id).items()
                                       if key in PERSONAL_SETTINGS}}
            document['workspaces'] = self._visible_workspaces(user, document)
        return document, row['revision']

    def save_config(self, token: str, document: dict, revision: int,
                    *, return_document: bool = False) -> int | tuple[dict, int]:
        self.user(token, admin=True)
        _, settings = WorkspaceStore._decode(document)
        with self.connect() as conn:
            current = self._config_row(conn, write=True)
            if current['revision'] != revision:
                raise ValueError('Объекты изменены в другом окне. Обновите данные и повторите действие.')
            self._validate_memberships(conn, document)
            document = deepcopy(document)
            document['settings'] = settings
            existing_numbers = {camera['id']: camera.get('display_id')
                                for workspace in current['document']['workspaces']
                                for camera in workspace['cameras']}
            for workspace in document['workspaces']:
                for camera in workspace['cameras']:
                    previous = existing_numbers.get(camera['id'])
                    requested = camera.get('display_id')
                    if requested is not None and requested != previous:
                        raise ValueError('ID камеры назначается автоматически и не изменяется.')
            try:
                for workspace in document['workspaces']:
                    for camera in workspace['cameras']:
                        previous = existing_numbers.get(camera['id'])
                        camera['display_id'] = previous if previous is not None else conn.execute(
                            "SELECT nextval('camera_display_id_seq') AS number").fetchone()['number']
            except psycopg.errors.SequenceGeneratorLimitExceeded:
                raise ValueError('Четырёхзначные ID камер закончились. Требуется расширить диапазон идентификаторов.') from None
            # A workspace save must never publish the administrator's personal
            # appearance as the default for another account.
            for key in PERSONAL_SETTINGS:
                document['settings'][key] = current['document'].get('settings', {}).get(key, DEFAULT_SETTINGS[key])
            row = conn.execute('UPDATE application_config SET document=%s,revision=revision+1,updated_at=now() '
                               'WHERE id=1 AND revision=%s RETURNING revision', (Jsonb(document), revision)).fetchone()
            if row is None:
                raise ValueError('Объекты изменены в другом окне. Войдите заново, чтобы обновить данные.')
            saved_revision = row['revision']
        # The canonical document is exposed only after the transaction commits;
        # callers never display a camera number from a failed save.
        return (document, saved_revision) if return_document else saved_revision

    def save_preferences(self, token: str, values: dict) -> None:
        user = self.user(token)
        if not isinstance(values, dict) or set(values) - PERSONAL_SETTINGS:
            raise AccessDenied('Можно изменять только личные настройки интерфейса и просмотра.')
        validated = WorkspaceStore._validate_settings(values)
        with self.connect() as conn:
            self._write_preferences(conn, user.id, {key: validated[key] for key in values})

    def save_settings(self, token: str, values: dict, revision: int) -> int:
        user = self.user(token)
        validated = WorkspaceStore._validate_settings(values)
        global_keys = set(values) - PERSONAL_SETTINGS
        if global_keys and not user.is_admin:
            raise AccessDenied('Модель распознавания изменяет администратор.')
        if not global_keys:
            self.save_preferences(token, values)
            return revision
        with self.connect() as conn:
            current = self._config_row(conn, write=True)
            if current['revision'] != revision:
                raise ValueError('Объекты изменены в другом окне. Обновите данные и повторите действие.')
            document = current['document']
            document.setdefault('settings', {}).update({key: validated[key] for key in global_keys})
            row = conn.execute('UPDATE application_config SET document=%s,revision=revision+1,updated_at=now() '
                               'WHERE id=1 RETURNING revision', (Jsonb(document),)).fetchone()
            self._write_preferences(conn, user.id,
                                    {key: validated[key] for key in values if key in PERSONAL_SETTINGS})
            return row['revision']

    def list_inspectors(self, token: str) -> list[User]:
        user = self.user(token)
        with self.connect() as conn:
            rows = conn.execute("SELECT id,username,display_name,role FROM users WHERE role='inspector' "
                                'AND (%s OR id=%s) ORDER BY display_name,username',
                                (user.is_admin, user.id)).fetchall()
        return [User(**row) for row in rows]

    def create_inspector(self, token: str, username: str, display_name: str, password: str) -> User:
        self.user(token, admin=True)
        username = username.strip().lower() if isinstance(username, str) else ''
        if re.fullmatch(r'[a-z0-9_.-]{3,32}', username) is None:
            raise ValueError('Логин: 3–32 латинских символа, цифры, точка, дефис или подчёркивание.')
        display_name = self._person_name(display_name, 'имя инспектора', maximum=120)
        if not isinstance(password, str) or not 5 <= len(password) <= 128 or not password.strip():
            raise ValueError('Пароль должен содержать от 5 до 128 символов.')
        try:
            with self.connect() as conn:
                row = conn.execute("INSERT INTO users(username,display_name,role,password_hash) "
                                   "VALUES (%s,%s,'inspector',%s) RETURNING id,username,display_name,role",
                                   (username, display_name, hash_password(password))).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ValueError('Этот логин уже занят.') from None
        return User(**row)

    @staticmethod
    def _person_name(value, label, maximum=100) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or '\x00' in value:
            raise ValueError(f'Укажите {label} (до {maximum} символов).')
        return value.strip()

    @staticmethod
    def _worker_id(value) -> int:
        if type(value) is not int or not 1000 <= value <= 9999:
            raise ValueError('ID рабочего должен состоять из четырёх цифр.')
        return value

    @staticmethod
    def _photo(photo) -> bytes | None:
        if photo is None:
            return None
        if not isinstance(photo, bytes) or not photo or len(photo) > 5 * 1024 * 1024:
            raise ValueError('Фото должно быть изображением размером не более 5 МБ.')
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with Image.open(BytesIO(photo)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'} or source.width * source.height > 20_000_000:
                    raise ValueError('Используйте JPEG, PNG или WebP размером до 20 мегапикселей.')
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((640, 640))
                output = BytesIO()
                image.save(output, format='JPEG', quality=85)
                return output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError('Не удалось прочитать фото. Выберите JPEG, PNG или WebP.') from None

    def _worker_scope(self, conn, user: User, workspace_id=None) -> list[int] | None:
        document = self._config_row(conn)['document']
        visible = self._visible_workspaces(user, document)
        if workspace_id is not None:
            visible = [workspace for workspace in visible if workspace['id'] == workspace_id]
            if not visible:
                raise AccessDenied('Рабочая зона не найдена или недоступна.')
        elif user.is_admin:
            return None
        return sorted({worker_id for workspace in visible for worker_id in workspace.get('worker_ids', [])})

    def list_workers(self, token: str, workspace_id: str | None = None, search: str = '') -> list[dict]:
        user = self.user(token)
        search = str(search).strip()[:200]
        with self.connect() as conn:
            ids = self._worker_scope(conn, user, workspace_id)
            return conn.execute('SELECT id,first_name,last_name,photo IS NOT NULL AS has_photo FROM workers '
                                'WHERE (%s OR id=ANY(%s::integer[])) AND '
                                "(first_name ILIKE %s OR last_name ILIKE %s OR id::text ILIKE %s) "
                                'ORDER BY last_name,first_name,id',
                                (ids is None, ids or [], *([f'%{search}%'] * 3))).fetchall()

    def get_worker(self, token: str, worker_id: int) -> dict:
        user = self.user(token)
        worker_id = self._worker_id(worker_id)
        with self.connect() as conn:
            ids = self._worker_scope(conn, user)
            if ids is not None and worker_id not in ids:
                raise AccessDenied('Карточка рабочего недоступна.')
            row = conn.execute('SELECT id,first_name,last_name,photo,photo IS NOT NULL AS has_photo '
                               'FROM workers WHERE id=%s', (worker_id,)).fetchone()
        if row is None:
            raise ValueError('Рабочий не найден.')
        row['photo'] = bytes(row['photo']) if row['photo'] is not None else None
        return row

    def create_worker(self, token: str, first_name: str, last_name: str, photo=None) -> int:
        self.user(token, admin=True)
        first_name = self._person_name(first_name, 'имя рабочего')
        last_name = self._person_name(last_name, 'фамилию рабочего')
        photo = self._photo(photo)
        try:
            with self.connect() as conn:
                row = conn.execute('INSERT INTO workers(first_name,last_name,photo) VALUES (%s,%s,%s) RETURNING id',
                                   (first_name, last_name, photo)).fetchone()
        except psycopg.errors.SequenceGeneratorLimitExceeded:
            raise ValueError('Четырёхзначные ID закончились. Требуется расширить диапазон идентификаторов.') from None
        return row['id']

    def update_worker(self, token: str, worker_id: int, first_name: str, last_name: str,
                      photo=_UNCHANGED_PHOTO) -> None:
        self.user(token, admin=True)
        worker_id = self._worker_id(worker_id)
        first_name = self._person_name(first_name, 'имя рабочего')
        last_name = self._person_name(last_name, 'фамилию рабочего')
        replace_photo = photo is not _UNCHANGED_PHOTO
        photo = self._photo(photo) if replace_photo else None
        with self.connect() as conn:
            row = conn.execute('UPDATE workers SET first_name=%s,last_name=%s,'
                               'photo=CASE WHEN %s THEN %s ELSE photo END,updated_at=now() '
                               'WHERE id=%s RETURNING id',
                               (first_name, last_name, replace_photo, photo, worker_id)).fetchone()
            if row is None:
                raise ValueError('Рабочий уже удалён. Обновите список.')

    def delete_worker(self, token: str, worker_id: int) -> None:
        self.user(token, admin=True)
        worker_id = self._worker_id(worker_id)
        with self.connect() as conn:
            current = self._config_row(conn, write=True)
            if not conn.execute('DELETE FROM workers WHERE id=%s RETURNING id', (worker_id,)).fetchone():
                raise ValueError('Рабочий уже удалён. Обновите список.')
            document, changed = current['document'], False
            for workspace in document['workspaces']:
                if worker_id in workspace.get('worker_ids', []):
                    workspace['worker_ids'].remove(worker_id)
                    changed = True
            if changed:
                conn.execute('UPDATE application_config SET document=%s,revision=revision+1,updated_at=now() WHERE id=1',
                             (Jsonb(document),))

    def record_event(self, token: str, camera_id: str, camera_name: str, workspace_name: str,
                     count: int, snapshot: bytes, *, kind: str = 'no_helmet') -> int | None:
        user = self._recorders.get(token) or self.user(token)
        if count <= 0 or not snapshot:
            raise ValueError('Для события нужны обнаружение и кадр.')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', kind):
            raise ValueError('Некорректный вид нарушения.')
        with self.connect() as conn:
            document = self._config_row(conn)['document']
            workspace_id = None
            for workspace in document['workspaces']:
                camera = next((camera for camera in workspace['cameras'] if camera['id'] == camera_id), None)
                if camera is not None:
                    if not user.is_admin and user.id not in workspace.get('inspector_ids', []):
                        raise AccessDenied('Нет доступа к рабочей зоне этой камеры.')
                    workspace_id, workspace_name, camera_name = workspace['id'], workspace['name'], camera['name']
                    break
            if workspace_id is None:
                if not user.is_admin:
                    raise AccessDenied('Камера не найдена или недоступна.')
                matches = [workspace['id'] for workspace in document['workspaces']
                           if workspace['name'] == workspace_name]
                workspace_id = matches[0] if len(matches) == 1 else None
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (camera_id,))
            recent = conn.execute("SELECT id FROM events WHERE camera_id=%s AND kind=%s AND occurred_at > now()-interval '30 seconds' LIMIT 1",
                                  (camera_id, kind)).fetchone()
            if recent:
                return None
            row = conn.execute('INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot,kind,workspace_id) '
                               'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                               (camera_id, camera_name, workspace_name, count, snapshot, kind, workspace_id)).fetchone()
            return row['id']

    def list_events(self, token: str, search: str = '', state: str = 'all', before: int | None = None,
                    *, limit: int = 100) -> list[dict]:
        user = self.user(token)
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError('Некорректный размер страницы.')
        if state not in {'all', 'new', 'reviewed'}:
            raise ValueError('Неизвестный статус.')
        with self.connect() as conn:
            visible = self._visible_ids(user, self._config_row(conn)['document'])
            return conn.execute("SELECT e.id,e.occurred_at,e.camera_name,e.workspace_name,e.workspace_id,e.kind,e.object_count,"
                                "e.reviewed_at,e.note,u.display_name AS reviewer FROM events e "
                                "LEFT JOIN users u ON u.id=e.reviewed_by "
                                "WHERE (e.camera_name ILIKE %s OR e.workspace_name ILIKE %s) "
                                "AND (%s='all' OR (%s='new' AND e.reviewed_at IS NULL) "
                                "OR (%s='reviewed' AND e.reviewed_at IS NOT NULL)) "
                                "AND (%s OR e.workspace_id=ANY(%s::text[])) "
                                "AND (%s::bigint IS NULL OR e.id < %s) ORDER BY e.id DESC LIMIT %s",
                                (f'%{search}%', f'%{search}%', state, state, state,
                                 user.is_admin, visible, before, before, limit)).fetchall()

    def event_snapshot(self, token: str, event_id: int) -> bytes:
        user = self.user(token)
        with self.connect() as conn:
            visible = self._visible_ids(user, self._config_row(conn)['document'])
            row = conn.execute('SELECT snapshot FROM events WHERE id=%s '
                               'AND (%s OR workspace_id=ANY(%s::text[]))',
                               (event_id, user.is_admin, visible)).fetchone()
        if row is None:
            raise AccessDenied('Событие не найдено или недоступно.')
        return bytes(row['snapshot'])

    def review_event(self, token: str, event_id: int, note: str) -> None:
        user = self.user(token)
        with self.connect() as conn:
            visible = self._visible_ids(user, self._config_row(conn)['document'])
            changed = conn.execute('UPDATE events SET reviewed_by=%s,reviewed_at=now(),note=%s '
                                   'WHERE id=%s AND reviewed_at IS NULL '
                                   'AND (%s OR workspace_id=ANY(%s::text[])) RETURNING id',
                                   (user.id, note[:2000], event_id, user.is_admin, visible)).fetchone()
            if not changed:
                raise AccessDenied('Событие уже просмотрено, удалено или недоступно. Обновите журнал.')

    def update_event_note(self, token: str, event_id: int, note: str) -> None:
        user = self.user(token)
        with self.connect() as conn:
            visible = self._visible_ids(user, self._config_row(conn)['document'])
            row = conn.execute('UPDATE events SET note=%s WHERE id=%s '
                               'AND (%s OR workspace_id=ANY(%s::text[])) RETURNING id',
                               (note[:2000], event_id, user.is_admin, visible)).fetchone()
            if row is None:
                raise AccessDenied('Событие не найдено или недоступно.')

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
            saved, revision = self.database.save_config(self.token, document, self.revision,
                                                        return_document=True)
        except psycopg.Error:
            raise DatabaseError('Не удалось сохранить изменения в PostgreSQL.') from None
        numbers = {camera['id']: camera['display_id'] for workspace in saved['workspaces']
                   for camera in workspace['cameras']}
        for workspace in self.workspaces:
            for camera in workspace.cameras:
                camera.display_id = numbers[camera.id]
        self.revision = revision

    def update_settings(self, **kwargs) -> None:
        values = self._validate_settings({**self.settings, **kwargs})
        try:
            revision = self.database.save_settings(self.token, kwargs, self.revision)
        except psycopg.Error:
            raise DatabaseError('Не удалось сохранить настройки в PostgreSQL.') from None
        self.settings, self.revision = values, revision

    def reload(self) -> None:
        document, revision = self.database.load_config(self.token)
        draft = self.fork()
        draft.workspaces, draft.settings = self._decode(document)
        draft.revision = revision
        self.adopt(draft)

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
            workspace.inspector_ids = list(source.inspector_ids)
            workspace.worker_ids = list(source.worker_ids)
            updated.append(workspace)
        self.workspaces, self.settings, self.revision = updated, dict(draft.settings), draft.revision
