from __future__ import annotations

import ast
import calendar
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from threading import Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .config import (
    CONFIG_FIELDS,
    WORK_PROFILES,
    AppConfig,
    load_config,
    save_config_updates,
)
from .monitor import CameraMonitor
from .reporting import (
    AnalyticsSummary,
    build_analytics,
    duration_seconds,
    filter_sessions,
    parse_date_range,
    sessions_to_csv,
    sessions_to_json_payload,
)
from .storage import Event, EventStore, Session
from .vision import FaceService


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ROSTER_PAGE_SIZE = 24


@dataclass(slots=True)
class RosterPerson:
    name: str
    aliases: list[str]
    sessions: list[Session]
    total_seconds: float
    last_seen_ts: str | None
    snapshot_url: str | None
    best_confidence: float | None
    face_score: int


DASHBOARD_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f6f8;
  --surface: #ffffff;
  --surface-raised: #ffffff;
  --surface-subtle: #f8fafc;
  --text: #1f2328;
  --muted: #6b7280;
  --faint: #8a94a3;
  --line: #d9dee7;
  --line-soft: #eef1f5;
  --accent: #2563eb;
  --accent-soft: #eaf1ff;
  --success: #168a5b;
  --success-soft: #e8f6ef;
  --danger: #be123c;
  --danger-soft: #fff1f2;
  --session: #2563eb;
  --recognized: #168a5b;
  --unknown: #b45309;
  --shadow-sm: 0 1px 2px rgba(16, 24, 40, 0.08);
  --shadow-md: 0 16px 36px rgba(16, 24, 40, 0.10);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101317;
    --surface: #171b20;
    --surface-raised: #1d2229;
    --surface-subtle: #20262d;
    --text: #e7ecf2;
    --muted: #a7b0bd;
    --faint: #7e8997;
    --line: #303741;
    --line-soft: #252b33;
    --accent: #8ab4ff;
    --accent-soft: #1b2b45;
    --success: #7ad4a7;
    --success-soft: #153525;
    --danger: #f6a2b5;
    --danger-soft: #431924;
    --session: #8ab4ff;
    --recognized: #7ad4a7;
    --unknown: #f6c36b;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.32);
    --shadow-md: 0 18px 42px rgba(0, 0, 0, 0.34);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-width: 360px;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: inherit; }
