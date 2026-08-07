# Configuration

The default config file is `config.json` in the project root.

Create it with:

```powershell
uv run lab-monitor --config config.json init
```

The dashboard `Settings` page can edit the common fields below. Saving from the dashboard writes `config.json` but does not hot-apply runtime settings; restart the background service after saving.

## Fields

| Field | Default | Description |
| --- | ---: | --- |
| `camera_index` | `0` | Camera device index. The built-in camera is usually `0`; external cameras may be `1` or higher. |
| `data_dir` | `data` | Local data directory for SQLite, snapshots, face models, and enrollment samples. |
| `snapshot_retention_days` | `7` | How long feature snapshots are kept. Expired snapshots are deleted; session rows remain until log retention expires. |
| `log_retention_days` | `180` | How long SQLite event/session records are kept. |
| `min_dwell_seconds` | `60` | Minimum continuous visibility before a person is logged as an occupancy session. |
| `session_merge_gap_minutes` | `15` | Maximum gap for merging adjacent sessions belonging to the same identity. |
| `unknown_identity_prefix` | `Visitor` | Prefix for auto-named unknown faces, such as `Visitor 001`. |
| `poll_interval_ms` | `350` | Camera polling interval. Lower is more responsive but uses more CPU. |
| `min_event_interval_seconds` | `20` | Internal event de-duplication interval. Raw events are not shown in the primary dashboard. |
| `presence_heartbeat_seconds` | `300` | Internal presence heartbeat interval. |
| `stationary_seconds` | `120` | Low-motion duration before internal stationary behavior is recorded. |
| `max_tracking_distance_px` | `120` | Maximum distance for matching person tracks between frames. |
| `web_host` | `127.0.0.1` | Dashboard bind address. Keep local unless network access is intentionally secured. |
| `web_port` | `8765` | Dashboard port. |
| `face_recognition_threshold` | `75.0` | OpenCV LBPH face-recognition distance threshold. Lower is stricter. |
| `face_soft_match_threshold` | `115.0` | Secondary LBPH threshold for stitching temporary visitor identities when strict recognition fails. Lower is stricter. |
| `min_face_size_px` | `52` | Minimum detected face width and height after frame resizing. Larger values reject distant or noisy face detections. |
| `min_face_area_ratio` | `0.006` | Minimum face-box area as a fraction of the processed frame. |
| `min_person_area_ratio` | `0.015` | Minimum person-box area as a fraction of the processed frame. |
| `max_person_area_ratio` | `0.70` | Maximum person-box area as a fraction of the processed frame. This rejects full-frame duplicate detections. |
| `unknown_face_label` | `unknown` | Legacy internal label for unknown-face events. |

## Work Profiles

The Settings page supports:

- `Balanced`: default behavior for fixed indoor cameras.
- `Responsive`: faster dwell confirmation and polling.
- `Conservative`: slower confirmation and stricter recognition behavior.

Profiles update related monitoring fields, but you can still override individual values before saving.
