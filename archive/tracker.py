#!/usr/bin/env python3
"""Laptop activity tracker: logs active window + idle state per employee to SQLite."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn, get_or_create_employee, init_db

POLL_SECONDS = 10

# No idle daemon runs under this Hyprland session and /dev/input needs the `input`
# group, so activity is read from hardware interrupt counters (real keystrokes and
# touchpad events) plus cursor position and the focused window's class. The window
# TITLE is deliberately excluded: animated titles (terminal spinners, clocks, media
# players) tick on their own and would fake activity forever.
IDLE_THRESHOLD_SECONDS = 180

# /proc/interrupts lines for human input devices. USB controllers are excluded on
# purpose — their counters climb from unrelated bus traffic.
INPUT_IRQ_PATTERNS = ("i8042", "ELAN", "SYNA", "touchpad", "keyboard", "mouse")

# Each employee's laptop sets its own name here (one tracker process = one employee).
EMPLOYEE_NAME = "Deamon (test)"


def get_active_window():
    try:
        out = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=5
        )
        data = json.loads(out.stdout)
        return data.get("class", "unknown"), data.get("title", "")
    except Exception:
        return "unknown", ""


def get_cursor_pos():
    try:
        out = subprocess.run(
            ["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip()
    except Exception:
        return ""


def get_input_event_count() -> int:
    """Total interrupts raised by keyboard/touchpad — rises only on real human input."""
    total = 0
    try:
        for line in Path("/proc/interrupts").read_text().splitlines():
            if not any(p.lower() in line.lower() for p in INPUT_IRQ_PATTERNS):
                continue
            for token in line.split()[1:]:
                if not token.isdigit():
                    break   # per-CPU counts end where the controller description starts
                total += int(token)
    except Exception:
        pass
    return total


def main():
    init_db()
    employee_id = get_or_create_employee(EMPLOYEE_NAME)
    print(f"Tracking '{EMPLOYEE_NAME}' (id={employee_id})")

    last_signature = None
    last_change_ts = time.time()

    while True:
        app_class, app_title = get_active_window()
        signature = (app_class, get_cursor_pos(), get_input_event_count())

        now = time.time()
        if signature != last_signature:
            last_signature = signature
            last_change_ts = now
        is_idle = (now - last_change_ts) >= IDLE_THRESHOLD_SECONDS

        ts = datetime.now(timezone.utc).isoformat()
        try:
            con = get_conn()
            con.execute(
                "INSERT INTO activity_log (employee_id, ts, app_class, app_title, is_idle) VALUES (?, ?, ?, ?, ?)",
                (employee_id, ts, app_class, app_title, int(is_idle)),
            )
            con.commit()
            con.close()
        except Exception as e:
            # A transient DB lock must never kill the tracker — skip this sample instead.
            print("[tracker] db write failed, skipping sample:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
