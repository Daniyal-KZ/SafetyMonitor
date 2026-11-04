"""Opt-in real RF-DETR inference with the checkpoint-bound label map.

Usage: python -m tests.weights_smoke --image path/to/photo.jpg --model weights/model-v2.pt
No application records or configuration are changed.
"""
import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np

from safety_monitor.inference import MODEL_PATH, RFDETRDetector
from safety_monitor.ppe import BoxDetection, draw_detections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=MODEL_PATH)
    parser.add_argument('--output', type=Path, default=Path('.artifacts/weights-review'))
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error('Image file does not exist.')
    if not args.model.is_file():
        parser.error('Model weights are local files and must be supplied separately.')
    frame = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        parser.error('Cannot decode the image.')
    started = time.monotonic()
    model = RFDETRDetector(args.model)
    if not model.labels_verified:
        raise AssertionError('Provide a complete, matching .labels.json map beside the model.')
    prediction = model.predict(frame)
    detections = []
    for box, confidence, class_id in zip(prediction.xyxy, prediction.confidence,
                                         prediction.class_id, strict=True):
        name = model.label(int(class_id))
        if name is not None:
            detections.append(BoxDetection(name, float(confidence), tuple(map(float, box))))
    annotated = draw_detections(frame, detections)
    args.output.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode('.jpg', annotated)
    if not ok:
        raise RuntimeError('Failed to encode the annotated frame.')
    encoded.tofile(args.output / 'verified-detections.jpg')
    report = dict(model=str(args.model), source=str(args.image), model_sha256=model.digest,
                  device=model.device, labels=model.labels,
                  detections=[dict(name=d.name, confidence=d.confidence, box=d.box)
                              for d in detections], elapsed_seconds=time.monotonic()-started)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (args.output / 'inference-smoke.json').write_text(payload, encoding='utf-8')
    print(payload)


if __name__ == '__main__':
    main()
