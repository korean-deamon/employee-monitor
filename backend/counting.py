"""Count people crossing a line in the doorway.

Deliberately separate from everything attendance does. Attendance answers "who
is this person", from the face, and only when they stop at the camera. Counting
answers "how many bodies went through", from the body, for everyone — visitors
and students included, who are not in the system and never will be. The two
never share state, so a fault here cannot corrupt an attendance record.

Direction comes from which side of the line a tracked person was on before and
after, not from which way they appear to face: someone can walk backwards
through a doorway, and the camera sees the back of most people's heads anyway.
"""
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfgmod

COLOR_LINE = (0, 200, 255)       # BGR amber: not the face green, not the body blue
COLOR_IN = (120, 220, 120)
COLOR_OUT = (160, 160, 240)


def _side(point, start, end):
    """Which side of the line the point is on, as the sign of the cross product."""
    return ((end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0]))


def crossing_point(body, which):
    """The single point whose line crossing counts as the person passing.

    Which point to use is a property of the camera, not of the code:

      foot   the bottom-centre of the body box. Correct when the whole person
             is in frame, because the feet are what is actually on the floor —
             the box centre sits at the waist and crosses while they are still
             approaching.
      nose   needs pose mode. Right when the camera only sees people from the
             waist up: the box then ends wherever the body is cut off, which is
             the frame edge, and a point pinned to the frame edge never crosses
             anything.
      centre the middle of the box. A compromise, rarely the best choice.

    Falls back to the foot point when the chosen one is unavailable — pose mode
    off, or the nose not confidently found this frame.
    """
    if which == "nose" and body.get("nose"):
        return body["nose"]
    if which == "centre":
        return body["centre"]
    return body["foot"]


class Counter:
    """Track people between frames and count line crossings.

    The tracker is intentionally simple — nearest-foot-point matching. The
    doorway this watches has at most two people abreast, so the elaborate
    trackers that exist to untangle crowds would cost more than they return.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = {}            # id -> {foot, side, missed, counted_at}
        self.next_id = 1
        self.crossings = []         # pending, drained by the caller

    def line_points(self, width, height):
        (x1, y1), (x2, y2) = self.cfg["door_line"]
        return (x1 * width, y1 * height), (x2 * width, y2 * height)

    def _match(self, foot, width):
        """The nearest existing track within reach, or None for a new person."""
        limit = width * self.cfg["track_max_distance_ratio"]
        best, best_distance = None, limit
        for track_id, track in self.tracks.items():
            dx = track["foot"][0] - foot[0]
            dy = track["foot"][1] - foot[1]
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < best_distance:
                best, best_distance = track_id, distance
        return best

    def update(self, bodies, width, height, now):
        """Feed one frame of detected bodies. Returns the crossings it caused."""
        start, end = self.line_points(width, height)
        seen = set()
        fired = []

        for body in bodies:
            foot = crossing_point(body, self.cfg["count_point"])
            side = _side(foot, start, end)
            track_id = self._match(foot, width)

            if track_id is None:
                track_id = self.next_id
                self.next_id += 1
                # counted_at starts as None, not 0: a zero would mean "counted
                # at the epoch", which silently suppresses the first crossing
                # whenever the clock being passed in starts near zero.
                self.tracks[track_id] = {"foot": foot, "side": side,
                                         "missed": 0, "counted_at": None}
                seen.add(track_id)
                continue

            track = self.tracks[track_id]
            previous = track["side"]
            track["foot"], track["missed"] = foot, 0
            seen.add(track_id)

            # A sign flip means the foot point moved across the line. The
            # cooldown stops someone loitering on the line from being counted
            # over and over as they shift their weight.
            recent = (track["counted_at"] is not None
                      and now - track["counted_at"] < self.cfg["count_cooldown_seconds"])
            if previous * side < 0 and not recent:
                track["counted_at"] = now
                direction = "kirdi" if side > 0 else "chiqdi"
                if self.cfg["door_invert"]:
                    direction = "chiqdi" if direction == "kirdi" else "kirdi"
                fired.append({"direction": direction, "track": track_id})
            track["side"] = side

        for track_id in [t for t in self.tracks if t not in seen]:
            self.tracks[track_id]["missed"] += 1
            if self.tracks[track_id]["missed"] > self.cfg["track_max_missed"]:
                del self.tracks[track_id]

        self.crossings.extend(fired)
        return fired

    def draw(self, frame, totals):
        """Draw the line and the running totals onto a frame."""
        height, width = frame.shape[:2]
        start, end = self.line_points(width, height)
        p1 = (int(start[0]), int(start[1]))
        p2 = (int(end[0]), int(end[1]))
        cv2.line(frame, p1, p2, COLOR_LINE, 2)
        for marker in (p1, p2):
            cv2.circle(frame, marker, 5, COLOR_LINE, -1)

        # The tracked point is drawn so it is obvious which part of the person
        # the line is actually watching.
        for track in self.tracks.values():
            point = (int(track["foot"][0]), int(track["foot"][1]))
            cv2.circle(frame, point, 7, COLOR_LINE, 2)
            cv2.circle(frame, point, 2, COLOR_LINE, -1)

        label = f"kirdi {totals.get('kirdi', 0)}   chiqdi {totals.get('chiqdi', 0)}"
        cv2.rectangle(frame, (8, 8), (8 + 11 * len(label), 38), (20, 20, 20), -1)
        cv2.putText(frame, label, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
        return frame


def record(con, cfg, direction, ts, camera="entrance"):
    con.execute(
        """INSERT INTO crossings (ts, local_day, direction, camera)
           VALUES (?, ?, ?, ?)""",
        (ts.isoformat(), cfgmod.local_day(cfg, ts), direction, camera),
    )
    con.commit()


def totals_for_day(con, local_day):
    rows = con.execute(
        "SELECT direction, COUNT(*) c FROM crossings WHERE local_day = ? GROUP BY direction",
        (local_day,),
    ).fetchall()
    return {row["direction"]: row["c"] for row in rows}
