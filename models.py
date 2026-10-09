"""Подготовка COCO-датасета и обучение детектора СИЗ на Ultralytics YOLO26.

Примеры запуска из корня проекта:
    python models.py --prepare-only
    python models.py --time-hours 2
    python models.py --time-hours 0 --epochs 100 --batch 8

Зависимости для обучения: python -m pip install -r requirements-training.txt
Форматы и API: https://docs.ultralytics.com/datasets/detect/
              https://docs.ultralytics.com/modes/train/
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "yolo26n.pt"
DEFAULT_TIME_HOURS = 2.0


def _load_coco(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Не найдена разметка COCO: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), list) for key in ("images", "annotations", "categories")
    ):
        raise ValueError(f"Ожидаются списки images, annotations и categories в {path}")
    return data


def _categories(coco: dict) -> dict[int, str]:
    result = {}
    for category in coco["categories"]:
        category_id, name = category["id"], category["name"]
        if type(category_id) is not int or not isinstance(name, str) or not name.strip():
            raise ValueError("У категории должны быть целочисленный id и непустое name.")
        if category_id in result or name in result.values():
            raise ValueError(f"Повторяющаяся категория COCO: {category}")
        result[category_id] = name
    return result


def _normalize_box(bbox: list, width: float, height: float) -> tuple[tuple | None, bool]:
    """COCO [x, y, w, h] -> YOLO [cx, cy, w, h] в диапазоне 0..1."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"Некорректный bbox COCO: {bbox}")
    x, y, box_width, box_height = map(float, bbox)
    if not all(math.isfinite(value) for value in (x, y, box_width, box_height)):
        raise ValueError(f"В bbox есть бесконечность или NaN: {bbox}")
    if box_width <= 0 or box_height <= 0:
        return None, False
    x1, y1 = max(0.0, x), max(0.0, y)
    x2, y2 = min(width, x + box_width), min(height, y + box_height)
    clipped = (x1, y1, x2, y2) != (x, y, x + box_width, y + box_height)
    if x2 <= x1 or y2 <= y1:
        return None, clipped
    return (
        (x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
        (x2 - x1) / width, (y2 - y1) / height,
    ), clipped


def _link_or_copy(source: Path, destination: Path) -> None:
    """Жёсткая ссылка экономит место; при её недоступности используется копия."""
    if destination.exists():
        if source.samefile(destination):
            return
        source_stat, destination_stat = source.stat(), destination.stat()
        if (source_stat.st_size, source_stat.st_mtime_ns) == (
            destination_stat.st_size, destination_stat.st_mtime_ns
        ):
            return
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepare_dataset(
    data_dir: Path | str = ROOT / "data",
    output_dir: Path | str = ROOT / "data" / "yolo",
    *, strict: bool = False,
) -> Path:
    """Создать YOLO-разметку, YAML и отчёт, сохранив исходные JSON и изображения.

    train и valid (или val) обязательны; test используется, если присутствует.
    Отсутствующие файлы перечисляются в preparation_report.json. strict=True
    останавливает подготовку при первом отсутствующем изображении.
    """
    data_dir, output_dir = Path(data_dir).resolve(), Path(output_dir).resolve()
    validation_dir = "valid" if (data_dir / "valid").is_dir() else "val"
    split_dirs = {"train": data_dir / "train", "val": data_dir / validation_dir}
    if (data_dir / "test").is_dir():
        split_dirs["test"] = data_dir / "test"
    if output_dir == data_dir or any(
        output_dir.is_relative_to(folder) or folder.is_relative_to(output_dir)
        for folder in split_dirs.values()
    ):
        raise ValueError("Папка результата должна быть отделена от исходных train/valid/test.")
    datasets = {
        split: _load_coco(folder / "_annotations.coco.json")
        for split, folder in split_dirs.items()
    }
    train_categories = _categories(datasets["train"])
    used_ids = {annotation["category_id"] for annotation in datasets["train"]["annotations"]}
    if not used_ids or used_ids - train_categories.keys():
        raise ValueError("Обучающая разметка пуста или содержит неизвестные категории.")
    # SAFETY в этом экспорте — родительская категория без объектов, а не класс.
    names = [train_categories[category_id] for category_id in sorted(used_ids)]
    class_ids = {name: index for index, name in enumerate(names)}
    report = {"source": str(data_dir), "names": names, "splits": {}}

    for split, coco in datasets.items():
        folder = split_dirs[split]
        categories = _categories(coco)
        images = {}
        for image in coco["images"]:
            image_id = image["id"]
            if type(image_id) is not int or image_id < 0 or image_id in images:
                raise ValueError(f"Некорректный или повторяющийся image id в {split}: {image_id}")
            images[image_id] = image
        annotations = defaultdict(list)
        for annotation in coco["annotations"]:
            if annotation["image_id"] not in images:
                raise ValueError(f"Аннотация ссылается на неизвестное изображение в {split}.")
            category_name = categories.get(annotation["category_id"])
            if category_name not in class_ids:
                raise ValueError(f"Категория {category_name!r} из {split} отсутствует в train.")
            annotations[annotation["image_id"]].append(annotation)

        image_dir, label_dir = output_dir / "images" / split, output_dir / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        stats = {
            "source_images": len(images), "images": 0, "boxes": 0,
            "background_images": 0, "missing_images": [], "invalid_boxes": 0,
            "clipped_boxes": 0, "crowd_boxes": 0,
        }
        counts = Counter({name: 0 for name in names})
        image_paths = []
        for image_id, image in images.items():
            source = (folder / image["file_name"]).resolve()
            if not source.is_relative_to(folder):
                raise ValueError(f"Изображение выходит за пределы {folder}: {image['file_name']}")
            if not source.is_file():
                if strict:
                    raise FileNotFoundError(f"Не найдено изображение: {source}")
                stats["missing_images"].append(image["file_name"])
                continue
            width, height = float(image["width"]), float(image["height"])
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValueError(f"Некорректные размеры изображения: {source}")
            # Короткие имена предотвращают превышение длины пути на Windows.
            destination = image_dir / f"{image_id:08d}{source.suffix.lower()}"
            lines = []
            for annotation in annotations[image_id]:
                if annotation.get("iscrowd", 0):
                    stats["crowd_boxes"] += 1
                    continue
                box, clipped = _normalize_box(annotation["bbox"], width, height)
                if box is None:
                    stats["invalid_boxes"] += 1
                    continue
                stats["clipped_boxes"] += int(clipped)
                name = categories[annotation["category_id"]]
                lines.append(f"{class_ids[name]} " + " ".join(f"{value:.8f}" for value in box))
                counts[name] += 1
            _link_or_copy(source, destination)
            (label_dir / f"{image_id:08d}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
            image_paths.append("./" + destination.relative_to(output_dir).as_posix())
            stats["images"] += 1
            stats["boxes"] += len(lines)
            stats["background_images"] += int(not lines)
        if not image_paths or stats["boxes"] == 0:
            raise ValueError(f"В {split} нет доступных изображений с корректной разметкой.")
        # Списки исключают старые файлы при повторной подготовке изменённого датасета.
        (output_dir / f"{split}.txt").write_text("\n".join(image_paths) + "\n", encoding="utf-8")
        stats["class_counts"] = dict(counts)
        report["splits"][split] = stats
        print(
            f"{split}: {stats['images']} изображений, {stats['boxes']} объектов; "
            f"пропущено файлов: {len(stats['missing_images'])}, "
            f"неверных рамок: {stats['invalid_boxes']}, обрезано рамок: {stats['clipped_boxes']}",
            flush=True,
        )

    # JSON-строки также являются корректными YAML-значениями, включая кириллицу и пробелы.
    yaml_lines = ["path: " + json.dumps(output_dir.as_posix(), ensure_ascii=False)]
    yaml_lines += [f"{split}: {split}.txt" for split in datasets]
    yaml_lines += ["names:"] + [
        f"  {index}: {json.dumps(name, ensure_ascii=False)}" for index, name in enumerate(names)
    ]
    yaml_path = output_dir / "dataset.yaml"
    yaml_path.write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    (output_dir / "preparation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Классы: {dict(enumerate(names))}\nДатасет: {yaml_path}", flush=True)
    return yaml_path


def train_model(
    dataset_yaml: Path | str, *, model: str = DEFAULT_MODEL, epochs: int = 1000,
    time_hours: float = DEFAULT_TIME_HOURS,
    imgsz: int = 640, batch: int = -1, device: str = "0", workers: int = 2,
    project: Path | str = ROOT / "runs" / "detect", name: str = "ppe",
    patience: int = 0, seed: int = 42, resume: Path | None = None,
    evaluate_test: bool = False,
) -> Path:
    """Обучить детектор с лимитом времени; time_hours=0 включает лимит по эпохам."""
    if not math.isfinite(time_hours) or time_hours < 0:
        raise ValueError("time_hours должен быть конечным числом >= 0.")
    try:
        import torch
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Установите зависимости обучения в используемое окружение: "
            "python -m pip install -r requirements-training.txt"
        ) from exc
    dataset_yaml = Path(dataset_yaml).resolve()
    if not dataset_yaml.is_file():
        raise FileNotFoundError(f"Не найден YAML датасета: {dataset_yaml}")
    if device == "auto":
        device = "0" if torch.cuda.is_available() else "cpu"
    if device not in {"cpu", "mps"} and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA недоступна. Установите CUDA-сборку PyTorch: "
            "python -m pip install torch torchvision --index-url "
            "https://download.pytorch.org/whl/cu128 . "
            "Затем проверьте драйвер NVIDIA и torch.cuda.is_available()."
        )
    print(f"Устройство обучения: {device}", flush=True)
    if device == "0":
        gpu = torch.cuda.get_device_properties(0)
        print(
            f"CUDA {torch.version.cuda}: {gpu.name}, "
            f"видеопамять {gpu.total_memory / 1024 ** 3:.1f} ГБ", flush=True,
        )
    if device == "cpu":
        print("Обучение на CPU может занять много времени.", flush=True)
        if batch == -1:
            batch = 4  # AutoBatch рассчитан на CUDA; для явного CPU используем небольшой batch.
    if resume is not None and not resume.is_file():
        raise FileNotFoundError(f"Не найден checkpoint для продолжения: {resume}")
    detector = YOLO(str(resume) if resume is not None else model, task="detect")
    if resume is not None:
        # Число эпох и параметры оптимизатора берутся из сохранённого checkpoint.
        detector.train(resume=True, data=str(dataset_yaml), device=device, workers=workers)
    else:
        if time_hours:
            print(
                f"Лимит обучения: {time_hours:g} ч; число эпох определяется по скорости обучения. "
                "Подготовка данных и финальная оценка выполняются дополнительно.", flush=True,
            )
        detector.train(
            data=str(dataset_yaml), epochs=epochs, time=time_hours or None,
            imgsz=imgsz, batch=batch,
            device=device, workers=workers, project=str(Path(project).resolve()),
            name=name, patience=patience, seed=seed, deterministic=True,
            amp=True, cache=False, save=True, val=True, plots=True,
        )
    best_weights = Path(detector.trainer.best)
    if not best_weights.is_file():
        raise RuntimeError("Обучение завершилось без файла лучших весов best.pt.")
    print(f"Лучшие веса: {best_weights}", flush=True)
    if evaluate_test:
        # Test не участвует в обучении или выборе лучшей эпохи.
        report = json.loads((dataset_yaml.parent / "preparation_report.json").read_text(encoding="utf-8"))
        if "test" not in report["splits"]:
            raise ValueError("Для --evaluate-test нужна папка data/test с COCO-разметкой.")
        metrics = YOLO(str(best_weights)).val(
            data=str(dataset_yaml), split="test", imgsz=imgsz, batch=batch,
            device=device, workers=workers, project=str(best_weights.parent.parent),
            name="test", plots=True,
        )
        print(f"Test mAP50: {metrics.box.map50:.4f}; mAP50-95: {metrics.box.map:.4f}")
    return best_weights


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение YOLO на COCO-датасете СИЗ")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Папка с train/valid/test")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "yolo", help="Подготовленный датасет")
    parser.add_argument("--prepare-only", action="store_true", help="Подготовить датасет без обучения")
    parser.add_argument("--strict", action="store_true", help="Остановиться при отсутствующем изображении")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Предобученные веса или YAML архитектуры (по умолчанию {DEFAULT_MODEL})",
    )
    parser.add_argument("--epochs", type=int, default=1000, help="Число эпох при --time-hours 0")
    parser.add_argument(
        "--time-hours", type=float, default=DEFAULT_TIME_HOURS,
        help="Лимит обучения в часах (по умолчанию 2); 0 включает лимит по эпохам",
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=-1, help="-1: автоматически по 60%% VRAM; либо число > 0")
    parser.add_argument("--device", default="0", help="CUDA GPU 0 по умолчанию; также cpu или auto")
    parser.add_argument("--workers", type=int, default=2, help="Потоки загрузки; 0 для диагностики на Windows")
    parser.add_argument("--project", type=Path, default=ROOT / "runs" / "detect")
    parser.add_argument("--name", default="ppe")
    parser.add_argument("--patience", type=int, default=0, help="Ранняя остановка; по умолчанию отключена (0)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", type=Path, help="Продолжить прерванное обучение из weights/last.pt")
    parser.add_argument("--evaluate-test", action="store_true", help="После обучения оценить best.pt на test")
    args = parser.parse_args()
    if min(args.epochs, args.imgsz) < 1 or args.batch == 0 or args.batch < -1 or min(args.workers, args.patience) < 0:
        parser.error("epochs, imgsz > 0; batch = -1 или > 0; workers, patience >= 0.")
    if not math.isfinite(args.time_hours) or args.time_hours < 0:
        parser.error("time-hours должен быть конечным числом >= 0.")
    try:
        dataset_yaml = prepare_dataset(args.data_dir, args.output_dir, strict=args.strict)
        if not args.prepare_only:
            train_model(
                dataset_yaml, model=args.model, epochs=args.epochs,
                time_hours=args.time_hours, imgsz=args.imgsz,
                batch=args.batch, device=args.device, workers=args.workers,
                project=args.project, name=args.name, patience=args.patience,
                seed=args.seed, resume=args.resume, evaluate_test=args.evaluate_test,
            )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    # Защита необходима при запуске DataLoader на Windows.
    from multiprocessing import freeze_support

    freeze_support()
    main()
