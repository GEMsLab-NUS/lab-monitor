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

Dashboard:

```text
http://127.0.0.1:8765
```

## Update

```powershell
cd lab-monitor
git pull
.\scripts\deploy-windows.ps1
```

## Dashboard Pages

- `Calendar`: month, week, and day session views.
- `Roster`: people list for renaming visitors and merging temporary visitor labels into existing identities.
- `Analytics`: occupied hours, unique identities, peak hours, top identities, identity mix, and dwell distribution.
- `Export`: filtered CSV and JSON session exports.
- `Settings`: editable basic parameters and work profiles. Saving settings writes `config.json` and requires a service restart.

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
