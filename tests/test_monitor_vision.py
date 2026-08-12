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
from lab_monitor.vision import FaceService, is_face_box_usable, is_person_box_usable


class VisionQualityTests(unittest.TestCase):
    def test_person_box_rejects_full_frame_duplicate_detection(self) -> None:
        frame_shape = (480, 640, 3)
        self.assertFalse(is_person_box_usable((0, 0, 640, 480), frame_shape))
        self.assertTrue(is_person_box_usable((230, 120, 190, 300), frame_shape))

    def test_face_box_rejects_small_or_bad_aspect_detections(self) -> None:
        frame_shape = (480, 640, 3)
        self.assertFalse(is_face_box_usable((20, 20, 40, 40), frame_shape))
        self.assertFalse(is_face_box_usable((20, 20, 120, 45), frame_shape))
        self.assertFalse(is_face_box_usable((0, 130, 72, 76), frame_shape))
        self.assertTrue(is_face_box_usable((240, 130, 72, 76), frame_shape))

    def test_face_enrollment_prunes_old_samples_at_identity_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = FaceService(
                root / "faces",
                root / "enrolled_faces",
                threshold=60.0,
                min_face_size_px=64,
                min_face_area_ratio=0.01,
                min_sharpness=0.0,
            )
            frame = np.random.default_rng(7).integers(80, 180, size=(160, 160, 3), dtype=np.uint8)

            for index in range(5):
                self.assertTrue(
                    service.enroll_face_crop(
                        "Alice",
                        frame,
                        (30, 30, 80, 80),
                        f"sample_{index}",
                        max_samples=3,
                    )
                )

            self.assertLessEqual(service.sample_count("Alice"), 3)

    def test_renaming_multiple_visitor_labels_merges_face_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = FaceService(
                root / "faces",
                root / "enrolled_faces",
                threshold=60.0,
                min_face_size_px=64,
                min_face_area_ratio=0.01,
                min_sharpness=0.0,
            )
            frame = np.random.default_rng(11).integers(80, 180, size=(160, 160, 3), dtype=np.uint8)
            self.assertTrue(service.enroll_face_crop("Visitor 001", frame, (30, 30, 80, 80), "one"))
            self.assertTrue(service.enroll_face_crop("Visitor 002", frame, (30, 30, 80, 80), "two"))

            self.assertTrue(service.rename_label("Visitor 001", "Alice", max_samples=4))
            self.assertTrue(service.rename_label("Visitor 002", "Alice", max_samples=4))

            self.assertEqual(service.label_names(), {"Alice"})
            self.assertEqual(service.sample_count("Alice"), 2)


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

    def test_unknown_face_requires_repeated_observations_before_enrollment(self) -> None:
        class FakeFaceService:
            def __init__(self) -> None:
                self.enrolls = 0

            def enroll_face_crop(
                self,
                name: str,
                frame: np.ndarray,
                face: tuple[int, int, int, int],
                sample: str,
                *,
                max_samples: int | None = None,
            ) -> bool:
                self.enrolls += 1
                return True

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(
                data_dir=str(data_dir),
                min_dwell_seconds=1,
                min_unknown_face_observations=3,
            )
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            fake_face_service = FakeFaceService()
            monitor._face_service = fake_face_service  # type: ignore[assignment]
            monitor._identify_face = lambda frame, face: (None, 112.0)  # type: ignore[method-assign]
            now = monotonic()
            track = Track(
                id=1,
                bbox=(180, 80, 240, 340),
                centroid=(300.0, 250.0),
                first_seen=now - 20,
                last_seen=now,
            )
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)
            face = (240, 130, 80, 86)

            self.assertEqual(monitor._resolve_track_identity(track, frame, [face]), (None, 112.0, None))
            self.assertEqual(monitor._resolve_track_identity(track, frame, [face]), (None, 112.0, None))
            identity, confidence, used_face = monitor._resolve_track_identity(track, frame, [face])

            self.assertEqual(identity, "Visitor 001")
            self.assertEqual(confidence, 112.0)
            self.assertEqual(used_face, face)
            self.assertEqual(fake_face_service.enrolls, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
