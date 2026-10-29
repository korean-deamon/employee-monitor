#!/usr/bin/env python3
"""Attendance API — who is on camera, when they arrived, plus employee management.

Two gates: the dashboard password lets you *see* attendance; a second, separate
password is required to *change* who is in the system.
"""
import csv
import io
import json
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attendance
import config as cfgmod
import counting
from db import FULL_NAME_SQL, create_employee, get_conn, init_db
import tg
from faces import enroll_total_samples

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASS_FILE = DATA_DIR / "admin_pass.txt"
ADMIN_PASS_FILE = DATA_DIR / "manage_pass.txt"
SNAPSHOT_PATH = DATA_DIR / "snapshot.jpg"
CAMERA_STATUS_PATH = DATA_DIR / "camera_status.json"
ENROLL_REQUEST_PATH = DATA_DIR / "enroll_request.json"
ENROLL_RESULT_PATH = DATA_DIR / "enroll_result.json"

# A gap shorter than this inside a person's sightings is not a departure — they
# turned away or stepped out of frame. Matches the recognizer's own threshold.
SPAN_GAP_TOLERANCE_SECONDS = 300
ENROLL_SAMPLES = enroll_total_samples()
# Walking someone through every pose takes ~25s when it goes smoothly; the rest
# of the window absorbs retries for blur, distance and people stepping away.
ENROLL_WINDOW_SECONDS = 90
ADMIN_SESSION_HOURS = 8

app = FastAPI(title="Attendance API")
security = HTTPBasic()

_admin_sessions: dict[str, float] = {}   # token -> expiry (epoch seconds)


def _read_or_create_password(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(9))
    return path.read_text().strip()


def require_viewer(creds: HTTPBasicCredentials = Depends(security)):
    """Gate for everything: seeing the dashboard at all."""
    ok_user = secrets.compare_digest(creds.username, "admin")
    ok_pass = secrets.compare_digest(creds.password, _read_or_create_password(PASS_FILE))
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return True


def require_manager(admin_session: str | None = Cookie(default=None)):
    """Second gate: adding or removing employees needs the management password."""
    now = time.time()
    for token, expiry in list(_admin_sessions.items()):
        if expiry < now:
            del _admin_sessions[token]
    if not admin_session or _admin_sessions.get(admin_session, 0) < now:
        raise HTTPException(status_code=403, detail="Boshqaruv paroli kerak")
    return True


def query(sql, params=()):
    con = get_conn()
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── Viewing attendance ────────────────────────────────────────────────────────

@app.get("/api/employees")
def employees(_: bool = Depends(require_viewer)):
    return query(
        f"""SELECT e.id, {FULL_NAME_SQL} AS name, e.first_name, e.last_name,
                   e.position, COUNT(f.id) AS face_samples,
                   t.telegram_id IS NOT NULL AS telegram_linked, t.username
            FROM employees e
            LEFT JOIN face_embeddings f ON f.employee_id = e.id
            LEFT JOIN telegram_links t ON t.employee_id = e.id
            GROUP BY e.id ORDER BY e.first_name, e.last_name"""
    )


@app.get("/api/attendance")
def attendance_list(days: int = 1, employee_id: int | None = None,
                    day: str | None = None, cancelled: bool = False,
                    _: bool = Depends(require_viewer)):
    """Arrivals and departures, each graded against the working day.

    `day` pins one office date, which is what the correction screen works in;
    `cancelled` includes struck records, so a mistaken cancellation is visible
    and can be undone rather than silently vanishing.
    """
    cfg = cfgmod.load()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = f"""SELECT a.id, a.ts, a.local_day, a.event, a.source, a.confirmed,
                     a.reason, a.suspicious, a.note, a.cancelled, a.employee_id,
                     {FULL_NAME_SQL} AS name, e.position
                FROM attendance_events a JOIN employees e ON e.id = a.employee_id
               WHERE 1 = 1"""
    params: list = []
    if not cancelled:
        sql += " AND a.cancelled = 0"
    if day:
        sql += " AND a.local_day = ?"
        params.append(day)
    else:
        sql += " AND a.ts >= ?"
        params.append(since)
    if employee_id:
        sql += " AND a.employee_id = ?"
        params.append(employee_id)
    sql += " ORDER BY a.ts DESC LIMIT 200"

    rows = query(sql, params)
    for row in rows:
        row["grade"] = attendance.grade(cfg, cfgmod.parse_ts(row["ts"]), row["event"])
    return rows