.shell { min-height: 100vh; display: grid; grid-template-rows: 68px minmax(0, 1fr); }
.topbar {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 22px;
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(14px);
  position: sticky;
  top: 0;
  z-index: 10;
}
@media (prefers-color-scheme: dark) { .topbar { background: rgba(23, 27, 32, 0.92); } }
.brand { display: flex; align-items: center; gap: 12px; min-width: 238px; }
.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, #2563eb, #168a5b);
  box-shadow: var(--shadow-sm);
}
.brand h1 { margin: 0; font-size: 18px; font-weight: 650; }
.brand p { margin: 1px 0 0; color: var(--muted); font-size: 12px; }
.topnav { display: flex; align-items: center; gap: 4px; padding: 3px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-subtle); }
.topnav a {
  min-height: 30px;
  display: inline-flex;
  align-items: center;
  padding: 0 12px;
  border-radius: 6px;
  color: var(--muted);
  font-size: 13px;
  text-decoration: none;
}
.topnav .active { background: var(--surface); color: var(--text); box-shadow: var(--shadow-sm); font-weight: 650; }
.toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex: 1; min-width: 0; }
.toolbar-title { flex: 1; min-width: 190px; text-align: center; font-size: 21px; font-weight: 620; }
.button {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  text-decoration: none;
  white-space: nowrap;
  box-shadow: var(--shadow-sm);
}
.button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 650; }
.icon-button { width: 36px; padding: 0; font-size: 20px; box-shadow: none; }
.view-tabs { display: inline-flex; padding: 3px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-subtle); }
.view-tabs a {
  height: 30px;
  min-width: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: var(--muted);
  text-decoration: none;
  font-size: 13px;
}
.view-tabs .active { background: var(--surface); color: var(--text); box-shadow: var(--shadow-sm); font-weight: 650; }
.status-pill {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  color: var(--muted);
  white-space: nowrap;
}
.dot { width: 8px; height: 8px; border-radius: 999px; background: var(--success); box-shadow: 0 0 0 4px var(--success-soft); }
.dot.off { background: var(--danger); box-shadow: 0 0 0 4px var(--danger-soft); }
.layout { min-height: calc(100vh - 68px); display: grid; grid-template-columns: 328px minmax(0, 1fr); gap: 18px; padding: 18px; }
.layout.wide { grid-template-columns: 1fr; }
.sidebar { min-width: 0; display: grid; align-content: start; gap: 14px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow-sm); }
.panel-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 13px 14px; border-bottom: 1px solid var(--line-soft); }
.panel-title { margin: 0; font-size: 13px; font-weight: 700; }
.panel-note { color: var(--muted); font-size: 12px; }
.health-grid, .metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; background: var(--line-soft); }
.metric { min-height: 82px; background: var(--surface); padding: 13px; }
.metric span { color: var(--muted); font-size: 12px; }
.metric strong { display: block; margin-top: 6px; font-size: 22px; line-height: 1.1; font-weight: 720; }
.metric small { display: block; margin-top: 5px; color: var(--faint); font-size: 11px; }
.calendar-panel, .content-panel { min-width: 0; overflow: hidden; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow-md); }
.calendar-head, .content-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--line); background: var(--surface); }
.calendar-head h2, .content-head h2 { margin: 0; font-size: 15px; font-weight: 700; }
.calendar-subtitle, .content-subtitle { color: var(--muted); font-size: 12px; }
.content-body { display: grid; gap: 16px; padding: 16px; }
.live-shell { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
.live-stage {
  position: relative;
  min-height: min(66vh, 720px);
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #080b10;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.03);
}
.live-stage::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 2;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
  background-size: 34px 34px;
  opacity: .25;
}
.live-stage::after { content: ""; position: absolute; inset: 0; z-index: 3; pointer-events: none; background: linear-gradient(180deg, rgba(255,255,255,.04), transparent 34%, rgba(0,0,0,.24)); }
.live-frame { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; z-index: 1; }
.live-frame[hidden] { display: none; }
.live-empty { position: absolute; inset: 0; z-index: 4; display: grid; place-items: center; color: var(--muted); font-size: 14px; background: radial-gradient(circle at center, rgba(37,99,235,.12), transparent 42%); }
.live-empty[hidden] { display: none; }
.live-box {
  position: absolute;
  z-index: 5;
  border: 2px solid var(--unknown);
  border-radius: 7px;
  box-shadow: 0 0 0 1px rgba(0,0,0,.3), 0 0 24px rgba(180,83,9,.16);
  transition: left .18s ease, top .18s ease, width .18s ease, height .18s ease, border-color .18s ease;
}
.live-box.confirmed { border-color: var(--success); box-shadow: 0 0 0 1px rgba(0,0,0,.3), 0 0 24px rgba(22,138,91,.22); }
.live-box.face { border-color: var(--accent); border-style: dashed; box-shadow: 0 0 0 1px rgba(0,0,0,.2); }
.live-label { position: absolute; left: 8px; top: -28px; max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 5px 8px; border-radius: 999px; background: rgba(17, 24, 39, .78); color: #fff; font-size: 12px; font-weight: 750; backdrop-filter: blur(8px); }
.live-pulse { position: absolute; right: 18px; top: 18px; z-index: 6; width: 10px; height: 10px; border-radius: 999px; background: var(--success); box-shadow: 0 0 0 0 rgba(22,138,91,.55); animation: livePulse 1.4s infinite; }
@keyframes livePulse { 70% { box-shadow: 0 0 0 14px rgba(22,138,91,0); } 100% { box-shadow: 0 0 0 0 rgba(22,138,91,0); } }
.live-side { display: grid; gap: 12px; }
.live-card { border: 1px solid var(--line-soft); border-radius: 8px; background: var(--surface-raised); padding: 13px; box-shadow: var(--shadow-sm); }
.live-card h3 { margin: 0 0 10px; font-size: 13px; color: var(--muted); font-weight: 720; }
.live-row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-top: 1px solid var(--line-soft); font-size: 12px; }
.live-row:first-of-type { border-top: 0; }
.live-row span { color: var(--muted); }
.live-row strong { color: var(--text); text-align: right; }
.live-track-list { display: grid; gap: 8px; max-height: 330px; overflow: auto; }
.live-track { display: grid; gap: 7px; padding: 10px; border: 1px solid var(--line-soft); border-radius: 8px; background: var(--surface-subtle); }
.live-track-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; }
.live-track-top strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-progress { height: 6px; overflow: hidden; border-radius: 999px; background: var(--line-soft); }
.mini-progress span { display: block; height: 100%; width: 0%; border-radius: inherit; background: var(--success); transition: width .25s ease; }
.settings-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
.settings-main { display: grid; gap: 14px; }
.settings-side { display: grid; gap: 14px; }
.settings-side .panel { position: sticky; top: 86px; }
.settings-advanced { border: 1px solid var(--line-soft); border-radius: 8px; background: var(--surface); }
.settings-advanced summary { cursor: pointer; padding: 13px 14px; color: var(--text); font-size: 13px; font-weight: 700; }
.settings-advanced .panel { border: 0; border-top: 1px solid var(--line-soft); border-radius: 0; box-shadow: none; }
.calendar-scroll { overflow: auto; max-height: calc(100vh - 156px); }
.month-grid { min-width: 860px; display: grid; grid-template-columns: repeat(7, minmax(120px, 1fr)); border-left: 1px solid var(--line-soft); }
.weekday {
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: sticky;
  top: 0;
  z-index: 2;
  color: var(--muted);
  background: var(--surface-subtle);
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line);
  font-size: 12px;
  font-weight: 700;
}
.day-cell { min-height: 136px; padding: 8px; border-right: 1px solid var(--line-soft); border-bottom: 1px solid var(--line-soft); background: var(--surface); }
.day-cell.outside { background: var(--surface-subtle); }
.day-cell.selected { box-shadow: inset 0 0 0 2px var(--accent); }
.day-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.day-number {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: var(--text);
  font-size: 12px;
  text-decoration: none;
}
.today-number { background: var(--accent); color: #fff; font-weight: 750; }
.event-count { color: var(--faint); font-size: 11px; }
.time-grid { min-width: 1020px; display: grid; grid-template-columns: 72px repeat(7, minmax(126px, 1fr)); border-left: 1px solid var(--line-soft); }
.time-grid.day { min-width: 560px; grid-template-columns: 72px minmax(420px, 1fr); }
.time-head, .time-label, .time-slot { border-right: 1px solid var(--line-soft); border-bottom: 1px solid var(--line-soft); }
.time-head { min-height: 58px; display: grid; place-items: center; position: sticky; top: 0; z-index: 3; background: var(--surface-subtle); color: var(--muted); font-size: 12px; font-weight: 650; }
.time-head strong { width: 30px; height: 30px; display: grid; place-items: center; margin-top: 3px; border-radius: 999px; color: var(--text); font-size: 15px; }
.time-label { min-height: 72px; padding: 9px 8px; position: sticky; left: 0; z-index: 2; background: var(--surface-subtle); color: var(--faint); font-size: 12px; text-align: right; }
.time-slot { min-height: 72px; padding: 5px; background: var(--surface); }
.time-slot.current-hour { background: linear-gradient(180deg, var(--accent-soft), var(--surface) 46%); }
.session-chip {
  display: grid;
  grid-template-columns: 5px minmax(0, 1fr);
  gap: 7px;
  min-height: 34px;
  margin: 4px 0;
  padding: 6px 7px 6px 0;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface-raised);
  color: var(--text);
  box-shadow: var(--shadow-sm);
  text-decoration: none;
  cursor: pointer;
}
.session-chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.session-chip::before { content: ""; border-radius: 7px 0 0 7px; background: var(--session); }
.session-chip.known::before { background: var(--recognized); }
.session-chip.visitor::before { background: var(--unknown); }
.session-main { min-width: 0; }
.session-title { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 650; }
.session-time { display: block; margin-top: 1px; color: var(--muted); font-size: 11px; }
.session-popover {
  position: fixed;
  z-index: 50;
  width: min(286px, calc(100vw - 24px));
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-raised);
  box-shadow: var(--shadow-md);
}
.session-popover-media { display: block; width: 100%; height: 132px; object-fit: cover; border-bottom: 1px solid var(--line-soft); background: var(--surface-subtle); }
.session-popover-body { display: grid; gap: 9px; padding: 12px; }
.session-popover-title { margin: 0; font-size: 14px; line-height: 1.25; font-weight: 750; }
.session-popover-meta { display: grid; gap: 7px; }
.session-popover-row { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; }
.session-popover-row strong { color: var(--text); font-weight: 650; text-align: right; }
.empty { padding: 16px 14px; color: var(--muted); font-size: 13px; }
.dashboard-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.chart-list { display: grid; gap: 9px; padding: 14px; }
.bar-row { display: grid; grid-template-columns: 90px minmax(0, 1fr) 58px; gap: 10px; align-items: center; color: var(--muted); font-size: 12px; }
.bar-track { height: 10px; overflow: hidden; border-radius: 999px; background: var(--surface-subtle); border: 1px solid var(--line-soft); }
.bar-fill { height: 100%; min-width: 2px; border-radius: 999px; background: var(--accent); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 14px; }
.field { display: grid; gap: 6px; }
.field label { color: var(--muted); font-size: 12px; font-weight: 650; }
.field input, .field select {
  width: 100%;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-subtle);
  color: var(--text);
}
.form-actions { display: flex; gap: 10px; align-items: center; padding: 14px; border-top: 1px solid var(--line-soft); }
.notice { padding: 10px 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface-subtle); color: var(--muted); }
.notice.success { border-color: var(--success); color: var(--success); background: var(--success-soft); }
.notice.error { border-color: var(--danger); color: var(--danger); background: var(--danger-soft); }
.maintenance-progress { display: grid; gap: 8px; min-width: 260px; flex: 1; }
.maintenance-progress[hidden] { display: none; }
.progress-meta { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; }
.progress-track { height: 9px; overflow: hidden; border-radius: 999px; border: 1px solid var(--line-soft); background: var(--surface-subtle); }
.progress-fill { width: 0%; height: 100%; border-radius: inherit; background: var(--success); transition: width .25s ease; }
.roster-tabs { display: inline-flex; gap: 4px; padding: 3px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-subtle); }
.roster-tabs a { display: inline-flex; align-items: center; gap: 8px; min-height: 34px; padding: 0 12px; border-radius: 7px; color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 650; }
.roster-tabs a.active { color: var(--text); background: var(--surface); box-shadow: var(--shadow-sm); }
.roster-tabs span { color: var(--faint); font-size: 12px; font-weight: 700; }
.roster-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; }
.roster-card {
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-raised);
  box-shadow: var(--shadow-sm);
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
}
.roster-card:hover { transform: translateY(-2px); border-color: var(--line); box-shadow: var(--shadow-md); }
.roster-card.removing { transform: scale(.98); opacity: 0; pointer-events: none; }
.roster-media { position: relative; aspect-ratio: 4 / 3; overflow: hidden; background: var(--surface-subtle); border-bottom: 1px solid var(--line-soft); }
.roster-photo { width: 100%; height: 100%; object-fit: cover; display: block; }
.roster-avatar { width: 100%; height: 100%; display: grid; place-items: center; background: linear-gradient(135deg, var(--accent-soft), var(--surface-subtle)); color: var(--accent); font-size: 44px; font-weight: 800; }
.roster-score { position: absolute; left: 10px; bottom: 10px; padding: 5px 8px; border-radius: 999px; background: rgba(17, 24, 39, 0.72); color: #fff; font-size: 12px; font-weight: 750; backdrop-filter: blur(8px); }
.roster-card-body { display: grid; gap: 12px; padding: 12px; }
.roster-name { min-height: 20px; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 15px; font-weight: 760; }
.roster-aliases { display: flex; flex-wrap: wrap; gap: 5px; min-height: 24px; }
.alias-chip { max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 3px 7px; border: 1px solid var(--line-soft); border-radius: 999px; color: var(--muted); background: var(--surface-subtle); font-size: 11px; }
.roster-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--line-soft); border-radius: 8px; background: var(--line-soft); }
.roster-stat { min-width: 0; padding: 8px; background: var(--surface); }
.roster-stat span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 11px; }
.roster-stat strong { display: block; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-size: 13px; font-weight: 720; }
.roster-form { display: grid; grid-template-columns: minmax(0, 1fr) 78px; gap: 8px; }
.roster-form input { min-width: 0; min-height: 36px; padding: 0 10px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface-subtle); color: var(--text); }
.roster-form button { min-height: 36px; border: 1px solid var(--line); border-radius: 7px; background: var(--accent); color: #fff; font-weight: 650; }
.roster-delete-form button { width: 100%; min-height: 34px; border: 1px solid var(--danger); border-radius: 7px; background: var(--danger-soft); color: var(--danger); font-weight: 650; cursor: pointer; }
.roster-delete-form button:disabled { cursor: progress; opacity: .65; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-top: 4px; color: var(--muted); font-size: 13px; }
.pager-links { display: flex; gap: 6px; flex-wrap: wrap; }
.pager-links a, .pager-links span { min-width: 34px; min-height: 32px; display: inline-grid; place-items: center; padding: 0 10px; border: 1px solid var(--line); border-radius: 7px; color: var(--muted); text-decoration: none; background: var(--surface); }
.pager-links .active { color: #fff; border-color: var(--accent); background: var(--accent); }
@media (max-width: 1080px) {
  .topbar { height: auto; min-height: 68px; align-items: stretch; flex-direction: column; padding: 12px; }
  .brand { min-width: 0; }
  .toolbar, .topnav { flex-wrap: wrap; }
  .toolbar-title { order: -1; flex-basis: 100%; text-align: left; }
  .layout, .dashboard-grid, .form-grid { grid-template-columns: 1fr; padding: 12px; }
  .live-shell, .settings-layout { grid-template-columns: 1fr; }
  .live-stage { min-height: 420px; }
  .settings-side .panel { position: static; }
  .roster-grid { grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }
  .calendar-scroll { max-height: none; }
}
@media (max-width: 620px) {
  .health-grid, .metric-grid, .field-grid { grid-template-columns: 1fr; }
  .live-stage { min-height: 300px; }
  .status-pill { width: 100%; justify-content: center; }
  .roster-grid { grid-template-columns: 1fr; }
  .roster-tabs { width: 100%; }
  .roster-tabs a { flex: 1; justify-content: center; }
}
"""


DASHBOARD_JS = """
(() => {
  let popover = null;
  let activeChip = null;
  let closeTimer = null;

  const clearCloseTimer = () => {
    if (closeTimer) {
      window.clearTimeout(closeTimer);
      closeTimer = null;
    }
  };

  const closePopover = () => {
    clearCloseTimer();
    if (popover) {
      popover.remove();
      popover = null;
    }
    if (activeChip) {
      activeChip.removeAttribute('aria-expanded');
      activeChip = null;
    }
  };

  const scheduleClose = () => {
    clearCloseTimer();
    closeTimer = window.setTimeout(() => {
      const overChip = activeChip && activeChip.matches(':hover');
      const overPopover = popover && popover.matches(':hover');
      if (!overChip && !overPopover) closePopover();
    }, 90);
  };

  const addText = (parent, className, text) => {
    const element = document.createElement('span');
    element.className = className;
    element.textContent = text || '-';
    parent.appendChild(element);
    return element;
  };

  const addRow = (parent, label, value) => {
    const row = document.createElement('div');
    row.className = 'session-popover-row';
    addText(row, '', label);
    const strong = document.createElement('strong');
    strong.textContent = value || '-';
    row.appendChild(strong);
    parent.appendChild(row);
  };

  const positionPopover = (clientX, clientY, chip) => {
    const rect = chip.getBoundingClientRect();
    const x = Number.isFinite(clientX) && clientX > 0 ? clientX : rect.left + rect.width / 2;
    const y = Number.isFinite(clientY) && clientY > 0 ? clientY : rect.bottom;
    const margin = 12;
    const width = popover.offsetWidth || 286;
    const height = popover.offsetHeight || 220;
    let left = x + 12;
    let top = y + 12;
    if (left + width > window.innerWidth - margin) left = x - width - 12;
    if (top + height > window.innerHeight - margin) top = y - height - 12;
    popover.style.left = `${Math.max(margin, left)}px`;
    popover.style.top = `${Math.max(margin, top)}px`;
  };

  const showPopover = (chip, event) => {
    closePopover();
    activeChip = chip;
    chip.setAttribute('aria-expanded', 'true');
    popover = document.createElement('aside');
    popover.className = 'session-popover';
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Session details');

    const snapshot = chip.dataset.snapshot || '';
    if (snapshot && snapshot !== '#') {
      const img = document.createElement('img');
      img.className = 'session-popover-media';
      img.src = snapshot;
      img.alt = `${chip.dataset.identity || 'Session'} snapshot`;
      popover.appendChild(img);
    }

    const body = document.createElement('div');
    body.className = 'session-popover-body';
    const title = document.createElement('h3');
    title.className = 'session-popover-title';
    title.textContent = chip.dataset.identity || 'Session';
    body.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'session-popover-meta';
    addRow(meta, 'Time', chip.dataset.time);
    addRow(meta, 'Duration', chip.dataset.duration);
    addRow(meta, 'Confidence', chip.dataset.confidence);
    body.appendChild(meta);
    popover.appendChild(body);

    popover.addEventListener('mouseenter', clearCloseTimer);
    popover.addEventListener('mouseleave', scheduleClose);
    document.body.appendChild(popover);
    positionPopover(event.clientX, event.clientY, chip);
  };

  const focusCurrentHour = () => {
    const scroller = document.querySelector('.calendar-scroll');
    const current = document.querySelector('.current-hour');
    if (!scroller || !current || window.location.hash) return;
    const scrollerTop = scroller.getBoundingClientRect().top;
    const currentTop = current.getBoundingClientRect().top;
    scroller.scrollTop += currentTop - scrollerTop - 180;
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', focusCurrentHour, { once: true });
  } else {
    focusCurrentHour();
  }

  const showRosterNotice = (text, type = 'success') => {
    const body = document.querySelector('.content-body');
    if (!body) return;
    let notice = body.querySelector('.notice');
    if (!notice) {
      notice = document.createElement('div');
      body.prepend(notice);
    }
    notice.className = `notice ${type}`;
    notice.textContent = text;
  };

  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('.roster-delete-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button');
    const card = form.closest('.roster-card');
    if (button) button.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'fetch'
        }
      });
      if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
      card?.classList.add('removing');
      window.setTimeout(() => {
        card?.remove();
        const remaining = document.querySelectorAll('.roster-card').length;
        if (!remaining) window.location.reload();
      }, 180);
      showRosterNotice('Roster entry deleted.');
    } catch (error) {
      form.submit();
    }
  });

  const updateMaintenanceProgress = (state) => {
    const panel = document.querySelector('[data-maintenance-progress]');
    if (!panel) return;
    const progress = state.progress || {};
    const running = Boolean(state.running);
    const result = state.last_result;
    const error = state.last_error;
    const percent = Number(progress.percent || 0);
    const phase = error
      ? `Failed: ${error}`
      : running
        ? (progress.phase || 'Running cleanup')
        : result
          ? `Complete: removed ${result.deleted_people || 0} visitor record(s)`
          : (progress.phase || 'Idle');
    panel.hidden = !(running || result || error);
    panel.querySelector('[data-maintenance-phase]').textContent = phase;
    panel.querySelector('[data-maintenance-count]').textContent = progress.total
      ? `${progress.current || 0}/${progress.total}`
      : '';
    panel.querySelector('[data-maintenance-fill]').style.width = `${running ? percent : (result ? 100 : percent)}%`;
    const button = document.querySelector('[data-maintenance-submit]');
    if (button) button.disabled = running;
    return running;
  };

  const pollMaintenance = async () => {
    try {
      const response = await fetch('/api/maintenance/status', { headers: { 'Accept': 'application/json' } });
      if (!response.ok) return false;
      const state = await response.json();
      return updateMaintenanceProgress(state);
    } catch (error) {
      return false;
    }
  };

  const scheduleMaintenancePoll = () => {
    const tick = async () => {
      const running = await pollMaintenance();
      if (running) window.setTimeout(tick, 1000);
    };
    tick();
  };

  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('.maintenance-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button');
    if (button) button.disabled = true;
    updateMaintenanceProgress({ running: true, progress: { phase: 'Starting cleanup', current: 0, total: 1, percent: 0 } });
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'fetch'
        }
      });
      if (!response.ok) throw new Error(`Cleanup failed: ${response.status}`);
      scheduleMaintenancePoll();
      showRosterNotice('Roster cleanup started in the background.');
    } catch (error) {
      form.submit();
    }
  });

  if (document.querySelector('[data-maintenance-progress]')) {
    scheduleMaintenancePoll();
  }

  const liveStage = document.querySelector('[data-live-stage]');
  const liveFrame = document.querySelector('[data-live-frame]');
  const liveEmpty = document.querySelector('[data-live-empty]');
  let lastLivePayload = null;

  const setText = (selector, text) => {
    const element = document.querySelector(selector);
    if (element) element.textContent = text;
  };

  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const boxPlacement = (bbox, frame, stageRect) => {
    if (!frame || !frame.width || !frame.height) return null;
    const [x, y, width, height] = bbox || [];
    if (![x, y, width, height].every(Number.isFinite)) return null;
    const scale = Math.min(stageRect.width / frame.width, stageRect.height / frame.height);
    const displayedWidth = frame.width * scale;
    const displayedHeight = frame.height * scale;
    const offsetX = (stageRect.width - displayedWidth) / 2;
    const offsetY = (stageRect.height - displayedHeight) / 2;
    return {
      left: offsetX + x * scale,
      top: offsetY + y * scale,
      width: width * scale,
      height: height * scale
    };
  };

  const drawLiveBoxes = (payload) => {
    if (!liveStage) return;
    liveStage.querySelectorAll('.live-box').forEach((box) => box.remove());
    const frame = payload?.frame;
    if (!frame) return;
    const rect = liveStage.getBoundingClientRect();
    const addBox = (item, className, label) => {
      const placement = boxPlacement(item.bbox, frame, rect);
      if (!placement || placement.width < 8 || placement.height < 8) return;
      const box = document.createElement('div');
      box.className = `live-box ${className}`.trim();
      box.style.left = `${placement.left}px`;
      box.style.top = `${placement.top}px`;
      box.style.width = `${placement.width}px`;
      box.style.height = `${placement.height}px`;
      if (label) {
        const badge = document.createElement('span');
        badge.className = 'live-label';
        badge.textContent = label;
        box.appendChild(badge);
      }
      liveStage.appendChild(box);
    };
    (payload.tracks || []).forEach((track) => {
      const progress = Math.round(Number(track.dwell_progress || 0) * 100);
      addBox(track, track.confirmed ? 'confirmed' : '', `${track.identity || 'Candidate'} ${progress}%`);
    });
    (payload.faces || []).forEach((face) => addBox(face, 'face', 'Face'));
  };

  const renderLiveTracks = (tracks) => {
    const list = document.querySelector('[data-live-track-list]');
    if (!list) return;
    if (!tracks || !tracks.length) {
      list.innerHTML = '<div class="empty">No active candidates.</div>';
      return;
    }
    list.innerHTML = tracks.map((track) => {
      const progress = Math.max(0, Math.min(100, Math.round(Number(track.dwell_progress || 0) * 100)));
      const state = track.confirmed ? 'Confirmed' : 'Dwell';
      return `
        <div class="live-track">
          <div class="live-track-top"><strong>${escapeHtml(track.identity || 'Candidate')}</strong><span>${state}</span></div>
          <div class="mini-progress"><span style="width:${progress}%"></span></div>
          <div class="live-row"><span>Visible</span><strong>${Number(track.visible_seconds || 0).toFixed(1)}s</strong></div>
        </div>
      `;
    }).join('');
  };

  const updateLivePage = (payload) => {
    lastLivePayload = payload;
    setText('[data-live-running]', payload.running ? 'Running' : 'Stopped');
    setText('[data-live-camera]', payload.camera_open ? 'Open' : 'Closed');
    setText('[data-live-tracks-count]', String(payload.active_tracks || 0));
    setText('[data-live-faces-count]', String((payload.faces || []).length));
    setText('[data-live-updated]', payload.has_frame ? new Date().toLocaleTimeString() : 'Waiting');
    setText('[data-live-error]', payload.last_error || 'None');
    renderLiveTracks(payload.tracks || []);
    if (payload.has_frame && liveFrame) {
      liveFrame.src = `/api/live/frame?t=${Date.now()}`;
    } else {
      if (liveFrame) liveFrame.hidden = true;
      if (liveEmpty) liveEmpty.hidden = false;
      drawLiveBoxes(payload);
    }
  };

  const pollLive = async () => {
    if (!liveStage) return;
    try {
      const response = await fetch('/api/live/status', { cache: 'no-store', headers: { 'Accept': 'application/json' } });
      if (response.ok) updateLivePage(await response.json());
    } catch (error) {
      setText('[data-live-running]', 'Disconnected');
    } finally {
      window.setTimeout(pollLive, 850);
    }
  };

  if (liveFrame) {
    liveFrame.addEventListener('load', () => {
      liveFrame.hidden = false;
      if (liveEmpty) liveEmpty.hidden = true;
      drawLiveBoxes(lastLivePayload);
    });
  }
  if (liveStage) {
    window.addEventListener('resize', () => drawLiveBoxes(lastLivePayload));
    pollLive();
  }

  document.addEventListener('click', (event) => {
    const chip = event.target.closest('.session-chip[data-session-id]');
    if (!chip) {
      if (popover && !event.target.closest('.session-popover')) closePopover();
      return;
    }
    event.preventDefault();
    showPopover(chip, event);
  });

  document.addEventListener('mouseout', (event) => {
    const chip = event.target.closest('.session-chip[data-session-id]');
    if (!chip || chip.contains(event.relatedTarget)) return;
    scheduleClose();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePopover();
  });
})();
"""


class DashboardServer:
    def __init__(
        self,
        config: AppConfig,
        store: EventStore,
        monitor: CameraMonitor | None = None,
        config_path: str = "config.json",
    ) -> None:
        self.config = config
        self.store = store
        self.monitor = monitor
        self.config_path = config_path
        self._maintenance_lock = Lock()
        self._maintenance_state: dict[str, Any] = {
            "running": False,
            "last_result": None,
            "last_error": None,
            "progress_file": None,
        }
        self._server = self._build_server()
        self._thread: Thread | None = None
        self._started = False

    @property
    def url(self) -> str:
        return f"http://{self.config.web_host}:{self.config.web_port}"

    def start_background(self) -> None:
        self._thread = Thread(target=self._server.serve_forever, name="lab-monitor-web", daemon=True)
        self._started = True
        self._thread.start()

    def serve_forever(self) -> None:
        self._started = True
        self._server.serve_forever()

    def shutdown(self) -> None:
        if self._started:
            self._server.shutdown()
        self._server.server_close()

    def _build_server(self) -> ThreadingHTTPServer:
        store = self.store
        runtime_config = self.config
        monitor = self.monitor
        config_path = self.config_path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path in {"/", "/calendar"}:
                    self._send_html(render_calendar_page(store, runtime_config, monitor, query))
                    return
                if parsed.path == "/live":
                    self._send_html(render_live_page(runtime_config, monitor))
                    return
                if parsed.path == "/analytics":
                    self._send_html(render_analytics_page(store, runtime_config, monitor, query))
                    return
                if parsed.path == "/roster":
                    self._send_html(render_roster_page(store, runtime_config, monitor, query))
                    return
                if parsed.path == "/export":
                    self._send_html(render_export_page(store, runtime_config, monitor, query))
                    return
                if parsed.path == "/settings":
                    settings_config = load_config(config_path)
                    self._send_html(
                        render_settings_page(
                            settings_config,
                            runtime_config,
                            monitor,
                            query,
                            self._maintenance_snapshot(),
                        )
                    )
                    return
                if parsed.path == "/api/sessions":
                    limit = int(query.get("limit", ["1000"])[0])
                    self._send_json([session_to_dict(session) for session in store.list_sessions(limit)])
                    return
                if parsed.path == "/api/events":
                    limit = int(query.get("limit", ["100"])[0])
                    self._send_json([event_to_dict(event) for event in store.list_events(limit)])
                    return
                if parsed.path == "/api/stats":
                    self._send_json(build_stats_payload(store, monitor))
                    return
                if parsed.path == "/api/live/status":
                    self._send_json(build_live_payload(monitor))
                    return
                if parsed.path == "/api/live/frame":
                    self._send_live_frame(monitor)
                    return
                if parsed.path == "/api/maintenance/status":
                    self._send_json(self._maintenance_snapshot())
                    return
                if parsed.path == "/api/export/sessions.csv":
                    start, end = parse_date_range(query)
                    sessions = filter_sessions(store.list_sessions(5000), start, end)
                    self._send_download(
                        sessions_to_csv(sessions),
                        "text/csv; charset=utf-8",
                        "lab-monitor-sessions.csv",
                    )
                    return
                if parsed.path == "/api/export/sessions.json":
                    start, end = parse_date_range(query)
                    sessions = filter_sessions(store.list_sessions(5000), start, end)
                    summary = build_analytics(sessions, runtime_config.unknown_identity_prefix)
                    self._send_download(
                        json.dumps(
                            sessions_to_json_payload(sessions, summary, start, end),
                            indent=2,
                            ensure_ascii=True,
                        )
                        + "\n",
                        "application/json; charset=utf-8",
                        "lab-monitor-sessions.json",
                    )
                    return
                if parsed.path.startswith("/session-snapshot/"):
                    raw_id = parsed.path.rsplit("/", 1)[-1]
                    if raw_id.isdigit():
                        self._send_session_snapshot(int(raw_id), store)
                        return
                if parsed.path.startswith("/snapshot/"):
                    raw_id = parsed.path.rsplit("/", 1)[-1]
                    if raw_id.isdigit():
                        self._send_event_snapshot(int(raw_id), store)
                        return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                data = parse_qs(body)
                if parsed.path == "/api/identity/rename":
                    old_name = data.get("old_name", [""])[0].strip()
                    new_name = data.get("new_name", [""])[0].strip()
                    view = data.get("view", ["week"])[0]
                    selected = data.get("date", [date.today().isoformat()])[0]
                    return_to = data.get("return_to", [""])[0].strip()
                    if old_name and new_name and old_name != new_name:
                        store.rename_identity(
                            old_name,
                            new_name,
                            merge_gap_minutes=runtime_config.session_merge_gap_minutes,
                        )
                        if monitor is not None:
                            monitor.rename_identity(old_name, new_name)
                        else:
                            create_face_service(runtime_config).rename_label(
                                old_name,
                                new_name,
                                max_samples=runtime_config.max_face_samples_per_identity,
                            )
                    if return_to.startswith("/") and not return_to.startswith("//"):
                        self._redirect(return_to)
                    else:
                        self._redirect(f"/calendar?{urlencode({'view': view, 'date': selected})}")
                    return
                if parsed.path == "/api/identity/delete":
                    name = data.get("name", [""])[0].strip()
                    return_to = data.get("return_to", ["/roster?deleted=1"])[0].strip()
                    delete_result: dict[str, Any] = {"names": []}
                    if name:
                        delete_result = store.delete_identity(name)
                        names_to_delete = delete_result.get("names", [name])
                        if monitor is not None:
                            for item in names_to_delete:
                                monitor.delete_identity(str(item))
                        else:
                            face_service = create_face_service(runtime_config)
                            for item in names_to_delete:
                                face_service.delete_label(str(item))
                    if self._wants_json():
                        self._send_json({"ok": True, "deleted": delete_result})
                        return
                    if return_to.startswith("/") and not return_to.startswith("//"):
                        self._redirect(return_to)
                    else:
                        self._redirect("/roster?deleted=1")
                    return
                if parsed.path == "/api/settings":
                    flat = {key: values[0] for key, values in data.items()}
                    profile = flat.pop("profile", "")
                    try:
                        save_config_updates(config_path, flat, profile if profile else None)
                    except ValueError as exc:
                        self._redirect(f"/settings?{urlencode({'error': str(exc)})}")
                        return
                    self._redirect("/settings?saved=1")
                    return
                if parsed.path == "/api/maintenance/cleanup-nonface-visitors":
                    started = self._start_nonface_cleanup()
                    status = "started" if started else "running"
                    if self._wants_json():
                        self._send_json({"ok": True, "status": status, "maintenance": self._maintenance_snapshot()})
                        return
                    self._redirect(f"/settings?{urlencode({'maintenance': status})}")
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def log_message(self, format: str, *args: object) -> None:
                return

            def _redirect(self, location: str) -> None:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", location)
                self.end_headers()

            def _send_html(self, body: str) -> None:
                self._send_text(body, "text/html; charset=utf-8")

            def _send_json(self, payload: Any) -> None:
                self._send_text(json.dumps(payload, ensure_ascii=True), "application/json; charset=utf-8")

            def _wants_json(self) -> bool:
                accept = self.headers.get("Accept", "")
                requested_with = self.headers.get("X-Requested-With", "")
                return "application/json" in accept or requested_with.lower() == "fetch"

            def _maintenance_snapshot(self) -> dict[str, Any]:
                dashboard = self.server.dashboard  # type: ignore[attr-defined]
                with dashboard._maintenance_lock:
                    state = dict(dashboard._maintenance_state)
                state["progress"] = dashboard._read_maintenance_progress()
                return state

            def _start_nonface_cleanup(self) -> bool:
                dashboard = self.server.dashboard  # type: ignore[attr-defined]
                with dashboard._maintenance_lock:
                    if dashboard._maintenance_state["running"]:
                        return False
                    dashboard._maintenance_state = {
                        "running": True,
                        "last_result": None,
                        "last_error": None,
                        "progress_file": str(dashboard._maintenance_progress_path()),
                    }
                worker = Thread(
                    target=dashboard._run_nonface_cleanup,
                    name="lab-monitor-maintenance",
                    daemon=True,
                )
                worker.start()
                return True

            def _send_download(self, body: str, content_type: str, filename: str) -> None:
                data = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_text(self, body: str, content_type: str) -> None:
                data = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_live_frame(self, monitor: CameraMonitor | None) -> None:
                frame = monitor.latest_frame_jpeg() if monitor else None
                if frame is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)

            def _send_event_snapshot(self, event_id: int, store: EventStore) -> None:
                event = store.get_event(event_id)
                if event is None or event.snapshot_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_snapshot_file(event.snapshot_path, store)

            def _send_session_snapshot(self, session_id: int, store: EventStore) -> None:
                session = next((item for item in store.list_sessions(5000) if item.id == session_id), None)
                if session is None or session.snapshot_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_snapshot_file(session.snapshot_path, store)

            def _send_snapshot_file(self, snapshot_path: str, store: EventStore) -> None:
                file_path = (store.data_dir / snapshot_path).resolve()
                try:
                    if not file_path.is_relative_to(store.snapshot_dir.resolve()):
                        self.send_error(HTTPStatus.FORBIDDEN)
                        return
                except ValueError:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                if not file_path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer((self.config.web_host, self.config.web_port), Handler)
        server.dashboard = self  # type: ignore[attr-defined]
        return server

    def _run_nonface_cleanup(self) -> None:
        try:
            progress_path = self._maintenance_progress_path()
            self._write_maintenance_progress(
                {"phase": "Starting cleanup", "current": 0, "total": 1, "deleted_people": 0}
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lab_monitor",
                    "--config",
                    self.config_path,
                    "clean-roster",
                    "--include-low-evidence",
                    "--delete-nonface-visitors",
                    "--min-total-seconds",
                    "20",
                    "--progress-file",
                    str(progress_path),
                ],
                cwd=str(Path(self.config_path).resolve().parent),
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if completed.returncode != 0:
                error = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
                raise RuntimeError(error)
            output = completed.stdout.strip().splitlines()
            result = ast.literal_eval(output[-1]) if output else {"deleted_people": 0, "names": []}
            with self._maintenance_lock:
                self._maintenance_state = {
                    "running": False,
                    "last_result": result,
                    "last_error": None,
                    "progress_file": str(progress_path),
                }
        except Exception as exc:  # noqa: BLE001
            with self._maintenance_lock:
                self._maintenance_state = {
                    "running": False,
                    "last_result": None,
                    "last_error": str(exc),
                    "progress_file": str(self._maintenance_progress_path()),
                }

    def _maintenance_progress_path(self) -> Path:
        return self.config.data_path / "maintenance" / "cleanup-nonface-visitors.json"

    def _write_maintenance_progress(self, payload: dict[str, Any]) -> None:
        progress_path = self._maintenance_progress_path()
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_path.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")

    def _read_maintenance_progress(self) -> dict[str, Any]:
        progress_path = self._maintenance_progress_path()
        if not progress_path.exists():
            return {"phase": "Idle", "current": 0, "total": 0, "percent": 0}
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"phase": "Reading progress", "current": 0, "total": 0, "percent": 0}
        total = int(progress.get("total") or 0)
        current = int(progress.get("current") or 0)
        progress["percent"] = 100 if total <= 0 and progress.get("phase") == "Complete" else (
            min(100, max(0, round(current * 100 / total))) if total > 0 else 0
        )
        return progress


def render_calendar_page(
    store: EventStore,
    config: AppConfig,
    monitor: CameraMonitor | None,
    query: dict[str, list[str]] | None = None,
) -> str:
    query = query or {}
    selected_date = parse_selected_date(query)
    view = parse_view(query)
    sessions = store.list_sessions(5000)
    stats = store.stats()
    title = calendar_view_title(view, selected_date)
    toolbar = render_calendar_toolbar(view, selected_date, title)
    sidebar = render_status_panel(config, monitor, stats)
    main = f"""
      <main class="calendar-panel">
        <div class="calendar-head">
          <div>
            <h2>{escape(view_label(view))}</h2>
            <div class="calendar-subtitle">Sessions are shown as merged occupancy time ranges.</div>
          </div>
          <div class="calendar-subtitle">Auto refresh 15s</div>
        </div>
        <div class="calendar-scroll">{render_calendar_view(view, selected_date, sessions, config)}</div>
      </main>
    """
    return render_shell("Calendar", "calendar", toolbar, sidebar, main, config, monitor)


def render_live_page(config: AppConfig, monitor: CameraMonitor | None) -> str:
    status = monitor.status if monitor else None
    main = f"""
      <main class="content-panel">
        <div class="content-head">
          <div>
            <h2>Live view</h2>
            <div class="content-subtitle">Camera frame with current recognition overlays.</div>
          </div>
          <div class="content-subtitle">Updated <span data-live-updated>Waiting</span></div>
        </div>
        <div class="content-body">
          <div class="live-shell">
            <section class="live-stage" data-live-stage>
              <img class="live-frame" data-live-frame alt="Live camera frame" hidden>
              <div class="live-empty" data-live-empty>Waiting for camera feed</div>
              <div class="live-pulse"></div>
            </section>
            <aside class="live-side">
              <section class="live-card">
                <h3>System</h3>
                <div class="live-row"><span>Monitor</span><strong data-live-running>{"Running" if status and status.running else "Stopped"}</strong></div>
                <div class="live-row"><span>Camera</span><strong data-live-camera>{"Open" if status and status.camera_open else "Closed"}</strong></div>
                <div class="live-row"><span>Device</span><strong>#{config.camera_index}</strong></div>
                <div class="live-row"><span>Last error</span><strong data-live-error>{escape((status.last_error if status else None) or "None")}</strong></div>
              </section>
              <section class="live-card">
                <h3>Recognition</h3>
                <div class="live-row"><span>Active tracks</span><strong data-live-tracks-count>{status.active_tracks if status else 0}</strong></div>
                <div class="live-row"><span>Faces</span><strong data-live-faces-count>0</strong></div>
                <div class="live-row"><span>Dwell gate</span><strong>{config.min_dwell_seconds}s</strong></div>
                <div class="live-row"><span>Merge gap</span><strong>{config.session_merge_gap_minutes}m</strong></div>
              </section>
              <section class="live-card">
                <h3>Active tracks</h3>
                <div class="live-track-list" data-live-track-list><div class="empty">No active candidates.</div></div>
              </section>
            </aside>
          </div>
        </div>
      </main>
    """
    return render_shell("Live", "live", "", "", main, config, monitor, wide=True, auto_refresh=False)


def render_roster_page(
    store: EventStore,
    config: AppConfig,
    monitor: CameraMonitor | None,
    query: dict[str, list[str]] | None = None,
) -> str:
    query = query or {}
    people = build_roster_people(store, config)
    unnamed_people = [person for person in people if is_unnamed_identity(person.name, config)]
    named_people = [person for person in people if not is_unnamed_identity(person.name, config)]
    group = query.get("group", ["unnamed"])[0]
    if group not in {"unnamed", "named"}:
        group = "unnamed"
    active_people = unnamed_people if group == "unnamed" else named_people
    page = parse_positive_int(query.get("page", ["1"])[0], default=1)
    total_pages = max(1, (len(active_people) + ROSTER_PAGE_SIZE - 1) // ROSTER_PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * ROSTER_PAGE_SIZE
    page_people = active_people[start : start + ROSTER_PAGE_SIZE]
    assign_roster_snapshots(page_people, store, config)
    visitor_count = len(unnamed_people)
    alias_count = sum(len(person.aliases) for person in people)
    names = sorted({person.name for person in people})
    options = "".join(f'<option value="{escape(name)}"></option>' for name in names)
    notice = ""
    if query.get("saved", [""])[0] == "1":
        notice = '<div class="notice success">Roster updated.</div>'
    if query.get("deleted", [""])[0] == "1":
        notice = '<div class="notice success">Roster entry deleted.</div>'
    saved_return = f"/roster?{urlencode({'group': group, 'page': page, 'saved': '1'})}"
    deleted_return = f"/roster?{urlencode({'group': group, 'page': page, 'deleted': '1'})}"
    cards = "".join(render_roster_card(person, saved_return, deleted_return, config) for person in page_people)
    if not cards:
        cards = '<div class="empty">No identities on this page.</div>'
    roster_tabs = render_roster_tabs(group, len(unnamed_people), len(named_people))
    pager = render_roster_pager(group, page, total_pages, len(active_people))
    active_label = "Unnamed visitors" if group == "unnamed" else "Named identities"
    main = f"""
      <main class="content-panel">
        <div class="content-head">
          <div>
            <h2>Roster</h2>
            <div class="content-subtitle">Review temporary visitors and maintain named identity records.</div>
          </div>
          <div class="content-subtitle">{escape(active_label)} - page {page} of {total_pages}</div>
        </div>
        <div class="content-body">
          {notice}
          <div class="metric-grid">
            <div class="metric"><span>People</span><strong>{len(people)}</strong><small>Canonical identities</small></div>
            <div class="metric"><span>Visitors</span><strong>{visitor_count}</strong><small>Not renamed yet</small></div>
            <div class="metric"><span>Aliases</span><strong>{alias_count}</strong><small>Grouped old labels</small></div>
            <div class="metric"><span>Sessions</span><strong>{sum(len(person.sessions) for person in people)}</strong><small>Across roster</small></div>
          </div>
          <datalist id="identity-options">{options}</datalist>
          {roster_tabs}
          <section class="roster-grid">{cards}</section>
          {pager}
        </div>
      </main>
    """
    return render_shell("Roster", "roster", "", "", main, config, monitor, wide=True)


def render_analytics_page(
    store: EventStore,
    config: AppConfig,
    monitor: CameraMonitor | None,
    query: dict[str, list[str]] | None = None,
) -> str:
    query = query or {}
    start, end = parse_date_range(query)
    sessions = filter_sessions(store.list_sessions(5000), start, end)
    summary = build_analytics(sessions, config.unknown_identity_prefix)
    toolbar = render_range_toolbar("/analytics", start, end)
    main = f"""
      <main class="content-panel">
        <div class="content-head">
          <div>
            <h2>Operational analytics</h2>
            <div class="content-subtitle">Occupancy sessions, identity mix, peak periods, and dwell distribution.</div>
          </div>
          <div class="content-subtitle">{len(sessions)} sessions in range</div>
        </div>
        <div class="content-body">
          {render_analytics_metrics(summary)}
          <div class="dashboard-grid">
            {render_bar_panel("Daily occupancy", summary.daily_trend, "date", "hours", "h")}
            {render_bar_panel("Peak hours", summary.hourly_counts, "hour", "sessions", "")}
            {render_bar_panel("Top identities", summary.top_identities, "identity", "hours", "h")}
            {render_bar_panel("Dwell distribution", summary.dwell_buckets, "bucket", "sessions", "")}
          </div>
        </div>
      </main>
    """
    return render_shell("Analytics", "analytics", toolbar, "", main, config, monitor, wide=True)


def render_export_page(
    store: EventStore,
    config: AppConfig,
    monitor: CameraMonitor | None,
    query: dict[str, list[str]] | None = None,
) -> str:
    query = query or {}
    start, end = parse_date_range(query)
    sessions = filter_sessions(store.list_sessions(5000), start, end)
    range_query = urlencode(
        {
            "from": start.isoformat() if start else "",
            "to": end.isoformat() if end else "",
        }
    )
    toolbar = render_range_toolbar("/export", start, end)
    main = f"""
      <main class="content-panel">
        <div class="content-head">
          <div>
            <h2>Export records</h2>
            <div class="content-subtitle">Records are occupancy sessions only. Internal detector events are excluded.</div>
          </div>
          <div class="content-subtitle">{len(sessions)} sessions selected</div>
        </div>
        <div class="content-body">
          <section class="panel">
            <div class="panel-header">
              <h3 class="panel-title">Download</h3>
              <span class="panel-note">CSV or JSON</span>
            </div>
            <div class="form-actions">
              <a class="button primary" href="/api/export/sessions.csv?{range_query}">Export CSV</a>
              <a class="button primary" href="/api/export/sessions.json?{range_query}">Export JSON</a>
              <span class="panel-note">Use the date range controls above to filter exports.</span>
            </div>
          </section>
        </div>
      </main>
    """
    return render_shell("Export", "export", toolbar, "", main, config, monitor, wide=True)


def render_settings_page(
    saved_config: AppConfig,
    runtime_config: AppConfig,
    monitor: CameraMonitor | None,
    query: dict[str, list[str]] | None = None,
    maintenance_state: dict[str, Any] | None = None,
) -> str:
    query = query or {}
    maintenance_state = maintenance_state or {}
    notice = ""
    if query.get("saved", [""])[0] == "1":
        notice = '<div class="notice success">Settings saved. Restart the background service for changes to take effect.</div>'
    maintenance_notice = render_maintenance_notice(query, maintenance_state)
    if maintenance_notice:
        notice = maintenance_notice
    if query.get("error", [""])[0]:
        notice = f'<div class="notice error">{escape(query["error"][0])}</div>'
    main = f"""
      <main class="content-panel">
        <div class="content-head">
          <div>
            <h2>Settings</h2>
            <div class="content-subtitle">Edit config.json. Runtime settings still require a service restart.</div>
          </div>
          <div class="content-subtitle">Current runtime port {runtime_config.web_port}</div>
        </div>
        <div class="content-body">
          {notice}
          <div class="settings-layout">
            <form class="settings-main" method="post" action="/api/settings">
              <section class="panel">
                <div class="panel-header">
                  <h3 class="panel-title">Work profile</h3>
                  <span class="panel-note">Optional preset</span>
                </div>
                <div class="field-grid">
                  <div class="field">
                    <label for="profile">Profile</label>
                    <select id="profile" name="profile">
                      <option value="">Keep custom values</option>
                      {''.join(f'<option value="{escape(name)}">{escape(name)}</option>' for name in WORK_PROFILES)}
                    </select>
                  </div>
                  {render_config_field("camera_index", saved_config.camera_index)}
                </div>
              </section>
              {render_settings_compact(saved_config)}
              <div class="form-actions">
                <button class="button primary" type="submit">Save settings</button>
                <span class="panel-note">Changes are persisted only. Restart required.</span>
              </div>
            </form>
            <aside class="settings-side">
              <section class="panel">
                <div class="panel-header">
                  <h3 class="panel-title">Runtime</h3>
                  <span class="panel-note">Current process</span>
                </div>
                <div class="health-grid">
                  <div class="metric"><span>Host</span><strong>{escape(runtime_config.web_host)}</strong><small>Dashboard bind</small></div>
                  <div class="metric"><span>Port</span><strong>{runtime_config.web_port}</strong><small>Restart to change</small></div>
                </div>
              </section>
              <section class="panel">
                <div class="panel-header">
                  <h3 class="panel-title">Roster maintenance</h3>
                  <span class="panel-note">Runtime cleanup</span>
                </div>
                <div class="form-actions">
                  <form class="maintenance-form" method="post" action="/api/maintenance/cleanup-nonface-visitors">
                    <button class="button" type="submit" data-maintenance-submit {"disabled" if maintenance_state.get("running") else ""}>Remove visitors without avatars</button>
                  </form>
                </div>
                <div class="form-actions">
                  <div class="maintenance-progress" data-maintenance-progress {"hidden" if not maintenance_state.get("running") and not maintenance_state.get("last_result") and not maintenance_state.get("last_error") else ""}>
                    <div class="progress-meta"><span data-maintenance-phase>Idle</span><span data-maintenance-count></span></div>
                    <div class="progress-track"><div class="progress-fill" data-maintenance-fill></div></div>
                  </div>
                </div>
                <div class="empty">Runs in the background. Named identities are not removed.</div>
              </section>
            </aside>
          </div>
        </div>
      </main>
    """
    return render_shell("Settings", "settings", "", "", main, runtime_config, monitor, wide=True)


def render_maintenance_notice(query: dict[str, list[str]], state: dict[str, Any]) -> str:
    if state.get("running"):
        return '<div class="notice success">Roster cleanup is running in the background. This page will stay available.</div>'
    if state.get("last_error"):
        return f'<div class="notice error">Roster cleanup failed: {escape(str(state["last_error"]))}</div>'
    result = state.get("last_result")
    if isinstance(result, dict):
        deleted = escape(str(result.get("deleted_people", 0)))
        return f'<div class="notice success">Removed {deleted} visitor record(s) without verified avatars. Restart to reload the face model.</div>'
    if query.get("maintenance", [""])[0] == "started":
        return '<div class="notice success">Roster cleanup started in the background.</div>'
    if query.get("maintenance", [""])[0] == "running":
        return '<div class="notice success">Roster cleanup is already running.</div>'
    return ""


def create_face_service(config: AppConfig) -> FaceService:
    return FaceService(
        config.faces_path,
        config.enrolled_faces_path,
        config.face_recognition_threshold,
        min_face_size_px=config.min_face_size_px,
        min_face_area_ratio=config.min_face_area_ratio,
        min_sharpness=config.face_enrollment_min_sharpness,
        min_brightness=config.face_enrollment_min_brightness,
        max_brightness=config.face_enrollment_max_brightness,
    )


def render_shell(
    page_title: str,
    active: str,
    toolbar: str,
    sidebar: str,
    main: str,
    config: AppConfig,
    monitor: CameraMonitor | None,
    *,
    wide: bool = False,
    auto_refresh: bool = True,
) -> str:
    running = bool(monitor and monitor.status.running and monitor.status.camera_open)
    health = "Online" if running else "Offline"
    health_dot = "" if running else " off"
    layout_class = "layout wide" if wide else "layout"
    sidebar_html = f'<aside class="sidebar">{sidebar}</aside>' if sidebar else ""
    refresh_meta = '<meta http-equiv="refresh" content="15">' if auto_refresh else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>Lab Monitor - {escape(page_title)}</title>
  <style>{DASHBOARD_CSS}</style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">LM</div>
        <div>
          <h1>Lab Monitor</h1>
          <p>Occupancy sessions calendar</p>
        </div>
      </div>
      {render_topnav(active)}
      <nav class="toolbar">{toolbar}</nav>
      <div class="status-pill"><span class="dot{health_dot}"></span>{health}</div>
    </header>
    <div class="{layout_class}">
      {sidebar_html}
      {main}
    </div>
  </div>
  <script>{DASHBOARD_JS}</script>
</body>
</html>"""


