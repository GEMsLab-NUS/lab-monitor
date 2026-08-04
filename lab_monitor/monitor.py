from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

import numpy as np

from .config import AppConfig
from .storage import EventStore
from .tracking import BBox, CentroidTracker, Track
from .vision import FaceService, PersonDetector, crop, face_inside_person, load_cv2, person_box_from_face


@dataclass(slots=True)
class MonitorStatus:
    running: bool
    camera_open: bool
    active_tracks: int
    last_error: str | None = None


class CameraMonitor:
    def __init__(self, config: AppConfig, store: EventStore) -> None:
        self.config = config
        self.store = store
        self._running = False
        self._camera_open = False
        self._last_error: str | None = None
        self._tracker = CentroidTracker(config.max_tracking_distance_px)
        self._cv2 = load_cv2()
        self._person_detector = PersonDetector()
        self._face_service = FaceService(
            config.faces_path,
            config.enrolled_faces_path,
            config.face_recognition_threshold,
        )

    @property
    def status(self) -> MonitorStatus:
        return MonitorStatus(
            running=self._running,
            camera_open=self._camera_open,
            active_tracks=len(self._tracker.tracks),
            last_error=self._last_error,
        )

    def rename_identity(self, old_name: str, new_name: str) -> bool:
        renamed = self._face_service.rename_label(old_name, new_name)
        for track in self._tracker.tracks.values():
            if track.last_name == old_name:
                track.last_name = new_name
                if track.session_id is not None:
                    self.store.update_session(track.session_id, identity_name=new_name)
        return renamed

    def stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        cap = self._cv2.VideoCapture(self.config.camera_index)
        self._camera_open = cap.isOpened()
        if not self._camera_open:
            raise RuntimeError(f"Cannot open camera index {self.config.camera_index}")
        self._running = True
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    self._last_error = "Camera read failed"
                    sleep(1.0)
                    continue
                self.process_frame(frame)
                self.store.purge_old(
                    self.config.snapshot_retention_days,
                    self.config.log_retention_days,
                )
                sleep(max(0.05, self.config.poll_interval_ms / 1000))
        finally:
            cap.release()
            self._camera_open = False
            self._running = False

    def process_frame(self, frame: np.ndarray) -> None:
        frame = self._resize_for_processing(frame)
        height, width = frame.shape[:2]
        person_boxes = self._person_detector.detect(frame)
        face_boxes = self._face_service.detect_faces(frame)

        if not person_boxes and face_boxes:
            person_boxes = [person_box_from_face(face, width, height) for face in face_boxes]

        _new_tracks, removed_tracks = self._tracker.update(person_boxes)
        for track in removed_tracks:
            if track.dwell_confirmed_at is None:
                continue
            if track.session_id is not None:
                self.store.update_session(
                    track.session_id,
                    identity_name=track.last_name,
                    details={"visible_seconds": round(monotonic() - track.first_seen, 1)},
                )

        self._record_population_events(frame)
        self._record_track_behaviors(frame, face_boxes)
        self._refresh_face_identity(frame, face_boxes)

    def _record_population_events(self, frame: np.ndarray) -> None:
        dwelled_tracks = [track for track in self._tracker.tracks.values() if track.dwell_confirmed_at is not None]
        if len(dwelled_tracks) < 2:
            return
        first_track = dwelled_tracks[0]
        if self._should_emit(first_track, "multiple_people"):
            self._record_track_event(
                "multiple_people",
                first_track,
                frame,
                behavior="multiple_people",
                details={"count": len(dwelled_tracks)},
            )

    def _record_track_behaviors(self, frame: np.ndarray, face_boxes: list[BBox]) -> None:
        now = monotonic()
        for track in self._tracker.tracks.values():
            if track.dwell_confirmed_at is None:
                dwell_seconds = now - track.first_seen
                if dwell_seconds < self.config.min_dwell_seconds:
                    continue
                track.dwell_confirmed_at = now
                identity, confidence, face_box = self._resolve_track_identity(track, frame, face_boxes)
                track.last_name = identity
                snapshot_path = self._save_snapshot(frame, face_box or track.bbox, "session", track.id)
                track.session_id = self.store.upsert_session(
                    identity,
                    track_id=track.id,
                    merge_gap_minutes=self.config.session_merge_gap_minutes,
                    confidence=confidence,
                    snapshot_path=snapshot_path,
                    details={
                        "dwell_seconds": round(dwell_seconds, 1),
                        "required_seconds": self.config.min_dwell_seconds,
                        "bbox": list(face_box or track.bbox),
                    },
                )
                continue
            if track.session_id is not None:
                self.store.update_session(track.session_id, identity_name=track.last_name)
            if (
                track.stationary_since is not None
                and now - track.stationary_since >= self.config.stationary_seconds
                and self._should_emit(track, "stationary")
            ):
                self._record_track_event(
                    "stationary",
                    track,
                    frame,
                    behavior="stationary",
                    details={"seconds": round(now - track.stationary_since, 1)},
                )

    def _refresh_face_identity(self, frame: np.ndarray, face_boxes: list[BBox]) -> None:
        for face in face_boxes:
            track = self._track_for_face(face)
            if track is None or track.dwell_confirmed_at is None:
                continue
            name, confidence = self._face_service.recognize_face(frame, face)
            if name:
                name = self.store.resolve_identity(name)
                track.last_name = name
                if track.session_id is not None:
                    self.store.update_session(track.session_id, identity_name=name, confidence=confidence)
                continue
            if track.last_name is None:
                track.last_name = self.store.allocate_identity(self.config.unknown_identity_prefix)
            else:
                track.last_name = self.store.resolve_identity(track.last_name)
            if self._should_emit(track, f"auto_enroll:{track.last_name}", interval_seconds=3600):
                self._face_service.enroll_face_crop(
                    track.last_name,
                    frame,
                    face,
                    f"track{track.id}_{int(monotonic() * 1000)}",
                )
            if track.session_id is not None:
                self.store.update_session(track.session_id, identity_name=track.last_name, confidence=confidence)

    def _resolve_track_identity(
        self,
        track: Track,
        frame: np.ndarray,
        face_boxes: list[BBox],
    ) -> tuple[str, float | None, BBox | None]:
        for face in face_boxes:
            if not face_inside_person(face, track.bbox):
                continue
            name, confidence = self._face_service.recognize_face(frame, face)
            if name:
                return self.store.resolve_identity(name), confidence, face
            identity = (
                self.store.resolve_identity(track.last_name)
                if track.last_name
                else self.store.allocate_identity(self.config.unknown_identity_prefix)
            )
            self._face_service.enroll_face_crop(
                identity,
                frame,
                face,
                f"track{track.id}_{int(monotonic() * 1000)}",
            )
            return identity, confidence, face
        identity = (
            self.store.resolve_identity(track.last_name)
            if track.last_name
            else self.store.allocate_identity(self.config.unknown_identity_prefix)
        )
        return identity, None, None

    def _track_for_face(self, face: BBox) -> Track | None:
        for track in self._tracker.tracks.values():
            if face_inside_person(face, track.bbox):
                return track
        return None

    def _record_track_event(
        self,
        event_type: str,
        track: Track,
        frame: np.ndarray,
        *,
        person_name: str | None = None,
        confidence: float | None = None,
        behavior: str | None = None,
        details: dict[str, object] | None = None,
        snapshot_box: BBox | None = None,
    ) -> None:
        snapshot_path = self._save_snapshot(frame, snapshot_box or track.bbox, event_type, track.id)
        self.store.record_event(
            event_type,
            track_id=track.id,
            person_name=person_name or track.last_name,
            confidence=confidence,
            behavior=behavior,
            details=details or {"bbox": list(snapshot_box or track.bbox)},
            snapshot_path=snapshot_path,
        )

    def _save_snapshot(self, frame: np.ndarray, box: BBox, event_type: str, track_id: int) -> str | None:
        self.config.snapshot_path.mkdir(parents=True, exist_ok=True)
        image = crop(frame, box)
        if image.size == 0:
            return None
        file_name = f"{event_type}_track{track_id}_{int(monotonic() * 1000)}.jpg"
        output_path = self.config.snapshot_path / file_name
        ok = self._cv2.imwrite(str(output_path), image, [int(self._cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return None
        return str(Path("snapshots") / file_name)

    def _should_emit(self, track: Track, key: str, interval_seconds: int | None = None) -> bool:
        now = monotonic()
        interval = interval_seconds or self.config.min_event_interval_seconds
        last = track.last_event_at.get(key)
        if last is not None and now - last < interval:
            return False
        track.last_event_at[key] = now
        return True

    @staticmethod
    def _resize_for_processing(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if width <= 960:
            return frame
        scale = 960 / width
        cv2 = load_cv2()
        return cv2.resize(frame, (960, int(height * scale)))
