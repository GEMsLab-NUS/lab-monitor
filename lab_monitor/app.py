from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import signal
import sys

from .config import ensure_config_file, load_config
from .monitor import CameraMonitor
from .storage import EventStore
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


def cmd_enroll(args: argparse.Namespace) -> int:
    ensure_config_file(args.config)
    config = load_config(args.config)
    face_service = FaceService(
        config.faces_path,
        config.enrolled_faces_path,
        config.face_recognition_threshold,
        min_face_size_px=config.min_face_size_px,
        min_face_area_ratio=config.min_face_area_ratio,
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
