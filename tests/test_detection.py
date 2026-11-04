from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from safety_monitor.classes import CLASS_NAMES
from safety_monitor.detection import (AnalysisService, AnalysisResult, Detection, EVENT_TIMEZONE, FrameJob,
                                       ViolationTracker, missing_equipment)
from safety_monitor.inference import RFDETRDetector, read_labels, MODEL_RELATIVE_PATH
from safety_monitor.models import WorkspaceStore
from safety_monitor.events import EventStore


PERSON = Detection("PERSON", .95, (100, 40, 280, 460))


class PPEChecks(unittest.TestCase):
    def test_direct_violations_without_person_and_disabled_rules(self):
        negatives = [Detection('NO ' + name, .9, (20, 20, 80, 80)) for name in
                     ('HELMET', 'VEST', 'GLOVES', 'PPE GLASSES')]
        rules = ('helmet', 'vest', 'gloves', 'glasses')
        observations = missing_equipment(negatives, rules, (500, 700, 3))
        self.assertEqual([missing for _, missing in observations], [(rule,) for rule in rules])
        self.assertEqual(missing_equipment(negatives, (), (500, 700, 3)), [])

    def test_direct_violation_on_cropped_person_and_conflicting_label(self):
        person = Detection('PERSON', .9, (0, 0, 300, 500))
        negative = Detection('NO HELMET', .9, (120, 20, 200, 90))
        self.assertEqual(missing_equipment([person, negative], ('helmet',), (500, 700, 3)),
                         [(person, ('helmet',))])
        helmet = Detection('HELMET', .9, negative.box)
        self.assertEqual(missing_equipment([negative, helmet], ('helmet',), (500, 700, 3)), [])
        low = Detection('NO HELMET', .3, negative.box)
        self.assertEqual(missing_equipment([low], ('helmet',), (500, 700, 3)), [])

    def test_ppe_belongs_to_person_not_whole_camera(self):
        other = Detection("PERSON", .93, (400, 40, 580, 460))
        helmet = Detection("HELMET", .9, (145, 45, 220, 100))
        observations = missing_equipment([PERSON, other, helmet], ("helmet",), (500, 700, 3))
        self.assertEqual(observations[0][1], ())
        self.assertEqual(observations[1][1], ("helmet",))

    def test_all_equipment_and_no_person(self):
        gear = [Detection(name, .9, box) for name, box in (
            ("HELMET", (140, 45, 230, 80)), ("VEST", (130, 150, 240, 250)),
            ("GLOVES", (120, 250, 150, 280)), ("PPE GLASSES", (160, 90, 220, 110)))]
        required = ("helmet", "vest", "gloves", "glasses")
        self.assertEqual(missing_equipment([PERSON, *gear], required, (500, 700, 3))[0][1], ())
        self.assertEqual(missing_equipment(gear, required, (500, 700, 3)), [])

    def test_small_cropped_and_overlapping_people_abstain(self):
        for person in (Detection("PERSON", .9, (100, 5, 150, 80)), Detection("PERSON", .9, (0, 20, 150, 450))):
            self.assertEqual(missing_equipment([person], ("helmet", "glasses"), (500, 700, 3))[0][1], ())
        other = Detection("PERSON", .9, (130, 40, 310, 460))
        self.assertTrue(all(not missing for _, missing in missing_equipment([PERSON, other], ("helmet",), (500, 700, 3))))

    def test_confirmation_recovery_cooldown_and_long_gaps(self):
        tracker = ViolationTracker(confirm_seconds=2, cooldown=60)
        observation = [(PERSON, ("helmet",))]
        for now in (0, .5, 1, 1.5):
            self.assertEqual(tracker.update(observation, now), [])
        track, missing = tracker.update(observation, 2)[0]
        tracker.mark_logged(track, missing, 2)
        self.assertEqual(tracker.update(observation, 2.5), [])
        tracker.update([(PERSON, ())], 3)
        self.assertEqual(track.since, {})
        self.assertEqual(tracker.update(observation, 3.5), [])
        self.assertEqual(tracker.update(observation, 10), [])
        self.assertNotIn(track.id, tracker.tracks)

    def test_disappearance_breaks_evidence(self):
        tracker = ViolationTracker(confirm_seconds=1, min_samples=2)
        tracker.update([(PERSON, ("helmet",))], 0)
        tracker.update([], .5)
        self.assertEqual(tracker.update([(PERSON, ("helmet",))], 1), [])