@app.get("/api/summary")
def summary(day: str | None = None, _: bool = Depends(require_viewer)):
    """Every employee's day: arrival, departure, hours worked, breaks."""
    cfg = cfgmod.load()
    local_day = day or cfgmod.local_day(cfg, cfgmod.now_utc())
    con = get_conn()
    rows = con.execute(
        f"""SELECT DISTINCT a.employee_id, {FULL_NAME_SQL} AS name, e.position
              FROM attendance_events a JOIN employees e ON e.id = a.employee_id
             WHERE a.local_day = ? AND a.cancelled = 0
             ORDER BY a.ts""",
        (local_day,),
    ).fetchall()

    out = []
    for row in rows:
        day_data = attendance.day_summary(con, cfg, row["employee_id"], local_day)
        if not day_data:
            continue
        day_data["name"] = row["name"]
        day_data["position"] = row["position"]
        first = day_data["events"][0]
        day_data["arrival_grade"] = attendance.grade(
            cfg, cfgmod.parse_ts(first["ts"]), "keldi") if first["event"] == "keldi" else None
        # An early departure with no reason given is what a manager looks for.
        day_data["unexplained_early_leave"] = any(
            e["event"] == "ketdi"
            and attendance.grade(cfg, cfgmod.parse_ts(e["ts"]), "ketdi").get("needs_reason")
            and not e["reason"]
            for e in day_data["events"])
        out.append(day_data)
    con.close()
    return {"day": local_day, "work_start": cfg["work_start"],
            "work_end": cfg["work_end"], "employees": out}


