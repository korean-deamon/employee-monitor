"""Tunable settings for attendance rules and stand detection.

Everything here gets re-tuned once the real entrance camera is mounted — lens,
distance and ceiling height all move the pixel thresholds — so the values live
in one place and `data/config.json` can override any of them without a redeploy.
"""
import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

DEFAULTS = {
    # Timestamps are stored in UTC, but the working day is a local-time idea:
    # 09:00 means 09:00 in the office, not in Greenwich.
    "timezone": "Asia/Tashkent",
    "work_start": "09:00",
    "work_end": "18:00",

    # A deliberate stand: close to the camera, held still, for long enough.
    # The face must clear both limits — see StandDetector.min_width for why.
    "stand_min_face_ratio": 0.18,    # share of frame width: "did they come close"
    "stand_min_face_px": 100,        # absolute floor: "is there enough detail"
    "stand_seconds": 3.0,
    "stand_max_drift_ratio": 0.15,   # of frame width; more than this is walking past
    "stand_vote_ratio": 0.6,         # share of frames that must agree on one person
    "stand_gap_seconds": 1.5,        # face lost longer than this restarts the stand
    "stand_cooldown_seconds": 60,    # ignore a repeat stand by the same person

    # Nobody arrives and leaves inside this window, so a stand this soon after
    # an arrival is a repeat, not a departure.
    "min_session_seconds": 600,

    # Gone this long with an open day: close it at the last sighting.
    "absence_close_seconds": 1800,

    # Record an arrival even for someone who walked straight past without
    # stopping, so a forgotten stand never costs them their arrival time.
    "first_sighting_arrival": True,

    # Whole-body detection, for counting people through the door. Runs faster
    # than recognition because a person crosses a doorway in about a second and
    # two samples are not enough to tell which way they went.
    # body_mode: "off" | "box" | "seg" | "pose" — see bodies.py.
    "body_mode": "box",
    "body_fps": 8,
    "body_min_confidence": 0.4,

    # Door counting. Entirely separate from attendance: off by default, and the
    # camera loop skips it without touching anything else when disabled.
    # The line is given as fractions of the frame so it survives a change of
    # camera resolution; [[0,0.65],[1,0.65]] is a horizontal line at 65% height.
    "counter_enabled": False,
    "door_line": [[0.0, 0.65], [1.0, 0.65]],
    "door_invert": False,
    "track_max_distance_ratio": 0.25,   # of frame width, for matching frame to frame
    "track_max_missed": 6,              # frames a person may vanish before the track dies
    "count_cooldown_seconds": 2.0,      # stops loitering on the line counting repeatedly
    # Which point on the person has to cross the line — see counting.crossing_point.
    # "foot" is right once the camera sees whole people; "nose" is for a camera
    # that only catches them from the waist up, where the box bottom is just the
    # frame edge and never moves.
    "count_point": "foot",

    # Two switches, because the two halves get turned on at different times.
    # `telegram_enabled` lets the bot run at all, so pairing and /bugun can be
    # tried out; `telegram_send_notifications` is what actually delivers
    # attendance messages. Messages are composed and stored into the outbox
    # either way, so the whole flow can be reviewed before any phone buzzes.
    "telegram_enabled": False,
    "telegram_send_notifications": False,
    "pairing_code_hours": 24,
    "pairing_attempts_per_hour": 5,
}


def load():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass          # no override file, or it is unreadable: defaults stand
    return cfg


def tz(cfg):
    return ZoneInfo(cfg["timezone"])


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(ts) -> datetime:
    """Read a timestamp, treating a naive one as UTC.

    Accepts a datetime as well as a string: timestamps reach here both straight
    from the camera loop and back out of the database, and making every caller
    remember which it holds is how off-by-a-type bugs get in.
    """
    dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_local(cfg, dt: datetime) -> datetime:
    return dt.astimezone(tz(cfg))


def local_day(cfg, dt: datetime) -> str:
    """The office date a moment belongs to, as YYYY-MM-DD."""
    return to_local(cfg, dt).date().isoformat()


def _clock(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def day_boundary(cfg, dt: datetime, which: str) -> datetime:
    """`work_start` or `work_end` of the office day `dt` falls in, in UTC."""
    local = to_local(cfg, dt)
    boundary = datetime.combine(local.date(), _clock(cfg[which]), tzinfo=tz(cfg))
    return boundary.astimezone(timezone.utc)


def minutes_from_start(cfg, dt: datetime) -> int:
    """Minutes after `work_start`. Negative means early."""
    return round((dt - day_boundary(cfg, dt, "work_start")).total_seconds() / 60)


def minutes_before_end(cfg, dt: datetime) -> int:
    """Minutes before `work_end`. Negative means they stayed late."""
    return round((day_boundary(cfg, dt, "work_end") - dt).total_seconds() / 60)
