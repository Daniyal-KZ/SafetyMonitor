"""Camera configuration and atomic local storage; no demonstration data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable
from urllib.parse import urlsplit
from uuid import uuid4


MODULE_LABELS: dict[str, str] = {
    "helmet": "Каска",
    "vest": "Сигнальный жилет",
    "glasses": "Защитные очки",
    "gloves": "Перчатки",
    "boots": "Защитная обувь",
    "respirator": "Респиратор",
    "ear_protection": "Защита слуха",
}

DEFAULT_SETTINGS: dict[str, str | int | bool] = {
    "theme": "Light",
    "view_columns": 2,
    "auto_connect": True,
    "reconnect_seconds": 5,
}


def _new_id() -> str:
    return str(uuid4())


@dataclass
class Rules:
    """Selected PPE modules, independent for each camera."""

    helmet: bool = False
    vest: bool = False
    glasses: bool = False
    gloves: bool = False
    boots: bool = False
    respirator: bool = False
    ear_protection: bool = False


@dataclass
class Camera:
    name: str
    description: str = ""
    source: int | str = ""
    rules: Rules = field(default_factory=Rules)
    enabled: bool = True
    transport: str = "tcp"
    id: str = field(default_factory=_new_id)


@dataclass
class Workspace:
    name: str
    cameras: list[Camera] = field(default_factory=list)
    id: str = field(default_factory=_new_id)


def validate_source(source: int | str) -> int | str:
    """Validate USB indices, RTSP addresses and existing local video files."""

    return _validate_source(source, check_file=True)


def _validate_source(source: int | str, *, check_file: bool) -> int | str:
    if isinstance(source, bool) or not isinstance(source, (int, str)):
        raise ValueError("Укажите RTSP-адрес, локальный видеофайл или номер USB-камеры.")
    if isinstance(source, int):
        if source < 0:
            raise ValueError("Номер USB-камеры должен быть неотрицательным.")
        return source
    source = source.strip()
    if not source:
        raise ValueError("Укажите источник видеопотока.")
    if any(character.isspace() for character in source) and "://" in source:
        raise ValueError("RTSP-адрес не должен содержать пробелы; используйте кодирование URL.")
    if "://" in source:
        try:
            parsed = urlsplit(source)
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
            valid = (
                parsed.scheme.lower() in {"rtsp", "rtsps"}
                and bool(parsed.hostname)
                and valid_port
                and not parsed.netloc.rsplit("@", 1)[-1].endswith(":")
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            # Never echo source: it may contain the camera's password.
            raise ValueError("Некорректный RTSP-адрес. Проверьте протокол, адрес камеры и порт.")
        return source
    if "\x00" in source:
        raise ValueError("Недопустимый путь к локальному видеофайлу.")
    if check_file:
        try:
            is_file = Path(source).is_file()
        except (OSError, ValueError):
            is_file = False
        if not is_file:
            raise ValueError("Локальный видеофайл не найден. Для IP-камеры укажите RTSP-адрес.")
    return source


def source_label(source: int | str) -> str:
    """Human-readable source with all network credentials and query removed."""

    if isinstance(source, int) and not isinstance(source, bool):
        return f"USB-камера {source}"
    if not isinstance(source, str) or not source:
        return "Источник не задан"
    if "://" in source:
        try:
            parsed = urlsplit(source)
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme}://{host}{port}{parsed.path}"
        except ValueError:
            return "RTSP-камера"
    return Path(source).name


class WorkspaceStore:
    """Empty-by-default repository; a path enables atomic JSON persistence."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.workspaces: list[Workspace] = []
        self.settings = dict(DEFAULT_SETTINGS)
        if self.path is not None and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.workspaces, self.settings = self._decode(data)
            except (json.JSONDecodeError, UnicodeError, ValueError, TypeError) as exc:
                raise ValueError(
                    "Файл настроек повреждён или имеет неподдерживаемый формат. "
                    "Исходный файл сохранён; исправьте его или восстановите резервную копию."
                ) from exc

    @staticmethod
    def _clean_name(name: str, label: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Укажите {label}.")
        return name.strip()

    @staticmethod
    def _ensure_unique(name: str, existing: Iterable[str], label: str) -> None:
        if any(other.strip().casefold() == name.casefold() for other in existing):
            raise ValueError(f"{label} с таким названием уже существует.")

    @staticmethod
    def _camera_values(
        name: str, description: str, source: int | str, transport: str, enabled: bool,
        *, check_file: bool = True,
    ) -> dict:
        name = WorkspaceStore._clean_name(name, "название камеры")
        if not isinstance(description, str):
            raise ValueError("Описание камеры должно быть текстом.")
        if not isinstance(transport, str) or transport not in {"tcp", "udp"}:
            raise ValueError("Выберите транспорт RTSP: TCP или UDP.")
        if not isinstance(enabled, bool):
            raise ValueError("Состояние камеры должно быть логическим значением.")
        return {
            "name": name,
            "description": description.strip(),
            "source": _validate_source(source, check_file=check_file),
            "transport": transport,
            "enabled": enabled,
        }

    @staticmethod
    def _validate_settings(settings: dict) -> dict:
        if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
            raise ValueError("Неизвестные настройки приложения.")
        result = {**DEFAULT_SETTINGS, **settings}
        if not isinstance(result["theme"], str) or result["theme"] not in {"Light", "Dark", "System"}:
            raise ValueError("Выберите светлую, тёмную или системную тему.")
        columns = result["view_columns"]
        if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= 3:
            raise ValueError("Количество столбцов должно быть от 1 до 3.")
        if not isinstance(result["auto_connect"], bool):
            raise ValueError("Автоподключение должно быть логическим значением.")
        seconds = result["reconnect_seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 300:
            raise ValueError("Интервал переподключения должен быть от 1 до 300 секунд.")
        return result

    @classmethod
    def _decode(cls, data: dict) -> tuple[list[Workspace], dict]:
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or type(data.get("version")) is not int
            or set(data) - {"version", "settings", "workspaces"}
            or not isinstance(data.get("workspaces"), list)
        ):
            raise ValueError("Неподдерживаемый формат настроек.")
        settings = cls._validate_settings(data.get("settings", {}))
        workspaces: list[Workspace] = []
        ids: set[str] = set()
        for item in data["workspaces"]:
            if not isinstance(item, dict) or set(item) != {"id", "name", "cameras"}:
                raise ValueError("Некорректная запись рабочей зоны.")
            name = cls._clean_name(item["name"], "название рабочей зоны")
            cls._ensure_unique(name, (zone.name for zone in workspaces), "Рабочая зона")
            workspace_id = cls._read_id(item["id"], ids)
            if not isinstance(item["cameras"], list):
                raise ValueError("Некорректный список камер.")
            workspace = Workspace(name=name, id=workspace_id)
            for camera_data in item["cameras"]:
                if not isinstance(camera_data, dict) or set(camera_data) != {
                    "id", "name", "description", "source", "rules", "enabled", "transport"
                }:
                    raise ValueError("Некорректная запись камеры.")
                values = cls._camera_values(
                    check_file=False,
                    **{key: camera_data[key] for key in ("name", "description", "source", "transport", "enabled")}
                )
                cls._ensure_unique(values["name"], (camera.name for camera in workspace.cameras), "Камера")
                rule_data = camera_data["rules"]
                if (
                    not isinstance(rule_data, dict)
                    or set(rule_data) != {entry.name for entry in fields(Rules)}
                    or any(not isinstance(value, bool) for value in rule_data.values())
                ):
                    raise ValueError("Некорректные настройки модулей СИЗ.")
                workspace.cameras.append(
                    Camera(**values, rules=Rules(**rule_data), id=cls._read_id(camera_data["id"], ids))
                )
            workspaces.append(workspace)
        return workspaces, settings

    @staticmethod
    def _read_id(value: str, ids: set[str]) -> str:
        if not isinstance(value, str) or not value.strip() or value in ids:
            raise ValueError("Некорректный или повторяющийся идентификатор.")
        ids.add(value)
        return value

    def save(self) -> None:
        """Write in the same directory, replacing the target only after fsync."""

        data = {
            "version": 1,
            "settings": dict(self.settings),
            "workspaces": [asdict(workspace) for workspace in self.workspaces],
        }
        self._decode(data)  # Also validate changes made directly to dataclasses.
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _commit(self, undo: Callable[[], None]) -> None:
        try:
            self.save()
        except Exception:
            undo()
            raise

    def get_workspace(self, workspace_id: str) -> Workspace:
        for workspace in self.workspaces:
            if workspace.id == workspace_id:
                return workspace
        raise KeyError("Рабочая зона не найдена.")

    def add_workspace(self, name: str) -> Workspace:
        name = self._clean_name(name, "название рабочей зоны")
        self._ensure_unique(name, (item.name for item in self.workspaces), "Рабочая зона")
        workspace = Workspace(name=name)
        self.workspaces.append(workspace)
        self._commit(lambda: self.workspaces.remove(workspace))
        return workspace

    def rename_workspace(self, workspace_id: str, name: str) -> None:
        workspace = self.get_workspace(workspace_id)
        name = self._clean_name(name, "название рабочей зоны")
        self._ensure_unique(name, (item.name for item in self.workspaces if item.id != workspace_id), "Рабочая зона")
        previous = workspace.name
        workspace.name = name
        self._commit(lambda: setattr(workspace, "name", previous))

    def remove_workspace(self, workspace_id: str) -> None:
        workspace = self.get_workspace(workspace_id)
        index = self.workspaces.index(workspace)
        self.workspaces.pop(index)
        self._commit(lambda: self.workspaces.insert(index, workspace))

    def add_camera(
        self, workspace_id: str, name: str, description: str = "", source: int | str = "",
        transport: str = "tcp", enabled: bool = True,
    ) -> Camera:
        workspace = self.get_workspace(workspace_id)
        values = self._camera_values(name, description, source, transport, enabled)
        self._ensure_unique(values["name"], (item.name for item in workspace.cameras), "Камера")
        camera = Camera(**values)
        workspace.cameras.append(camera)
        self._commit(lambda: workspace.cameras.remove(camera))
        return camera

    def update_camera(
        self, workspace_id: str, camera_id: str, *, name: str, description: str,
        source: int | str, transport: str, enabled: bool,
    ) -> None:
        workspace = self.get_workspace(workspace_id)
        camera = self._get_camera(workspace, camera_id)
        values = self._camera_values(
            name, description, source, transport, enabled, check_file=source != camera.source,
        )
        self._ensure_unique(values["name"], (item.name for item in workspace.cameras if item.id != camera_id), "Камера")
        previous = {key: getattr(camera, key) for key in values}
        for key, value in values.items():
            setattr(camera, key, value)

        def undo() -> None:
            for key, value in previous.items():
                setattr(camera, key, value)

        self._commit(undo)

    @staticmethod
    def _get_camera(workspace: Workspace, camera_id: str) -> Camera:
        for camera in workspace.cameras:
            if camera.id == camera_id:
                return camera
        raise KeyError("Камера не найдена.")

    def remove_camera(self, workspace_id: str, camera_id: str) -> None:
        workspace = self.get_workspace(workspace_id)
        camera = self._get_camera(workspace, camera_id)
        index = workspace.cameras.index(camera)
        workspace.cameras.pop(index)
        self._commit(lambda: workspace.cameras.insert(index, camera))

    def update_settings(self, **kwargs) -> None:
        result = self._validate_settings({**self.settings, **kwargs})
        previous = self.settings
        self.settings = result
        self._commit(lambda: setattr(self, "settings", previous))
