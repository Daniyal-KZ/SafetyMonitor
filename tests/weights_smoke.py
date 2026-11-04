"""Opt-in real weights -> inference worker -> annotated evidence check."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np

from safety_monitor.detection import AnalysisService, EVENT_TIMEZONE, draw_detections
from safety_monitor.events import EventStore
from safety_monitor.models import Camera


def main():
    source = next(iter(sorted(Path('data/ppe5/images/test').glob('*.jpg'))))
    frame = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    output = Path('.artifacts/weights-review')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        service = AnalysisService(EventStore(directory))
        try:
            deadline = time.monotonic()+60
            while service._model is None and time.monotonic() < deadline:
                if 'загрузка' not in service.status and 'ожидание' not in service.status:
                    raise AssertionError(service.status)
                time.sleep(.1)
            assert service._model is not None, service.status
            assert service._model.labels_verified
            camera = Camera('Local model smoke')
            captured = time.monotonic()
            service.submit(camera, 'Test only', frame, captured, datetime.now(EVENT_TIMEZONE).isoformat())
            deadline = time.monotonic()+20
            result = None
            while time.monotonic() < deadline:
                result = service.display_result(camera.id)
                if result is not None:
                    break
                time.sleep(.05)
            assert result is not None, service.status
            assert result.frame is frame
            assert {'HELMET', 'VEST'} <= {item.name for item in result.detections}, result.detections
            annotated = draw_detections(result.frame, result.detections)
            assert np.any(annotated != frame), 'No boxes drawn'
            cv2.imencode('.jpg', annotated)[1].tofile(output/'verified-detections.jpg')
            report = dict(status=service.status, source=str(source),
                          labels=service._model.labels,
                          detections=[dict(name=d.name, confidence=d.confidence, box=d.box)
                                      for d in result.detections],
                          elapsed_seconds=result.completed-captured)
            service.reset_camera(camera.id)
            assert service.display_result(camera.id) is None
            assert service.events.query()[1] == 0
            (output/'worker-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            service.close(timeout=30)


if __name__ == '__main__':
    main()
