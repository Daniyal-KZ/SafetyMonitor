"""Opt-in local checkpoint/label diagnostic; no network or camera access."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import time

import cv2
import numpy as np

from safety_monitor.ppe import iou
from safety_monitor.inference import RFDETRDetector
from safety_monitor.classes import CLASS_NAMES


def main():
    model = RFDETRDetector()
    root = Path('.artifacts/SafetyMonitor_/data/ppe4/test')
    dataset = json.loads((root / '_annotations.coco.json').read_text(encoding='utf-8'))
    annotations = defaultdict(list)
    for row in dataset['annotations']:
        annotations[row['image_id']].append(row)
    categories = {row['id']: row['name'] for row in dataset['categories']}
    selected = []
    counts = Counter()
    for row in dataset['images']:
        classes = {ann['category_id'] for ann in annotations[row['id']]}
        if any(counts[k] < 8 for k in classes):
            selected.append(row)
            counts.update(classes)
    matches = defaultdict(Counter)
    times = []
    for row in selected[:40]:
        frame = cv2.imdecode(np.fromfile(root / row['file_name'], dtype=np.uint8), cv2.IMREAD_COLOR)
        start = time.monotonic()
        pred = model.predict(frame)
        times.append(time.monotonic() - start)
        for box, confidence, class_id in zip(pred.xyxy, pred.confidence, pred.class_id):
            candidates = []
            for ann in annotations[row['id']]:
                x, y, w, h = ann['bbox']
                candidates.append((iou(box, (x, y, x+w, y+h)), ann['category_id']))
            overlap, label = max(candidates, default=(0, -1))
            if overlap > .5 and confidence > .5:
                matches[categories[label]][int(class_id)] += 1
    print(json.dumps({'images': len(times), 'iou_matches': dict(matches),
                      'median_seconds': float(np.median(times)), 'slots': model.slots}, indent=2))
    matches = defaultdict(Counter)
    raw_counts = Counter()
    output = Path('.artifacts/weights-review')
    output.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(sorted(Path('data/ppe5/images/test').glob('*.jpg'))[:60]):
        frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        height, width = frame.shape[:2]
        truth = []
        for line in (Path('data/ppe5/labels/test') / (path.stem+'.txt')).read_text().splitlines():
            label, cx, cy, w, h = map(float, line.split())
            truth.append((int(label), ((cx-w/2)*width, (cy-h/2)*height,
                                       (cx+w/2)*width, (cy+h/2)*height)))
        pred = model.predict(frame)
        for box, confidence, class_id in zip(pred.xyxy, pred.confidence, pred.class_id):
            raw_counts[int(class_id)] += 1
            overlap, label = max(((iou(box, gt), cat) for cat, gt in truth), default=(0, -1))
            if overlap > .5 and confidence > .5:
                matches[CLASS_NAMES[label]][int(class_id)] += 1
            if index < 3:
                x1,y1,x2,y2 = map(int,box)
                cv2.rectangle(frame,(x1,y1),(x2,y2),(0,255,0),2)
                cv2.putText(frame,f'ID {int(class_id)} {confidence:.2f}',(x1,max(16,y1-4)),
                            cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
        if index < 3:
            cv2.imencode('.jpg',frame)[1].tofile(output/f'raw-{index}.jpg')
    report = {'images': 60, 'iou_matches': dict(matches), 'raw_counts': dict(raw_counts)}
    (output/'class-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
