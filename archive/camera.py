#!/usr/bin/env python3
"""Camera watcher: detects a person + a cell phone in frame via YOLO (GPU).
If a phone stays visible near a person for longer than PHONE_VIOLATION_SECONDS,
logs a violation. Also tracks presence (person in frame) for arrival/departure.

Detection runs once per DETECT_EVERY_SECONDS; the preview image is refreshed much
more often (PREVIEW_FPS) with the last known boxes drawn on, so the dashboard feed
looks like video instead of a slideshow without costing extra GPU time.
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn, get_or_create_employee, init_db

import cv2
from ultralytics import YOLO

EMPLOYEE_NAME = "Deamon (test)"
CAMERA_INDEX = 0
DETECT_EVERY_SECONDS = 1.0
PREVIEW_FPS = 12   # preview is cheap (encode only); detection still runs once a second
PHONE_VIOLATION_SECONDS = 10  # TEST MODE: 10s. Change to 180 (3 min) for production use.
PRESENCE_ABSENT_THRESHOLD = 30  # seconds without a detected person = "left desk"
CONF_THRESHOLD = 0.6
PHONE_PROXIMITY_MARGIN = 60  # px: phone box must be within/near a person box to count as "in use"
STATIC_WINDOW_SECONDS = 30   # how long to watch a "person" box before judging it a poster
STATIC_MAX_MOVEMENT_PX = 18  # a real person drifts more than this over the window; a poster never moves
MIN_PHONE_EVENT_SECONDS = 5  # shorter phone sightings are blips, not events

MODEL_PATH = Path(__file__).resolve().parent / "models" / "yolov8n.pt"
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "snapshot.jpg"
STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "camera_status.json"
VIOLATIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "violations"

# COCO class ids: 0 = person, 67 = cell phone
PERSON_CLASS = 0
PHONE_CLASS = 67

COLOR_PERSON = (160, 229, 0)   # BGR
COLOR_PHONE = (94, 77, 255)    # BGR

TELEGRAM_NOTIFY_ENABLED = False  # OFF until the owner explicitly asks to turn it on
TELEGRAM_TARGET = "telegram:6457806904"  # owner's chat only — never the bot's default home channel

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file + rename so a reader never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def draw_boxes(frame, person_boxes, phone_boxes):
    """Draw the last known detections onto a fresh frame (cheap, no model call)."""
    for boxes, color, label in ((person_boxes, COLOR_PERSON, "PERSON"), (phone_boxes, COLOR_PHONE, "PHONE")):
        for b in boxes:
            p1, p2 = (int(b[0]), int(b[1])), (int(b[2]), int(b[3]))
            cv2.rectangle(frame, p1, p2, color, 2)
            cv2.putText(frame, label, (p1[0], max(14, p1[1] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return frame


def insert_phone_event(con, employee_id, start_ts, end_ts, duration, is_violation) -> int:
    cur = con.execute(
        "INSERT INTO phone_events (employee_id, start_ts, end_ts, duration_seconds, is_violation) VALUES (?, ?, ?, ?, ?)",
        (employee_id, start_ts, end_ts, duration, int(is_violation)),
    )
    con.commit()
    return cur.lastrowid


def finalize_phone_event(con, event_id, end_ts, duration) -> None:
    """Close the row opened when the violation fired — one session is one row, never two."""
    con.execute(
        "UPDATE phone_events SET end_ts = ?, duration_seconds = ? WHERE id = ?",
        (end_ts, duration, event_id),
    )
    con.commit()


def laptop_is_active(con, employee_id) -> bool:
    row = con.execute(
        "SELECT is_idle, ts FROM activity_log WHERE employee_id = ? ORDER BY ts DESC LIMIT 1",
        (employee_id,),
    ).fetchone()
    if not row:
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["ts"])).total_seconds()
    return age < 60 and row["is_idle"] == 0


def send_telegram_alert(employee_name, duration_seconds, violation_filename):
    """Send the photo captured at the moment of violation, then a text alert, via the Docker-hosted Hermes bot."""
    if not TELEGRAM_NOTIFY_ENABLED:
        print(f"[notify-disabled] would have sent alert ({duration_seconds}s, {violation_filename})")
        return
    docker_photo_path = f"/host_home/Projects/employee-monitor/data/violations/{violation_filename}"
    photo_cmd = f"su hermes -c \"hermes send -t {TELEGRAM_TARGET} 'MEDIA:{docker_photo_path}'\""
    text = f"⚠️ {employee_name} telefonda {duration_seconds} soniyadan ko'proq o'tirdi — ishlamayapti!"
    text_cmd = f"su hermes -c \"hermes send -t {TELEGRAM_TARGET} '{text}'\""
    for cmd in (photo_cmd, text_cmd):
        try:
            subprocess.run(["docker", "exec", "hermes", "sh", "-c", cmd], timeout=20, capture_output=True)
        except Exception as e:
            print("[notify] failed:", e)


def boxes_near(box_a, box_b, margin):
    """True if box_a's center falls inside box_b expanded by `margin` px on each side."""
    ax = (box_a[0] + box_a[2]) / 2
    ay = (box_a[1] + box_a[3]) / 2
    return (box_b[0] - margin <= ax <= box_b[2] + margin) and (box_b[1] - margin <= ay <= box_b[3] + margin)


