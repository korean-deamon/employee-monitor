#!/usr/bin/env python3
"""Version 1 — identify enrolled people on camera and record when they arrived.

Recognition runs a few times a second; the preview is encoded much more often so
the dashboard feed stays smooth. Nothing else is tracked in V1: no phone
detection, no app activity — just who is here and since when.
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attendance
import config as cfgmod
from db import get_conn, init_db
from faces import (ENROLL_POSES, MATCH_THRESHOLD, MIN_DET_SCORE, MIN_FACE_WIDTH_PX,
                   REDUNDANT_SIMILARITY, best_similarity, blob_to_embedding, detect_faces,
                   embedding_to_blob, find_duplicate, identify, load_known_faces)
from standing import StandDetector
import bodies
import counting
import tg

CAMERA_INDEX = 0
RECOGNIZE_EVERY_SECONDS = 0.5
PREVIEW_FPS = 12
RELOAD_FACES_EVERY_SECONDS = 30   # pick up newly enrolled people without a restart

SAMPLE_EVERY_SECONDS = 15           # how often a visible person is recorded as "seen"
CLOSE_CHECK_EVERY_SECONDS = 60      # how often open days are checked for closing

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_PATH = DATA_DIR / "snapshot.jpg"
COUNTER_SNAPSHOT_PATH = DATA_DIR / "counter_snapshot.jpg"
STATUS_PATH = DATA_DIR / "camera_status.json"
ENROLL_REQUEST_PATH = DATA_DIR / "enroll_request.json"
ENROLL_RESULT_PATH = DATA_DIR / "enroll_result.json"
FACES_DIR = DATA_DIR / "faces"
SECONDS_BETWEEN_ENROLL_SAMPLES = 0.8   # spacing between shots of the same pose
POSE_CHANGE_PAUSE_SECONDS = 2.5        # long enough to read the next pose and turn
MAX_SKIPS_BEFORE_ACCEPT = 5            # don't stall on someone who cannot vary their pose

COLOR_KNOWN = (160, 229, 0)     # BGR
COLOR_UNKNOWN = (120, 120, 120)

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def log_sighting(con, employee_id, ts=None):
    con.execute(
        "INSERT INTO presence_samples (employee_id, ts) VALUES (?, ?)",
        (employee_id, ts or datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def name_of(ids_names, employee_id):
    return next((n for i, n in ids_names if i == employee_id), str(employee_id))


def announce(cfg, con, employee_id, result, ts):
    """Queue the Telegram message this attendance decision calls for.

    Queued, not sent: whether anything leaves the building is `telegram_enabled`,
    and `bot.py` decides that. Here the message is only composed and recorded.
    """
    grade = result["grade"]
    if result["event"] == "keldi":
        tg.arrival_message(cfg, con, employee_id, result["id"], result["ts"], grade)
    elif grade.get("needs_reason"):
        tg.early_departure_message(cfg, con, employee_id, result["id"], result["ts"], grade)
    else:
        summary = attendance.day_summary(con, cfg, employee_id,
                                         cfgmod.local_day(cfg, ts))
        tg.departure_message(cfg, con, employee_id, result["id"], result["ts"],
                             grade, summary)


def open_days(con, cfg, ids_names, now):
    """Who is currently counted as in the building, newest arrival first."""
    rows = con.execute(
        """SELECT a.employee_id, a.ts, a.source
             FROM attendance_events a
            WHERE a.cancelled = 0
              AND a.event = 'keldi'
              AND a.id = (SELECT MAX(b.id) FROM attendance_events b
                           WHERE b.employee_id = a.employee_id AND b.cancelled = 0)
            ORDER BY a.ts"""
    ).fetchall()
    return [{"employee_id": r["employee_id"], "name": name_of(ids_names, r["employee_id"]),
             "arrived_at": r["ts"], "source": r["source"]} for r in rows]


class Enroller:
    """Runs an admin-requested enrollment using the camera this process owns.

    The admin page cannot open the camera itself, so it drops a request file and
    this picks it up, samples the closest face a few times, and reports back.
    """

    def __init__(self):
        self.request = None
        self.saved = 0
        self.pose_index = 0
        self.saved_in_pose = 0
        self.skips_in_row = 0
        self.next_sample_at = 0.0
        self.stored = []    # this person's embeddings, for the same-person and pose checks

    def _pose(self):
        return ENROLL_POSES[min(self.pose_index, len(ENROLL_POSES) - 1)]

    def _report(self, state, message=""):
        payload = {"state": state, "saved": self.saved, "message": message}
        if self.request:
            payload["employee_id"] = self.request["employee_id"]
            payload["samples"] = self.request["samples"]
            payload["instruction"] = self._pose()[0]
            payload["pose"] = min(self.pose_index + 1, len(ENROLL_POSES))
            payload["pose_total"] = len(ENROLL_POSES)
        tmp = ENROLL_RESULT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, ENROLL_RESULT_PATH)

    def _finish(self, state, message=""):
        self._report(state, message)
        ENROLL_REQUEST_PATH.unlink(missing_ok=True)
        self.request = None
        self.saved = 0
        self.stored = []

    def _wait(self, now, message):
        """Nothing stored this round — tell the admin why and try again shortly."""
        self._report("running", message)
        self.next_sample_at = now + SECONDS_BETWEEN_ENROLL_SAMPLES

    def poll(self, con, frame, faces, now):
        """Advance an enrollment if one is pending. Returns True while active."""
        if self.request is None:
            try:
                self.request = json.loads(ENROLL_REQUEST_PATH.read_text())
            except (OSError, ValueError):
                return False
            self.saved = 0
            self.pose_index = 0
            self.saved_in_pose = 0
            self.skips_in_row = 0
            self.next_sample_at = 0.0
            # A re-scan adds to what is already on file, so existing samples count
            # as references for the same-person and pose checks from the first shot.
            self.stored = [blob_to_embedding(r["embedding"]) for r in con.execute(
                "SELECT embedding FROM face_embeddings WHERE employee_id = ?",
                (self.request["employee_id"],)).fetchall()]
            self._report("running")
            print(f"[enroll] boshlandi: employee_id={self.request['employee_id']}")

        if now > self.request["expires_at"]:
            self._finish("timeout", f"Vaqt tugadi — {self.saved} ta namuna olindi")
            print(f"[enroll] vaqt tugadi ({self.saved} ta namuna)")
            return False

        if not faces:
            self._report("running", "Yuz ko'rinmayapti")
            return True
        if now < self.next_sample_at:
            return True

        # The person being enrolled stands closest, so theirs is the biggest box.
        face = max(faces, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))

        if face["det_score"] < MIN_DET_SCORE:
            self._wait(now, "Yuz aniq ko'rinmayapti — qimirlamay turing")
            return True
        if face["bbox"][2] - face["bbox"][0] < MIN_FACE_WIDTH_PX:
            self._wait(now, "Kameraga yaqinroq keling")
            return True

        # Catch the same person being added twice under two names before any
        # sample is stored — afterwards the two records are impossible to tell apart.
        if self.saved == 0:
            dup = find_duplicate(con, face["embedding"], exclude_employee_id=self.request["employee_id"])
            if dup:
                self._finish("duplicate",
                             f"Bu yuz allaqachon ro'yxatda: {dup['name']} "
                             f"(o'xshashlik {dup['similarity']:.2f})")
                print(f"[enroll] rad etildi — allaqachon {dup['name']} sifatida ro'yxatda")
                return False

        if self.stored:
            closest = best_similarity(face["embedding"], self.stored)
            if closest < MATCH_THRESHOLD:
                # Someone walked in front of the camera mid-enrollment; storing
                # their face under this name would poison the record.
                self._wait(now, "Boshqa odam ko'rinyapti — skanerlanayotgan xodim kameraga qarasin")
                return True
            if closest > REDUNDANT_SIMILARITY and self.skips_in_row < MAX_SKIPS_BEFORE_ACCEPT:
                self.skips_in_row += 1
                self._wait(now, "Boshingizni biroz ko'proq buring")
                return True

        FACES_DIR.mkdir(parents=True, exist_ok=True)
        img_path = FACES_DIR / f"{self.request['employee_id']}_{int(now)}.jpg"
        cv2.imwrite(str(img_path), frame)
        con.execute(
            "INSERT INTO face_embeddings (employee_id, embedding, source_image) VALUES (?, ?, ?)",
            (self.request["employee_id"], embedding_to_blob(face["embedding"]), str(img_path)),
        )
        con.commit()
        self.stored.append(face["embedding"])
        self.saved += 1
        self.saved_in_pose += 1
        self.skips_in_row = 0
        print(f"[enroll] namuna {self.saved}/{self.request['samples']} — {self._pose()[0]}")

        if self.saved_in_pose >= self._pose()[1]:
            self.pose_index += 1
            self.saved_in_pose = 0
            self.next_sample_at = now + POSE_CHANGE_PAUSE_SECONDS
        else:
            self.next_sample_at = now + SECONDS_BETWEEN_ENROLL_SAMPLES

        if self.pose_index >= len(ENROLL_POSES) or self.saved >= self.request["samples"]:
            self._finish("done")
            print("[enroll] tugadi")
            return False
        self._report("running")
        return True


def draw_labels(frame, annotations, progress=0.0, min_width=0):
    """Box every face, and show the two numbers that matter while tuning:

    the face width in pixels (against the width a stand requires) and how far
    the current stand has got. Without these on screen, picking the threshold
    for a new camera is guesswork.
    """
    for ann in annotations:
        x1, y1, x2, y2 = (int(v) for v in ann["bbox"])
        wide_enough = (x2 - x1) >= min_width
        color = COLOR_KNOWN if ann["name"] else COLOR_UNKNOWN
        label = ann["name"] or "noma'lum"
        if ann["name"]:
            label += f"  {ann['similarity']:.2f}"
        label += f"  {x2 - x1}px" + ("" if wide_enough else " uzoq")
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2 if wide_enough else 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 8)), (x1 + tw + 8, y1), color, -1)
        cv2.putText(frame, label, (x1 + 4, max(12, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (10, 15, 15), 1, cv2.LINE_AA)

    if progress > 0:
        h, w = frame.shape[:2]
        bar_w = int(w * 0.6)
        x, y = (w - bar_w) // 2, h - 28
        cv2.rectangle(frame, (x, y), (x + bar_w, y + 12), (60, 60, 60), -1)
        cv2.rectangle(frame, (x, y), (x + int(bar_w * progress), y + 12), COLOR_KNOWN, -1)
    return frame


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    init_db()
    cfg = cfgmod.load()
    con = get_conn()
    ids_names, matrix = load_known_faces(con)
    # A crash or a power cut can leave yesterday open; settle it before counting
    # anyone as present today.
    for closed in attendance.close_stale(con, cfg, cfgmod.now_utc()):
        print(f"[ketdi] {name_of(ids_names, closed['employee_id'])} "
              f"(oldingi kun yopildi, {closed['ts']})")
    con.close()
    print(f"Ish vaqti: {cfg['work_start']}–{cfg['work_end']} ({cfg['timezone']})")
    print(f"Turish sharti: yuz kadrning ≥{cfg['stand_min_face_ratio']:.0%} kengligi "
          f"(kamida {cfg['stand_min_face_px']}px), {cfg['stand_seconds']} sekund")
    print(f"Ro'yxatdan o'tgan yuz namunalari: {matrix.shape[0]}")
    if matrix.shape[0] == 0:
        print("DIQQAT: hech kim ro'yxatdan o'tmagan — avval enroll.py ni ishlating")

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("ERROR: kamera ochilmadi", CAMERA_INDEX)
        return
    print("Kamera ochildi, tanish boshlandi")

    enroller = Enroller()
    stand = StandDetector(cfg)
    present = []            # who currently has an open day
    last_result = None      # most recent attendance decision, for the dashboard
    last_sample = {}        # employee_id -> when their sighting was last stored
    annotations = []        # last known boxes+names, redrawn on every preview frame
    unknown_count = 0
    last_recognize = 0.0
    last_preview = 0.0
    last_close_check = time.time()
    last_reload = time.time()
    last_body = 0.0
    found_bodies = []       # last known bodies, redrawn on every preview frame
    # Counting keeps its own object, its own snapshot and its own table. Nothing
    # below reaches into attendance, and every call sits inside try/except so a
    # fault here degrades to "no counting" rather than "no attendance".
    counter = counting.Counter(cfg) if cfg["counter_enabled"] else None
    counter_totals = {}
    preview_interval = 1.0 / PREVIEW_FPS
    body_interval = 1.0 / max(1, cfg["body_fps"])

    try:
        while _running:
            if not cap.grab():
                time.sleep(0.2)
                continue

            now = time.time()
            if now - last_preview < preview_interval:
                time.sleep(0.01)
                continue
            last_preview = now

            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue

            # Bodies are looked for several times more often than faces: a
            # person crosses a doorway in about a second, and recognition's two
            # samples a second cannot say which way they went.
            if cfg["body_mode"] != "off" and now - last_body >= body_interval:
                last_body = now
                try:
                    found_bodies = bodies.detect(frame, cfg["body_mode"],
                                                 cfg["body_min_confidence"])
                except Exception as e:
                    print("[tana] aniqlash uzildi:", e)
                    found_bodies = []

                if counter is not None:
                    try:
                        crossed = counter.update(found_bodies, frame.shape[1],
                                                 frame.shape[0], now)
                        if crossed:
                            con2 = get_conn()
                            stamp = datetime.now(timezone.utc)
                            for event in crossed:
                                counting.record(con2, cfg, event["direction"], stamp)
                                print(f"[{event['direction']}] eshikdan o'tdi")
                            counter_totals = counting.totals_for_day(
                                con2, cfgmod.local_day(cfg, stamp))
                            con2.close()
                    except Exception as e:
                        print("[counter] uzildi, davomat ishlayapti:", e)

            run_recognition = (now - last_recognize) >= RECOGNIZE_EVERY_SECONDS
            if run_recognition:
                last_recognize = now

                if now - last_reload >= RELOAD_FACES_EVERY_SECONDS:
                    last_reload = now
                    try:
                        con = get_conn()
                        ids_names, matrix = load_known_faces(con)
                        con.close()
                    except Exception as e:
                        print("[faces] reload failed:", e)

                faces_found = detect_faces(frame)
                try:
                    con = get_conn()
                    if enroller.poll(con, frame, faces_found, now):
                        last_reload = 0.0   # force a reload so a new face is live at once
                    con.close()
                except Exception as e:
                    print("[enroll] failed:", e)

                annotations = []
                unknown_count = 0
                matches = []
                for face in faces_found:
                    match = identify(face["embedding"], ids_names, matrix)
                    if match:
                        matches.append({"employee_id": match["employee_id"],
                                        "bbox": face["bbox"]})
                        annotations.append({"bbox": face["bbox"], "name": match["name"],
                                            "similarity": match["similarity"]})
                    else:
                        unknown_count += 1
                        annotations.append({"bbox": face["bbox"], "name": None, "similarity": 0.0})

                stood = stand.update(matches, frame.shape[1], now)

                try:
                    con = get_conn()
                    ts = datetime.now(timezone.utc)

                    if stood is not None:
                        name = name_of(ids_names, stood)
                        result = attendance.register_stand(con, cfg, stood, ts)
                        if result["status"] == "already":
                            print(f"[turdi] {name} — allaqachon yozilgan")
                            tg.already_message(cfg, con, stood, result["ts"])
                        else:
                            print(f"[{result['event']}] {name} — {result['grade']['text']}")
                            announce(cfg, con, stood, result, ts)
                        last_result = {"name": name, **result}

                    for emp in {m["employee_id"] for m in matches}:
                        if emp not in last_sample or now - last_sample[emp] >= SAMPLE_EVERY_SECONDS:
                            last_sample[emp] = now
                            log_sighting(con, emp, ts=ts.isoformat())
                        # Someone who walks straight in without stopping still
                        # gets their arrival, just marked as the weaker source.
                        first = attendance.register_first_sighting(con, cfg, emp, ts)
                        if first:
                            print(f"[keldi] {name_of(ids_names, emp)} "
                                  f"(turmadi) — {first['grade']['text']}")
                            announce(cfg, con, emp, first, ts)

                    if now - last_close_check >= CLOSE_CHECK_EVERY_SECONDS:
                        last_close_check = now
                        for closed in attendance.close_stale(con, cfg, ts):
                            print(f"[ketdi] {name_of(ids_names, closed['employee_id'])} "
                                  f"(o'zi yopildi, {closed['ts']})")
                            tg.auto_close_message(cfg, con, closed["employee_id"],
                                                  closed["id"], closed["ts"])

                    present = open_days(con, cfg, ids_names, ts)
                    con.close()
                except Exception as e:
                    print("[recognizer] db step failed, continuing:", e)

                atomic_write_text(STATUS_PATH, json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "enrolled_samples": int(matrix.shape[0]),
                    "unknown_faces": unknown_count,
                    "stand_progress": round(stand.progress(now), 2),
                    # The widest face on screen, so the stand threshold can be
                    # picked from a measurement instead of by eye.
                    "max_face_width": max((int(a["bbox"][2] - a["bbox"][0])
                                           for a in annotations), default=0),
                    "stand_min_face_width": stand.min_width(frame.shape[1]),
                    "bodies": len(found_bodies),
                    "body_mode": cfg["body_mode"],
                    "last_event": last_result,
                    "present": present,
                }))

            canvas = draw_labels(frame, annotations, stand.progress(time.time()),
                                 stand.min_width(frame.shape[1]))
            if found_bodies:
                canvas = bodies.draw(canvas, found_bodies, cfg["body_mode"])
            ok_enc, buf = cv2.imencode(".jpg", canvas)
            if counter is not None and ok_enc:
                # A second image, so the counting line never appears on the
                # dashboard that was working before counting existed.
                try:
                    marked = counter.draw(canvas.copy(), counter_totals)
                    ok2, buf2 = cv2.imencode(".jpg", marked)
                    if ok2:
                        atomic_write_bytes(COUNTER_SNAPSHOT_PATH, buf2.tobytes())
                except Exception as e:
                    print("[counter] chizish uzildi:", e)
            if ok_enc:
                atomic_write_bytes(SNAPSHOT_PATH, buf.tobytes())
    finally:
        cap.release()
        # The camera stopping is not people going home, so nobody is marked as
        # having left here. Whoever is still open gets closed at the next start,
        # timed at their real last sighting.
        if present:
            print(f"To'xtatildi — {len(present)} ta ochiq kun qoldi, "
                  f"keyingi ishga tushishda yopiladi")


if __name__ == "__main__":
    main()
