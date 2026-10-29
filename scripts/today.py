#!/usr/bin/env python3
"""Show today's attendance records, or clear them to run the test again.

    python scripts/today.py
    python scripts/today.py --clear

Clearing only removes today's attendance events, so a tuning run can be
repeated without the previous run's arrival making every stand a departure.
Face data and employees are never touched.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import config as cfgmod
from db import FULL_NAME_SQL, get_conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="удалить сегодняшние записи")
    ap.add_argument("--day", help="YYYY-MM-DD (по умолчанию: сегодня)")
    args = ap.parse_args()

    cfg = cfgmod.load()
    day = args.day or cfgmod.local_day(cfg, cfgmod.now_utc())
    con = get_conn()

    rows = con.execute(
        f"""SELECT a.id, a.ts, a.event, a.source, a.reason, a.note,
                   {FULL_NAME_SQL} AS name
              FROM attendance_events a JOIN employees e ON e.id = a.employee_id
             WHERE a.local_day = ? AND a.cancelled = 0
             ORDER BY a.ts""",
        (day,),
    ).fetchall()

    print(f"\n{day} — записей: {len(rows)}\n")
    for row in rows:
        local = cfgmod.to_local(cfg, cfgmod.parse_ts(row["ts"]))
        extra = row["reason"] or row["note"] or ""
        print(f"  {local:%H:%M:%S}  {row['event']:<6} {row['name']:<16} "
              f"{row['source']:<14} {extra}")
    print()

    if args.clear and rows:
        con.execute("DELETE FROM attendance_events WHERE local_day = ?", (day,))
        con.commit()
        print(f"Удалено записей: {len(rows)}. Образцы лиц и сотрудники сохранены.\n")
    con.close()


if __name__ == "__main__":
    main()