def render_topnav(active: str) -> str:
    items = [
        ("calendar", "/calendar", "Calendar"),
        ("live", "/live", "Live"),
        ("roster", "/roster", "Roster"),
        ("analytics", "/analytics", "Analytics"),
        ("export", "/export", "Export"),
        ("settings", "/settings", "Settings"),
    ]
    return '<nav class="topnav">' + "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in items
    ) + "</nav>"


def render_calendar_toolbar(view: str, selected_date: date, title: str) -> str:
    today_link = f"/calendar?view={view}&date={date.today().isoformat()}"
    return f"""
      <a class="button" href="{today_link}">Today</a>
      <a class="button icon-button" href="{nav_link(view, selected_date, -1)}" aria-label="Previous">&lsaquo;</a>
      <a class="button icon-button" href="{nav_link(view, selected_date, 1)}" aria-label="Next">&rsaquo;</a>
      <div class="toolbar-title">{escape(title)}</div>
      <div class="view-tabs">{render_view_tabs(view, selected_date)}</div>
    """


def render_range_toolbar(action: str, start: date | None, end: date | None) -> str:
    return f"""
      <form class="toolbar" method="get" action="{action}">
        <div class="field"><input name="from" type="date" value="{start.isoformat() if start else ""}" aria-label="From date"></div>
        <div class="field"><input name="to" type="date" value="{end.isoformat() if end else ""}" aria-label="To date"></div>
        <button class="button" type="submit">Apply range</button>
      </form>
    """


