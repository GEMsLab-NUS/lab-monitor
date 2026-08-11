# Lab Monitor

Lab Monitor is a local camera-based occupancy monitor for lab usage logs. It records merged occupancy sessions and retained feature snapshots only. It does not write video files.

## Features

- Runs locally with `uv` and Python 3.12.
- Monitors a computer camera for people in frame.
- Creates an occupancy session only after a person remains visible for `min_dwell_seconds`.
- Merges adjacent sessions for the same identity within `session_merge_gap_minutes`.
- Auto-names unknown faces as `Visitor 001`, `Visitor 002`, and so on.
- Lets you rename or merge visitor identities from the `Roster` page.
- Provides a local English-only dashboard with `Calendar`, `Roster`, `Analytics`, `Export`, and `Settings`.
- Exports occupancy sessions as CSV or JSON.
- Saves cropped feature snapshots for sessions; no continuous video is stored.

## Install

One-command Windows deployment from GitHub:

```powershell
powershell -ExecutionPolicy Bypass -Command "git clone https://github.com/GEMsLab-NUS/lab-monitor.git; cd lab-monitor; .\scripts\deploy-windows.ps1"
```

If the repository is already cloned:

```powershell
cd lab-monitor
.\scripts\deploy-windows.ps1
```

The deployment script installs `uv` if it is missing, syncs Python dependencies, creates `config.json` if needed, and starts the background service.

Manual setup:

```powershell
git clone https://github.com/GEMsLab-NUS/lab-monitor.git
cd lab-monitor
uv sync
uv run lab-monitor --config config.json init
```

## Run

Foreground:

```powershell
.\scripts\run-foreground.ps1
```

Background:

```powershell
.\scripts\start-background.ps1
```

Stop background service:

```powershell
.\scripts\stop-background.ps1
```

Clean invalid roster/head records:

```cmd
clean-head-data.cmd
```

Dashboard:

```text
http://127.0.0.1:8765
```

## Update Existing Deployment

For normal updates on a computer that already has Lab Monitor deployed, run the CMD wrapper from the repository root:

```cmd
update-lab-monitor.cmd
```

The wrapper calls `scripts\update-windows.ps1`, which checks GitHub first. If there is no new commit, it prints an up-to-date message and exits without stopping the service, backing up the database, or redeploying.

When a new commit exists, the updater will:

1. Verify it is running inside the cloned repository.
2. Fetch the latest GitHub branch metadata.
3. Stop if tracked local code changes would be overwritten.
4. Stop the background service.
5. Back up `data\lab_monitor.sqlite3` to `data\backups\lab_monitor.YYYYMMDD-HHMMSS.sqlite3`, including SQLite WAL/SHM sidecar files when present.
6. Run `git pull --ff-only`.
7. Run `scripts\deploy-windows.ps1` to sync dependencies, keep or create `config.json`, and restart the service.

Equivalent PowerShell command:

```powershell
.\scripts\update-windows.ps1
```

Manual update:

```powershell
cd lab-monitor
git pull
.\scripts\deploy-windows.ps1
```

The update path is designed to keep existing local data:

- `data/` is ignored by git and is not touched by `git pull`.
- `config.json` is ignored by git and is not overwritten by `deploy-windows.ps1`.
- The SQLite database at `data/lab_monitor.sqlite3` is reused in place.
- New database tables and indexes are created automatically on startup when needed.
- New config fields are filled from application defaults if they are missing from an older `config.json`.

Recommended safe update with a database backup:

```powershell
cd lab-monitor
.\scripts\stop-background.ps1
Copy-Item .\data\lab_monitor.sqlite3 .\data\lab_monitor.backup.sqlite3
git pull
.\scripts\deploy-windows.ps1
```

To move an existing installation to another computer:

1. Clone the repository on the new computer.
2. Copy the old `data/` folder into the new repository root.
3. Copy the old `config.json` into the new repository root if you want the same camera and dashboard settings.
4. Run `.\scripts\deploy-windows.ps1`.

