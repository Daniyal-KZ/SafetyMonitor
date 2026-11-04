"""Local RF-DETR versions with checkpoint-bound class maps."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .classes import canonical_name, RULE_CLASSES

ROOT = Path(__file__).resolve().parent.parent
MODEL_RELATIVE_PATH = "weights/weights.pt"
MODEL_PATH = ROOT / MODEL_RELATIVE_PATH
LABELS_PATH = MODEL_PATH.with_suffix(".labels.json")
MODEL_VERSIONS = {
    "model-v1": "weights/model-v1.pt",
    "model-v2": "weights/model-v2.pt",
}


def model_name(path: Path) -> str:
    if path.resolve() == MODEL_PATH.resolve():
        return "model-v1"
    return next((name for name, relative in MODEL_VERSIONS.items()
                 if path.resolve() == (ROOT / relative).resolve()), path.stem)


def read_labels(path: Path, digest: str, slots: int) -> dict[int, str | None]:
    """Only use explicit, checkpoint-bound IDs; never guess a dataset's order.

    Null means an explicitly ignored category. An empty map means unverified.
    Old Roboflow exports can use 1-based IDs, including the last logit slot.
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("sha256") != digest:
        raise ValueError("Карта классов относится к другим весам.")
    labels = data.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("В карте классов нужен объект labels: ID → название.")
    result = {}
    for key, value in labels.items():
        if not key.isdecimal() or not 0 <= int(key) < slots:
            raise ValueError("ID в карте классов выходит за пределы модели.")
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Название класса должно быть строкой или null.")
        result[int(key)] = value.strip() if value is not None else None
    return result


class RFDETRDetector:
    def __init__(self, path: Path = MODEL_PATH) -> None:
        import torch
        from rfdetr import RFDETRSmall

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Не найден файл модели: {path.name}")
        self.digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        state = checkpoint.get("model", {})
        if not isinstance(state, dict) or "class_embed.weight" not in state:
            raise ValueError("weights.pt должен содержать веса RF-DETR Small.")
        self.slots = int(state["class_embed.weight"].shape[0])
        if self.slots < 2:
            raise ValueError("В weights.pt отсутствует корректный классификатор.")
        has_model_config = isinstance(checkpoint.get("model_config"), dict)
        del checkpoint, state
        self.labels = read_labels(path.with_suffix(".labels.json"), self.digest, self.slots)
        self.labels_verified = set(self.labels) == set(range(self.slots))
        known = {canonical_name(name) for name in self.labels.values() if name}
        self.supported_rules = tuple(
            rule for rule, name in RULE_CLASSES.items()
            if self.labels_verified and "PERSON" in known and name in known
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if has_model_config:
            self.model = RFDETRSmall.from_checkpoint(str(path), device=self.device)
        else:
            self.model = RFDETRSmall(pretrain_weights=str(path),
                                     num_classes=self.slots - 1, device=self.device)
        self.resolution = self.model.model_config.resolution

    def predict(self, frame):
        import cv2
        prediction = self.model.predict(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                                        threshold=0.25, include_source_image=False)
        # This checkpoint can return duplicate boxes (observed for helmets).
        # Suppress only same-class overlaps; a helmet inside PERSON must remain.
        return prediction.with_nms(threshold=0.5, class_agnostic=False)

    def label(self, class_id: int) -> str | None:
        if class_id not in self.labels:
            return f"CLASS {class_id}"
        name = self.labels[class_id]
        return (canonical_name(name) or name) if name else None