def render_status_panel(config: AppConfig, monitor: CameraMonitor | None, stats: dict[str, Any]) -> str:
    monitor_status = monitor.status if monitor else None
    running = bool(monitor_status and monitor_status.running)
    camera_open = bool(monitor_status and monitor_status.camera_open)
    active = monitor_status.active_tracks if monitor_status else 0
    last_error = monitor_status.last_error if monitor_status else None
    latest = stats["latest_event_at"] or "None"
    latest_text = format_ts_for_display(str(latest)) if latest != "None" else latest
    return f"""
      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">System status</h2>
          <span class="panel-note">{escape(latest_text)}</span>
        </div>
        <div class="health-grid">
          <div class="metric"><span>Monitor</span><strong>{"Running" if running else "Stopped"}</strong><small>{escape(last_error or "No errors")}</small></div>
          <div class="metric"><span>Camera</span><strong>{"Open" if camera_open else "Closed"}</strong><small>Device #{config.camera_index}</small></div>
          <div class="metric"><span>Candidates</span><strong>{active}</strong><small>Not logged before dwell</small></div>
          <div class="metric"><span>Sessions</span><strong>{stats["total_sessions"]}</strong><small>Merged by identity</small></div>
          <div class="metric"><span>Dwell gate</span><strong>{config.min_dwell_seconds}s</strong><small>Required before logging</small></div>
          <div class="metric"><span>Merge gap</span><strong>{config.session_merge_gap_minutes}m</strong><small>Adjacent sessions merge</small></div>
        </div>
      </section>
    """


