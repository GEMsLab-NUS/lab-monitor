from __future__ import annotations

from typing import Any

from .storage import EventStore, Session
from .vision import FaceService


def session_snapshot_has_face(store: EventStore, session: Session, face_service: FaceService) -> bool:
    if not session.snapshot_path:
        return False
    file_path = (store.data_dir / session.snapshot_path).resolve()
    try:
        if not file_path.is_relative_to(store.snapshot_dir.resolve()):
            return False
    except ValueError:
        return False
    frame = face_service.cv2.imread(str(file_path))
    if frame is None:
        return False
    return any(face_service.is_enrollable_face(frame, face) for face in face_service.detect_faces(frame))


def find_nonface_visitor_names(
    store: EventStore,
    unknown_prefix: str,
    face_service: FaceService,
) -> list[str]:
    aliases = store.list_identity_aliases()
    sessions_by_name: dict[str, list[Session]] = {}
    for session in store.list_sessions(5000):
        sessions_by_name.setdefault(store.resolve_identity(session.identity_name), []).append(session)
    names: list[str] = []
    for raw_name in store.list_identity_names():
        canonical = store.resolve_identity(raw_name)
        if canonical != raw_name or not canonical.startswith(unknown_prefix):
            continue
        if any(store.resolve_identity(new_name) == canonical for new_name in aliases.values()):
            continue
        person_sessions = sessions_by_name.get(canonical, [])
        if person_sessions and not any(session_snapshot_has_face(store, item, face_service) for item in person_sessions):
            names.append(canonical)
    return names


def cleanup_nonface_visitors(
    store: EventStore,
    unknown_prefix: str,
    face_service: FaceService,
) -> dict[str, Any]:
    names = find_nonface_visitor_names(store, unknown_prefix, face_service)
    result: dict[str, Any] = {
        "deleted_people": 0,
        "deleted_identities": 0,
        "deleted_sessions": 0,
        "deleted_snapshots": 0,
        "names": [],
    }
    for name in names:
        deleted = store.delete_identity(name)
        result["deleted_people"] += 1
        result["deleted_identities"] += int(deleted["deleted_identities"])
        result["deleted_sessions"] += int(deleted["deleted_sessions"])
        result["deleted_snapshots"] += int(deleted["deleted_snapshots"])
        result["names"].extend(str(item) for item in deleted["names"])
    return result