def is_static_poster(position_history) -> bool:
    """A real person's box drifts over time; a wall poster's stays frozen in place."""
    now = time.time()
    recent = [p for p in position_history if now - p[0] <= STATIC_WINDOW_SECONDS]
    if len(recent) < 10 or (recent[-1][0] - recent[0][0]) < STATIC_WINDOW_SECONDS * 0.6:
        return False  # not enough history yet — give it the benefit of the doubt
    xs = [p[1] for p in recent]
    ys = [p[2] for p in recent]
    return max(max(xs) - min(xs), max(ys) - min(ys)) < STATIC_MAX_MOVEMENT_PX


def log_presence(con, employee_id, event, ts=None):
    con.execute(
        "INSERT INTO presence_log (employee_id, ts, event) VALUES (?, ?, ?)",
        (employee_id, ts or datetime.now(timezone.utc).isoformat(), event),
    )
    con.commit()


def resume_or_close_presence(con, employee_id) -> bool:
    """A previous run may have died leaving an unmatched 'arrived'.

    Close it at the last moment the camera was actually observing (the status file),
    so the record keeps arrival/departure paired instead of piling up arrivals.
    Returns True when the open session is still current and should be kept.
    """
    row = con.execute(
        "SELECT event, ts FROM presence_log WHERE employee_id = ? ORDER BY ts DESC LIMIT 1",
        (employee_id,),
    ).fetchone()
    if not row or row["event"] != "arrived":
        return False

    arrived_at = datetime.fromisoformat(row["ts"])
    last_seen = arrived_at
    try:
        last_seen = max(last_seen, datetime.fromisoformat(json.loads(STATUS_PATH.read_text())["ts"]))
    except Exception:
        pass

    gap = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if gap <= PRESENCE_ABSENT_THRESHOLD:
        return True  # camera only blinked — same session continues, don't log a duplicate arrival
    log_presence(con, employee_id, "left", ts=last_seen.isoformat())
    print("[presence] closed dangling session from previous run")
    return False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    init_db()
    employee_id = get_or_create_employee(EMPLOYEE_NAME)
    print(f"Camera watcher for '{EMPLOYEE_NAME}' (id={employee_id})")

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = YOLO(str(MODEL_PATH) if MODEL_PATH.exists() else "yolov8n.pt")
    model.to(device)
    print("Model loaded. Device:", device)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("ERROR: could not open camera", CAMERA_INDEX)
        return

    con = get_conn()
    present = resume_or_close_presence(con, employee_id)
    con.close()
    if present:
        print("[presence] resumed open session from previous run")

    phone_start = None
    phone_event_id = None
    violation_fired = False
    last_present_ts = time.time()
    position_history = []   # [(ts, cx, cy), ...] of the primary person box, for the poster filter
    person_boxes, phone_boxes = [], []
    person_seen = phone_seen = laptop_active = truly_working = False

    last_detect = 0.0
    last_preview = 0.0
    preview_interval = 1.0 / PREVIEW_FPS

    try:
        while _running:
            if not cap.grab():          # keep draining the driver buffer so frames stay fresh
                time.sleep(0.2)
                continue

            now = time.time()
            if now - last_preview < preview_interval:
                time.sleep(0.01)
                continue
            last_preview = now

            ok, frame = cap.retrieve()
            if not ok:
                continue

            run_detection = (now - last_detect) >= DETECT_EVERY_SECONDS
            if run_detection:
                last_detect = now
                results = model(frame, verbose=False, classes=[PERSON_CLASS, PHONE_CLASS], device=device)
                person_boxes, phone_boxes = [], []
                if results and results[0].boxes is not None:
                    for box in results[0].boxes:
                        if float(box.conf[0]) < CONF_THRESHOLD:
                            continue
                        xyxy = tuple(float(v) for v in box.xyxy[0])
                        (person_boxes if int(box.cls[0]) == PERSON_CLASS else phone_boxes).append(xyxy)

                # --- Static-poster filter: track ONE box by nearest-neighbor continuity,
                # so a poster's history never gets mixed with a real person's movement. ---
                raw_person_seen = len(person_boxes) > 0
                if raw_person_seen:
                    if position_history:
                        last_cx, last_cy = position_history[-1][1], position_history[-1][2]
                        primary = min(
                            person_boxes,
                            key=lambda b: ((b[0] + b[2]) / 2 - last_cx) ** 2 + ((b[1] + b[3]) / 2 - last_cy) ** 2,
                        )
                    else:
                        primary = max(person_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    cx, cy = (primary[0] + primary[2]) / 2, (primary[1] + primary[3]) / 2
                    position_history.append((now, cx, cy))
                    position_history = [p for p in position_history if now - p[0] <= STATIC_WINDOW_SECONDS]
                else:
                    position_history = []
                person_seen = raw_person_seen and not is_static_poster(position_history)

                # --- Phone only counts if it's actually near a real (non-poster) person ---
                phone_seen = bool(phone_boxes) and person_seen and any(
                    boxes_near(pb, person, PHONE_PROXIMITY_MARGIN)
                    for pb in phone_boxes for person in person_boxes
                )

            annotated = draw_boxes(frame, person_boxes, phone_boxes)
            ok_enc, buf = cv2.imencode(".jpg", annotated)
            if ok_enc:
                atomic_write_bytes(SNAPSHOT_PATH, buf.tobytes())

            if not run_detection:
                continue    # preview-only tick: nothing to record

            try:
                con = get_conn()
                laptop_active = laptop_is_active(con, employee_id)
                truly_working = person_seen and laptop_active and not phone_seen

                # --- Presence (arrived / left desk) ---
                if person_seen:
                    last_present_ts = now
                    if not present:
                        present = True
                        log_presence(con, employee_id, "arrived")
                        print("[presence] arrived")
                elif present and (now - last_present_ts) > PRESENCE_ABSENT_THRESHOLD:
                    present = False
                    log_presence(con, employee_id, "left")
                    print("[presence] left desk")

                # --- Phone usage: one continuous session == exactly one row ---
                if phone_seen:
                    if phone_start is None:
                        phone_start = now
                        violation_fired = False
                        phone_event_id = None
                    elapsed = now - phone_start
                    if elapsed >= PHONE_VIOLATION_SECONDS and not violation_fired:
                        violation_fired = True
                        phone_event_id = insert_phone_event(
                            con, employee_id,
                            datetime.fromtimestamp(phone_start, tz=timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(),
                            int(elapsed), is_violation=True,
                        )
                        VIOLATIONS_DIR.mkdir(parents=True, exist_ok=True)
                        violation_filename = f"violation_{int(now)}.jpg"
                        if ok_enc:
                            atomic_write_bytes(VIOLATIONS_DIR / violation_filename, buf.tobytes())
                        print(f"[ALERT] phone used {int(elapsed)}s continuously — violation logged")
                        send_telegram_alert(EMPLOYEE_NAME, int(elapsed), violation_filename)
                elif phone_start is not None:
                    elapsed = now - phone_start
                    end_ts = datetime.now(timezone.utc).isoformat()
                    if phone_event_id is not None:
                        finalize_phone_event(con, phone_event_id, end_ts, int(elapsed))
                    elif elapsed >= MIN_PHONE_EVENT_SECONDS:
                        insert_phone_event(
                            con, employee_id,
                            datetime.fromtimestamp(phone_start, tz=timezone.utc).isoformat(),
                            end_ts, int(elapsed), is_violation=False,
                        )
                    phone_start = None
                    phone_event_id = None
                    violation_fired = False

                con.close()
            except Exception as e:
                print("[camera] db step failed, continuing:", e)

            atomic_write_text(STATUS_PATH, json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "person_seen": person_seen,
                "phone_seen": phone_seen,
                "laptop_active": laptop_active,
                "truly_working": truly_working,
                "phone_seen_since": datetime.fromtimestamp(phone_start, tz=timezone.utc).isoformat() if phone_start else None,
                "phone_violation_seconds": PHONE_VIOLATION_SECONDS,
            }))
    finally:
        cap.release()
        try:
            con = get_conn()
            if phone_start is not None:
                elapsed = time.time() - phone_start
                end_ts = datetime.now(timezone.utc).isoformat()
                if phone_event_id is not None:
                    finalize_phone_event(con, phone_event_id, end_ts, int(elapsed))
                elif elapsed >= MIN_PHONE_EVENT_SECONDS:
                    insert_phone_event(
                        con, employee_id,
                        datetime.fromtimestamp(phone_start, tz=timezone.utc).isoformat(),
                        end_ts, int(elapsed), is_violation=False,
                    )
            if present:
                log_presence(con, employee_id, "left")
                print("[presence] left desk (shutdown)")
            con.close()
        except Exception as e:
            print("[camera] shutdown bookkeeping failed:", e)


if __name__ == "__main__":
    main()