def build_roster_people(store: EventStore, config: AppConfig) -> list[RosterPerson]:
    sessions = store.list_sessions(5000)
    aliases = store.list_identity_aliases()
    identity_names = store.list_identity_names()
    sessions_by_name: dict[str, list[Session]] = {}
    aliases_by_name: dict[str, set[str]] = {}
    roster_names: set[str] = set()

    for session in sessions:
        canonical = store.resolve_identity(session.identity_name)
        roster_names.add(canonical)
        sessions_by_name.setdefault(canonical, []).append(session)

    for old_name, new_name in aliases.items():
        canonical = store.resolve_identity(new_name)
        roster_names.add(canonical)
        if old_name != canonical:
            aliases_by_name.setdefault(canonical, set()).add(old_name)

    for raw_name in identity_names:
        canonical = store.resolve_identity(raw_name)
        roster_names.add(canonical)
        if raw_name != canonical:
            aliases_by_name.setdefault(canonical, set()).add(raw_name)

    people: list[RosterPerson] = []
    for name in roster_names:
        person_sessions = sorted(
            sessions_by_name.get(name, []),
            key=lambda item: item.last_seen_ts,
            reverse=True,
        )
        latest_session = person_sessions[0] if person_sessions else None
        people.append(
            RosterPerson(
                name=name,
                aliases=sorted(aliases_by_name.get(name, set())),
                sessions=person_sessions,
                total_seconds=sum(duration_seconds(session) for session in person_sessions),
                last_seen_ts=latest_session.last_seen_ts if latest_session else None,
                snapshot_url=None,
                best_confidence=best_face_distance(person_sessions),
                face_score=face_evidence_score(person_sessions),
            )
        )

    def sort_key(person: RosterPerson) -> tuple[int, int, float, str, int]:
        known_rank = 1 if not person.name.startswith(config.unknown_identity_prefix) else 0
        last_seen = person.last_seen_ts or ""
        return (person.face_score, len(person.sessions), person.total_seconds, last_seen, known_rank)

    return sorted(people, key=sort_key, reverse=True)


