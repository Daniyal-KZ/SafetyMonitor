import unittest

import numpy as np

from safety_monitor.detection import Track
from safety_monitor.incidents import IncidentCoordinator, appearance


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = IncidentCoordinator()
        self.frame = np.zeros((500, 700, 3), dtype=np.uint8)
        self.frame[40:250, 100:280] = (30, 50, 220)
        self.frame[250:460, 100:280] = (180, 40, 20)
        self.track = Track(1, (100, 40, 280, 460), 0)

    def observe(self, now, camera='a', missing=('helmet',), clear=(), track=None, workspace='zone', frame=None):
        self.coordinator.begin_frame(now)
        return self.coordinator.observe(camera, 0, workspace, track or self.track, missing, clear,
                                        self.frame if frame is None else frame, now)

    def test_waits_30_seconds_then_logs_once_for_long_stay(self):
        for second in range(30):
            self.assertEqual(self.observe(second)[1], ())
        person, missing = self.observe(30)
        self.assertEqual(missing, ('helmet',))
        self.coordinator.mark_logged(person, missing)
        for second in range(31, 300):
            self.assertEqual(self.observe(second)[1], ())

    def test_two_cameras_share_timer_without_double_speed_or_duplicates(self):
        for second in range(30):
            one, missing = self.observe(second, 'a')
            two, missing2 = self.observe(second, 'b')
            self.assertEqual(one.id, two.id)
            self.assertEqual(missing + missing2, ())
        one, missing = self.observe(30, 'b')
        self.assertEqual(missing, ('helmet',))
        self.coordinator.mark_logged(one, missing)
        self.assertEqual(self.observe(30, 'a')[1], ())

    def test_camera_handoff_carries_timer(self):
        for second in range(20):
            first, _ = self.observe(second, 'a')
        for second in range(20, 30):
            other, missing = self.observe(second, 'b')
            self.assertEqual(other.id, first.id)
            self.assertEqual(missing, ())
        self.assertEqual(self.observe(30, 'b')[1], ('helmet',))

    def test_putting_on_ppe_cancels_pending_event(self):
        for second in range(29):
            self.observe(second)
        self.assertEqual(self.observe(29, missing=(), clear=('helmet',))[1], ())
        for second in range(30, 60):
            self.assertEqual(self.observe(second)[1], ())
        self.assertEqual(self.observe(60)[1], ('helmet',))

    def test_recovery_rearms_only_after_five_seconds_of_visible_ppe(self):
        for second in range(31):
            person, missing = self.observe(second)
        self.coordinator.mark_logged(person, missing)
        for second in range(31, 36):
            self.observe(second, missing=(), clear=('helmet',))
        self.assertTrue(person.rules['helmet'].logged)
        self.observe(36, missing=(), clear=('helmet',))
        self.assertFalse(person.rules['helmet'].logged)
        for second in range(37, 67):
            self.assertEqual(self.observe(second)[1], ())
        self.assertEqual(self.observe(67)[1], ('helmet',))

    def test_long_gap_resets_timer_but_remembers_logged_violation(self):
        for second in range(20):
            self.observe(second)
        for second in range(40, 70):
            self.assertEqual(self.observe(second, 'b')[1], ())
        person, missing = self.observe(70, 'b')
        self.coordinator.mark_logged(person, missing)
        self.assertEqual(self.observe(120, 'a')[1], ())
        self.assertTrue(person.rules['helmet'].logged)

    def test_different_people_zones_and_ambiguous_clothing_do_not_merge(self):
        first, _ = self.observe(0)
        second_track = Track(2, self.track.box, 0)
        # Even identical clothing cannot merge two simultaneous tracks on one camera.
        second, _ = self.observe(0, track=second_track)
        self.assertNotEqual(first.id, second.id)
        ambiguous, _ = self.observe(1, 'b')
        self.assertNotIn(ambiguous.id, (first.id, second.id))
        elsewhere, _ = self.observe(1, 'c', workspace='other')
        self.assertNotEqual(elsewhere.id, ambiguous.id)

    def test_no_person_box_does_not_claim_cross_camera_identity(self):
        head = Track(1, (120, 40, 200, 100), 0, kind='NO HELMET')
        self.assertIsNone(appearance(self.frame, head))
        one, _ = self.observe(0, 'a', track=head)
        two, _ = self.observe(0, 'b', track=head)
        self.assertNotEqual(one.id, two.id)

    def test_storage_failure_can_retry_and_expired_identity_is_forgotten(self):
        for second in range(31):
            person, missing = self.observe(second)
        # The writer marks logged only after the photo and SQLite transaction succeed.
        self.assertEqual(self.observe(31)[1], ('helmet',))
        self.coordinator.mark_logged(person, missing)
        later, missing = self.observe(400)
        self.assertNotEqual(person.id, later.id)
        self.assertEqual(missing, ())
