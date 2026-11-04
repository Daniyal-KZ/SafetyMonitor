"""Real tiny images exercise remapping, split integrity and archive validation."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from models import CLASS_MAP, convert_label, extract_dataset, prepare_dataset, training_options
from safety_monitor.classes import CLASS_NAMES


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "construction-ppe"
        self.output = self.root / "ppe5"
        for index, split in enumerate(("train", "val", "test")):
            (self.source / "images" / split).mkdir(parents=True)
            (self.source / "labels" / split).mkdir(parents=True)
            Image.new("RGB", (64, 64), (index*60, 20, 30)).save(self.source / "images" / split / "one.jpg")
            (self.source / "labels" / split / "one.txt").write_text(
                "\n".join(f"{key} 0.5 0.5 0.2 0.2" for key in range(11)))

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return prepare_dataset(self.source, self.output)

    def test_exact_five_classes_and_source_unchanged(self):
        original = (self.source / "labels/train/one.txt").read_bytes()
        dataset = self.prepare()
        self.assertTrue(dataset.is_file())
        report = json.loads((self.output / "preparation_report.json").read_text())
        self.assertEqual(report["names"], list(CLASS_NAMES))
        self.assertTrue(all(value == 1 for value in report["splits"]["train"]["class_counts"].values()))
        self.assertEqual((self.source / "labels/train/one.txt").read_bytes(), original)
        labels = next((self.output / "labels/train").glob("*.txt")).read_text().splitlines()
        self.assertEqual({int(line.split()[0]) for line in labels}, set(range(5)))
        self.prepare()  # repeat preparation remains valid

    def test_missing_label_fails_closed(self):
        self.prepare()
        (self.source / "labels/train/one.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertFalse((self.output / "dataset.yaml").exists())

    def test_no_class_and_split_leakage_are_rejected(self):
        (self.source / "images/val/one.jpg").write_bytes((self.source / "images/train/one.jpg").read_bytes())
        with self.assertRaises(ValueError):
            self.prepare()

    def test_invalid_and_ignored_labels(self):
        for text in ("0 nan .5 .1 .1", "0 .5 .5 -1 .2", "99 .5 .5 .1 .1", "0 .5 .5", "1.5 .5 .5 .1 .1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                convert_label(text)
        self.assertEqual(convert_label("7 .5 .5 .1 .1\n3 .5 .5 .1 .1"), [])
        for old, new in CLASS_MAP.items():
            self.assertTrue(convert_label(f"{old} .5 .5 .1 .1")[0].startswith(str(new) + " "))

    def test_archive_traversal_and_flat_official_layout(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.txt", "bad")
        with self.assertRaises(ValueError):
            extract_dataset(archive, self.root / "unpacked")
        self.assertFalse((self.root / "escape.txt").exists())
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("images/train/one.jpg", b"image")
        target = extract_dataset(archive, self.root / "unpacked")
        self.assertTrue((target / "images/train/one.jpg").exists())
        with self.assertRaises(FileExistsError):
            extract_dataset(archive, self.root / "unpacked")

    def test_training_options_do_not_override_explicit_values(self):
        options = training_options("rtx3050", imgsz=640, batch=1)
        self.assertEqual((options["imgsz"], options["batch"]), (640, 1))
        self.assertNotIn("time", options)
        self.assertEqual(options["optimizer"], "AdamW")