def assign_roster_snapshots(people: list[RosterPerson], store: EventStore, config: AppConfig) -> None:
    if not people:
        return
    try:
        face_service = create_face_service(config)
    except RuntimeError:
        face_service = None
    for person in people:
        session = select_roster_snapshot_session(person.sessions, store, face_service)
        person.snapshot_url = f"/session-snapshot/{session.id}" if session else None


def select_roster_snapshot_session(
    sessions: list[Session],
    store: EventStore,
    face_service: FaceService | None,
) -> Session | None:
    candidates = [session for session in sessions if session.snapshot_path]
    candidates.sort(
        key=lambda session: (
            session.confidence is None,
            float(session.confidence) if session.confidence is not None else 999.0,
        )
    )
    for session in candidates:
        if face_service is None or roster_snapshot_has_face(session, store, face_service):
            return session
    return None


def roster_snapshot_has_face(session: Session, store: EventStore, face_service: FaceService) -> bool:
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


def render_roster_tabs(active: str, unnamed_count: int, named_count: int) -> str:
    return f"""
      <nav class="roster-tabs" aria-label="Roster sections">
        <a class="{"active" if active == "unnamed" else ""}" href="/roster?group=unnamed">Unnamed visitors <span>{unnamed_count}</span></a>
        <a class="{"active" if active == "named" else ""}" href="/roster?group=named">Named identities <span>{named_count}</span></a>
      </nav>
    """


