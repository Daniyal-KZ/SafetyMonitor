"""Exercise tile rendering without a display or a real camera."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from safety_monitor.detection import AnalysisResult, Detection
from safety_monitor.models import Camera
from safety_monitor.video import VideoPlayerFrame


class RenderChecks(unittest.TestCase):
    def test_boxes_use_analyzed_frame_instead_of_newer_live_frame(self):
        live = np.full((100, 200, 3), 180, dtype=np.uint8)
        analyzed = np.full((100, 200, 3), 40, dtype=np.uint8)
        result = AnalysisResult(20, 19, analyzed, (Detection('VEST', .8, (10, 20, 50, 90)),))
        tile = SimpleNamespace(
            _cached_frame=live, analysis=SimpleNamespace(display_result=lambda key: result),
            camera=Camera('Test'), _analysis_caption='', analysis_label=Mock(),
            _get_widget_scaling=lambda: 1,
            video_area=SimpleNamespace(winfo_width=lambda: 204, winfo_height=lambda: 104),
            _last_packet_sequence=123, _last_render_key=None, video_label=Mock(),
        )
        with patch('safety_monitor.video.draw_detections', return_value=analyzed) as draw:
            with patch('safety_monitor.video.ctk.CTkImage', return_value='test-image'):
                VideoPlayerFrame._render_current_frame(tile)
        self.assertIs(draw.call_args.args[0], analyzed)
        self.assertEqual(tile._last_render_key[0], 19)
        self.assertEqual(tile._image, 'test-image')
        self.assertIn('VEST', tile._analysis_caption)
        self.assertIn('контроль СИЗ выключен', tile._analysis_caption)
