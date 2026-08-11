from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from time import monotonic


BBox = tuple[int, int, int, int]


@dataclass(slots=True)
class Track:
    id: int
    bbox: BBox
    centroid: tuple[float, float]
    first_seen: float
    last_seen: float
    missing_frames: int = 0
    stationary_since: float | None = None
    dwell_confirmed_at: float | None = None
    session_id: int | None = None
    last_event_at: dict[str, float] = field(default_factory=dict)
    last_name: str | None = None
    unknown_face_observations: int = 0


class CentroidTracker:
    def __init__(self, max_distance_px: int = 120, max_missing_frames: int = 8) -> None:
        self.max_distance_px = max_distance_px
        self.max_missing_frames = max_missing_frames
        self._next_id = 1
        self.tracks: dict[int, Track] = {}

    def update(self, boxes: list[BBox]) -> tuple[list[Track], list[Track]]:
        now = monotonic()
        detections = [(box, self._centroid(box)) for box in boxes]
        unmatched_track_ids = set(self.tracks)
        unmatched_detection_indices = set(range(len(detections)))

        candidates: list[tuple[float, int, int]] = []
        for track_id, track in self.tracks.items():
            for detection_index, (_, centroid) in enumerate(detections):
                candidates.append((self._distance(track.centroid, centroid), track_id, detection_index))
        candidates.sort(key=lambda item: item[0])

        for distance, track_id, detection_index in candidates:
            if distance > self.max_distance_px:
                continue
            if track_id not in unmatched_track_ids or detection_index not in unmatched_detection_indices:
                continue
            box, centroid = detections[detection_index]
            track = self.tracks[track_id]
            moved = self._distance(track.centroid, centroid)
            track.bbox = box
            track.centroid = centroid
            track.last_seen = now
            track.missing_frames = 0
            if moved > 12:
                track.stationary_since = None
            elif track.stationary_since is None:
                track.stationary_since = now
            unmatched_track_ids.remove(track_id)
            unmatched_detection_indices.remove(detection_index)

        for track_id in list(unmatched_track_ids):
            track = self.tracks[track_id]
            track.missing_frames += 1

        new_tracks: list[Track] = []
        for detection_index in sorted(unmatched_detection_indices):
            box, centroid = detections[detection_index]
            track = Track(
                id=self._next_id,
                bbox=box,
                centroid=centroid,
                first_seen=now,
                last_seen=now,
                stationary_since=now,
            )
            self.tracks[track.id] = track
            self._next_id += 1
            new_tracks.append(track)

        removed_tracks: list[Track] = []
        for track_id, track in list(self.tracks.items()):
            if track.missing_frames > self.max_missing_frames:
                removed_tracks.append(track)
                del self.tracks[track_id]
        return new_tracks, removed_tracks

    @staticmethod
    def _centroid(box: BBox) -> tuple[float, float]:
        x, y, w, h = box
        return (x + w / 2, y + h / 2)

    @staticmethod
    def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return hypot(a[0] - b[0], a[1] - b[1])