def render_roster_pager(group: str, page: int, total_pages: int, total_items: int) -> str:
    if total_pages <= 1:
        return f'<div class="pager"><span>{total_items} entries</span></div>'
    pages = sorted({1, total_pages, page - 1, page, page + 1})
    links: list[str] = []
    previous_page = 0
    for item in pages:
        if item < 1 or item > total_pages:
            continue
        if previous_page and item - previous_page > 1:
            links.append("<span>...</span>")
        href = f"/roster?{urlencode({'group': group, 'page': item})}"
        if item == page:
            links.append(f'<span class="active">{item}</span>')
        else:
            links.append(f'<a href="{href}">{item}</a>')
        previous_page = item
    summary = f"{total_items} entries, {ROSTER_PAGE_SIZE} per page"
    prev_link = (
        f'<a href="/roster?{urlencode({"group": group, "page": page - 1})}">Previous</a>'
        if page > 1
        else "<span>Previous</span>"
    )
    next_link = (
        f'<a href="/roster?{urlencode({"group": group, "page": page + 1})}">Next</a>'
        if page < total_pages
        else "<span>Next</span>"
    )
    return f'<div class="pager"><span>{summary}</span><div class="pager-links">{prev_link}{"".join(links)}{next_link}</div></div>'


def render_roster_card(person: RosterPerson, saved_return: str, deleted_return: str, config: AppConfig) -> str:
    initials = "".join(part[:1] for part in person.name.split()[:2]).upper() or "ID"
    media = (
        f'<img class="roster-photo" src="{escape(person.snapshot_url)}" alt="{escape(person.name)} snapshot" loading="lazy">'
        if person.snapshot_url
        else f'<div class="roster-avatar">{escape(initials)}</div>'
    )
    aliases = render_alias_chips(person, config)
    last_seen = format_ts_for_display(person.last_seen_ts) if person.last_seen_ts else "Never"
    score_label = f"Face {person.face_score}%" if person.snapshot_url else "No verified face"
    return f"""
      <article class="roster-card">
        <div class="roster-media">
          {media}
          <div class="roster-score">{escape(score_label)}</div>
        </div>
        <div class="roster-card-body">
          <h3 class="roster-name">{escape(person.name)}</h3>
          <div class="roster-aliases">{aliases}</div>
          <div class="roster-stats">
            <div class="roster-stat"><span>Sessions</span><strong>{len(person.sessions)}</strong></div>
            <div class="roster-stat"><span>Total</span><strong>{escape(format_duration(person.total_seconds))}</strong></div>
            <div class="roster-stat"><span>Last seen</span><strong>{escape(last_seen)}</strong></div>
          </div>
          <form class="roster-form" method="post" action="/api/identity/rename">
            <input type="hidden" name="old_name" value="{escape(person.name)}">
            <input type="hidden" name="return_to" value="{escape(saved_return)}">
            <input name="new_name" list="identity-options" value="{escape(person.name)}" aria-label="Rename {escape(person.name)}">
            <button type="submit">Rename</button>
          </form>
          <form class="roster-delete-form" method="post" action="/api/identity/delete">
            <input type="hidden" name="name" value="{escape(person.name)}">
            <input type="hidden" name="return_to" value="{escape(deleted_return)}">
            <button type="submit">Delete</button>
          </form>
        </div>
      </article>
    """


def render_alias_chips(person: RosterPerson, config: AppConfig) -> str:
    visitor_aliases = [alias for alias in person.aliases if is_unnamed_identity(alias, config)]
    visible_aliases = [alias for alias in person.aliases if not is_unnamed_identity(alias, config)]
    chips = [f'<span class="alias-chip">{escape(alias)}</span>' for alias in visible_aliases[:4]]
    if visitor_aliases and not is_unnamed_identity(person.name, config):
        chips.insert(0, f'<span class="alias-chip">{len(visitor_aliases)} merged visitor labels</span>')
    elif visitor_aliases:
        chips.extend(f'<span class="alias-chip">{escape(alias)}</span>' for alias in visitor_aliases[:4])
    hidden_count = max(0, len(visible_aliases) - 4)
    if hidden_count:
        chips.append(f'<span class="alias-chip">+{hidden_count} more</span>')
    return "".join(chips) if chips else '<span class="alias-chip">No aliases</span>'


def is_unnamed_identity(name: str, config: AppConfig) -> bool:
    return name.startswith(config.unknown_identity_prefix)


def parse_positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def best_face_distance(sessions: list[Session]) -> float | None:
    distances = [float(session.confidence) for session in sessions if session.confidence is not None]
    return min(distances) if distances else None


def face_evidence_score(sessions: list[Session]) -> int:
    best = best_face_distance(sessions)
    if best is None:
        return 0
    return max(1, min(100, int(round(100 * (1 - min(best, 140.0) / 140.0)))))


def render_settings_compact(config: AppConfig) -> str:
    primary_groups = [
        (
            "Monitoring",
            "Core logging behavior",
            ["min_dwell_seconds", "session_merge_gap_minutes", "poll_interval_ms", "max_tracking_distance_px"],
        ),
        (
            "Identity recognition",
            "Face matching and visitor naming",
            [
                "face_recognition_threshold",
                "face_soft_match_threshold",
                "min_unknown_face_observations",
                "unknown_identity_prefix",
                "min_face_size_px",
            ],
        ),
        (
            "Retention and network",
            "Stored history and dashboard bind",
            ["snapshot_retention_days", "log_retention_days", "web_host", "web_port"],
        ),
    ]
    primary_keys = {"camera_index"}
    sections = []
    for title, note, keys in primary_groups:
        primary_keys.update(keys)
        sections.append(render_settings_section(title, note, keys, config))
    advanced_keys = [key for key in CONFIG_FIELDS if key not in primary_keys]
    sections.append(
        '<details class="settings-advanced">'
        '<summary>Advanced</summary>'
        + render_settings_section("Advanced parameters", "Fine tuning", advanced_keys, config)
        + "</details>"
    )
    return "".join(sections)


def render_settings_section(title: str, note: str, keys: list[str], config: AppConfig) -> str:
    values = config.to_dict()
    return (
        '<section class="panel">'
        f'<div class="panel-header"><h3 class="panel-title">{escape(title)}</h3><span class="panel-note">{escape(note)}</span></div>'
        '<div class="field-grid">'
        + "".join(render_config_field(key, values[key]) for key in keys)
        + "</div></section>"
    )


def render_settings_groups(config: AppConfig) -> str:
    grouped: dict[str, list[str]] = {}
    for key, spec in CONFIG_FIELDS.items():
        grouped.setdefault(spec["group"], []).append(key)
    values = config.to_dict()
    return "".join(
        '<section class="panel">'
        f'<div class="panel-header"><h3 class="panel-title">{escape(group)}</h3><span class="panel-note">Validated</span></div>'
        '<div class="field-grid">'
        + "".join(render_config_field(key, values[key]) for key in keys)
        + "</div></section>"
        for group, keys in grouped.items()
    )


def render_config_field(key: str, value: Any) -> str:
    field_type = CONFIG_FIELDS[key]["type"]
    input_type = "number" if field_type in {int, float} else "text"
    step = ' step="0.1"' if field_type is float else ""
    return (
        '<div class="field">'
        f'<label for="{key}">{escape(pretty_label(key))}</label>'
        f'<input id="{key}" name="{key}" type="{input_type}" value="{escape(value)}"{step}>'
        "</div>"
    )


def render_analytics_metrics(summary: AnalyticsSummary) -> str:
    peak = "None" if summary.peak_hour is None else f"{summary.peak_hour:02d}:00"
    return f"""
      <section class="panel" style="margin-bottom: 14px;">
        <div class="panel-header">
          <h3 class="panel-title">Summary</h3>
          <span class="panel-note">Operational view</span>
        </div>
        <div class="metric-grid">
          <div class="metric"><span>Total sessions</span><strong>{summary.total_sessions}</strong><small>Filtered range</small></div>
          <div class="metric"><span>Occupied hours</span><strong>{summary.occupied_hours}</strong><small>Summed duration</small></div>
          <div class="metric"><span>Unique identities</span><strong>{summary.unique_identities}</strong><small>Known and visitors</small></div>
          <div class="metric"><span>Unknown visitors</span><strong>{summary.unknown_visitors}</strong><small>Auto-named identities</small></div>
          <div class="metric"><span>Peak hour</span><strong>{peak}</strong><small>{summary.peak_hour_sessions} sessions</small></div>
          <div class="metric"><span>Identity mix</span><strong>{summary.identity_mix["known"]}/{summary.identity_mix["visitors"]}</strong><small>Known / visitors</small></div>
        </div>
      </section>
    """


