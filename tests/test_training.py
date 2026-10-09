"""Проверки COCO -> YOLO без установки PyTorch и запуска обучения."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from models import prepare_dataset


class DatasetPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "data"
        self.output = self.source / "yolo"
        self.train = {
            "categories": [
                {"id": 0, "name": "SAFETY"}, {"id": 10, "name": "Helmet"},
                {"id": 20, "name": "No-Helmet"}, {"id": 40, "name": "Person"},
            ],
            "images": [
                {"id": 12, "file_name": "sample.jpg", "width": 100, "height": 100},
                {"id": 13, "file_name": "missing.jpg", "width": 100, "height": 100},
            ],
            "annotations": [
                {"image_id": 12, "category_id": 10, "bbox": [-10, 10, 30, 20]},
                {"image_id": 12, "category_id": 20, "bbox": [50, 50, 10, 10]},
                {"image_id": 12, "category_id": 40, "bbox": [0, 0, 100, 100]},
                {"image_id": 12, "category_id": 10, "bbox": [0, 0, 0, 20]},
            ],
        }
        self.valid = {
            "categories": [{"id": 7, "name": "Helmet"}],
            "images": [{"id": 0, "file_name": "sample.jpg", "width": 100, "height": 100}],
            "annotations": [{"image_id": 0, "category_id": 7, "bbox": [0, 0, 10, 20]}],
        }
        for split, coco in (("train", self.train), ("valid", self.valid)):
            folder = self.source / split
            folder.mkdir(parents=True)
            # Конвертеру нужны размеры из COCO и существующий файл, не декодер JPEG.
            (folder / "sample.jpg").write_bytes(b"source image is unchanged")
            self.write_coco(split, coco)

    def write_coco(self, split: str, coco: dict) -> None:
        (self.source / split / "_annotations.coco.json").write_text(
            json.dumps(coco), encoding="utf-8"
        )

    def prepare(self, **kwargs) -> Path:
        with contextlib.redirect_stdout(io.StringIO()):
            return prepare_dataset(self.source, self.output, **kwargs)

    def test_mapping_clipping_and_missing_files(self) -> None:
        original = (self.source / "train" / "_annotations.coco.json").read_bytes()
        yaml_path = self.prepare()
        labels = (self.output / "labels" / "train" / "00000012.txt").read_text().splitlines()
        self.assertEqual(labels, [
            "0 0.10000000 0.20000000 0.20000000 0.20000000",
            "1 0.55000000 0.55000000 0.10000000 0.10000000",
            "2 0.50000000 0.50000000 1.00000000 1.00000000",
        ])
        self.assertTrue((self.output / "labels" / "val" / "00000000.txt").read_text().startswith("0 "))
        self.assertEqual((self.output / "train.txt").read_text(), "./images/train/00000012.jpg\n")
        self.assertNotIn("SAFETY", yaml_path.read_text())
        self.assertNotIn("test:", yaml_path.read_text())
        report = json.loads((self.output / "preparation_report.json").read_text())
        train = report["splits"]["train"]
        self.assertEqual(train["missing_images"], ["missing.jpg"])
        self.assertEqual((train["boxes"], train["clipped_boxes"], train["invalid_boxes"]), (3, 1, 1))
        self.assertEqual((self.source / "train" / "_annotations.coco.json").read_bytes(), original)
        self.assertEqual((self.source / "train" / "sample.jpg").read_bytes(), b"source image is unchanged")

    def test_strict_mode_rejects_missing_image(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.prepare(strict=True)

    def test_unknown_annotation_image_is_rejected(self) -> None:
        self.train["annotations"][0]["image_id"] = 999
        self.write_coco("train", self.train)
        with self.assertRaisesRegex(ValueError, "неизвестное изображение"):
            self.prepare()

    def test_output_cannot_overlap_source_split(self) -> None:
        with self.assertRaisesRegex(ValueError, "отделена"):
            prepare_dataset(self.source, self.source / "train" / "prepared")

    def test_background_images_and_rerun_manifest(self) -> None:
        self.train["images"].append(
            {"id": 99, "file_name": "background.jpg", "width": 100, "height": 100}
        )
        (self.source / "train" / "background.jpg").write_bytes(b"background")
        self.write_coco("train", self.train)
        self.prepare()
        self.assertEqual((self.output / "labels" / "train" / "00000099.txt").read_text(), "")
        self.train["images"].pop()
        self.write_coco("train", self.train)
        self.prepare()
        self.assertNotIn("00000099", (self.output / "train.txt").read_text())


if __name__ == "__main__":
    unittest.main()