@app.get("/api/timeline")
def timeline(days: int = 1, _: bool = Depends(require_viewer)):
    """Each person's day as continuous presence spans.

    Sightings closer together than the gap tolerance belong to one stretch at the
    desk, so briefly turning away does not shatter the day into fragments.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = query(
        f"""SELECT s.employee_id, {FULL_NAME_SQL} AS name, s.ts
            FROM presence_samples s JOIN employees e ON e.id = s.employee_id
            WHERE s.ts >= ? ORDER BY s.employee_id, s.ts""",
        [since.isoformat()],
    )

    by_person: dict[int, dict] = {}
    for r in rows:
        person = by_person.setdefault(r["employee_id"],
                                      {"employee_id": r["employee_id"], "name": r["name"], "spans": []})
        ts = datetime.fromisoformat(r["ts"])
        spans = person["spans"]
        if spans and (ts - datetime.fromisoformat(spans[-1]["end"])).total_seconds() <= SPAN_GAP_TOLERANCE_SECONDS:
            spans[-1]["end"] = r["ts"]
        else:
            spans.append({"start": r["ts"], "end": r["ts"]})

    out = []
    for person in by_person.values():
        person["total_seconds"] = int(sum(
            (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds()
            for s in person["spans"]))
        person["first_seen"] = person["spans"][0]["start"]
        person["last_seen"] = person["spans"][-1]["end"]
        out.append(person)
    out.sort(key=lambda p: p["first_seen"])
    return out


@app.get("/api/report/late")
def late_report(days: int = 30, employee_id: int | None = None,
                _: bool = Depends(require_viewer)):
    """Every late arrival in the period, and the per-person totals.

    Each arrival is listed on its own rather than only summed, because "you were
    23 minutes late on the 14th" is answerable and "you are 3 hours late this
    month" is not.
    """
    cfg = cfgmod.load()
    since = cfgmod.local_day(cfg, cfgmod.now_utc() - timedelta(days=days))
    sql = f"""SELECT a.id, a.employee_id, a.ts, a.local_day, a.source,
                     {FULL_NAME_SQL} AS name, e.position
                FROM attendance_events a JOIN employees e ON e.id = a.employee_id
               WHERE a.event = 'keldi' AND a.cancelled = 0 AND a.local_day >= ?"""
    params: list = [since]
    if employee_id:
        sql += " AND a.employee_id = ?"
        params.append(employee_id)
    sql += " ORDER BY a.ts DESC"

    arrivals, totals = [], {}
    for row in query(sql, params):
        grade = attendance.grade(cfg, cfgmod.parse_ts(row["ts"]), "keldi")
        minutes = grade.get("late_minutes", 0)
        if not minutes:
            continue
        row["late_minutes"] = minutes
        row["late_text"] = attendance.human_minutes(minutes)
        row["time"] = cfgmod.to_local(cfg, cfgmod.parse_ts(row["ts"])).strftime("%H:%M")
        arrivals.append(row)

        person = totals.setdefault(row["employee_id"],
                                   {"employee_id": row["employee_id"], "name": row["name"],
                                    "position": row["position"], "days": 0, "minutes": 0,
                                    "worst_minutes": 0, "worst_day": None})
        person["days"] += 1
        person["minutes"] += minutes
        if minutes > person["worst_minutes"]:
            person["worst_minutes"] = minutes
            person["worst_day"] = row["local_day"]

    for person in totals.values():
        person["total_text"] = attendance.human_minutes(person["minutes"])
        person["average_minutes"] = round(person["minutes"] / person["days"])
    ranked = sorted(totals.values(), key=lambda p: -p["minutes"])

    return {"since": since, "work_start": cfg["work_start"],
            "total_minutes": sum(a["late_minutes"] for a in arrivals),
            "arrivals": arrivals, "by_employee": ranked}


@app.get("/api/report/monthly")
def monthly_report(month: str | None = None, _: bool = Depends(require_viewer)):
    """Per-employee totals for a month: hours, lateness, early departures.

    The two counts a manager actually acts on are kept apart: an early
    departure with a reason given is information, one without is a question.
    """
    cfg = cfgmod.load()
    month = month or cfgmod.local_day(cfg, cfgmod.now_utc())[:7]
    con = get_conn()
    days = [r["local_day"] for r in con.execute(
        "SELECT DISTINCT local_day FROM attendance_events "
        "WHERE local_day LIKE ? AND cancelled = 0 ORDER BY local_day",
        (f"{month}-%",)).fetchall()]

    people = con.execute(
        f"""SELECT DISTINCT a.employee_id, {FULL_NAME_SQL} AS name, e.position
              FROM attendance_events a JOIN employees e ON e.id = a.employee_id
             WHERE a.local_day LIKE ? AND a.cancelled = 0
             ORDER BY e.first_name, e.last_name""", (f"{month}-%",)).fetchall()

    rows = []
    for person in people:
        totals = {"employee_id": person["employee_id"], "name": person["name"],
                  "position": person["position"], "days": 0, "worked_seconds": 0,
                  "late_days": 0, "late_minutes": 0, "early_leaves": 0,
                  "unexplained_early_leaves": 0, "break_seconds": 0,
                  "manual_events": 0}
        for day in days:
            summary = attendance.day_summary(con, cfg, person["employee_id"], day)
            if not summary:
                continue
            totals["days"] += 1
            totals["worked_seconds"] += summary["worked_seconds"]
            totals["break_seconds"] += sum(b["seconds"] or 0 for b in summary["breaks"])
            for event in summary["events"]:
                grade = attendance.grade(cfg, cfgmod.parse_ts(event["ts"]), event["event"])
                if event["source"] == "manual":
                    totals["manual_events"] += 1
                if event["event"] == "keldi" and grade.get("late_minutes"):
                    totals["late_days"] += 1
                    totals["late_minutes"] += grade["late_minutes"]
                if event["event"] == "ketdi" and grade.get("needs_reason"):
                    totals["early_leaves"] += 1
                    if not event["reason"]:
                        totals["unexplained_early_leaves"] += 1
        rows.append(totals)
    con.close()
    return {"month": month, "work_start": cfg["work_start"], "work_end": cfg["work_end"],
            "days_recorded": len(days), "employees": rows}


@app.get("/api/report/monthly.csv")
def monthly_csv(month: str | None = None, _: bool = Depends(require_viewer)):
    """The same figures as a file, for whoever keeps the payroll spreadsheet."""
    report = monthly_report(month=month, _=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Xodim", "Lavozim", "Ish kuni", "Ishlagan soat", "Tanaffus soat",
                     "Kechikkan kun", "Kechikish (daqiqa)", "Erta ketish",
                     "Sababsiz erta ketish", "Qo'lda tuzatilgan"])
    for row in report["employees"]:
        writer.writerow([
            row["name"], row["position"], row["days"],
            f"{row['worked_seconds'] / 3600:.2f}".replace(".", ","),
            f"{row['break_seconds'] / 3600:.2f}".replace(".", ","),
            row["late_days"], row["late_minutes"], row["early_leaves"],
            row["unexplained_early_leaves"], row["manual_events"],
        ])
    filename = f"davomat-{report['month']}.csv"
    return Response(
        # The BOM is what makes Excel open a UTF-8 CSV without mangling the
        # Uzbek letters; without it the first column header comes out wrong.
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Camera ────────────────────────────────────────────────────────────────────

def _mjpeg_frames():
    """Push each new snapshot as it lands, over one long-lived connection."""
    last_mtime = 0.0
    idle_ticks = 0
    while idle_ticks < 600:          # stop if the camera stops writing (~60s)
        try:
            mtime = SNAPSHOT_PATH.stat().st_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                idle_ticks = 0
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + SNAPSHOT_PATH.read_bytes() + b"\r\n"
            else:
                idle_ticks += 1
        except OSError:
            idle_ticks += 1
        time.sleep(0.02)


@app.get("/api/camera/stream")
def camera_stream(_: bool = Depends(require_viewer)):
    return StreamingResponse(_mjpeg_frames(),
                             media_type="multipart/x-mixed-replace; boundary=frame")


COUNTER_SNAPSHOT_PATH = DATA_DIR / "counter_snapshot.jpg"


def _counter_frames():
    """The counting view's own stream, so the dashboard image stays untouched."""
    last_mtime = 0.0
    idle = 0
    while idle < 600:
        try:
            mtime = COUNTER_SNAPSHOT_PATH.stat().st_mtime
            if mtime != last_mtime:
                last_mtime, idle = mtime, 0
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + COUNTER_SNAPSHOT_PATH.read_bytes() + b"\r\n")
            else:
                idle += 1
        except OSError:
            idle += 1
        time.sleep(0.02)