def render_bar_panel(title: str, rows: list[dict[str, Any]], label_key: str, value_key: str, suffix: str) -> str:
    max_value = max((float(row.get(value_key, 0)) for row in rows), default=0.0)
    body = '<div class="empty">No data yet.</div>'
    if rows:
        rendered_rows = []
        for row in rows:
            value = float(row.get(value_key, 0))
            if value <= 0:
                continue
            label = row.get(label_key, "")
            if label_key == "hour":
                label = f"{int(label):02d}:00"
            width = 0 if max_value <= 0 else max(2, int((value / max_value) * 100))
            rendered_rows.append(
                '<div class="bar-row">'
                f'<span>{escape(label)}</span>'
                f'<span class="bar-track"><span class="bar-fill" style="width:{width}%"></span></span>'
                f'<span>{value:g}{suffix}</span>'
                "</div>"
            )
        body = '<div class="chart-list">' + ("".join(rendered_rows) or '<div class="empty">No data yet.</div>') + "</div>"
    return f"""
      <section class="panel">
        <div class="panel-header">
          <h3 class="panel-title">{escape(title)}</h3>
          <span class="panel-note">Range filtered</span>
        </div>
        {body}
      </section>
    """


def build_stats_payload(store: EventStore, monitor: CameraMonitor | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"storage": store.stats()}
    if monitor:
        status = monitor.status
        payload["monitor"] = {
            "running": status.running,
            "camera_open": status.camera_open,
            "active_tracks": status.active_tracks,
            "last_error": status.last_error,
        }
    return payload


def build_live_payload(monitor: CameraMonitor | None) -> dict[str, Any]:
    if monitor is None:
        return {
            "running": False,
            "camera_open": False,
            "active_tracks": 0,
            "last_error": "Monitor not attached",
            "has_frame": False,
            "frame": None,
            "faces": [],
            "tracks": [],
        }
    status = monitor.status
    payload = monitor.live_snapshot()
    payload.update(
        {
            "running": status.running,
            "camera_open": status.camera_open,
            "active_tracks": status.active_tracks,
            "last_error": status.last_error,
        }
    )
    return payload


def parse_selected_date(query: dict[str, list[str]]) -> date:
    raw = query.get("date", [date.today().isoformat()])[0]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def parse_view(query: dict[str, list[str]]) -> str:
    view = query.get("view", ["week"])[0]
    return view if view in {"month", "week", "day"} else "week"


def nav_link(view: str, selected_date: date, direction: int) -> str:
    if view == "month":
        next_month = selected_date.month + direction
        year = selected_date.year
        if next_month < 1:
            next_month = 12
            year -= 1
        elif next_month > 12:
            next_month = 1
            year += 1
        day = min(selected_date.day, calendar.monthrange(year, next_month)[1])
        target = date(year, next_month, day)
    elif view == "day":
        target = selected_date + timedelta(days=direction)
    else:
        target = selected_date + timedelta(days=7 * direction)
    return f"/calendar?view={view}&date={target.isoformat()}"


def render_view_tabs(active_view: str, selected_date: date) -> str:
    labels = {"month": "Month", "week": "Week", "day": "Day"}
    return "".join(
        f'<a class="{"active" if view == active_view else ""}" href="/calendar?view={view}&date={selected_date.isoformat()}">{label}</a>'
        for view, label in labels.items()
    )


def calendar_view_title(view: str, selected_date: date) -> str:
    if view == "month":
        return f"{calendar.month_name[selected_date.month]} {selected_date.year}"
    if view == "day":
        return selected_date.strftime("%b %d, %Y")
    start = selected_date - timedelta(days=selected_date.weekday())
    end = start + timedelta(days=6)
    return f"{start.strftime('%b %d')} - {end.strftime('%b %d')}"


def view_label(view: str) -> str:
    return {"month": "Month view", "week": "Week view", "day": "Day view"}[view]


def render_calendar_view(view: str, selected_date: date, sessions: list[Session], config: AppConfig) -> str:
    if view == "month":
        return render_month_view(selected_date, sessions, config)
    if view == "day":
        return render_day_view(selected_date, sessions, config)
    return render_week_view(selected_date, sessions, config)


def render_month_view(selected_date: date, sessions: list[Session], config: AppConfig) -> str:
    grouped = group_sessions_by_date(sessions)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(selected_date.year, selected_date.month)
    parts = ['<section class="month-grid">']
    parts.extend(f'<div class="weekday">{label}</div>' for label in WEEKDAY_LABELS)
    today = date.today()
    for week in weeks:
        for day in week:
            classes = ["day-cell"]
            if day.month != selected_date.month:
                classes.append("outside")
            if day == selected_date:
                classes.append("selected")
            day_sessions = grouped.get(day, [])
            number_class = "day-number today-number" if day == today else "day-number"
            chips = "".join(render_session_chip(session, config) for session in day_sessions[:4])
            if len(day_sessions) > 4:
                chips += f'<a class="more" href="/calendar?view=day&date={day.isoformat()}">+ {len(day_sessions) - 4} more</a>'
            parts.append(
                f'<div class="{" ".join(classes)}">'
                '<div class="day-top">'
                f'<a class="{number_class}" href="/calendar?view=day&date={day.isoformat()}">{day.day}</a>'
                f'<span class="event-count">{len(day_sessions) if day_sessions else ""}</span>'
                "</div>"
                f"{chips}</div>"
            )
    parts.append("</section>")
    return "".join(parts)


def render_week_view(selected_date: date, sessions: list[Session], config: AppConfig) -> str:
    start = selected_date - timedelta(days=selected_date.weekday())
    days = [start + timedelta(days=i) for i in range(7)]
    return render_time_grid(days, sessions, "week", config)


def render_day_view(selected_date: date, sessions: list[Session], config: AppConfig) -> str:
    return render_time_grid([selected_date], sessions, "day", config)


def render_time_grid(days: list[date], sessions: list[Session], view: str, config: AppConfig) -> str:
    grouped = group_sessions_by_date_hour(sessions)
    grid_class = "time-grid day" if view == "day" else "time-grid"
    now = datetime.now().astimezone()
    parts = [f'<section class="{grid_class}">', '<div class="time-head"></div>']
    for day in days:
        number_class = "today-number" if day == date.today() else ""
        parts.append(
            f'<div class="time-head"><span>{WEEKDAY_LABELS[day.weekday()]}</span><strong class="{number_class}">{day.day}</strong></div>'
        )
    for hour in range(24):
        parts.append(f'<div class="time-label">{hour:02d}:00</div>')
        for day in days:
            slot_classes = ["time-slot"]
            if day == now.date() and hour == now.hour:
                slot_classes.append("current-hour")
            chips = "".join(render_session_chip(session, config) for session in grouped.get((day, hour), []))
            parts.append(f'<div class="{" ".join(slot_classes)}">{chips}</div>')
    parts.append("</section>")
    return "".join(parts)


def render_session_chip(session: Session, config: AppConfig) -> str:
    start = session_local_datetime(session.start_ts)
    end = session_local_datetime(session.end_ts)
    title = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} {session.identity_name}"
    kind = "visitor" if session.identity_name.startswith(config.unknown_identity_prefix) else "known"
    href = f"/session-snapshot/{session.id}" if session.snapshot_path else "#"
    duration = format_duration(duration_seconds(session))
    confidence = f"{session.confidence:.1f}" if session.confidence is not None else "Unknown"
    return (
        f'<a class="session-chip {kind}" href="{href}" title="{escape(title)}" '
        f'data-session-id="{session.id}" '
        f'data-identity="{escape(session.identity_name)}" '
        f'data-time="{start.strftime("%H:%M")} - {end.strftime("%H:%M")}" '
        f'data-duration="{escape(duration)}" '
        f'data-confidence="{escape(confidence)}" '
        f'data-snapshot="{escape(href)}" '
        'aria-haspopup="dialog">'
        '<span class="session-main">'
        f'<span class="session-title">{escape(session.identity_name)}</span>'
        f'<span class="session-time">{start.strftime("%H:%M")} - {end.strftime("%H:%M")}</span>'
        "</span></a>"
    )


def group_sessions_by_date(sessions: list[Session]) -> dict[date, list[Session]]:
    grouped: dict[date, list[Session]] = {}
    for session in sessions:
        start = session_local_datetime(session.start_ts)
        grouped.setdefault(start.date(), []).append(session)
    for day_sessions in grouped.values():
        day_sessions.sort(key=lambda item: item.start_ts)
    return grouped


def group_sessions_by_date_hour(sessions: list[Session]) -> dict[tuple[date, int], list[Session]]:
    grouped: dict[tuple[date, int], list[Session]] = {}
    for session in sessions:
        start = session_local_datetime(session.start_ts)
        grouped.setdefault((start.date(), start.hour), []).append(session)
    for hour_sessions in grouped.values():
        hour_sessions.sort(key=lambda item: item.start_ts)
    return grouped


def session_local_datetime(raw_ts: str) -> datetime:
    parsed = datetime.fromisoformat(raw_ts)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone()


def format_ts_for_display(raw_ts: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw_ts)
    except ValueError:
        return raw_ts
    return parsed.astimezone().strftime("%m/%d %H:%M")


def format_duration(seconds: float) -> str:
    minutes = max(0, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    remainder = minutes % 60
    if remainder == 0:
        return f"{hours} h"
    return f"{hours} h {remainder} min"


def pretty_label(key: str) -> str:
    return key.replace("_", " ").title()


def session_to_dict(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "identity_name": session.identity_name,
        "track_id": session.track_id,
        "start_ts": session.start_ts,
        "end_ts": session.end_ts,
        "last_seen_ts": session.last_seen_ts,
        "confidence": session.confidence,
        "duration_minutes": round(duration_seconds(session) / 60, 1),
        "details": session.details,
        "snapshot_url": f"/session-snapshot/{session.id}" if session.snapshot_path else None,
    }


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "ts": event.ts,
        "event_type": event.event_type,
        "track_id": event.track_id,
        "person_name": event.person_name,
        "confidence": event.confidence,
        "behavior": event.behavior,
        "details": event.details,
        "snapshot_url": f"/snapshot/{event.id}" if event.snapshot_path else None,
    }


def escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
