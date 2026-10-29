"""Whole-body detection for the door camera.

The face model only sees a face that is turned towards the camera, which is
exactly what someone walking through a doorway is not doing. Counting people
through a door therefore needs a second model that finds the body regardless of
which way the person is facing.

Three shapes are available, and they are not interchangeable:

    box   — a rectangle. All that counting needs: a centre that crosses a line.
    seg   — an outline following the body. Shows what the model really sees,
            useful while judging whether detection is good enough.
    pose  — a skeleton of joints. The only one that can later answer "working or
            on the phone", because that question is about posture, not position.
    full  — outline and skeleton together. Runs both models, so it costs twice
            as much per frame; it is for looking at, not for running all day.

Nothing here resolves fingers. The skeleton ends at the wrist, and at a doorway
a whole hand is around fifty pixels wide — individual fingers are not present in
the image to be found. Posture answers the questions fingers would.

`box` is the cheapest and is the default; the others cost more per frame.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_FILES = {
    "box": "yolov8n.pt",
    "seg": "yolov8n-seg.pt",
    "pose": "yolov8n-pose.pt",
}
PERSON_CLASS = 0        # COCO class 0

COLOR_BODY = (255, 140, 0)      # BGR: blue, so it never reads as a face box
COLOR_OUTLINE = (210, 110, 40)  # dimmer than the skeleton: in "full" the
COLOR_JOINT = (80, 200, 255)    # joints are the point, the outline is context

# COCO 17-keypoint skeleton.
SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),        # shoulders and arms
    (5, 11), (6, 12), (11, 12),                      # torso
    (11, 13), (13, 15), (12, 14), (14, 16),          # legs
]

# Of the five head points the model returns, only the nose is drawn. Its height
# against the shoulders is what shows a head tilted down — the main half of
# "looking at a phone". The eyes and ears are a few pixels across at doorway
# distance, too unreliable to act on and only clutter the picture.
NOSE = 0
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
HEAD_POINTS = (0, 1, 2, 3, 4)
KEYPOINT_MIN_CONFIDENCE = 0.4

_models = {}


def available():
    """Which shapes can actually be used — the weights are separate downloads."""
    return {mode: (MODELS_DIR / name).exists() for mode, name in MODEL_FILES.items()}


def get_model(mode: str):
    """Load once per mode. Falls back to `box`, which ships with the project."""
    if mode not in MODEL_FILES or not (MODELS_DIR / MODEL_FILES[mode]).exists():
        mode = "box"
    if mode not in _models:
        from ultralytics import YOLO
        _models[mode] = YOLO(str(MODELS_DIR / MODEL_FILES[mode]))
    return mode, _models[mode]


def detect(frame, mode: str = "box", min_confidence: float = 0.4):
    """Every person in the frame, with whatever shape `mode` provides."""
    if mode == "full":
        # Two models, two passes. They are matched back together by box overlap
        # rather than by index, because each model orders its own detections.
        outlined = detect(frame, "seg", min_confidence)
        posed = detect(frame, "pose", min_confidence)
        for body in outlined:
            partner = _closest(body, posed)
            if partner is not None:
                body["keypoints"] = partner.get("keypoints")
        return outlined

    mode, model = get_model(mode)
    result = model.predict(frame, classes=[PERSON_CLASS], conf=min_confidence,
                           verbose=False)[0]

    bodies = []
    for index, box in enumerate(result.boxes):
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
        body = {"box": (x1, y1, x2, y2), "conf": float(box.conf[0]),
                # The feet are the part of a person that is actually on the
                # floor, so the bottom-centre is what should cross a door line;
                # the box centre sits around the waist and crosses too early.
                "foot": ((x1 + x2) / 2, y2),
                "centre": ((x1 + x2) / 2, (y1 + y2) / 2)}

        if mode == "seg" and result.masks is not None and index < len(result.masks):
            body["polygon"] = np.asarray(result.masks.xy[index], dtype=np.int32)
        if mode == "pose" and result.keypoints is not None and index < len(result.keypoints):
            points = result.keypoints.data[index].cpu().numpy()   # (17, 3)
            body["keypoints"] = points
            # The nose, when the model is sure of it. Offered as a crossing
            # point because it stays in frame when the rest of the body does
            # not — a camera that only sees people from the waist up has no
            # feet to track.
            if points[NOSE][2] >= KEYPOINT_MIN_CONFIDENCE:
                body["nose"] = (float(points[NOSE][0]), float(points[NOSE][1]))
        bodies.append(body)
    return bodies


def _closest(body, candidates):
    """The candidate whose box overlaps this one most, if any really does."""
    best, best_score = None, 0.0
    ax1, ay1, ax2, ay2 = body["box"]
    for other in candidates:
        bx1, by1, bx2, by2 = other["box"]
        overlap = (max(0.0, min(ax2, bx2) - max(ax1, bx1))
                   * max(0.0, min(ay2, by2) - max(ay1, by1)))
        if overlap <= 0:
            continue
        union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - overlap)
        score = overlap / union if union else 0.0
        if score > best_score:
            best, best_score = other, score
    return best if best_score > 0.3 else None


def draw(frame, bodies, mode: str = "box"):
    """Mark each body. Shape follows `mode`; the label always carries the score."""
    for body in bodies:
        x1, y1, x2, y2 = (int(v) for v in body["box"])

        if mode in ("seg", "full") and "polygon" in body and len(body["polygon"]):
            cv2.polylines(frame, [body["polygon"]], True, COLOR_OUTLINE, 2)
        if mode in ("pose", "full") and body.get("keypoints") is not None:
            points = body["keypoints"]
            for a, b in SKELETON:
                if points[a][2] < KEYPOINT_MIN_CONFIDENCE or points[b][2] < KEYPOINT_MIN_CONFIDENCE:
                    continue
                cv2.line(frame, (int(points[a][0]), int(points[a][1])),
                         (int(points[b][0]), int(points[b][1])), COLOR_BODY, 2)
            # Neck: nose to the midpoint between the shoulders. This one line is
            # what makes a head tilted towards a phone visible at a glance.
            nose = points[NOSE]
            left, right = points[LEFT_SHOULDER], points[RIGHT_SHOULDER]
            if (nose[2] >= KEYPOINT_MIN_CONFIDENCE
                    and left[2] >= KEYPOINT_MIN_CONFIDENCE
                    and right[2] >= KEYPOINT_MIN_CONFIDENCE):
                neck = (int((left[0] + right[0]) / 2), int((left[1] + right[1]) / 2))
                cv2.line(frame, (int(nose[0]), int(nose[1])), neck, COLOR_BODY, 2)

            for index, (x, y, score) in enumerate(points):
                if index in HEAD_POINTS and index != NOSE:
                    continue
                if score >= KEYPOINT_MIN_CONFIDENCE:
                    cv2.circle(frame, (int(x), int(y)), 3, COLOR_JOINT, -1)
        if mode == "box":
            cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_BODY, 2)

        label = f"tana {body['conf']:.2f}"
        cv2.putText(frame, label, (x1 + 2, min(frame.shape[0] - 4, y2 + 16)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_BODY, 1, cv2.LINE_AA)

        # The point that will decide a line crossing, drawn so the door line can
        # be positioned against something real rather than guessed at.
        fx, fy = (int(v) for v in body["foot"])
        cv2.circle(frame, (fx, fy), 5, COLOR_BODY, -1)
    return frame