Do not commit or upload `data/` or `config.json`; they may contain face snapshots, face models, identity aliases, and local device settings.

If an old database already contains split visitor identities, use the `Roster` page after updating to merge temporary visitor names into the correct person. Adjacent sessions for the same identity are consolidated using `session_merge_gap_minutes`.

The root updater also ensures the monitor is running in background mode at the end of the command. If there is no GitHub update, it skips backup and redeploy work but still starts the background service when needed.

## Dashboard Pages

- `Calendar`: month, week, and day session views.
- `Roster`: paged card view for renaming, direct deletion, and merging temporary visitor labels into existing identities. It has separate tabs for unnamed visitors and named identities. Entries are sorted by face evidence so stronger face matches appear first.
- `Analytics`: occupied hours, unique identities, peak hours, top identities, identity mix, and dwell distribution.
- `Export`: filtered CSV and JSON session exports.
- `Settings`: editable basic parameters and work profiles. Saving settings writes `config.json` and requires a service restart.

## Roster Cleanup

Use the `Roster` page to manually remove invalid visitor/head records. The `Delete` button removes the roster entry, its sessions, aliases, retained snapshots, and any matching enrolled face label immediately.

For bulk cleanup on a deployed computer, run:

```cmd
clean-head-data.cmd
```

To preview the cleanup without changing data:

```powershell
.\scripts\clean-head-data.ps1 -DryRun
```

The cleanup command:

- Stops the background service.
- Backs up `data\lab_monitor.sqlite3` to `data\backups\lab_monitor.before-head-clean.YYYYMMDD-HHMMSS.sqlite3`.
- Removes orphan temporary visitors with no sessions.
- Removes very short temporary visitor sessions without face evidence.
- Removes temporary visitor records whose retained snapshots do not contain a verifiable face.
- Removes matching temporary face labels from the local face model.
- Restarts the background service.

The default low-evidence cutoff is 20 seconds. Known renamed people and visitor entries with face evidence are kept.

## Recognition Filtering

New installs use stricter defaults to reduce non-face visitor records:

- `face_recognition_threshold`: lower is stricter for LBPH matching.
- `face_soft_match_threshold`: lower is stricter for fallback matching.
- `min_face_size_px` and `min_face_area_ratio`: reject small detections and distant false positives.
- `min_unknown_face_observations`: require repeated high-quality unknown-face observations before creating a new visitor.
- `face_enrollment_min_sharpness`, `face_enrollment_min_brightness`, `face_enrollment_max_brightness`: reject blurred, too-dark, or overexposed crops.

Existing deployed machines keep their current `config.json`. To use the stricter profile after updating, open `Settings`, choose `Balanced` or `Conservative`, save, then restart with:

```powershell
.\scripts\stop-background.ps1
.\scripts\start-background.ps1
```

## Face Enrollment

From images:

```powershell
uv run lab-monitor --config config.json enroll --name Alice --images D:\face_samples\alice
```

From camera:

```powershell
uv run lab-monitor --config config.json enroll --name Alice --camera --samples 20
```

## Recording Rules

1. A detected person is first tracked as a candidate.
2. The person must remain visible for `min_dwell_seconds`.
3. After the dwell gate is reached, a session is created.
4. The session end time is updated while the person remains visible.
5. If the same identity returns within `session_merge_gap_minutes`, the session is merged.
6. Calendar and exports show sessions, not raw detector events.

## Data Layout

```text
data/
  lab_monitor.sqlite3
  snapshots/
  faces/
    labels.json
    lbph_model.yml
  enrolled_faces/
  logs/
```

Runtime data is intentionally excluded from git. `config.json` is also local-only; use `config.example.json` as the shared template.

## Privacy Notes

Face snapshots, face models, and occupancy logs are sensitive data. Deploy only in authorized areas with appropriate notice and retention rules. The default dashboard binds to `127.0.0.1`; add authentication and network controls before exposing it beyond the local machine.
