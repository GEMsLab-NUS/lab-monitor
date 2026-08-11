from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import signal
import sys

from .config import ensure_config_file, load_config
from .monitor import CameraMonitor
from .storage import EventStore, Session, duration_seconds_for_session
from .vision import FaceService
from .web import DashboardServer


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"lab-monitor: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-monitor")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create config.json if it does not exist.")
    init.set_defaults(func=cmd_init)

    run = sub.add_parser("run", help="Run camera monitor and local dashboard.")
    run.add_argument("--no-web", action="store_true", help="Disable dashboard.")
    run.set_defaults(func=cmd_run)

    web = sub.add_parser("web", help="Serve dashboard without opening the camera.")
    web.set_defaults(func=cmd_web)

    cleanup = sub.add_parser("cleanup", help="Apply log and snapshot retention now.")
    cleanup.set_defaults(func=cmd_cleanup)

    clean_roster = sub.add_parser("clean-roster", help="Remove invalid temporary visitor identities.")
    clean_roster.add_argument("--include-low-evidence", action="store_true", help="Also delete short visitor sessions without face evidence.")
    clean_roster.add_argument("--min-total-seconds", type=float, default=20.0, help="Low-evidence duration cutoff.")
    clean_roster.add_argument("--dry-run", action="store_true", help="Show what would be removed without deleting.")
    clean_roster.set_defaults(func=cmd_clean_roster)

    enroll = sub.add_parser("enroll", help="Enroll a known face.")
    enroll.add_argument("--name", required=True, help="Person name to enroll.")
    enroll.add_argument("--images", help="Folder containing enrollment images.")
    enroll.add_argument("--camera", action="store_true", help="Capture samples from camera.")
    enroll.add_argument("--samples", type=int, default=20, help="Camera samples to capture.")
    enroll.set_defaults(func=cmd_enroll)
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    target = ensure_config_file(args.config)
    print(f"Config ready: {target.resolve()}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    store = EventStore(config.database_path, config.data_path)
    monitor = CameraMonitor(config, store)
    server = None if args.no_web else DashboardServer(config, store, monitor, args.config)
    if server:
        server.start_background()
        print(f"Dashboard: {server.url}")
    print("Camera monitor running. Press Ctrl+C to stop.")

    stop_requested = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        monitor.stop()
        if server:
            server.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        if not stop_requested:
            monitor.run_forever()
    finally:
        if server:
            server.shutdown()
        store.close()
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    store = EventStore(config.database_path, config.data_path)
    server = DashboardServer(config, store, config_path=args.config)
    print(f"Dashboard: {server.url}")
    try:
        server.serve_forever()
    finally:
        store.close()
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    store = EventStore(config.database_path, config.data_path)
    result = store.purge_old(config.snapshot_retention_days, config.log_retention_days)
    store.close()
    print(result)
    return 0


def cmd_clean_roster(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    store = EventStore(config.database_path, config.data_path)
    face_service = FaceService(
        config.faces_path,
        config.enrolled_faces_path,
        config.face_recognition_threshold,
        min_face_size_px=config.min_face_size_px,
        min_face_area_ratio=config.min_face_area_ratio,
        min_sharpness=config.face_enrollment_min_sharpness,
        min_brightness=config.face_enrollment_min_brightness,
        max_brightness=config.face_enrollment_max_brightness,
    )
    if args.dry_run:
        result = preview_roster_cleanup(
            store,
            config.unknown_identity_prefix,
            include_low_evidence=args.include_low_evidence,
            min_total_seconds=args.min_total_seconds,
        )
    else:
        result = store.cleanup_roster(
            unknown_prefix=config.unknown_identity_prefix,
            include_low_evidence=args.include_low_evidence,
            min_total_seconds=args.min_total_seconds,
        )
        for name in result["names"]:
            face_service.delete_label(str(name))
    store.close()
    print(result)
    return 0


def preview_roster_cleanup(
    store: EventStore,
    unknown_prefix: str,
    *,
    include_low_evidence: bool,
    min_total_seconds: float,
) -> dict[str, object]:
    sessions = store.list_sessions(5000)
    aliases = store.list_identity_aliases()
    sessions_by_name: dict[str, list[Session]] = {}
    for session in sessions:
        sessions_by_name.setdefault(store.resolve_identity(session.identity_name), []).append(session)
    names: list[str] = []
    for raw_name in store.list_identity_names():
        canonical = store.resolve_identity(raw_name)
        if canonical != raw_name or not canonical.startswith(unknown_prefix):
            continue
        person_sessions = sessions_by_name.get(canonical, [])
        alias_count = sum(1 for new_name in aliases.values() if store.resolve_identity(new_name) == canonical)
        if not person_sessions and alias_count == 0:
            names.append(canonical)
            continue
        if include_low_evidence and alias_count == 0:
            total_seconds = sum(duration_seconds_for_session(item) for item in person_sessions)
            has_face_evidence = any(item.confidence is not None for item in person_sessions)
            if person_sessions and not has_face_evidence and total_seconds <= min_total_seconds:
                names.append(canonical)
    return {"dry_run": True, "deleted_people": len(names), "names": names}


def cmd_enroll(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    face_service = FaceService(
        config.faces_path,
        config.enrolled_faces_path,
        config.face_recognition_threshold,
        min_face_size_px=config.min_face_size_px,
        min_face_area_ratio=config.min_face_area_ratio,
        min_sharpness=config.face_enrollment_min_sharpness,
        min_brightness=config.face_enrollment_min_brightness,
        max_brightness=config.face_enrollment_max_brightness,
    )
    if args.images:
        image_dir = Path(args.images)
        if not image_dir.exists():
            raise FileNotFoundError(image_dir)
        image_paths = [
            path
            for path in image_dir.iterdir()
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        ]
        saved = face_service.enroll_from_images(args.name, image_paths)
    elif args.camera:
        saved = face_service.enroll_from_camera(args.name, config.camera_index, args.samples)
    else:
        raise ValueError("Use --images FOLDER or --camera")
    print(f"Enrolled {saved} face sample(s) for {args.name}")
    return 0 if saved else 2


def find_python() -> str:
    return shutil.which("python") or shutil.which("py") or sys.executable


if __name__ == "__main__":
    raise SystemExit(main())
