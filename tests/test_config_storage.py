from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from lab_monitor.config import AppConfig, load_config, save_config_updates, validate_config_updates
from lab_monitor.reporting import build_analytics, sessions_to_csv, sessions_to_json_payload
from lab_monitor.storage import EventStore, utc_now


class ConfigTests(unittest.TestCase):
    def test_load_config_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"camera_index": 1, "bad": True}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_default_paths_are_resolved(self) -> None:
        config = AppConfig(data_dir="data-test")
        self.assertTrue(config.data_path.is_absolute())
        self.assertEqual(config.database_path.name, "lab_monitor.sqlite3")

    def test_validate_config_updates_accepts_valid_values(self) -> None:
        updates = validate_config_updates(
            {
                "camera_index": "1",
                "min_dwell_seconds": "45",
                "face_recognition_threshold": "72.5",
                "unknown_identity_prefix": "Guest",
            }
        )
        self.assertEqual(updates["camera_index"], 1)
        self.assertEqual(updates["min_dwell_seconds"], 45)
        self.assertEqual(updates["face_recognition_threshold"], 72.5)
        self.assertEqual(updates["unknown_identity_prefix"], "Guest")

    def test_validate_config_updates_rejects_invalid_ranges(self) -> None:
        with self.assertRaises(ValueError):
            validate_config_updates({"web_port": "99999"})

    def test_save_config_updates_preserves_known_config_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            saved = save_config_updates(
                path,
                {"camera_index": "2", "unknown_identity_prefix": "Guest"},
                profile="Responsive",
            )
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved.camera_index, 2)
            self.assertEqual(saved.unknown_identity_prefix, "Guest")
            self.assertEqual(set(reloaded), set(AppConfig.__dataclass_fields__))


class StorageTests(unittest.TestCase):
    def test_record_and_list_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            event_id = store.record_event("person_entered", track_id=7, details={"a": 1})
            event = store.get_event(event_id)
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event.event_type, "person_entered")
            self.assertEqual(event.track_id, 7)
            self.assertEqual(event.details, {"a": 1})
            self.assertEqual(len(store.list_events()), 1)
            store.close()

    def test_purge_old_snapshot_keeps_log_until_log_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            store = EventStore(data_dir / "events.sqlite3", data_dir)
            snapshot = data_dir / "snapshots" / "old.jpg"
            snapshot.write_bytes(b"fake")
            old_ts = (utc_now() - timedelta(days=10)).isoformat(timespec="seconds")
            event_id = store.record_event(
                "person_entered",
                snapshot_path="snapshots/old.jpg",
                ts=old_ts,
            )
            os.utime(snapshot, (0, 0))
            result = store.purge_old(snapshot_retention_days=7, log_retention_days=180)
            event = store.get_event(event_id)
            self.assertGreaterEqual(result["deleted_snapshots"], 1)
            self.assertFalse(snapshot.exists())
            self.assertIsNotNone(event)
            assert event is not None
            self.assertIsNone(event.snapshot_path)
            store.close()

    def test_sessions_merge_by_identity_within_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            first = store.upsert_session(
                "Visitor 001",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+00:00",
            )
            second = store.upsert_session(
                "Visitor 001",
                track_id=2,
                merge_gap_minutes=15,
                ts="2026-08-04T10:10:00+00:00",
            )
            sessions = store.list_sessions()
            self.assertEqual(first, second)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].start_ts, "2026-08-04T10:00:00+00:00")
            self.assertEqual(sessions[0].end_ts, "2026-08-04T10:10:00+00:00")
            store.close()

    def test_rename_identity_updates_sessions_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            store.upsert_session("Visitor 001", track_id=1, merge_gap_minutes=15)
            event_id = store.record_event("face_unknown", person_name="Visitor 001")
            store.rename_identity("Visitor 001", "Alice")
            self.assertEqual(store.list_sessions()[0].identity_name, "Alice")
            event = store.get_event(event_id)
            assert event is not None
            self.assertEqual(event.person_name, "Alice")
            store.close()

    def test_rename_identity_aliases_old_label_and_prevents_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            self.assertEqual(store.allocate_identity("Visitor"), "Visitor 001")
            store.upsert_session(
                "Visitor 001",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+00:00",
            )

            store.rename_identity("Visitor 001", "Alice")

            self.assertEqual(store.resolve_identity("Visitor 001"), "Alice")
            self.assertEqual(store.list_identity_aliases(), {"Visitor 001": "Alice"})
            self.assertIn("Visitor 001", store.list_identity_names())
            self.assertIn("Alice", store.list_identity_names())
            self.assertEqual(store.allocate_identity("Visitor"), "Visitor 002")
            second = store.upsert_session(
                "Visitor 001",
                track_id=2,
                merge_gap_minutes=15,
                ts="2026-08-04T10:20:00+00:00",
            )
            session = next(item for item in store.list_sessions() if item.id == second)
            self.assertEqual(session.identity_name, "Alice")
            store.close()

    def test_rename_identity_can_consolidate_adjacent_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            first = store.upsert_session(
                "Visitor 007",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+00:00",
            )
            store.update_session(first, ts="2026-08-04T10:05:00+00:00")
            store.upsert_session(
                "Visitor 008",
                track_id=2,
                merge_gap_minutes=15,
                ts="2026-08-04T10:20:00+00:00",
            )

            store.rename_identity("Visitor 008", "Visitor 007", merge_gap_minutes=45)

            sessions = store.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].identity_name, "Visitor 007")
            self.assertEqual(sessions[0].start_ts, "2026-08-04T10:00:00+00:00")
            self.assertEqual(sessions[0].last_seen_ts, "2026-08-04T10:20:00+00:00")
            store.close()

    def test_export_csv_includes_session_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            store.upsert_session(
                "Visitor 001",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+00:00",
            )
            csv_text = sessions_to_csv(store.list_sessions())
            self.assertIn("identity,start,end,duration_minutes", csv_text)
            self.assertIn("Visitor 001", csv_text)
            store.close()

    def test_json_export_includes_summary_and_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            store.upsert_session(
                "Alice",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+00:00",
            )
            sessions = store.list_sessions()
            summary = build_analytics(sessions, "Visitor")
            payload = sessions_to_json_payload(sessions, summary, None, None)
            self.assertEqual(payload["summary"]["total_sessions"], 1)
            self.assertEqual(payload["identities"], ["Alice"])
            self.assertEqual(payload["sessions"][0]["identity"], "Alice")
            store.close()

    def test_analytics_handles_empty_and_populated_sessions(self) -> None:
        empty = build_analytics([], "Visitor")
        self.assertEqual(empty.total_sessions, 0)
        self.assertIsNone(empty.peak_hour)

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3", Path(tmp))
            session_id = store.upsert_session(
                "Visitor 001",
                track_id=1,
                merge_gap_minutes=15,
                ts="2026-08-04T10:00:00+08:00",
            )
            store.update_session(session_id, ts="2026-08-04T11:30:00+08:00")
            summary = build_analytics(store.list_sessions(), "Visitor")
            self.assertEqual(summary.total_sessions, 1)
            self.assertEqual(summary.occupied_hours, 1.5)
            self.assertEqual(summary.unknown_visitors, 1)
            self.assertEqual(summary.peak_hour, 10)
            store.close()


if __name__ == "__main__":
    unittest.main()
