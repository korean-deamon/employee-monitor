"""Decide what a camera sighting means for the attendance record.

The camera answers "who" and "when". What the moment *means* — an arrival, a
departure, a repeat of something already recorded — depends on what the day
already holds, and that decision lives here rather than in the camera loop.

Clock times never decide the event. They only grade it: an arrival is recorded
whether it lands at 08:45 or 09:40, and only the report calls one of them late.
Keeping it that way is what stops an early arrival or a late departure from
falling outside the rules and going unrecorded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfgmod

REASON_LABELS = {
    "obed": "Obed",
    "shifokor": "Shifokor",
    "shaxsiy": "Shaxsiy ish",
    "topshiriq": "Ish topshirig'i",
}


def last_event(con, employee_id, local_day):
    return con.execute(
        """SELECT * FROM attendance_events
           WHERE employee_id = ? AND local_day = ? AND cancelled = 0
           ORDER BY ts DESC LIMIT 1""",
        (employee_id, local_day),
    ).fetchone()


def _insert(con, employee_id, ts, local_day, event, source, note=None):
    cur = con.execute(
        """INSERT INTO attendance_events
               (employee_id, ts, local_day, event, source, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (employee_id, ts.isoformat(), local_day, event, source, note),
    )
    con.commit()
    return cur.lastrowid


def human_minutes(total: int) -> str:
    """Minutes as "1 soat 3 daqiqa" — a reader should not have to divide by 60."""
    hours, minutes = divmod(abs(int(total)), 60)
    if hours and minutes:
        return f"{hours} soat {minutes} daqiqa"
    if hours:
        return f"{hours} soat"
    return f"{minutes} daqiqa"


def grade(cfg, ts, event):
    """How the report should describe this event's timing."""
    if event == "keldi":
        offset = cfgmod.minutes_from_start(cfg, ts)
        if offset > 0:
            return {"late_minutes": offset,
                    "text": f"{human_minutes(offset)} kechikish"}
        if offset == 0:
            return {"early_minutes": 0, "text": "o'z vaqtida"}
        return {"early_minutes": -offset, "text": f"{human_minutes(offset)} erta"}

    remaining = cfgmod.minutes_before_end(cfg, ts)
    if remaining > 0:
        # Left before the day was over: this is the case that needs a reason.
        return {"early_leave_minutes": remaining, "needs_reason": True,
                "text": f"{human_minutes(remaining)} erta ketdi"}
    if remaining == 0:
        return {"overtime_minutes": 0, "text": "to'liq kun"}
    return {"overtime_minutes": -remaining,
            "text": f"{human_minutes(remaining)} qo'shimcha"}


def register_stand(con, cfg, employee_id, ts):
    """An employee deliberately stood at the camera. Record what it means.

    Returns {"status": ..., "event": ..., "ts": ..., "grade": ...} where status
    is "recorded" or "already" (a repeat too soon after an arrival to be a
    departure — nobody arrives and leaves within minutes).
    """
    local_day = cfgmod.local_day(cfg, ts)
    previous = last_event(con, employee_id, local_day)

    if previous is None:
        event = "keldi"
    elif previous["event"] == "keldi":
        elapsed = (ts - cfgmod.parse_ts(previous["ts"])).total_seconds()
        if elapsed < cfg["min_session_seconds"]:
            return {"status": "already", "event": "keldi", "ts": previous["ts"]}
        event = "ketdi"
    else:
        # They checked out earlier and came back: a new arrival, which is how a
        # lunch break closes itself without anyone marking a return.
        event = "keldi"

    event_id = _insert(con, employee_id, ts, local_day, event, "deliberate")
    return {"status": "recorded", "id": event_id, "event": event,
            "ts": ts.isoformat(), "grade": grade(cfg, ts, event)}


def register_first_sighting(con, cfg, employee_id, ts):
    """Record an arrival for someone who walked in without stopping.

    Without this, forgetting to stand would erase the arrival entirely. With it,
    the worst case is an arrival marked `first_sighting` instead of `deliberate`.
    """
    if not cfg["first_sighting_arrival"]:
        return None
    local_day = cfgmod.local_day(cfg, ts)
    if last_event(con, employee_id, local_day) is not None:
        return None
    event_id = _insert(con, employee_id, ts, local_day, "keldi", "first_sighting")
    return {"status": "recorded", "id": event_id, "event": "keldi",
            "ts": ts.isoformat(), "grade": grade(cfg, ts, "keldi")}


