from __future__ import annotations

from pathlib import Path
from time import monotonic
import tempfile
import unittest

import numpy as np

from lab_monitor.config import AppConfig
from lab_monitor.monitor import CameraMonitor
from lab_monitor.storage import EventStore
from lab_monitor.tracking import Track
from lab_monitor.vision import is_face_box_usable, is_person_box_usable


class VisionQualityTests(unittest.TestCase):
    def test_person_box_rejects_full_frame_duplicate_detection(self) -> None:
        frame_shape = (480, 640, 3)
        self.assertFalse(is_person_box_usable((0, 0, 640, 480), frame_shape))
        self.assertTrue(is_person_box_usable((230, 120, 190, 300), frame_shape))

    def test_face_box_rejects_small_or_bad_aspect_detections(self) -> None:
        frame_shape = (480, 640, 3)
        self.assertFalse(is_face_box_usable((20, 20, 40, 40), frame_shape))
        self.assertFalse(is_face_box_usable((20, 20, 120, 45), frame_shape))
        self.assertTrue(is_face_box_usable((240, 130, 72, 76), frame_shape))


class MonitorIdentityTests(unittest.TestCase):
    def test_track_without_face_does_not_allocate_visitor_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=1)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            now = monotonic()
            track = Track(
                id=1,
                bbox=(0, 319, 123, 161),
                centroid=(61.5, 399.5),
                first_seen=now - 20,
                last_seen=now,
            )
            frame = np.zeros((480, 640, 3), dtype=np.uint8)

            identity, confidence, face = monitor._resolve_track_identity(track, frame, [])

            self.assertIsNone(identity)
            self.assertIsNone(confidence)
            self.assertIsNone(face)
            self.assertEqual(store.list_identity_names(), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
