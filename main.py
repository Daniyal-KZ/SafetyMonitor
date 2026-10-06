"""Start the local camera monitoring application."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Safety Monitor — рабочие зоны и видеонаблюдение")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent / "data" / "config.json",
                        help="Файл настроек и рабочих зон")
    args = parser.parse_args()
    try:
        from safety_monitor.app import App
    except ModuleNotFoundError as exc:
        if exc.name in {"customtkinter", "cv2", "PIL", "numpy", "darkdetect", "packaging"}:
            parser.exit(1, "Установите зависимости: python -m pip install -r requirements.txt\n")
        raise
    try:
        app = App(config_path=args.config)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Не удалось открыть настройки: {exc}\n")
    try:
        app.mainloop()
    finally:
        app.close()


if __name__ == "__main__":
    main()