def close_stale(con, cfg, now):
    """Close the day for anyone whose arrival is still open and who is long gone.

    An open day would report as an endless shift, so every day gets closed — but
    timed at the moment they were last actually seen, never at the moment we
    noticed, so the record survives being questioned.

    This reads the database rather than in-process state on purpose: a restart
    or a power cut must not leave yesterday hanging open forever.
    """
    rows = con.execute(
        """SELECT a.employee_id, a.local_day, a.ts AS arrived_ts,
                  -- Only sightings at or after the arrival can close it; an
                  -- older one would date the departure before the arrival.
                  (SELECT MAX(s.ts) FROM presence_samples s
                    WHERE s.employee_id = a.employee_id AND s.ts >= a.ts) AS last_seen
             FROM attendance_events a
            WHERE a.cancelled = 0
              AND a.event = 'keldi'
              AND a.id = (SELECT MAX(b.id) FROM attendance_events b
                           WHERE b.employee_id = a.employee_id AND b.cancelled = 0)"""
    ).fetchall()

    closed = []
    for row in rows:
        seen_at = cfgmod.parse_ts(row["last_seen"] or row["arrived_ts"])
        if (now - seen_at).total_seconds() < cfg["absence_close_seconds"]:
            continue
        event_id = _insert(con, row["employee_id"], seen_at, row["local_day"],
                           "ketdi", "auto_close",
                           note="Belgilanmagan — oxirgi ko'rinish bo'yicha yopildi")
        closed.append({"id": event_id, "employee_id": row["employee_id"],
                       "ts": seen_at.isoformat()})
    return closed


def confirm_event(con, event_id, employee_id, confirmed, reason=None):
    """Record the employee's answer to a departure question.

    `employee_id` is required and checked: the button's payload travels through
    Telegram, so an id arriving from outside must never let one person edit
    another's record.
    """
    cur = con.execute(
        """UPDATE attendance_events
              SET confirmed = ?, reason = ?, confirmed_at = ?
            WHERE id = ? AND employee_id = ?""",
        (confirmed, reason, cfgmod.now_utc().isoformat(), event_id, employee_id),
    )
    con.commit()
    return cur.rowcount > 0


def cancel_event(con, event_id, employee_id):
    """Undo a departure the employee says never happened.

    The row is marked cancelled rather than deleted: "the camera recorded this
    and the employee rejected it" is worth keeping, and the next stand needs to
    see the day as it was before.
    """
    cur = con.execute(
        """UPDATE attendance_events
              SET cancelled = 1, confirmed = 'yoq', confirmed_at = ?
            WHERE id = ? AND employee_id = ?""",
        (cfgmod.now_utc().isoformat(), event_id, employee_id),
    )
    con.commit()
    return cur.rowcount > 0


def edit_event(con, cfg, event_id, ts=None, event=None, reason=None, note=None):
    """Admin correction. Separate from `confirm_event` because this one is not
    the employee answering a question but a manager overriding the record, and
    it stamps the row as `manual` so a report can tell the two apart.
    """
    row = con.execute("SELECT * FROM attendance_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return None
    new_ts = ts or cfgmod.parse_ts(row["ts"])
    con.execute(
        """UPDATE attendance_events
              SET ts = ?, local_day = ?, event = ?, reason = ?, note = ?,
                  source = 'manual', cancelled = 0
            WHERE id = ?""",
        (new_ts.isoformat(), cfgmod.local_day(cfg, new_ts),
         event or row["event"], reason if reason is not None else row["reason"],
         note if note is not None else row["note"], event_id),
    )
    con.commit()
    return event_id


def add_event(con, cfg, employee_id, ts, event, reason=None, note=None):
    """Create a record by hand, for a day the camera missed entirely."""
    event_id = _insert(con, employee_id, ts, cfgmod.local_day(cfg, ts),
                       event, "manual", note=note)
    if reason:
        con.execute("UPDATE attendance_events SET reason = ? WHERE id = ?",
                    (reason, event_id))
        con.commit()
    return event_id


def day_summary(con, cfg, employee_id, local_day):
    """One employee's day as arrival, departure, worked time and breaks."""
    rows = con.execute(
        """SELECT * FROM attendance_events
           WHERE employee_id = ? AND local_day = ? AND cancelled = 0
           ORDER BY ts""",
        (employee_id, local_day),
    ).fetchall()
    if not rows:
        return None

    worked = 0.0
    breaks = []
    open_at = None
    for row in rows:
        ts = cfgmod.parse_ts(row["ts"])
        if row["event"] == "keldi":
            if open_at is None:
                open_at = ts
            if breaks and breaks[-1]["end"] is None:
                breaks[-1]["end"] = row["ts"]
                breaks[-1]["seconds"] = int(
                    (ts - cfgmod.parse_ts(breaks[-1]["start"])).total_seconds())
        elif open_at is not None:
            worked += (ts - open_at).total_seconds()
            open_at = None
            breaks.append({"start": row["ts"], "end": None, "seconds": None,
                           "reason": row["reason"]})

    first, last = rows[0], rows[-1]
    return {
        "employee_id": employee_id,
        "local_day": local_day,
        "arrived": first["ts"] if first["event"] == "keldi" else None,
        "left": last["ts"] if last["event"] == "ketdi" else None,
        "still_in": open_at is not None,
        "worked_seconds": int(worked),
        # A break still open means they left and have not come back, so it is
        # the end of the day rather than a break.
        "breaks": [b for b in breaks if b["end"] is not None],
        "events": [dict(r) for r in rows],
    }
