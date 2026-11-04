"""Start the local camera monitoring application."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Safety Monitor — рабочие зоны и видеонаблюдение")
    parser.parse_args()
    try:
        from safety_monitor.app import App
    except ModuleNotFoundError as exc:
        if exc.name in {"customtkinter", "cv2", "PIL", "numpy", "darkdetect", "packaging", "psycopg", "dotenv"}:
            parser.exit(1, "Установите зависимости: python -m pip install -r requirements.txt\n")
        raise
    try:
        app = App()
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Не удалось открыть настройки: {exc}\n")
    try:
        app.mainloop()
    finally:
        app.close()


if __name__ == "__main__":
    main()
