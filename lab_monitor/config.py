from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AppConfig:
    camera_index: int = 0
    data_dir: str = "data"
    snapshot_retention_days: int = 7
    log_retention_days: int = 180
    min_dwell_seconds: int = 60
    session_merge_gap_minutes: int = 15
    unknown_identity_prefix: str = "Visitor"
    poll_interval_ms: int = 350
    min_event_interval_seconds: int = 20
    presence_heartbeat_seconds: int = 300
    stationary_seconds: int = 120
    max_tracking_distance_px: int = 120
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    face_recognition_threshold: float = 62.0
    face_soft_match_threshold: float = 90.0
    min_face_size_px: int = 72
    min_face_area_ratio: float = 0.012
    min_unknown_face_observations: int = 4
    face_enrollment_min_sharpness: float = 18.0
    face_enrollment_min_brightness: float = 35.0
    face_enrollment_max_brightness: float = 220.0
    face_learning_interval_seconds: int = 600
    max_face_samples_per_identity: int = 48
    min_person_area_ratio: float = 0.015
    max_person_area_ratio: float = 0.70
    unknown_face_label: str = "unknown"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self.data_path / "lab_monitor.sqlite3"

    @property
    def snapshot_path(self) -> Path:
        return self.data_path / "snapshots"

    @property
    def faces_path(self) -> Path:
        return self.data_path / "faces"

    @property
    def enrolled_faces_path(self) -> Path:
        return self.data_path / "enrolled_faces"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CONFIG_FIELDS: dict[str, dict[str, Any]] = {
    "camera_index": {"type": int, "min": 0, "max": 16, "group": "Basic"},
    "min_dwell_seconds": {"type": int, "min": 1, "max": 3600, "group": "Monitoring behavior"},
    "session_merge_gap_minutes": {"type": int, "min": 0, "max": 720, "group": "Monitoring behavior"},
    "poll_interval_ms": {"type": int, "min": 50, "max": 5000, "group": "Monitoring behavior"},
    "stationary_seconds": {"type": int, "min": 5, "max": 7200, "group": "Monitoring behavior"},
    "max_tracking_distance_px": {"type": int, "min": 10, "max": 1000, "group": "Monitoring behavior"},
    "face_recognition_threshold": {"type": float, "min": 1.0, "max": 300.0, "group": "Identity recognition"},
    "face_soft_match_threshold": {"type": float, "min": 1.0, "max": 300.0, "group": "Identity recognition"},
    "min_face_size_px": {"type": int, "min": 20, "max": 300, "group": "Identity recognition"},
    "min_face_area_ratio": {"type": float, "min": 0.0005, "max": 0.25, "group": "Identity recognition"},
    "min_unknown_face_observations": {"type": int, "min": 1, "max": 30, "group": "Identity recognition"},
    "face_enrollment_min_sharpness": {"type": float, "min": 0.0, "max": 500.0, "group": "Identity recognition"},
    "face_enrollment_min_brightness": {"type": float, "min": 0.0, "max": 255.0, "group": "Identity recognition"},
    "face_enrollment_max_brightness": {"type": float, "min": 0.0, "max": 255.0, "group": "Identity recognition"},
    "face_learning_interval_seconds": {"type": int, "min": 60, "max": 86400, "group": "Identity recognition"},
    "max_face_samples_per_identity": {"type": int, "min": 5, "max": 500, "group": "Identity recognition"},
    "min_person_area_ratio": {"type": float, "min": 0.0005, "max": 0.80, "group": "Monitoring behavior"},
    "max_person_area_ratio": {"type": float, "min": 0.05, "max": 1.0, "group": "Monitoring behavior"},
    "unknown_identity_prefix": {"type": str, "min_length": 1, "max_length": 40, "group": "Identity recognition"},
    "snapshot_retention_days": {"type": int, "min": 1, "max": 3650, "group": "Retention"},
    "log_retention_days": {"type": int, "min": 1, "max": 3650, "group": "Retention"},
    "web_host": {"type": str, "min_length": 1, "max_length": 80, "group": "Network"},
    "web_port": {"type": int, "min": 1, "max": 65535, "group": "Network"},
}


