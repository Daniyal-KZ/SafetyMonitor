"""Merged model settings, per-camera rules and journal delivery boundaries."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from safety_monitor.detection import Analysis
from safety_monitor.models import Camera, Rules, WorkspaceStore
from safety_monitor.video import VideoPlayerFrame


class MergedSettingsTests(unittest.TestCase):
    def test_existing_database_document_defaults_to_local_model(self):
        _, settings = WorkspaceStore._decode(dict(version=1, settings={}, workspaces=[]))
        self.assertEqual(settings['model_path'], 'best.pt')

    def test_absent_checkpoint_does_not_block_loading_database_config(self):
        _, settings = WorkspaceStore._decode(dict(version=1,
            settings={'model_path': 'weights/model-v2.pt'}, workspaces=[]))
        self.assertEqual(settings['model_path'], 'weights/model-v2.pt')

    def test_model_setting_requires_local_checkpoint_path(self):
        for path in (None, True, '', 'https://example.com/model.pt', 'model.json', 'bad\x00.pt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                WorkspaceStore._validate_settings({'model_path': path})


class TileAnalysisTests(unittest.TestCase):
    def tile(self):
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        packet = SimpleNamespace(generation=1, sequence=5, frame=frame,
                                 state='streaming', status='ok')
        analysis = Mock()
        analysis.snapshot.return_value = None
        return SimpleNamespace(
            _closed=False, _after_id=None, _worker=SimpleNamespace(snapshot=lambda: packet),
            _generation=1, _last_packet_sequence=-1, _cached_frame=None,
            camera=Camera('Camera', source=0, rules=Rules(helmet=False, vest=True)),
            workspace_name='Factory', model_path='weights/model-v2.pt',
            _analysis=analysis, _analysis_context=None, _analysis_epoch=0,
            _analysis_sequence=-1, _last_analysis_submit=0, _last_analysis_packet=None,
            _last_render_key=None, _set_status=Mock(), _clear_frame=Mock(),
            _render_current_frame=Mock(), _schedule_tick=Mock(), on_analysis=Mock(),
        )

    def test_vest_only_camera_submits_its_selected_model_and_rules(self):
        tile = self.tile()
        VideoPlayerFrame._tick(tile)
        call = tile._analysis.submit.call_args
        self.assertEqual(call.kwargs, {'required': ('vest',), 'model_path': 'weights/model-v2.pt'})

    def test_annotated_frame_is_displayed_and_recorded_once(self):
        tile = self.tile()
        VideoPlayerFrame._tick(tile)
        key = tile._analysis.submit.call_args.args[0]
        annotated = np.full((24, 32, 3), 99, dtype=np.uint8)
        result = Analysis(key, annotated, 'confirmed', 1, 'no_vest')
        tile._analysis.snapshot.return_value = result
        VideoPlayerFrame._tick(tile)
        VideoPlayerFrame._tick(tile)
        self.assertIs(tile._cached_frame, annotated)
        tile.on_analysis.assert_called_once_with(tile.camera, 'Factory', result)

    def test_changed_rules_or_model_discard_old_inference(self):
        for change in ('rules', 'model'):
            with self.subTest(change=change):
                tile = self.tile()
                VideoPlayerFrame._tick(tile)
                old_key = tile._analysis.submit.call_args.args[0]
                tile._analysis.snapshot.return_value = Analysis(
                    old_key, np.ones((24, 32, 3), dtype=np.uint8), 'stale', 1, 'no_vest')
                if change == 'rules':
                    tile.camera.rules.vest = False
                    tile.camera.rules.gloves = True
                else:
                    tile.model_path = 'weights/model-v1.pt'
                VideoPlayerFrame._tick(tile)
                tile.on_analysis.assert_not_called()
                self.assertNotEqual(tile._analysis_context, old_key[:-1])


if __name__ == '__main__':
    unittest.main()