@app.get("/api/counter/stream")
def counter_stream(_: bool = Depends(require_viewer)):
    return StreamingResponse(_counter_frames(),
                             media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/counter/today")
def counter_today(day: str | None = None, _: bool = Depends(require_viewer)):
    """How many people went in and out, plus the most recent crossings.

    Separate from attendance on purpose: this counts every body through the
    door, most of whom are not employees, so the two numbers are not meant to
    agree and are never presented together as if they should.
    """
    cfg = cfgmod.load()
    local_day = day or cfgmod.local_day(cfg, cfgmod.now_utc())
    con = get_conn()
    totals = counting.totals_for_day(con, local_day)
    recent = [dict(r) for r in con.execute(
        """SELECT id, ts, direction FROM crossings
            WHERE local_day = ? ORDER BY ts DESC LIMIT 30""", (local_day,)).fetchall()]
    hourly = [dict(r) for r in con.execute(
        """SELECT substr(ts, 12, 2) AS hour, direction, COUNT(*) AS n
             FROM crossings WHERE local_day = ?
            GROUP BY hour, direction ORDER BY hour""", (local_day,)).fetchall()]
    con.close()
    return {"day": local_day, "enabled": cfg["counter_enabled"],
            "line": cfg["door_line"],
            "in": totals.get("kirdi", 0), "out": totals.get("chiqdi", 0),
            "inside": totals.get("kirdi", 0) - totals.get("chiqdi", 0),
            "recent": recent, "hourly": hourly}


@app.get("/api/camera/status")
def camera_status(_: bool = Depends(require_viewer)):
    try:
        data = json.loads(CAMERA_STATUS_PATH.read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["ts"])).total_seconds()
    except (OSError, ValueError, KeyError):
        # Missing file, or a read that raced a write: report offline, never a 500.
        return JSONResponse({"online": False})
    data["online"] = age < 15
    return data


# ── Managing employees (second password) ──────────────────────────────────────

