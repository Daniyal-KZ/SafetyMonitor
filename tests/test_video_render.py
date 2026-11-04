"""Exercise cached-frame rendering without a display or a real camera."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from safety_monitor.video import VideoPlayerFrame


class RenderChecks(unittest.TestCase):
    def tile(self, frame):
        return SimpleNamespace(
            _cached_frame=frame, _analysis_sequence=19, _analysis_context=(1, True, 0),
            _get_widget_scaling=lambda: 1,
            video_area=SimpleNamespace(winfo_width=lambda: 204, winfo_height=lambda: 104,
                                       winfo_viewable=lambda: True),
            _last_packet_sequence=123, _last_render_key=None, video_label=Mock(),
        )

    def test_render_uses_annotated_cached_frame_and_preserves_aspect_ratio(self):
        analyzed = np.full((100, 200, 3), 40, dtype=np.uint8)
        tile = self.tile(analyzed)
        with patch('safety_monitor.video.ctk.CTkImage', return_value='test-image') as image:
            VideoPlayerFrame._render_current_frame(tile)
        self.assertEqual(image.call_args.kwargs['size'], (200, 100))
        self.assertTrue(np.all(np.asarray(image.call_args.kwargs['light_image']) == 40))
        self.assertEqual(tile._last_render_key[1], 19)
        self.assertEqual(tile._image, 'test-image')

    def test_unchanged_frame_is_not_redrawn_and_hidden_tile_does_not_render(self):
        tile = self.tile(np.zeros((100, 200, 3), dtype=np.uint8))
        with patch('safety_monitor.video.ctk.CTkImage', return_value='test-image') as image:
            VideoPlayerFrame._render_current_frame(tile)
            VideoPlayerFrame._render_current_frame(tile)
            image.assert_called_once()
            tile.video_area.winfo_viewable = lambda: False
            tile._cached_frame = np.ones((100, 200, 3), dtype=np.uint8)
            VideoPlayerFrame._render_current_frame(tile)
            image.assert_called_once()