class EventChecks(unittest.TestCase):
    def test_two_cameras_save_one_event_only_after_30_seconds(self):
        with patch.object(AnalysisService, '_load'):
            service = AnalysisService(self.store)
            service.close()
        service._stop = service._reload = False
        self.frame[40:250, 100:280] = (30, 50, 220)
        self.frame[250:460, 100:280] = (180, 40, 20)
        prediction = SimpleNamespace(xyxy=np.array([PERSON.box]), confidence=np.array([.95]),
                                     class_id=np.array([2]))
        service._model = SimpleNamespace(predict=lambda frame: prediction, label=lambda key: 'PERSON',
                                         supported_rules=('helmet', 'vest'), resolution=512)
        base = time.monotonic()
        for second in range(95):
            for camera in ('a', 'b'):
                service._process(FrameJob(camera, camera, 'Цех', ('helmet', 'vest'), 0, self.frame,
                                         base+second, datetime.now(EVENT_TIMEZONE).isoformat()))
            if second == 29:
                self.assertEqual(self.store.query()[1], 0)
        rows, total = self.store.query()
        self.assertEqual(total, 1)
        self.assertEqual(set(json.loads(rows[0]['missing'])), {'helmet', 'vest'})
        self.assertEqual(json.loads(rows[0]['details'])['cameras'], ['a', 'b'])
        self.assertEqual(len(list(self.store.directory.rglob('*.jpg'))), 1)

    def test_direct_violation_creates_snapshot_with_caption_and_cooldown(self):
        with patch.object(AnalysisService, '_load'):
            service = AnalysisService(self.store)
            service.close()
        service._stop = service._reload = False
        prediction = SimpleNamespace(xyxy=np.array([(20, 20, 100, 100)]),
                                     confidence=np.array([.9]), class_id=np.array([5]))
        service._model = SimpleNamespace(predict=lambda frame: prediction,
                                         label=lambda key: 'NO HELMET',
                                         supported_rules=('helmet',), resolution=512)
        base = time.monotonic()
        for index in range(80):
            service._process(FrameJob('cam', 'Вход', 'Цех', ('helmet',), 0, self.frame,
                                     base+index*.5, datetime.now(EVENT_TIMEZONE).isoformat()))
        rows, total = self.store.query()
        self.assertEqual(total, 1)
        self.assertEqual(json.loads(rows[0]['missing']), ['helmet'])
        self.assertEqual(json.loads(rows[0]['details'])['evidence_class'], 'NO HELMET')
        with Image.open(self.store.snapshot_path(rows[0])) as image:
            self.assertEqual(image.width, 700)
            self.assertGreater(image.height, 500)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EventStore(Path(self.temp.name) / "журнал")
        self.frame = np.zeros((500, 700, 3), dtype=np.uint8)

    def add_event(self, **overrides):
        data = dict(occurred_at="2026-10-24T14:15:16+05:00", camera_id="cam-1", camera_name="Камера входа",
                    workspace="Цех", track_id=1, missing=("helmet",), frame=self.frame, details={"status": "requires_review"})
        self.store.add(**(data | overrides))

    def test_restart_photo_filters_pagination_and_export(self):
        self.add_event()
        self.add_event(camera_id="cam-2", camera_name="Выход", missing=("vest",))
        restored = EventStore(self.store.directory)
        rows, total = restored.query("камера", "helmet")
        self.assertEqual(total, 1)
        with Image.open(restored.snapshot_path(rows[0])) as image:
            self.assertEqual(image.size, (700, 500))
        self.assertEqual(restored.query(limit=1, offset=1)[1], 2)
        self.assertEqual(restored.query(rule="gloves")[1], 0)
        output = Path(self.temp.name) / "export.csv"
        restored.export_csv(output, rule="helmet")
        self.assertIn("Камера входа", output.read_text(encoding="utf-8-sig"))
        self.assertNotIn("Выход", output.read_text(encoding="utf-8-sig"))

    def test_failed_photo_does_not_create_row(self):
        with patch("safety_monitor.events.cv2.imencode", return_value=(False, None)):
            with self.assertRaises(OSError):
                self.add_event()
        self.assertEqual(self.store.query()[1], 0)

    def test_sql_failure_removes_unregistered_photo(self):
        import sqlite3
        with patch.object(self.store, "connection", side_effect=sqlite3.OperationalError("read only")):
            with self.assertRaises(sqlite3.OperationalError):
                self.add_event()
        self.assertEqual(list(self.store.directory.rglob("*.jpg")), [])
        self.assertEqual(self.store.query()[1], 0)

    def test_cropped_box_is_not_absence_evidence(self):
        observations = missing_equipment([Detection("PERSON", .9, (0, 0, 700, 500))], ("helmet",), self.frame.shape)
        self.assertEqual(observations[0][1], ())

    def test_inference_to_journal_and_camera_reset(self):
        with patch.object(AnalysisService, "_load"):
            service = AnalysisService(self.store)
            service.close()
        # Run the worker's processing function deterministically with detector output.
        service._stop = False
        service._reload = False
        prediction = SimpleNamespace(xyxy=np.array([PERSON.box]), confidence=np.array([.95]),
                                     class_id=np.array([3]))
        service._model = SimpleNamespace(predict=lambda frame: prediction, label=lambda key: "PERSON",
                                         supported_rules=("helmet",), resolution=512)
        base = time.monotonic()
        for index in range(65):
            job = FrameJob("cam-1", "Камера входа", "Цех", ("helmet",), 0, self.frame,
                           base+index*.5, datetime.now(EVENT_TIMEZONE).isoformat())
            service._process(job)
        rows, total = self.store.query()
        self.assertEqual(total, 1)
        self.assertEqual(json.loads(rows[0]["missing"]), ["helmet"])
        service.reset_camera("cam-1")
        service._process(job)
        self.assertEqual(self.store.query()[1], 1)

    def test_slow_result_is_shown_on_its_own_frame_until_expiry(self):
        with patch.object(AnalysisService, "_load"):
            service = AnalysisService(self.store)
            service.close()
        now = time.monotonic()
        service._results['cam'] = AnalysisResult(now, now-3, self.frame, (PERSON,))
        self.assertEqual(service.result('cam'), [PERSON])
        self.assertIs(service.display_result('cam').frame, self.frame)
        with patch('safety_monitor.detection.time.monotonic', return_value=now+3):
            self.assertIsNone(service.display_result('cam'))

    def test_reload_invalidates_inflight_camera_jobs(self):
        with patch.object(AnalysisService, "_load"):
            service = AnalysisService(self.store)
            service.close()
        service._tokens['cam'] = 2
        service.reload()
        self.assertEqual(service._tokens['cam'], 3)


