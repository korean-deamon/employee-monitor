#!/usr/bin/env python3
"""Back up the attendance database, and drop copies older than the retention.

    python scripts/backup.py            # make a backup, prune old ones
    python scripts/backup.py --list     # show what is stored

Uses SQLite's own backup API rather than copying the file. A plain copy of a
database being written to can capture a half-finished transaction and restore as
a corrupt file; the backup API takes a consistent snapshot of a live database.

Losing `activity.db` means losing every enrolled face and the whole attendance
history, and nothing else in the project can reconstruct either.
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT / "data" / "activity.db"
BACKUP_DIR = PROJECT / "data" / "backups"
KEEP_DAYS = 30


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    target = BACKUP_DIR / f"activity_{stamp}.db"

    source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def prune(keep_days: int) -> list:
    cutoff = time.time() - keep_days * 86400
    removed = []
    for old in sorted(BACKUP_DIR.glob("activity_*.db")):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            removed.append(old.name)
    return removed


def listing():
    files = sorted(BACKUP_DIR.glob("activity_*.db"))
    if not files:
        print("Резервных копий нет.")
        return
    total = 0
    print(f"\nКопий: {len(files)} — {BACKUP_DIR}\n")
    for path in files:
        info = path.stat()
        total += info.st_size
        age = datetime.now() - datetime.fromtimestamp(info.st_mtime)
        print(f"  {path.name:<30} {human(info.st_size):>9}   {age.days} дн. назад")
    print(f"\n  всего: {human(total)}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="показать копии")
    parser.add_argument("--keep", type=int, default=KEEP_DAYS, help="сколько дней хранить")
    args = parser.parse_args()

    if args.list:
        listing()
        return

    if not DB_PATH.exists():
        sys.exit(f"база не найдена: {DB_PATH}")

    target = make_backup()
    removed = prune(args.keep)
    print(f"копия: {target.name}  ({human(target.stat().st_size)})")
    if removed:
        print(f"удалено (старше {args.keep} дн.): {len(removed)}")


if __name__ == "__main__":
    main()
