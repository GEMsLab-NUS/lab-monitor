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
    def test_person_detection_without_face_does_not_start_candidate_track(self) -> None:
        class FakePersonDetector:
            def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
                return [(500, 250, 90, 190)]

        class FakeFaceService:
            def detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
                return []

            def is_enrollable_face(self, frame: np.ndarray, face: tuple[int, int, int, int]) -> bool:
                return True

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=1)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            monitor._person_detector = FakePersonDetector()  # type: ignore[assignment]
            monitor._face_service = FakeFaceService()  # type: ignore[assignment]
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)

            monitor.process_frame(frame)

            self.assertEqual(monitor.status.active_tracks, 0)
            self.assertEqual(monitor.live_snapshot()["tracks"], [])
            store.close()

    def test_face_supported_person_detection_starts_track(self) -> None:
        class FakePersonDetector:
            def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
                return [(180, 80, 240, 340)]

        class FakeFaceService:
            def detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
                return [(240, 130, 80, 86)]

            def is_enrollable_face(self, frame: np.ndarray, face: tuple[int, int, int, int]) -> bool:
                return True

            def recognize_face(
                self,
                frame: np.ndarray,
                face: tuple[int, int, int, int],
                threshold: float | None = None,
            ) -> tuple[str | None, float | None]:
                return None, None

            def enroll_face_crop(
                self,
                name: str,
                frame: np.ndarray,
                face: tuple[int, int, int, int],
                sample: str,
                *,
                max_samples: int | None = None,
            ) -> bool:
                return True

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=60)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            monitor._person_detector = FakePersonDetector()  # type: ignore[assignment]
            monitor._face_service = FakeFaceService()  # type: ignore[assignment]
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)

            monitor.process_frame(frame)

            self.assertEqual(monitor.status.active_tracks, 1)
            self.assertEqual(monitor.live_snapshot()["faces"], [{"bbox": [240, 130, 80, 86]}])
            store.close()

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

    def test_live_state_keeps_latest_frame_faces_and_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=10)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            now = monotonic()
            monitor._tracker.tracks[1] = Track(
                id=1,
                bbox=(180, 80, 240, 340),
                centroid=(300.0, 250.0),
                first_seen=now - 5,
                last_seen=now,
                last_name="Visitor 001",
            )
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)

            monitor._update_live_state(frame, [(240, 130, 80, 86)])
            payload = monitor.live_snapshot()

            self.assertTrue(payload["has_frame"])
            self.assertIsNotNone(monitor.latest_frame_jpeg())
            self.assertEqual(payload["frame"]["width"], 640)  # type: ignore[index]
            self.assertEqual(payload["faces"], [{"bbox": [240, 130, 80, 86]}])
            self.assertEqual(payload["tracks"][0]["identity"], "Visitor 001")  # type: ignore[index]
            store.close()

    def test_live_state_omits_unnamed_candidate_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=10)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            now = monotonic()
            monitor._tracker.tracks[1] = Track(
                id=1,
                bbox=(180, 80, 240, 340),
                centroid=(300.0, 250.0),
                first_seen=now - 5,
                last_seen=now,
            )
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)

            monitor._update_live_state(frame, [(240, 130, 80, 86)])
            payload = monitor.live_snapshot()

            self.assertEqual(payload["tracks"], [])
            store.close()

    def test_known_face_requires_repeated_observations_before_identity_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=1)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            monitor._identify_face = lambda frame, face: ("Alice", 42.0)  # type: ignore[method-assign]
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

            self.assertEqual(monitor._resolve_track_identity(track, frame, [face]), (None, 42.0, None))
            identity, confidence, used_face = monitor._resolve_track_identity(track, frame, [face])

            self.assertEqual(identity, "Alice")
            self.assertEqual(confidence, 42.0)
            self.assertEqual(used_face, face)
            store.close()

    def test_face_identity_seen_before_dwell_is_used_when_logging_later(self) -> None:
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
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=10)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            fake_face_service = FakeFaceService()
            monitor._face_service = fake_face_service  # type: ignore[assignment]
            monitor._identify_face = lambda frame, face: ("Alice", 42.0)  # type: ignore[method-assign]
            now = monotonic()
            monitor._tracker.tracks[1] = Track(
                id=1,
                bbox=(180, 80, 240, 340),
                centroid=(300.0, 250.0),
                first_seen=now - 5,
                last_seen=now,
            )
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)
            face = (240, 130, 80, 86)

            monitor._observe_face_identities(frame, [face])
            monitor._observe_face_identities(frame, [face])
            track = monitor._tracker.tracks[1]
            track.first_seen = monotonic() - 20
            monitor._record_track_behaviors(frame, [])

            sessions = store.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].identity_name, "Alice")
            self.assertEqual(fake_face_service.enrolls, 1)
            store.close()

    def test_unknown_face_is_added_to_roster_before_dwell_without_session(self) -> None:
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
            config = AppConfig(data_dir=str(data_dir), min_dwell_seconds=60)
            store = EventStore(config.database_path, config.data_path)
            monitor = CameraMonitor(config, store)
            fake_face_service = FakeFaceService()
            monitor._face_service = fake_face_service  # type: ignore[assignment]
            monitor._identify_face = lambda frame, face: (None, 118.0)  # type: ignore[method-assign]
            now = monotonic()
            monitor._tracker.tracks[1] = Track(
                id=1,
                bbox=(180, 80, 240, 340),
                centroid=(300.0, 250.0),
                first_seen=now,
                last_seen=now,
            )
            frame = np.full((480, 640, 3), 120, dtype=np.uint8)
            face = (240, 130, 80, 86)

            monitor._observe_face_identities(frame, [face])

            self.assertEqual(store.list_identity_names(), ["Visitor 001"])
            self.assertEqual(store.list_sessions(), [])
            self.assertEqual(fake_face_service.enrolls, 1)
            store.close()

    def test_unknown_face_reuses_allocated_visitor_after_first_enrollment(self) -> None:
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

            identity, confidence, used_face = monitor._resolve_track_identity(track, frame, [face])
            second_identity, second_confidence, second_face = monitor._resolve_track_identity(track, frame, [face])

            self.assertEqual(identity, "Visitor 001")
            self.assertEqual(second_identity, "Visitor 001")
            self.assertEqual(confidence, 112.0)
            self.assertEqual(second_confidence, 112.0)
            self.assertEqual(used_face, face)
            self.assertEqual(second_face, face)
            self.assertEqual(store.list_identity_names(), ["Visitor 001"])
            self.assertEqual(fake_face_service.enrolls, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
