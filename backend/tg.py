"""Pairing, message composition and the outbox — everything except the network.

Keeping the network out of this module is what makes the Telegram side testable
and what lets the whole flow run with sending switched off: messages are
composed and stored exactly as they would be sent, so the outbox can be reviewed
before a single employee's phone buzzes. `bot.py` is the only part that talks to
Telegram, and it only ever reads what this module wrote.
"""
import json
import secrets
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attendance
import config as cfgmod
from db import FULL_NAME_SQL

# No 0/O/1/I/L: these codes get read aloud and written on paper, and a code the
# employee cannot retype is worse than a slightly shorter one.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8

REASONS = [
    ("obed", "Обед"),
    ("shifokor", "Врач"),
    ("shaxsiy", "Личное дело"),
    ("topshiriq", "Рабочее поручение"),
    ("boshqa", "Другое"),
]
REASON_LABELS = dict(REASONS)

# Display wording only — the database keeps keldi/ketdi as the event names.
EVENT_WORDS = {"keldi": "вошёл", "ketdi": "вышел"}


# ── Pairing ───────────────────────────────────────────────────────────────────

def _raw_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    """Group as XXXX-XXXX; easier to read back without losing one's place."""
    return f"{code[:4]}-{code[4:]}"


def normalize_code(text: str) -> str:
    """Accept whatever the employee typed: spaces, dashes, lower case."""
    return "".join(ch for ch in text.upper() if ch in CODE_ALPHABET)


