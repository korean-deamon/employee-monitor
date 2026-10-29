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

print("\n1. Осознанная остановка утром = приход")
r = attendance.register_stand(con, cfg, ali, at(8, 47))
check("событие", r["event"], "keldi")
check("источник", "recorded", r["status"])
check("минут раньше", r["grade"]["early_minutes"], 13)

print("\n2. Сразу повторно = повтор, а не уход")
r = attendance.register_stand(con, cfg, ali, at(8, 48))
check("состояние", r["status"], "already")
check("событие", r["event"], "keldi")

print("\n3. Остановка до 18:00 = уход + нужна причина")
r = attendance.register_stand(con, cfg, ali, at(15, 20))
check("событие", r["event"], "ketdi")
check("нужна причина", r["grade"]["needs_reason"], True)
check("ранний уход", r["grade"]["early_leave_minutes"], 160)

print("\n4. Возвращение = новый приход (перерыв закрывается сам)")
r = attendance.register_stand(con, cfg, ali, at(16, 10))
check("событие", r["event"], "keldi")

print("\n5. Остановка после 18:00 = уход, причина не нужна")
r = attendance.register_stand(con, cfg, ali, at(18, 30))
check("событие", r["event"], "ketdi")
check("причина не нужна", r["grade"].get("needs_reason"), None)
check("переработка", r["grade"]["overtime_minutes"], 30)

print("\n6. Приход с опозданием")
r = attendance.register_stand(con, cfg, vali, at(9, 17))
check("событие", r["event"], "keldi")
check("опоздание", r["grade"]["late_minutes"], 17)

print("\n7. Не остановившийся тоже получает приход (запасной путь)")
dil = db.create_employee(con, "Dilnoza", "Karimova")
r = attendance.register_first_sighting(con, cfg, dil, at(8, 55))
check("событие", r["event"], "keldi")
check("второй раз не записывает",
      attendance.register_first_sighting(con, cfg, dil, at(9, 5)), None)

print("\n8. Конец дня — открытый день закрывается сам")
con.execute("INSERT INTO presence_samples (employee_id, ts) VALUES (?, ?)",
            (vali, at(17, 55).isoformat()))
con.commit()
closed = attendance.close_stale(con, cfg, at(18, 40))
# Vali (last seen 17:55) and Dilnoza (seen once at 08:55, never again) both have
# an arrival with nothing after it, so both days get closed.
check("закрыт", len(closed), 2)
by_emp = {c["employee_id"]: c["ts"] for c in closed}
check("по времени последнего появления", by_emp[vali], at(17, 55).isoformat())
check("без образцов — по времени прихода", by_emp[dil], at(8, 55).isoformat())
check("кто ещё виден, не закрывается",
      len(attendance.close_stale(con, cfg, at(18, 0))), 0)

print("\n9. Итоги дня")
s = attendance.day_summary(con, cfg, ali, cfgmod.local_day(cfg, at(12, 0)))
check("приход", s["arrived"], at(8, 47).isoformat())
check("уход", s["left"], at(18, 30).isoformat())
check("число перерывов", len(s["breaks"]), 1)
check("перерыв 50 минут", s["breaks"][0]["seconds"], 50 * 60)
check("отработанное время", s["worked_seconds"], (6 * 3600 + 33 * 60) + (2 * 3600 + 20 * 60))

print("\n10. Остановка — проход мимо не записывается")
det = StandDetector(cfg)
fired = None
for i in range(12):                       # face crosses the frame over ~3s
    x = 100 + i * 90
    fired = det.update([{"employee_id": ali, "bbox": (x, 0, x + 260, 260)}], 1280, i * 0.3)
    if fired:
        break
check("прошедший мимо", fired, None)

print("\n11. Остановка — стоящий на месте записывается")
det = StandDetector(cfg)
fired = None
for i in range(14):
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, i * 0.3)
    if fired:
        break
check("подошедший и стоящий", fired, ali)

print("\n12. Остановка — далёкое лицо не учитывается")
det = StandDetector(cfg)
fired = None
for i in range(14):
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 600, 100)}], 1280, i * 0.3)
    if fired:
        break
check("лицо 100px", fired, None)

print("\n13. Если двое сменяют друг друга, имя не угадывается")
det = StandDetector(cfg)
fired = None
for i in range(14):
    who = ali if i % 2 else vali
    fired = det.update([{"employee_id": who, "bbox": (500, 0, 760, 260)}], 1280, i * 0.3)
    if fired:
        break
check("смешанное голосование", fired, None)

print("\n14. Пропуск кадра не сбрасывает остановку")
det = StandDetector(cfg)
fired = None
for i in range(14):
    # Every third cycle the detector misses the face entirely.
    seen = [] if i % 3 == 2 else [{"employee_id": ali, "bbox": (500, 0, 760, 260)}]
    fired = det.update(seen, 1280, i * 0.3)
    if fired:
        break
check("выдержал пропуск кадра", fired, ali)

print("\n15. Если лицо исчезло надолго, остановка отменяется")
det = StandDetector(cfg)
det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, 0.0)
det.update([], 1280, 2.0)        # gone longer than stand_gap_seconds
fired = None
for i in range(6):               # only 1.8s of standing after the gap
    fired = det.update([{"employee_id": ali, "bbox": (500, 0, 760, 260)}], 1280, 2.1 + i * 0.3)
check("отсчёт начался заново", fired, None)

con.close()
print(f"\n{'=' * 46}\n{ok} ok, {fail} ошибок\n")
sys.exit(1 if fail else 0)
