#!/usr/bin/env python3
"""Face detection + identity matching built on InsightFace.

One model instance serves both enrollment (building reference embeddings from
photos) and live recognition, so a face is measured the same way in both paths.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

# buffalo_l: detection + 512-d ArcFace embeddings. Holds up on angled, non-frontal
# faces, which is what a corner-mounted office camera produces.
MODEL_PACK = "buffalo_l"
DETECT_SIZE = (640, 640)

# Cosine similarity above this means "same person". Tuned for ArcFace/buffalo_l:
# genuine pairs typically land above 0.5, different people below 0.3.
MATCH_THRESHOLD = 0.40

# A blurred or tiny face still produces an embedding, just a bad one — and a bad
# reference sample is worse than no sample, because it drags every later compare
# toward whoever happens to be blurred the same way. Enrollment rejects these.
MIN_DET_SCORE = 0.60
MIN_FACE_WIDTH_PX = 70

# Two samples this alike describe the same head position: storing both costs a
# compare on every frame and teaches the model nothing new.
REDUNDANT_SIMILARITY = 0.93

# What makes recognition hold up at a corner camera is not sample *count* but the
# range of head positions covered, so enrollment walks the person through poses
# instead of taking N shots of them sitting still.
ENROLL_POSES = [
    ("To'g'riga qarang", 2),
    ("Boshingizni chapga buring", 2),
    ("Boshingizni o'ngga buring", 2),
    ("Biroz yuqoriga qarang", 1),
    ("Biroz pastga qarang", 1),
]


def enroll_total_samples() -> int:
    return sum(count for _, count in ENROLL_POSES)


_app = None


def get_app():
    """Load the model once per process (first call downloads ~300MB)."""
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        _app = FaceAnalysis(name=MODEL_PACK, providers=providers)
        _app.prepare(ctx_id=0, det_size=DETECT_SIZE)
    return _app


def detect_faces(frame):
    """Return every face found in a BGR frame, with bbox and a normalized embedding."""
    faces = get_app().get(frame)
    out = []
    for f in faces:
        emb = np.asarray(f.normed_embedding, dtype=np.float32)
        bbox = tuple(float(v) for v in f.bbox)
        out.append({"bbox": bbox, "embedding": emb, "det_score": float(f.det_score)})
    return out


def embedding_to_blob(emb: np.ndarray) -> bytes:
    return np.asarray(emb, dtype=np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def load_known_faces(con):
    """All enrolled references as (names, matrix) ready for one vectorized compare."""
    from db import FULL_NAME_SQL
    rows = con.execute(
        f"""SELECT f.employee_id, {FULL_NAME_SQL} AS name, f.embedding
            FROM face_embeddings f JOIN employees e ON e.id = f.employee_id"""
    ).fetchall()
    if not rows:
        return [], np.zeros((0, 512), dtype=np.float32)
    ids_names = [(r["employee_id"], r["name"]) for r in rows]
    matrix = np.vstack([blob_to_embedding(r["embedding"]) for r in rows])
    return ids_names, matrix


def find_duplicate(con, embedding: np.ndarray, exclude_employee_id: int | None = None):
    """Is this face already enrolled under a different employee?

    The test uses the same threshold as recognition on purpose: if the system
    would read this face as an existing person, enrolling it under a second name
    guarantees the two records get mixed up in the attendance log.
    """
    from db import FULL_NAME_SQL
    sql = f"""SELECT f.employee_id, {FULL_NAME_SQL} AS name, f.embedding
              FROM face_embeddings f JOIN employees e ON e.id = f.employee_id"""
    params: list = []
    if exclude_employee_id is not None:
        sql += " WHERE f.employee_id != ?"
        params.append(exclude_employee_id)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return None

    matrix = np.vstack([blob_to_embedding(r["embedding"]) for r in rows])
    sims = matrix @ np.asarray(embedding, dtype=np.float32)
    best = int(np.argmax(sims))
    score = float(sims[best])
    if score < MATCH_THRESHOLD:
        return None
    return {"employee_id": rows[best]["employee_id"], "name": rows[best]["name"], "similarity": score}


def best_similarity(embedding: np.ndarray, references) -> float:
    """Closest match between this face and a list of reference embeddings.

    Used during enrollment to ask two questions of a fresh sample: is this still
    the same person, and does this pose differ from what is already stored?
    """
    if len(references) == 0:
        return 0.0
    matrix = np.vstack([np.asarray(r, dtype=np.float32) for r in references])
    return float(np.max(matrix @ np.asarray(embedding, dtype=np.float32)))


def identify(embedding: np.ndarray, ids_names, matrix):
    """Best matching enrolled employee, or None when nobody is close enough.

    Embeddings are unit-length, so a dot product is the cosine similarity.
    """
    if matrix.shape[0] == 0:
        return None
    sims = matrix @ np.asarray(embedding, dtype=np.float32)
    best = int(np.argmax(sims))
    score = float(sims[best])
    if score < MATCH_THRESHOLD:
        return None
    employee_id, name = ids_names[best]
    return {"employee_id": employee_id, "name": name, "similarity": score}
