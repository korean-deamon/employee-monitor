"""Tell a deliberate check-in apart from someone walking past the camera.

Attendance is only recorded when an employee stops in front of the camera on
purpose. Three things have to hold at once, and each rules out a different kind
of false trigger:

  near      — a large face means they came up to the camera, not past it
  still     — a face crossing the frame is someone walking through the doorway
  sustained — a held pose is a decision; a passing glance is not

Identity comes from a vote across the frames of the stand rather than any single
frame, so one bad reading cannot name the wrong person.
"""


class StandDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.track = None        # the stand being accumulated, if any
        self.cooldown = {}       # employee_id -> epoch seconds until re-triggerable

    def _reset(self):
        self.track = None

    def min_width(self, frame_width):
        """How wide a face must be to count as standing at the camera.

        Two conditions, because they answer different questions. The ratio asks
        whether the person came up to the camera, and it survives a change of
        resolution — a 640-wide webcam and a 2560-wide IP camera agree on it.
        The pixel floor asks whether there is enough detail to recognise anyone
        at all, which is a property of the model, not of the framing.
        """
        return max(int(frame_width * self.cfg["stand_min_face_ratio"]),
                   self.cfg["stand_min_face_px"])

    def _candidate(self, matches, frame_width):
        """The nearest recognized face, if any is close enough to count."""
        needed = self.min_width(frame_width)
        near = [m for m in matches if m["bbox"][2] - m["bbox"][0] >= needed]
        if not near:
            return None
        return max(near, key=lambda m: m["bbox"][2] - m["bbox"][0])

    def update(self, matches, frame_width, now):
        """Feed one recognition cycle.

        `matches` holds this cycle's recognized faces as {"employee_id", "bbox"}.
        Returns the employee_id whose stand just completed, else None.
        """
        candidate = self._candidate(matches, frame_width)
        if candidate is None:
            # A single frame where the face was missed or turned away must not
            # undo a stand that is nearly complete, so the track survives a
            # short gap and only a real absence clears it.
            if self.track is not None and now - self.track["last"] > self.cfg["stand_gap_seconds"]:
                self._reset()
            return None

        x1, _, x2, _ = candidate["bbox"]
        center = (x1 + x2) / 2
        drift_limit = frame_width * self.cfg["stand_max_drift_ratio"]

        if self.track is None:
            self.track = {"started": now, "last": now, "anchor": center,
                          "votes": {candidate["employee_id"]: 1}}
            return None

        # Lost the face for a moment, or it moved across the frame: whatever this
        # is, it is not one person holding still, so start counting again.
        if now - self.track["last"] > self.cfg["stand_gap_seconds"] \
                or abs(center - self.track["anchor"]) > drift_limit:
            self.track = {"started": now, "last": now, "anchor": center,
                          "votes": {candidate["employee_id"]: 1}}
            return None

        self.track["last"] = now
        votes = self.track["votes"]
        votes[candidate["employee_id"]] = votes.get(candidate["employee_id"], 0) + 1

        if now - self.track["started"] < self.cfg["stand_seconds"]:
            return None

        winner = max(votes, key=votes.get)
        agreement = votes[winner] / sum(votes.values())
        self._reset()

        if agreement < self.cfg["stand_vote_ratio"]:
            # Two people took turns being closest; naming either one is a guess.
            return None
        if now < self.cooldown.get(winner, 0):
            return None

        self.cooldown[winner] = now + self.cfg["stand_cooldown_seconds"]
        return winner

    def progress(self, now):
        """How far along the current stand is, for the live preview (0.0–1.0)."""
        if self.track is None:
            return 0.0
        return min(1.0, (now - self.track["started"]) / self.cfg["stand_seconds"])