class WeightLabels(unittest.TestCase):
    def test_model_version_survives_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            store = WorkspaceStore(path)
            for version in ('model-v1', 'model-v2'):
                relative = f'weights/{version}.pt'
                store.update_settings(model_path=relative)
                self.assertEqual(WorkspaceStore(path).settings['model_path'], relative)

    def test_old_config_cannot_select_another_model(self):
        self.assertEqual(WorkspaceStore._validate_settings({'model_path': 'best.pt'})['model_path'],
                         MODEL_RELATIVE_PATH)

    def test_id_five_is_kept_and_missing_map_does_not_invent_names(self):
        detector = RFDETRDetector.__new__(RFDETRDetector)
        detector.labels = {0: None, 1: 'GLOVES', 2: 'HELMET', 3: 'PERSON', 4: 'PPE GLASSES', 5: 'VEST'}
        self.assertEqual(detector.label(5), 'VEST')
        self.assertEqual(detector.label(3), 'PERSON')
        self.assertIsNone(detector.label(0))
        detector.labels = {}
        self.assertEqual(detector.label(5), 'CLASS 5')

    def test_label_map_is_bound_to_checkpoint_and_validates_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'weights.labels.json'
            self.assertEqual(read_labels(path, 'abc', 6), {})
            for data in ({'sha256': 'other', 'labels': {}},
                         {'sha256': 'abc', 'labels': {'6': 'VEST'}},
                         {'sha256': 'abc', 'labels': {'1': ''}}):
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_labels(path, 'abc', 6)