WORK_PROFILES: dict[str, dict[str, Any]] = {
    "Balanced": {
        "min_dwell_seconds": 60,
        "session_merge_gap_minutes": 15,
        "poll_interval_ms": 350,
        "stationary_seconds": 120,
        "max_tracking_distance_px": 120,
        "face_recognition_threshold": 62.0,
        "face_soft_match_threshold": 90.0,
        "min_face_size_px": 72,
        "min_face_area_ratio": 0.012,
        "min_unknown_face_observations": 4,
        "face_enrollment_min_sharpness": 18.0,
        "face_enrollment_min_brightness": 35.0,
        "face_enrollment_max_brightness": 220.0,
        "face_learning_interval_seconds": 600,
        "max_face_samples_per_identity": 48,
        "min_person_area_ratio": 0.015,
        "max_person_area_ratio": 0.70,
    },
    "Responsive": {
        "min_dwell_seconds": 20,
        "session_merge_gap_minutes": 10,
        "poll_interval_ms": 180,
        "stationary_seconds": 60,
        "max_tracking_distance_px": 160,
        "face_recognition_threshold": 70.0,
        "face_soft_match_threshold": 100.0,
        "min_face_size_px": 64,
        "min_face_area_ratio": 0.009,
        "min_unknown_face_observations": 3,
        "face_enrollment_min_sharpness": 14.0,
        "face_enrollment_min_brightness": 30.0,
        "face_enrollment_max_brightness": 230.0,
        "face_learning_interval_seconds": 300,
        "max_face_samples_per_identity": 64,
        "min_person_area_ratio": 0.010,
        "max_person_area_ratio": 0.78,
    },
    "Conservative": {
        "min_dwell_seconds": 120,
        "session_merge_gap_minutes": 30,
        "poll_interval_ms": 700,
        "stationary_seconds": 240,
        "max_tracking_distance_px": 90,
        "face_recognition_threshold": 55.0,
        "face_soft_match_threshold": 80.0,
        "min_face_size_px": 88,
        "min_face_area_ratio": 0.016,
        "min_unknown_face_observations": 6,
        "face_enrollment_min_sharpness": 24.0,
        "face_enrollment_min_brightness": 45.0,
        "face_enrollment_max_brightness": 210.0,
        "face_learning_interval_seconds": 900,
        "max_face_samples_per_identity": 36,
        "min_person_area_ratio": 0.025,
        "max_person_area_ratio": 0.55,
    },
}


def load_config(path: str | Path | None = None) -> AppConfig:
    config = AppConfig()
    if path is None:
        default_path = Path("config.json")
        if not default_path.exists():
            return config
        path = default_path

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = set(AppConfig.__dataclass_fields__)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown config key(s): {', '.join(unknown)}")
    return AppConfig(**{**config.to_dict(), **raw})


def validate_config_updates(values: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    errors: list[str] = []
    for key, spec in CONFIG_FIELDS.items():
        if key not in values:
            continue
        raw = values[key]
        field_type = spec["type"]
        try:
            if field_type is int:
                value = int(str(raw).strip())
            elif field_type is float:
                value = float(str(raw).strip())
            else:
                value = str(raw).strip()
        except (TypeError, ValueError):
            errors.append(f"{key} must be a {field_type.__name__}.")
            continue

        if field_type in {int, float}:
            if value < spec["min"] or value > spec["max"]:
                errors.append(f"{key} must be between {spec['min']} and {spec['max']}.")
                continue
        else:
            if len(value) < spec["min_length"] or len(value) > spec["max_length"]:
                errors.append(
                    f"{key} must be {spec['min_length']} to {spec['max_length']} characters."
                )
                continue
        updates[key] = value
    if errors:
        raise ValueError(" ".join(errors))
    min_brightness = updates.get("face_enrollment_min_brightness")
    max_brightness = updates.get("face_enrollment_max_brightness")
    if min_brightness is not None and max_brightness is not None and min_brightness > max_brightness:
        raise ValueError("face_enrollment_min_brightness must be less than or equal to face_enrollment_max_brightness.")
    return updates


def save_config_updates(path: str | Path, updates: dict[str, Any], profile: str | None = None) -> AppConfig:
    target = ensure_config_file(path)
    current = load_config(target).to_dict()
    if profile in WORK_PROFILES:
        current.update(WORK_PROFILES[profile])
    current.update(validate_config_updates(updates))
    config = AppConfig(**current)
    target.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return config


def ensure_config_file(path: str | Path) -> Path:
    target = Path(path)
    if not target.exists():
        target.write_text(
            json.dumps(AppConfig().to_dict(), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return target