@app.post("/api/admin/login")
def admin_login(response: Response, password: str = Body(..., embed=True),
                _: bool = Depends(require_viewer)):
    if not secrets.compare_digest(password, _read_or_create_password(ADMIN_PASS_FILE)):
        raise HTTPException(status_code=403, detail="Parol noto'g'ri")
    token = secrets.token_urlsafe(24)
    _admin_sessions[token] = time.time() + ADMIN_SESSION_HOURS * 3600
    response.set_cookie("admin_session", token, httponly=True, samesite="lax",
                        max_age=ADMIN_SESSION_HOURS * 3600)
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response, admin_session: str | None = Cookie(default=None),
                 _: bool = Depends(require_viewer)):
    _admin_sessions.pop(admin_session or "", None)
    response.delete_cookie("admin_session")
    return {"ok": True}


@app.get("/api/admin/session")
def admin_session_state(admin_session: str | None = Cookie(default=None),
                        _: bool = Depends(require_viewer)):
    return {"signed_in": bool(admin_session and _admin_sessions.get(admin_session, 0) > time.time())}


@app.post("/api/admin/employees")
def add_employee(first_name: str = Body(...), last_name: str = Body(default=""),
                 position: str = Body(default=""),
                 _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    if not first_name.strip():
        raise HTTPException(status_code=400, detail="Ism kiritilmadi")
    con = get_conn()
    employee_id = create_employee(con, first_name, last_name, position)
    con.close()
    return {"id": employee_id}


@app.patch("/api/admin/employees/{employee_id}")
def edit_employee(employee_id: int, first_name: str = Body(...), last_name: str = Body(default=""),
                  position: str = Body(default=""),
                  _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Fix a name or change a position without touching faces or history."""
    if not first_name.strip():
        raise HTTPException(status_code=400, detail="Ism kiritilmadi")
    con = get_conn()
    cur = con.execute(
        "UPDATE employees SET first_name = ?, last_name = ?, position = ? WHERE id = ?",
        (first_name.strip(), last_name.strip(), position.strip(), employee_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Xodim topilmadi")
    return {"ok": True}


@app.delete("/api/admin/employees/{employee_id}")
def remove_employee(employee_id: int,
                    _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Remove the person and their face data. Attendance history is kept."""
    con = get_conn()
    con.execute("DELETE FROM face_embeddings WHERE employee_id = ?", (employee_id,))
    con.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/admin/employees/{employee_id}/capture")
def start_capture(employee_id: int,
                  _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Ask the recognizer to enroll the face currently in front of the camera.

    The recognizer owns the camera, so capture has to go through it — two
    processes cannot open the same device.
    """
    con = get_conn()
    row = con.execute("SELECT id FROM employees WHERE id = ?", (employee_id,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(status_code=404, detail="Xodim topilmadi")

    ENROLL_RESULT_PATH.unlink(missing_ok=True)
    tmp = ENROLL_REQUEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "employee_id": employee_id,
        "samples": ENROLL_SAMPLES,
        "expires_at": time.time() + ENROLL_WINDOW_SECONDS,
    }))
    tmp.replace(ENROLL_REQUEST_PATH)
    return {"started": True, "samples": ENROLL_SAMPLES, "window_seconds": ENROLL_WINDOW_SECONDS}


@app.get("/api/admin/capture")
def capture_state(_v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    try:
        return json.loads(ENROLL_RESULT_PATH.read_text())
    except (OSError, ValueError):
        return {"state": "idle"}


# ── Telegram pairing ──────────────────────────────────────────────────────────

@app.post("/api/admin/employees/{employee_id}/pairing-code")
def issue_pairing_code(employee_id: int,
                       _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Hand out a one-time code the employee sends to the bot."""
    cfg = cfgmod.load()
    con = get_conn()
    if not con.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
        con.close()
        raise HTTPException(status_code=404, detail="Xodim topilmadi")
    issued = tg.new_code(con, cfg, employee_id)
    con.close()
    return issued


@app.delete("/api/admin/employees/{employee_id}/telegram")
def unlink_telegram(employee_id: int,
                    _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    con = get_conn()
    removed = tg.unlink(con, employee_id)
    con.close()
    return {"unlinked": removed}


@app.get("/api/admin/outbox")
def outbox(limit: int = 50,
           _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """What the system has decided to say, sent or not.

    While notifications are off this is the only place the messages appear, so
    the whole flow can be reviewed before anyone's phone buzzes.
    """
    cfg = cfgmod.load()
    rows = query(
        f"""SELECT o.id, o.kind, o.body, o.buttons, o.created_at, o.sent_at, o.error,
                   o.employee_id, o.telegram_id, {FULL_NAME_SQL} AS name
              FROM telegram_outbox o LEFT JOIN employees e ON e.id = o.employee_id
             ORDER BY o.id DESC LIMIT ?""",
        [limit],
    )
    for row in rows:
        row["buttons"] = json.loads(row["buttons"]) if row["buttons"] else None
    return {"enabled": cfg["telegram_enabled"],
            "sending": cfg["telegram_send_notifications"],
            "messages": rows}


# ── Manual correction ─────────────────────────────────────────────────────────

@app.patch("/api/admin/events/{event_id}")
def edit_event(event_id: int, ts: str = Body(default=""), event: str = Body(default=""),
               reason: str = Body(default=""), note: str = Body(default=""),
               _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Fix a record the camera got wrong.

    `ts` is accepted as office-local wall clock ("2026-10-27T09:05"), because
    that is what the person correcting it is reading off the timeline.
    """
    cfg = cfgmod.load()
    if event and event not in ("keldi", "ketdi"):
        raise HTTPException(status_code=400, detail="event: keldi yoki ketdi")
    moment = None
    if ts:
        try:
            moment = datetime.fromisoformat(ts).replace(tzinfo=cfgmod.tz(cfg))
        except ValueError:
            raise HTTPException(status_code=400, detail="ts noto'g'ri")
    con = get_conn()
    result = attendance.edit_event(con, cfg, event_id, ts=moment, event=event or None,
                                   reason=reason or None, note=note or None)
    con.close()
    if result is None:
        raise HTTPException(status_code=404, detail="Yozuv topilmadi")
    return {"ok": True, "id": result}


@app.post("/api/admin/events")
def add_event(employee_id: int = Body(...), ts: str = Body(...), event: str = Body(...),
              reason: str = Body(default=""), note: str = Body(default=""),
              _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Create a record for a day the camera missed entirely."""
    cfg = cfgmod.load()
    if event not in ("keldi", "ketdi"):
        raise HTTPException(status_code=400, detail="event: keldi yoki ketdi")
    try:
        moment = datetime.fromisoformat(ts).replace(tzinfo=cfgmod.tz(cfg))
    except ValueError:
        raise HTTPException(status_code=400, detail="ts noto'g'ri")
    con = get_conn()
    if not con.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
        con.close()
        raise HTTPException(status_code=404, detail="Xodim topilmadi")
    event_id = attendance.add_event(con, cfg, employee_id, moment, event,
                                    reason=reason or None, note=note or None)
    con.close()
    return {"ok": True, "id": event_id}


@app.post("/api/admin/events/{event_id}/restore")
def restore_event(event_id: int,
                  _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Undo a cancellation. The row was never deleted, so this just un-strikes it."""
    con = get_conn()
    cur = con.execute(
        "UPDATE attendance_events SET cancelled = 0 WHERE id = ? AND cancelled = 1",
        (event_id,))
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bekor qilingan yozuv topilmadi")
    return {"ok": True}


@app.delete("/api/admin/events/{event_id}")
def cancel_event(event_id: int,
                 _v: bool = Depends(require_viewer), _m: bool = Depends(require_manager)):
    """Strike a record the camera should not have made.

    Marked cancelled, never deleted: what the camera saw and who overruled it
    both stay answerable.
    """
    con = get_conn()
    cur = con.execute(
        """UPDATE attendance_events SET cancelled = 1, note = COALESCE(note, 'Admin bekor qildi')
            WHERE id = ?""", (event_id,))
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Yozuv topilmadi")
    return {"ok": True}


init_db()


@app.get("/", dependencies=[Depends(require_viewer)])
def dashboard():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/admin", dependencies=[Depends(require_viewer)])
def admin_page():
    return FileResponse(FRONTEND_DIR / "admin.html")


@app.get("/counter", dependencies=[Depends(require_viewer)])
def counter_page():
    """The counting view lives on its own page, so the attendance dashboard is
    exactly what it was before counting existed."""
    return FileResponse(FRONTEND_DIR / "counter.html")
