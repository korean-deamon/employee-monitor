"""Exercise the attendance rules against a throwaway database."""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path("/home/deamon/Projects/employee-monitor/backend")
sys.path.insert(0, str(BACKEND))

import db

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"

import attendance
import config as cfgmod
from standing import StandDetector

cfg = cfgmod.load()
TZ = ZoneInfo(cfg["timezone"])
ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  ok    {label}")
    else:
        fail += 1
        print(f"  XATO  {label}: {got!r} != {want!r}")


def at(hour, minute):
    """A local wall-clock time today, as the UTC instant that gets stored."""
    local = datetime.now(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(cfgmod.timezone.utc)


db.init_db()
con = db.get_conn()
ali = db.create_employee(con, "Ali", "Valiyev")
vali = db.create_employee(con, "Vali", "Aliyev")

print("\n1. Ertalab ataylab turish = keldi")
r = attendance.register_stand(con, cfg, ali, at(8, 47))
check("hodisa", r["event"], "keldi")
check("manba", "recorded", r["status"])
check("erta daqiqa", r["grade"]["early_minutes"], 13)

print("\n2. Darhol qayta turish = takroriy, ketdi emas")
r = attendance.register_stand(con, cfg, ali, at(8, 48))
check("holat", r["status"], "already")
check("hodisa", r["event"], "keldi")

print("\n3. 18:00 dan oldin turish = ketdi + sabab kerak")
r = attendance.register_stand(con, cfg, ali, at(15, 20))
check("hodisa", r["event"], "ketdi")
check("sabab kerak", r["grade"]["needs_reason"], True)
check("erta ketish", r["grade"]["early_leave_minutes"], 160)

print("\n4. Qaytib kelish = yangi keldi (obed o'zi yopiladi)")
r = attendance.register_stand(con, cfg, ali, at(16, 10))
check("hodisa", r["event"], "keldi")

print("\n5. 18:00 dan keyin turish = ketdi, sabab kerak emas")
r = attendance.register_stand(con, cfg, ali, at(18, 30))
check("hodisa", r["event"], "ketdi")
check("sabab kerak emas", r["grade"].get("needs_reason"), None)
check("qo'shimcha vaqt", r["grade"]["overtime_minutes"], 30)

print("\n6. Kechikib kelish")
r = attendance.register_stand(con, cfg, vali, at(9, 17))
check("hodisa", r["event"], "keldi")
check("kechikish", r["grade"]["late_minutes"], 17)

print("\n7. Turmagan odam ham keldi bo'ladi (zaxira)")
dil = db.create_employee(con, "Dilnoza", "Karimova")
r = attendance.register_first_sighting(con, cfg, dil, at(8, 55))
check("hodisa", r["event"], "keldi")
check("ikkinchi marta yozmaydi",
      attendance.register_first_sighting(con, cfg, dil, at(9, 5)), None)

print("\n8. Kun oxiri — ochiq kun o'zi yopiladi")
con.execute("INSERT INTO presence_samples (employee_id, ts) VALUES (?, ?)",
            (vali, at(17, 55).isoformat()))
con.commit()
closed = attendance.close_stale(con, cfg, at(18, 40))
# Vali (last seen 17:55) and Dilnoza (seen once at 08:55, never again) both have
# an arrival with nothing after it, so both days get closed.
check("yopildi", len(closed), 2)
by_emp = {c["employee_id"]: c["ts"] for c in closed}
check("oxirgi ko'rinish vaqti bilan", by_emp[vali], at(17, 55).isoformat())
check("namunasi yo'q bo'lsa kelish vaqti bilan", by_emp[dil], at(8, 55).isoformat())
check("hali ko'rinib turgan odam yopilmaydi",
      len(attendance.close_stale(con, cfg, at(18, 0))), 0)

print("\n9. Kun hisobi")
s = attendance.day_summary(con, cfg, ali, cfgmod.local_day(cfg, at(12, 0)))
check("keldi", s["arrived"], at(8, 47).isoformat())
check("ketdi", s["left"], at(18, 30).isoformat())
check("tanaffus soni", len(s["breaks"]), 1)
check("tanaffus 50 daqiqa", s["breaks"][0]["seconds"], 50 * 60)
check("ishlagan vaqti", s["worked_seconds"], (6 * 3600 + 33 * 60) + (2 * 3600 + 20 * 60))

print("\n10. Turish aniqlash — yonidan o'tish yozilmaydi")
det = StandDetector(cfg)
fired = None
for i in range(12):                       # face crosses the frame over ~3s
    x = 100 + i * 90
    fired = det.update([{"employee_id": ali, "bbox": (x, 0, x + 260, 260)}], 1280, i * 0.3)
    if fired:
        break
check("o'tib ketgan odam", fired, None)

print("\n11. Turish aniqlash — joyida turish yoziladi")
det = StandDetector(cfg)
fired = None
for i in range(14):
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, i * 0.3)
    if fired:
        break
check("kelib turgan odam", fired, ali)

print("\n12. Turish aniqlash — uzoqdagi yuz hisobga olinmaydi")
det = StandDetector(cfg)
fired = None
for i in range(14):
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 600, 100)}], 1280, i * 0.3)
    if fired:
        break
check("100px yuz", fired, None)

print("\n13. Ikki odam almashib tursa, ism taxmin qilinmaydi")
det = StandDetector(cfg)
fired = None
for i in range(14):
    who = ali if i % 2 else vali
    fired = det.update([{"employee_id": who, "bbox": (500, 0, 760, 260)}], 1280, i * 0.3)
    if fired:
        break
check("aralash ovoz", fired, None)

print("\n14. Bitta kadr tushib qolsa turish nolga qaytmaydi")
det = StandDetector(cfg)
fired = None
for i in range(14):
    # Every third cycle the detector misses the face entirely.
    seen = [] if i % 3 == 2 else [{"employee_id": ali, "bbox": (500, 0, 760, 260)}]
    fired = det.update(seen, 1280, i * 0.3)
    if fired:
        break
check("kadr tushishiga chidadi", fired, ali)

print("\n15. Yuz uzoq ketsa turish bekor bo'ladi")
det = StandDetector(cfg)
det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, 0.0)
det.update([], 1280, 2.0)        # gone longer than stand_gap_seconds
fired = None
for i in range(6):               # only 1.8s of standing after the gap
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, 2.1 + i * 0.3)
check("hisob qaytadan boshlandi", fired, None)

con.close()
print(f"\n{'=' * 46}\n{ok} ok, {fail} xato\n")
sys.exit(1 if fail else 0)
