#!/usr/bin/env python3
"""Register an employee's face so the recognizer can name them later.

    python backend/enroll.py --name "Arthur" --capture 5
    python backend/enroll.py --name "Arthur" --images a.jpg b.jpg
    python backend/enroll.py --list
"""
import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import FULL_NAME_SQL, create_employee, find_employee_by_name, get_conn, init_db
from faces import detect_faces, embedding_to_blob

CAMERA_INDEX = 0
SECONDS_BETWEEN_SHOTS = 1.5
FACES_DIR = Path(__file__).resolve().parent.parent / "data" / "faces"


def save_embedding(con, employee_id, embedding, source):
    con.execute(
        "INSERT INTO face_embeddings (employee_id, embedding, source_image) VALUES (?, ?, ?)",
        (employee_id, embedding_to_blob(embedding), source),
    )
    con.commit()


def largest_face(faces):
    """The subject is the closest face — ignore anyone in the background."""
    if not faces:
        return None
    return max(faces, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))


def enroll_from_images(con, employee_id, name, paths):
    saved = 0
    for p in paths:
        frame = cv2.imread(str(p))
        if frame is None:
            print(f"  ✗ o'qib bo'lmadi: {p}")
            continue
        face = largest_face(detect_faces(frame))
        if face is None:
            print(f"  ✗ yuz topilmadi: {p}")
            continue
        save_embedding(con, employee_id, face["embedding"], str(p))
        saved += 1
        print(f"  ✓ {p}")
    return saved


def enroll_from_camera(con, employee_id, name, shots):
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("ERROR: kamera ochilmadi")
        return 0

    FACES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n{shots} ta surat olinadi. Har safar boshingizni biroz burib turing")
    print("(to'g'riga, chapga, o'ngga, yuqoriga, pastga) — turli burchak aniqlikni oshiradi.\n")
    saved = 0
    for i in range(shots):
        for n in (3, 2, 1):
            print(f"  surat {i+1}/{shots} — {n}...", flush=True)
            time.sleep(SECONDS_BETWEEN_SHOTS / 3)
        for _ in range(5):          # drain buffered frames so we get a current one
            cap.grab()
        ok, frame = cap.retrieve()
        if not ok:
            print("  ✗ kadr olinmadi")
            continue
        face = largest_face(detect_faces(frame))
        if face is None:
            print("  ✗ yuz topilmadi, qaytadan")
            continue
        img_path = FACES_DIR / f"{employee_id}_{int(time.time())}.jpg"
        cv2.imwrite(str(img_path), frame)
        save_embedding(con, employee_id, face["embedding"], str(img_path))
        saved += 1
        print(f"  ✓ saqlandi (aniqlik {face['det_score']:.2f})")
    cap.release()
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="xodim ismi")
    ap.add_argument("--capture", type=int, metavar="N", help="kameradan N ta surat olish")
    ap.add_argument("--images", nargs="+", metavar="FILE", help="tayyor rasm fayllaridan")
    ap.add_argument("--list", action="store_true", help="ro'yxatdan o'tganlarni ko'rsatish")
    args = ap.parse_args()

    init_db()
    con = get_conn()

    if args.list:
        rows = con.execute(
            f"""SELECT e.id, {FULL_NAME_SQL} AS name, COUNT(f.id) AS faces
                FROM employees e LEFT JOIN face_embeddings f ON f.employee_id = e.id
                GROUP BY e.id ORDER BY e.first_name, e.last_name"""
        ).fetchall()
        print(f"\n{'ID':<4} {'Ism':<24} Yuz namunalari")
        print("-" * 46)
        for r in rows:
            print(f"{r['id']:<4} {r['name']:<24} {r['faces']}")
        print()
        con.close()
        return

    if not args.name or not (args.capture or args.images):
        ap.error("--name va (--capture N yoki --images FILE...) kerak")

    first, _, last = args.name.strip().partition(" ")
    existing = find_employee_by_name(con, first, last)
    employee_id = existing["id"] if existing else create_employee(con, first, last)
    print(f"Xodim: {args.name} (id={employee_id})")

    if args.images:
        saved = enroll_from_images(con, employee_id, args.name, args.images)
    else:
        saved = enroll_from_camera(con, employee_id, args.name, args.capture)

    total = con.execute(
        "SELECT COUNT(*) c FROM face_embeddings WHERE employee_id = ?", (employee_id,)
    ).fetchone()["c"]
    con.close()
    print(f"\n{saved} ta yangi namuna saqlandi. {args.name} uchun jami: {total}")


if __name__ == "__main__":
    main()
