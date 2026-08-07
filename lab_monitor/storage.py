from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


@dataclass(slots=True)
class Event:
    id: int
    ts: str
    event_type: str
    track_id: int | None
    person_name: str | None
    confidence: float | None
    behavior: str | None
    details: dict[str, Any]
    snapshot_path: str | None


@dataclass(slots=True)
class Session:
    id: int
    identity_name: str
    track_id: int | None
    start_ts: str
    end_ts: str
    last_seen_ts: str
    confidence: float | None
    snapshot_path: str | None
    details: dict[str, Any]


class EventStore:
    def __init__(self, database_path: Path, data_dir: Path) -> None:
        self.database_path = database_path
        self.data_dir = data_dir.resolve()
        self.snapshot_dir = self.data_dir / "snapshots"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                track_id INTEGER,
                person_name TEXT,
                confidence REAL,
                behavior TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                snapshot_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

            CREATE TABLE IF NOT EXISTS identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_name TEXT NOT NULL,
                track_id INTEGER,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                last_seen_ts TEXT NOT NULL,
                confidence REAL,
                snapshot_path TEXT,
                details_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_identity ON sessions(identity_name, last_seen_ts DESC);

            CREATE TABLE IF NOT EXISTS identity_aliases (
                old_name TEXT PRIMARY KEY,
                new_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_identity_aliases_new_name ON identity_aliases(new_name);
            """
        )
        self._conn.commit()

    def allocate_identity(self, prefix: str = "Visitor") -> str:
        rows = self._conn.execute(
            """
            SELECT name FROM identities WHERE name LIKE ?
            UNION
            SELECT old_name AS name FROM identity_aliases WHERE old_name LIKE ?
            UNION
            SELECT new_name AS name FROM identity_aliases WHERE new_name LIKE ?
            """,
            (f"{prefix} %", f"{prefix} %", f"{prefix} %"),
        ).fetchall()
        used: set[int] = set()
        for row in rows:
            suffix = str(row["name"]).removeprefix(f"{prefix} ")
            if suffix.isdigit():
                used.add(int(suffix))
        next_id = 1
        while next_id in used:
            next_id += 1
        name = f"{prefix} {next_id:03d}"
        now = iso_now()
        self._conn.execute(
            "INSERT INTO identities(name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
        self._conn.commit()
        return name

    def resolve_identity(self, name: str) -> str:
        current = name
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            row = self._conn.execute(
                "SELECT new_name FROM identity_aliases WHERE old_name = ?",
                (current,),
            ).fetchone()
            if row is None:
                return current
            current = str(row["new_name"])
        return current or name

    def list_identity_names(self) -> list[str]:
        rows = self._conn.execute("SELECT name FROM identities ORDER BY name").fetchall()
        return [str(row["name"]) for row in rows]

    def list_identity_aliases(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT old_name, new_name FROM identity_aliases ORDER BY old_name"
        ).fetchall()
        return {str(row["old_name"]): self.resolve_identity(str(row["new_name"])) for row in rows}

    def ensure_identity(self, name: str) -> None:
        now = iso_now()
        self._conn.execute(
            """
            INSERT INTO identities(name, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (name, now, now),
        )
        self._conn.commit()

    def rename_identity(self, old_name: str, new_name: str, merge_gap_minutes: int | None = None) -> None:
        if not old_name or not new_name:
            raise ValueError("Both old and new names are required.")
        old_name = old_name.strip()
        new_name = new_name.strip()
        canonical_old_name = self.resolve_identity(old_name)
        now = iso_now()
        self._conn.execute(
            """
            INSERT INTO identities(name, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (new_name, now, now),
        )
        self._conn.execute(
            """
            INSERT INTO identities(name, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (old_name, now, now),
        )
        if canonical_old_name != old_name:
            self._conn.execute(
                """
                INSERT INTO identities(name, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (canonical_old_name, now, now),
            )
        self._conn.execute(
            "UPDATE sessions SET identity_name = ? WHERE identity_name IN (?, ?)",
            (new_name, old_name, canonical_old_name),
        )
        self._conn.execute(
            "UPDATE events SET person_name = ? WHERE person_name IN (?, ?)",
            (new_name, old_name, canonical_old_name),
        )
        self._conn.execute(
            """
            UPDATE identity_aliases
            SET new_name = ?, updated_at = ?
            WHERE new_name IN (?, ?)
            """,
            (new_name, now, old_name, canonical_old_name),
        )
        self._conn.execute(
            """
            INSERT INTO identity_aliases(old_name, new_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(old_name) DO UPDATE SET
                new_name = excluded.new_name,
                updated_at = excluded.updated_at
            """,
            (old_name, new_name, now, now),
        )
        if canonical_old_name != old_name:
            self._conn.execute(
                """
                INSERT INTO identity_aliases(old_name, new_name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(old_name) DO UPDATE SET
                    new_name = excluded.new_name,
                    updated_at = excluded.updated_at
                """,
                (canonical_old_name, new_name, now, now),
            )
        self._conn.commit()
        if merge_gap_minutes is not None:
            self.consolidate_sessions(new_name, merge_gap_minutes)

    def consolidate_sessions(self, identity_name: str, merge_gap_minutes: int) -> int:
        identity_name = self.resolve_identity(identity_name)
        rows = self._conn.execute(
            """
            SELECT * FROM sessions
            WHERE identity_name = ?
            ORDER BY start_ts ASC, id ASC
            """,
            (identity_name,),
        ).fetchall()
        if len(rows) < 2:
            return 0

        merged_count = 0
        current = dict(rows[0])
        for row in rows[1:]:
            candidate = dict(row)
            current_last = datetime.fromisoformat(str(current["last_seen_ts"]))
            candidate_start = datetime.fromisoformat(str(candidate["start_ts"]))
            if candidate_start <= current_last + timedelta(minutes=merge_gap_minutes):
                current = self._merge_session_rows(current, candidate)
                merged_count += 1
                continue
            self._write_merged_session(current)
            current = candidate
        self._write_merged_session(current)
        self._conn.commit()
        return merged_count

    def _merge_session_rows(self, current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        current_end = datetime.fromisoformat(str(current["end_ts"]))
        candidate_end = datetime.fromisoformat(str(candidate["end_ts"]))
        current_last = datetime.fromisoformat(str(current["last_seen_ts"]))
        candidate_last = datetime.fromisoformat(str(candidate["last_seen_ts"]))
        current_details = json.loads(current["details_json"] or "{}")
        candidate_details = json.loads(candidate["details_json"] or "{}")
        merged_ids = list(current_details.get("merged_session_ids", []))
        merged_ids.append(candidate["id"])
        current_details.update({f"merged_{candidate['id']}": candidate_details})
        current_details["merged_session_ids"] = merged_ids
        current_confidence = current["confidence"]
        candidate_confidence = candidate["confidence"]
        confidences = [
            float(value)
            for value in (current_confidence, candidate_confidence)
            if value is not None
        ]
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (candidate["id"],))
        current["end_ts"] = max(current_end, candidate_end).isoformat(timespec="seconds")
        current["last_seen_ts"] = max(current_last, candidate_last).isoformat(timespec="seconds")
        current["confidence"] = min(confidences) if confidences else None
        current["snapshot_path"] = current["snapshot_path"] or candidate["snapshot_path"]
        current["details_json"] = json.dumps(current_details, ensure_ascii=True)
        return current

    def _write_merged_session(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """
            UPDATE sessions
            SET start_ts = ?, end_ts = ?, last_seen_ts = ?,
                confidence = ?, snapshot_path = ?, details_json = ?
            WHERE id = ?
            """,
            (
                row["start_ts"],
                row["end_ts"],
                row["last_seen_ts"],
                row["confidence"],
                row["snapshot_path"],
                row["details_json"],
                row["id"],
            ),
        )

    def upsert_session(
        self,
        identity_name: str,
        *,
        track_id: int | None,
        merge_gap_minutes: int,
        confidence: float | None = None,
        snapshot_path: str | None = None,
        details: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> int:
        now = ts or iso_now()
        identity_name = self.resolve_identity(identity_name)
        self.ensure_identity(identity_name)
        reference = datetime.fromisoformat(now)
        cutoff = (reference - timedelta(minutes=merge_gap_minutes)).isoformat(timespec="seconds")
        row = self._conn.execute(
            """
            SELECT * FROM sessions
            WHERE identity_name = ? AND last_seen_ts >= ?
            ORDER BY last_seen_ts DESC, id DESC
            LIMIT 1
            """,
            (identity_name, cutoff),
        ).fetchone()
        if row:
            session_id = int(row["id"])
            merged_details = json.loads(row["details_json"] or "{}")
            merged_details.update(details or {})
            self._conn.execute(
                """
                UPDATE sessions
                SET end_ts = ?, last_seen_ts = ?, track_id = ?,
                    confidence = COALESCE(?, confidence),
                    snapshot_path = COALESCE(snapshot_path, ?),
                    details_json = ?
                WHERE id = ?
                """,
                (
                    now,
                    now,
                    track_id,
                    confidence,
                    snapshot_path,
                    json.dumps(merged_details, ensure_ascii=True),
                    session_id,
                ),
            )
            self._conn.commit()
            return session_id
        cur = self._conn.execute(
            """
            INSERT INTO sessions (
                identity_name, track_id, start_ts, end_ts, last_seen_ts,
                confidence, snapshot_path, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity_name,
                track_id,
                now,
                now,
                now,
                confidence,
                snapshot_path,
                json.dumps(details or {}, ensure_ascii=True),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_session(
        self,
        session_id: int,
        *,
        identity_name: str | None = None,
        confidence: float | None = None,
        snapshot_path: str | None = None,
        details: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        now = ts or iso_now()
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return
        merged_details = json.loads(row["details_json"] or "{}")
        merged_details.update(details or {})
        new_identity = self.resolve_identity(identity_name or row["identity_name"])
        self.ensure_identity(new_identity)
        self._conn.execute(
            """
            UPDATE sessions
            SET identity_name = ?, end_ts = ?, last_seen_ts = ?,
                confidence = COALESCE(?, confidence),
                snapshot_path = COALESCE(snapshot_path, ?),
                details_json = ?
            WHERE id = ?
            """,
            (
                new_identity,
                now,
                now,
                confidence,
                snapshot_path,
                json.dumps(merged_details, ensure_ascii=True),
                session_id,
            ),
        )
        self._conn.commit()

    def list_sessions(self, limit: int = 1000) -> list[Session]:
        limit = max(1, min(limit, 5000))
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY start_ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def record_event(
        self,
        event_type: str,
        *,
        track_id: int | None = None,
        person_name: str | None = None,
        confidence: float | None = None,
        behavior: str | None = None,
        details: dict[str, Any] | None = None,
        snapshot_path: str | None = None,
        ts: str | None = None,
    ) -> int:
        if person_name:
            person_name = self.resolve_identity(person_name)
        cur = self._conn.execute(
            """
            INSERT INTO events (
                ts, event_type, track_id, person_name, confidence,
                behavior, details_json, snapshot_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts or iso_now(),
                event_type,
                track_id,
                person_name,
                confidence,
                behavior,
                json.dumps(details or {}, ensure_ascii=True),
                snapshot_path,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_event(self, event_id: int) -> Event | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return self._row_to_event(row) if row else None

    def list_events(self, limit: int = 100, event_type: str | None = None) -> list[Event]:
        limit = max(1, min(limit, 1000))
        if event_type:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY ts DESC, id DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        total_sessions = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        latest = self._conn.execute(
            "SELECT end_ts AS ts FROM sessions ORDER BY end_ts DESC, id DESC LIMIT 1"
        ).fetchone()
        by_type_rows = self._conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM events
            GROUP BY event_type
            ORDER BY n DESC
            """
        ).fetchall()
        return {
            "total_events": total,
            "total_sessions": total_sessions,
            "latest_event_at": latest["ts"] if latest else None,
            "by_type": {row["event_type"]: row["n"] for row in by_type_rows},
        }

    def purge_old(self, snapshot_retention_days: int, log_retention_days: int) -> dict[str, int]:
        deleted_snapshots = self._purge_old_snapshots(snapshot_retention_days)
        deleted_logs = self._purge_old_logs(log_retention_days)
        self._conn.commit()
        return {"deleted_snapshots": deleted_snapshots, "deleted_logs": deleted_logs}

    def _purge_old_snapshots(self, retention_days: int) -> int:
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        rows = self._conn.execute(
            """
            SELECT id, snapshot_path
            FROM events
            WHERE snapshot_path IS NOT NULL AND ts < ?
            """,
            (cutoff,),
        ).fetchall()
        session_rows = self._conn.execute(
            """
            SELECT id, snapshot_path
            FROM sessions
            WHERE snapshot_path IS NOT NULL AND end_ts < ?
            """,
            (cutoff,),
        ).fetchall()
        deleted = 0
        for row in rows:
            if self._delete_snapshot_file(row["snapshot_path"]):
                deleted += 1
            self._conn.execute(
                "UPDATE events SET snapshot_path = NULL WHERE id = ?",
                (row["id"],),
            )
        for row in session_rows:
            if self._delete_snapshot_file(row["snapshot_path"]):
                deleted += 1
            self._conn.execute(
                "UPDATE sessions SET snapshot_path = NULL WHERE id = ?",
                (row["id"],),
            )

        for file_path in self.snapshot_dir.rglob("*.jpg"):
            if self._is_file_older_than(file_path, retention_days):
                file_path.unlink(missing_ok=True)
                deleted += 1
        return deleted

    def _purge_old_logs(self, retention_days: int) -> int:
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        session_cur = self._conn.execute("DELETE FROM sessions WHERE end_ts < ?", (cutoff,))
        return (cur.rowcount or 0) + (session_cur.rowcount or 0)

    def _delete_snapshot_file(self, snapshot_path: str) -> bool:
        file_path = (self.data_dir / snapshot_path).resolve()
        try:
            if not file_path.is_relative_to(self.snapshot_dir.resolve()):
                return False
        except ValueError:
            return False
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    @staticmethod
    def _is_file_older_than(file_path: Path, retention_days: int) -> bool:
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        return modified < utc_now() - timedelta(days=retention_days)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            ts=row["ts"],
            event_type=row["event_type"],
            track_id=row["track_id"],
            person_name=row["person_name"],
            confidence=row["confidence"],
            behavior=row["behavior"],
            details=json.loads(row["details_json"] or "{}"),
            snapshot_path=row["snapshot_path"],
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            identity_name=row["identity_name"],
            track_id=row["track_id"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            last_seen_ts=row["last_seen_ts"],
            confidence=row["confidence"],
            snapshot_path=row["snapshot_path"],
            details=json.loads(row["details_json"] or "{}"),
        )
