"""Prepare Construction-PPE as five classes and train YOLO26 on Windows."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

from safety_monitor.classes import CLASS_NAMES

ROOT = Path(__file__).resolve().parent
DATASET_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/construction-ppe.zip"
SOURCE_NAMES = ("helmet", "gloves", "vest", "boots", "goggles", "none", "Person",
                "no_helmet", "no_goggle", "no_gloves", "no_boots")
CLASS_MAP = {6: 0, 0: 1, 2: 2, 1: 3, 4: 4}
PROFILES = {
    "rtx3050": {"model": "yolo26n.pt", "imgsz": 800, "batch": 4, "workers": 0},
    "quality": {"model": "yolo26s.pt", "imgsz": 960, "batch": -1, "workers": 2},
    "cpu": {"model": "yolo26n.pt", "imgsz": 640, "batch": 2, "workers": 0},
}


def extract_dataset(archive: Path, destination: Path) -> Path:
    """Validate all archive paths before writing; reject traversal/symlinks."""
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if sum(item.file_size for item in members) > 3 * 1024**3:
            raise ValueError("Архив превышает допустимый размер 3 ГБ.")
        rooted = any(item.filename.startswith("construction-ppe/") for item in members)
        root = destination / "construction-ppe"
        extract_root = destination if rooted else root
        for item in members:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            target = (extract_root / str(path)).resolve()
            if (path.is_absolute() or ".." in path.parts or ":" in item.filename
                    or not target.is_relative_to(destination)
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Небезопасный путь в архиве датасета.")
            allowed = {"construction-ppe"} if rooted else {"images", "labels", "data.yaml", "LICENSE"}
            if path.parts and path.parts[0] not in allowed:
                raise ValueError("Ожидается официальный архив с папкой construction-ppe.")
        if root.exists():
            raise FileExistsError(f"Исходный датасет уже существует: {root}")
        bundle.extractall(extract_root)
    return root


def download_dataset(archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(".part")
    print(f"Скачивание: {DATASET_URL}", flush=True)
    try:
        request = urllib.request.Request(DATASET_URL, headers={"User-Agent": "SafetyMonitor/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 1024**3:
                    raise ValueError("Архив неожиданно велик: загрузка остановлена.")
                output.write(chunk)
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Архив повреждён.")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


def convert_label(text: str, location: str = "") -> list[str]:
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"Ожидается YOLO bbox (5 чисел): {location}")
        try:
            old_id = int(parts[0])
            x, y, w, h = map(float, parts[1:])
        except ValueError as exc:
            raise ValueError(f"Некорректная разметка: {location}") from exc
        if (old_id not in range(len(SOURCE_NAMES)) or not all(math.isfinite(v) for v in (x, y, w, h))
                or not 0 <= x <= 1 or not 0 <= y <= 1 or not 0 < w <= 1 or not 0 < h <= 1):
            raise ValueError(f"Класс или координаты за пределами YOLO: {location}")
        if old_id in CLASS_MAP:
            x1, y1, x2, y2 = max(0, x-w/2), max(0, y-h/2), min(1, x+w/2), min(1, y+h/2)
            result.append(f"{CLASS_MAP[old_id]} {(x1+x2)/2:.8f} {(y1+y2)/2:.8f} {x2-x1:.8f} {y2-y1:.8f}")
    return list(dict.fromkeys(result))


def prepare_dataset(data_dir: Path | str = ROOT / "data/construction-ppe",
                    output_dir: Path | str = ROOT / "data/ppe5") -> Path:
    from PIL import Image

    source, output = Path(data_dir).resolve(), Path(output_dir).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Исходный датасет и результат должны быть в отдельных папках.")
    if not (source / "images/train").is_dir():
        raise FileNotFoundError(f"Нет {source / 'images/train'}. Сначала запустите models.py --download --prepare-only.")
    for metadata in (source / "data.yaml", source / "construction-ppe.yaml"):
        if metadata.exists():
            import yaml
            names = yaml.safe_load(metadata.read_text(encoding="utf-8"))["names"]
            names = [names[i] for i in range(len(names))] if isinstance(names, dict) else names
            if tuple(names) != SOURCE_NAMES:
                raise ValueError("Порядок классов исходного датасета отличается от Construction-PPE.")
    output.mkdir(parents=True, exist_ok=True)
    yaml_path = output / "dataset.yaml"
    yaml_path.unlink(missing_ok=True)
    report = {"source": str(source), "url": DATASET_URL, "names": CLASS_NAMES,
              "mapping": CLASS_MAP, "splits": {}, "duplicates_removed": []}
    seen: dict[str, str] = {}
    for split in ("train", "val", "test"):
        image_dir, label_dir = source / "images" / split, source / "labels" / split
        images = sorted(p for p in image_dir.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"})
        if not images:
            raise ValueError(f"Пустой или отсутствующий split: {split}")
        target_images, target_labels = output / "images" / split, output / "labels" / split
        target_images.mkdir(parents=True, exist_ok=True)
        target_labels.mkdir(parents=True, exist_ok=True)
        manifest, counts = [], Counter({name: 0 for name in CLASS_NAMES})
        background = 0
        for image_path in images:
            relative = image_path.relative_to(image_dir)
            label_path = (label_dir / relative).with_suffix(".txt")
            if not label_path.is_file():
                raise FileNotFoundError(f"Нет разметки (для фона нужен пустой txt): {label_path}")
            with Image.open(image_path) as image:
                image.verify()
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            if digest in seen:
                report["duplicates_removed"].append({"image": str(image_path), "kept_split": seen[digest]})
                continue
            seen[digest] = split
            lines = convert_label(label_path.read_text(encoding="utf-8-sig"), str(label_path))
            stem = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:20]
            destination = target_images / (stem + image_path.suffix.lower())
            if not destination.exists():
                try:
                    os.link(image_path, destination)
                except OSError:
                    shutil.copy2(image_path, destination)
            elif not image_path.samefile(destination):
                shutil.copy2(image_path, destination)
            (target_labels / (stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")
            manifest.append("./" + destination.relative_to(output).as_posix())
            counts.update(CLASS_NAMES[int(line.split()[0])] for line in lines)
            background += not lines
        if not manifest or (split in {"train", "val"} and any(v == 0 for v in counts.values())):
            raise ValueError(f"В {split} отсутствуют изображения или один из пяти классов: {dict(counts)}")
        (output / f"{split}.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
        report["splits"][split] = {"images": len(manifest), "background": background, "class_counts": dict(counts)}
        print(f"{split}: {len(manifest)} изображений; {dict(counts)}", flush=True)
    (output / "preparation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    yaml_path.write_text("path: " + json.dumps(output.as_posix(), ensure_ascii=False) +
                        "\ntrain: train.txt\nval: val.txt\ntest: test.txt\nnames:\n" +
                        "".join(f"  {i}: {json.dumps(name)}\n" for i, name in enumerate(CLASS_NAMES)), encoding="utf-8")
    print(f"Готово: {yaml_path}", flush=True)
    return yaml_path


def training_options(profile: str, **overrides) -> dict:
    options = dict(PROFILES[profile], epochs=120, patience=25, optimizer="AdamW", lr0=0.001,
                   lrf=0.01, weight_decay=0.0005, warmup_epochs=3.0, cos_lr=True,
                   mosaic=0.5, close_mosaic=15, mixup=0.0, degrees=5.0, translate=0.1,
                   scale=0.3, fliplr=0.5, flipud=0.0, hsv_h=0.015, hsv_s=0.4, hsv_v=0.3,
                   amp=True, cache=False, seed=42, deterministic=True, plots=True, val=True, save=True)
    options.update({key: value for key, value in overrides.items() if value is not None})
    return options


def train_model(dataset_yaml: Path, profile: str = "rtx3050", *, resume: Path | None = None,
                evaluate_test: bool = False, device: str = "0", **overrides) -> Path:
    import torch
    from ultralytics import YOLO

    if device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("CUDA недоступна. Установите CUDA-сборку PyTorch или укажите --profile cpu --device cpu.")
    options = training_options(profile, **overrides)
    model = options.pop("model")
    if (ROOT / "weights" / model).is_file():
        model = str(ROOT / "weights" / model)
    if resume is not None and not resume.is_file():
        raise FileNotFoundError(f"Нет checkpoint: {resume}")
    detector = YOLO(str(resume) if resume else model)
    if resume:
        if tuple(detector.names[i] for i in range(len(detector.names))) != CLASS_NAMES:
            raise ValueError("Checkpoint не соответствует пяти классам PPE. Начните новое обучение.")
        detector.train(resume=True, data=str(dataset_yaml), device=device, workers=options["workers"])
    else:
        detector.train(data=str(dataset_yaml), device=device, project=str(ROOT / "runs/detect"), name="ppe5", **options)
    best = Path(detector.trainer.best)
    if not best.is_file():
        raise RuntimeError("Обучение не создало best.pt.")
    # Legacy YOLO training must never overwrite the deployed RF-DETR model.
    destination = ROOT / ".artifacts/legacy-training/ppe5_yolo.pt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    shutil.copy2(best, temporary)
    os.replace(temporary, destination)
    if evaluate_test:
        metrics = YOLO(str(best)).val(data=str(dataset_yaml), split="test", imgsz=options["imgsz"],
                                     batch=max(1, options["batch"]), device=device, workers=options["workers"])
        print(f"Test mAP50={metrics.box.map50:.4f}; mAP50-95={metrics.box.map:.4f}")
    print(f"Экспериментальные веса YOLO (не используются приложением): {destination}", flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Пять классов СИЗ: подготовка и обучение")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--archive", type=Path, help="Ранее скачанный construction-ppe.zip")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/construction-ppe")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/ppe5")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--profile", choices=PROFILES, default="rtx3050")
    parser.add_argument("--model")
    for key in ("epochs", "imgsz", "batch", "workers", "patience"):
        parser.add_argument("--" + key, type=int)
    parser.add_argument("--device")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--evaluate-test", action="store_true")
    args = parser.parse_args()
    for key in ("epochs", "imgsz", "batch", "workers", "patience"):
        value = getattr(args, key)
        minimum = 0 if key in {"workers", "patience"} else 1
        if value is not None and value < minimum and not (key == "batch" and value == -1):
            parser.error(f"Недопустимое значение --{key}")
    try:
        if not args.data_dir.exists() and (args.download or args.archive):
            if args.data_dir.name != "construction-ppe":
                raise ValueError("Для архива папка --data-dir должна называться construction-ppe.")
            archive = args.archive or ROOT / "data/construction-ppe.zip"
            if not archive.is_file():
                if args.archive:
                    raise FileNotFoundError(archive)
                download_dataset(archive)
            extract_dataset(archive, args.data_dir.parent)
        dataset = prepare_dataset(args.data_dir, args.output_dir)
        if not args.prepare_only:
            train_model(dataset, args.profile, resume=args.resume, evaluate_test=args.evaluate_test,
                        device=args.device or ("cpu" if args.profile == "cpu" else "0"),
                        **{key: getattr(args, key) for key in ("model", "epochs", "imgsz", "batch", "workers", "patience")})
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, ImportError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