def new_code(con, cfg, employee_id: int) -> dict:
    """Issue a fresh code, retiring any unused one this employee still has.

    Only one code per employee can be live, otherwise a code handed out and
    forgotten weeks ago would still work.
    """
    now = cfgmod.now_utc()
    con.execute(
        "UPDATE pairing_codes SET expires_at = ? WHERE employee_id = ? AND used_at IS NULL",
        (now.isoformat(), employee_id),
    )
    code = _raw_code()
    expires = now + timedelta(hours=cfg["pairing_code_hours"])
    con.execute(
        """INSERT INTO pairing_codes (code, employee_id, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (code, employee_id, now.isoformat(), expires.isoformat()),
    )
    con.commit()
    return {"code": code, "display": format_code(code), "expires_at": expires.isoformat()}


def _too_many_attempts(con, cfg, telegram_id: int) -> bool:
    since = (cfgmod.now_utc() - timedelta(hours=1)).isoformat()
    row = con.execute(
        "SELECT COUNT(*) c FROM pairing_attempts WHERE telegram_id = ? AND ts >= ?",
        (telegram_id, since),
    ).fetchone()
    return row["c"] >= cfg["pairing_attempts_per_hour"]


def redeem(con, cfg, raw_text: str, telegram_id: int, username: str = "") -> dict:
    """Try to link a Telegram account using a code the employee sent.

    Returns {"ok": bool, "reason": str, ...}. Every failure is rate limited,
    because eight characters are only unguessable while guessing is expensive.
    """
    if _too_many_attempts(con, cfg, telegram_id):
        return {"ok": False, "reason": "rate_limited"}

    code = normalize_code(raw_text)
    now = cfgmod.now_utc()

    def fail(reason):
        con.execute("INSERT INTO pairing_attempts (telegram_id, ts) VALUES (?, ?)",
                    (telegram_id, now.isoformat()))
        con.commit()
        return {"ok": False, "reason": reason}

    if len(code) != CODE_LENGTH:
        return fail("malformed")

    row = con.execute("SELECT * FROM pairing_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        return fail("unknown")
    if row["used_at"]:
        return fail("used")
    if cfgmod.parse_ts(row["expires_at"]) < now:
        return fail("expired")

    # One Telegram account stands for one person: a shared phone must not be
    # able to clock in two employees.
    taken = con.execute(
        "SELECT employee_id FROM telegram_links WHERE telegram_id = ? AND employee_id != ?",
        (telegram_id, row["employee_id"]),
    ).fetchone()
    if taken:
        return fail("account_taken")

    con.execute(
        """INSERT INTO telegram_links (employee_id, telegram_id, username, linked_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(employee_id) DO UPDATE SET
               telegram_id = excluded.telegram_id,
               username = excluded.username,
               linked_at = excluded.linked_at""",
        (row["employee_id"], telegram_id, username, now.isoformat()),
    )
    con.execute("UPDATE pairing_codes SET used_at = ?, used_by = ? WHERE code = ?",
                (now.isoformat(), telegram_id, code))
    con.commit()
    return {"ok": True, "employee_id": row["employee_id"],
            "name": employee_name(con, row["employee_id"])}


def unlink(con, employee_id: int) -> bool:
    cur = con.execute("DELETE FROM telegram_links WHERE employee_id = ?", (employee_id,))
    con.commit()
    return cur.rowcount > 0


def employee_for(con, telegram_id: int):
    return con.execute(
        "SELECT employee_id FROM telegram_links WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()


def link_for(con, employee_id: int):
    return con.execute(
        "SELECT * FROM telegram_links WHERE employee_id = ?", (employee_id,)
    ).fetchone()


def employee_name(con, employee_id: int) -> str:
    row = con.execute(
        f"SELECT {FULL_NAME_SQL} AS name FROM employees e WHERE e.id = ?", (employee_id,)
    ).fetchone()
    return row["name"] if row else str(employee_id)


# ── Outbox ────────────────────────────────────────────────────────────────────

def queue(con, kind: str, employee_id, body: str, buttons=None, event_id=None) -> int:
    """Store a message to be sent. Writes even with nobody linked and sending
    off, so the outbox doubles as the record of what the system decided to say.
    """
    link = link_for(con, employee_id) if employee_id else None
    # created_at is written here rather than left to SQLite's datetime('now'),
    # which formats as "2026-10-27 13:00:00" while every other timestamp in this
    # schema is "2026-10-27T13:00:00+00:00". Those two do not compare as strings
    # — a space sorts below "T" — so a mixed column silently breaks any
    # "happened since" query against it.
    cur = con.execute(
        """INSERT INTO telegram_outbox
               (employee_id, telegram_id, event_id, kind, body, buttons, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, link["telegram_id"] if link else None, event_id, kind, body,
         json.dumps(buttons) if buttons else None, cfgmod.now_utc().isoformat()),
    )
    con.commit()
    return cur.lastrowid


def queue_once(con, kind: str, employee_id, body: str, min_gap_seconds: int,
               buttons=None, event_id=None):
    """Queue a message only if the same kind did not already go to this person
    recently.

    Some messages are triggered by a condition that stays true rather than by
    something new happening — standing at the camera again after already being
    recorded is the clear case. Without a gap the queue fills with the same
    sentence once per cooldown, which on a phone is indistinguishable from the
    system being broken. Returns the row id, or None if it was suppressed.
    """
    since = (cfgmod.now_utc() - timedelta(seconds=min_gap_seconds)).isoformat()
    recent = con.execute(
        """SELECT 1 FROM telegram_outbox
            WHERE kind = ? AND employee_id = ? AND created_at >= ?
            LIMIT 1""",
        (kind, employee_id, since),
    ).fetchone()
    if recent:
        return None
    return queue(con, kind, employee_id, body, buttons=buttons, event_id=event_id)


def pending(con, limit: int = 50):
    """Unsent messages that have somewhere to go, oldest first."""
    return con.execute(
        """SELECT * FROM telegram_outbox
            WHERE sent_at IS NULL AND telegram_id IS NOT NULL AND error IS NULL
            ORDER BY id LIMIT ?""",
        (limit,),
    ).fetchall()


def mark_sent(con, outbox_id: int):
    con.execute("UPDATE telegram_outbox SET sent_at = ?, error = NULL WHERE id = ?",
                (cfgmod.now_utc().isoformat(), outbox_id))
    con.commit()


def mark_failed(con, outbox_id: int, error: str):
    con.execute("UPDATE telegram_outbox SET error = ? WHERE id = ?", (error[:500], outbox_id))
    con.commit()


# ── Message texts ─────────────────────────────────────────────────────────────

def _hhmm(cfg, ts) -> str:
    return cfgmod.to_local(cfg, cfgmod.parse_ts(ts)).strftime("%H:%M")


def arrival_message(cfg, con, employee_id, event_id, ts, grade) -> int:
    lines = [f"✅ Вы пришли · {_hhmm(cfg, ts)}"]
    if grade.get("late_minutes"):
        lines.append(f"Опоздание {attendance.human_minutes(grade['late_minutes'])}")
    elif grade.get("early_minutes"):
        lines.append(f"Раньше на {attendance.human_minutes(grade['early_minutes'])}")
    else:
        lines.append("Вовремя")
    return queue(con, "keldi", employee_id, "\n".join(lines), event_id=event_id)


def departure_message(cfg, con, employee_id, event_id, ts, grade, summary) -> int:
    """Departure after the day is over: report the day, ask nothing."""
    lines = [f"👋 Вы ушли · {_hhmm(cfg, ts)}"]
    if summary and summary.get("arrived"):
        lines.append(f"Сегодня: {_hhmm(cfg, summary['arrived'])} – {_hhmm(cfg, ts)}")
    if summary and summary.get("worked_seconds"):
        hours, rest = divmod(summary["worked_seconds"], 3600)
        lines.append(f"Итого: {hours} ч {rest // 60} мин")
    breaks = sum(b["seconds"] or 0 for b in (summary or {}).get("breaks", []))
    if breaks:
        lines.append(f"Перерыв: {attendance.human_minutes(breaks // 60)}")
    if grade.get("overtime_minutes"):
        lines.append(f"Переработка: {attendance.human_minutes(grade['overtime_minutes'])}")
    return queue(con, "ketdi", employee_id, "\n".join(lines), event_id=event_id)


def early_departure_message(cfg, con, employee_id, event_id, ts, grade) -> int:
    """Departure before the day is over — the one case that needs an answer.

    The record already stands; the buttons only let the employee correct or
    explain it, so an unanswered message still leaves a usable record.
    """
    body = (f"🚪 Вы ушли? · {_hhmm(cfg, ts)}\n"
            f"До конца рабочего дня {attendance.human_minutes(grade['early_leave_minutes'])}.")
    buttons = [[{"text": "Да", "data": f"leave:ha:{event_id}"},
                {"text": "Нет", "data": f"leave:yoq:{event_id}"},
                {"text": "Причина", "data": f"leave:sabab:{event_id}"}]]
    return queue(con, "ketdi_savol", employee_id, body, buttons=buttons, event_id=event_id)


def reason_prompt(event_id) -> dict:
    """The follow-up keyboard shown when they tap "Sabab"."""
    rows, row = [], []
    for key, label in REASONS:
        row.append({"text": label, "data": f"reason:{key}:{event_id}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"body": "Причина?", "buttons": rows}


def auto_close_message(cfg, con, employee_id, event_id, ts) -> int:
    body = (f"🌙 Вы не отметили уход.\n"
            f"Записали {_hhmm(cfg, ts)} — по последнему появлению.")
    buttons = [[{"text": "Верно", "data": f"close:ok:{event_id}"},
                {"text": "Исправить", "data": f"close:fix:{event_id}"}]]
    return queue(con, "auto_close", employee_id, body, buttons=buttons, event_id=event_id)


# Someone standing at the camera a second time is reassured once; told the same
# thing every minute they are being pestered. Half an hour is long enough that a
# genuine second visit still gets an answer.
ALREADY_GAP_SECONDS = 1800


def already_message(cfg, con, employee_id, ts):
    return queue_once(con, "allaqachon", employee_id,
                      f"ℹ️ Уже записано · {_hhmm(cfg, ts)}",
                      ALREADY_GAP_SECONDS)


def day_report(cfg, con, employee_id, local_day) -> str:
    """Text for /bugun — the employee's own record, which they can always see."""
    summary = attendance.day_summary(con, cfg, employee_id, local_day)
    if not summary:
        return f"{local_day} — записей нет."

    lines = [f"📅 {local_day}"]
    for event in summary["events"]:
        mark = "✅" if event["event"] == "keldi" else "👋"
        extra = ""
        if event["reason"]:
            extra = f" · {REASON_LABELS.get(event['reason'], event['reason'])}"
        elif event["source"] == "auto_close":
            extra = " · закрыто системой"
        elif event["source"] == "first_sighting":
            extra = " · вы не останавливались"
        lines.append(f"{mark} {EVENT_WORDS.get(event['event'], event['event'])} {_hhmm(cfg, event['ts'])}{extra}")

    minutes_late = _lateness(cfg, con, employee_id, local_day)
    if minutes_late:
        lines.append(f"\n🔴 Опоздание {attendance.human_minutes(minutes_late)} "
                     f"(начало работы {cfg['work_start']})")
    if summary["worked_seconds"]:
        hours, rest = divmod(summary["worked_seconds"], 3600)
        lines.append(f"Итого: {hours} ч {rest // 60} мин")
    if summary["still_in"]:
        lines.append("Сейчас вы на работе")
    return "\n".join(lines)


def _lateness(cfg, con, employee_id, local_day):
    """How late this day's arrival was, in minutes. 0 if on time or absent."""
    row = con.execute(
        """SELECT ts FROM attendance_events
            WHERE employee_id = ? AND local_day = ? AND event = 'keldi' AND cancelled = 0
            ORDER BY ts LIMIT 1""",
        (employee_id, local_day),
    ).fetchone()
    if not row:
        return 0
    return attendance.grade(cfg, cfgmod.parse_ts(row["ts"]), "keldi").get("late_minutes", 0)


def late_report(cfg, con, employee_id, days: int = 30) -> str:
    """Text for /kechikish — each late day named, then the total.

    Listed day by day rather than only totalled: a single number invites an
    argument, a list of dates and times answers it.
    """
    today = cfgmod.to_local(cfg, cfgmod.now_utc()).date()
    lines, total = [], 0
    for offset in range(days, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        minutes = _lateness(cfg, con, employee_id, day)
        if not minutes:
            continue
        total += minutes
        row = con.execute(
            """SELECT ts FROM attendance_events
                WHERE employee_id = ? AND local_day = ? AND event = 'keldi' AND cancelled = 0
                ORDER BY ts LIMIT 1""",
            (employee_id, day),
        ).fetchone()
        lines.append(f"🔴 {day}  {_hhmm(cfg, row['ts'])}  "
                     f"— опоздание {attendance.human_minutes(minutes)}")

    if not lines:
        return (f"✅ За последние {days} дней опозданий нет.\n"
                f"Начало работы: {cfg['work_start']}")
    header = f"⏰ Опоздания (за {days} дней)\nНачало работы: {cfg['work_start']}\n"
    footer = (f"\n\nДней с опозданием: {len(lines)}\n"
              f"Итого: {attendance.human_minutes(total)}")
    return header + "\n".join(lines) + footer


def week_report(cfg, con, employee_id) -> str:
    """Text for /hafta — this week's days, so nothing has to be taken on trust."""
    today = cfgmod.to_local(cfg, cfgmod.now_utc()).date()
    monday = today - timedelta(days=today.weekday())

    lines = ["🗓 Эта неделя"]
    total = 0
    for offset in range((today - monday).days + 1):
        day = (monday + timedelta(days=offset)).isoformat()
        summary = attendance.day_summary(con, cfg, employee_id, day)
        if not summary:
            lines.append(f"{day} — нет")
            continue
        total += summary["worked_seconds"]
        arrived = _hhmm(cfg, summary["arrived"]) if summary["arrived"] else "—"
        minutes_late = _lateness(cfg, con, employee_id, day)
        late = f"  🔴 опоздание {attendance.human_minutes(minutes_late)}" if minutes_late else ""
        if summary["left"]:
            hours, rest = divmod(summary["worked_seconds"], 3600)
            worked = f"{hours}ч {rest // 60}м"
            left = _hhmm(cfg, summary["left"])
        else:
            # No departure yet, so there are no hours to report. Saying "0s 0d"
            # here reads as "worked nothing", which is a different claim.
            worked, left = "открыт", "…"
        lines.append(f"{day}  {arrived}–{left}  {worked}{late}")

    hours, rest = divmod(total, 3600)
    lines.append(f"\nИтого: {hours} ч {rest // 60} мин")
    return "\n".join(lines)
