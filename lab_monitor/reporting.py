from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from io import StringIO
import json
from typing import Any

from .storage import Session


@dataclass(slots=True)
class AnalyticsSummary:
    total_sessions: int
    occupied_hours: float
    unique_identities: int
    unknown_visitors: int
    peak_hour: int | None
    peak_hour_sessions: int
    daily_trend: list[dict[str, Any]]
    hourly_counts: list[dict[str, Any]]
    top_identities: list[dict[str, Any]]
    dwell_buckets: list[dict[str, Any]]
    identity_mix: dict[str, int]


def parse_date_range(params: dict[str, list[str]]) -> tuple[date | None, date | None]:
    return _parse_date(params.get("from", [""])[0]), _parse_date(params.get("to", [""])[0])


def filter_sessions(sessions: list[Session], start_date: date | None, end_date: date | None) -> list[Session]:
    if start_date is None and end_date is None:
        return sessions
    start_dt = datetime.combine(start_date, time.min).astimezone() if start_date else None
    end_dt = datetime.combine(end_date, time.max).astimezone() if end_date else None
    filtered: list[Session] = []
    for session in sessions:
        start = session_datetime(session.start_ts)
        end = session_datetime(session.end_ts)
        if start_dt and end < start_dt:
            continue
        if end_dt and start > end_dt:
            continue
        filtered.append(session)
    return filtered


def build_analytics(sessions: list[Session], unknown_prefix: str = "Visitor") -> AnalyticsSummary:
    total_seconds = sum(duration_seconds(session) for session in sessions)
    identities = {session.identity_name for session in sessions}
    unknown = {name for name in identities if name.startswith(unknown_prefix)}
    by_day: dict[date, float] = defaultdict(float)
    by_hour: dict[int, int] = defaultdict(int)
    by_identity: dict[str, dict[str, Any]] = defaultdict(lambda: {"sessions": 0, "seconds": 0.0})
    buckets = {
        "<15m": 0,
        "15-30m": 0,
        "30-60m": 0,
        "1-2h": 0,
        "2h+": 0,
    }

    for session in sessions:
        start = session_datetime(session.start_ts)
        seconds = duration_seconds(session)
        by_day[start.date()] += seconds
        by_hour[start.hour] += 1
        by_identity[session.identity_name]["sessions"] += 1
        by_identity[session.identity_name]["seconds"] += seconds
        minutes = seconds / 60
        if minutes < 15:
            buckets["<15m"] += 1
        elif minutes < 30:
            buckets["15-30m"] += 1
        elif minutes < 60:
            buckets["30-60m"] += 1
        elif minutes < 120:
            buckets["1-2h"] += 1
        else:
            buckets["2h+"] += 1

    peak_hour = None
    peak_count = 0
    if by_hour:
        peak_hour, peak_count = max(by_hour.items(), key=lambda item: item[1])

    return AnalyticsSummary(
        total_sessions=len(sessions),
        occupied_hours=round(total_seconds / 3600, 2),
        unique_identities=len(identities),
        unknown_visitors=len(unknown),
        peak_hour=peak_hour,
        peak_hour_sessions=peak_count,
        daily_trend=[
            {"date": day.isoformat(), "hours": round(seconds / 3600, 2)}
            for day, seconds in sorted(by_day.items())
        ],
        hourly_counts=[{"hour": hour, "sessions": by_hour.get(hour, 0)} for hour in range(24)],
        top_identities=[
            {
                "identity": name,
                "sessions": stats["sessions"],
                "hours": round(stats["seconds"] / 3600, 2),
            }
            for name, stats in sorted(
                by_identity.items(),
                key=lambda item: (item[1]["seconds"], item[1]["sessions"]),
                reverse=True,
            )[:8]
        ],
        dwell_buckets=[{"bucket": bucket, "sessions": count} for bucket, count in buckets.items()],
        identity_mix={
            "known": len(identities) - len(unknown),
            "visitors": len(unknown),
        },
    )


def sessions_to_csv(sessions: list[Session]) -> str:
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "identity",
            "start",
            "end",
            "duration_minutes",
            "confidence",
            "snapshot_url",
            "details",
        ],
    )
    writer.writeheader()
    for session in sessions:
        writer.writerow(
            {
                "id": session.id,
                "identity": session.identity_name,
                "start": session.start_ts,
                "end": session.end_ts,
                "duration_minutes": round(duration_seconds(session) / 60, 1),
                "confidence": "" if session.confidence is None else round(session.confidence, 2),
                "snapshot_url": f"/session-snapshot/{session.id}" if session.snapshot_path else "",
                "details": json.dumps(session.details, ensure_ascii=True),
            }
        )
    return output.getvalue()


def sessions_to_json_payload(
    sessions: list[Session],
    summary: AnalyticsSummary,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    return {
        "meta": {
            "from": start_date.isoformat() if start_date else None,
            "to": end_date.isoformat() if end_date else None,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": asdict(summary),
        "sessions": [
            {
                "id": session.id,
                "identity": session.identity_name,
                "start": session.start_ts,
                "end": session.end_ts,
                "duration_minutes": round(duration_seconds(session) / 60, 1),
                "confidence": session.confidence,
                "snapshot_url": f"/session-snapshot/{session.id}" if session.snapshot_path else None,
                "details": session.details,
            }
            for session in sessions
        ],
        "identities": sorted({session.identity_name for session in sessions}),
    }


def duration_seconds(session: Session) -> float:
    return max(0.0, (session_datetime(session.end_ts) - session_datetime(session.start_ts)).total_seconds())


def session_datetime(raw_ts: str) -> datetime:
    parsed = datetime.fromisoformat(raw_ts)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone()


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
