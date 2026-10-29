"""Exercise the door counter: direction, repeats, two people, loitering."""
import sys
import tempfile
from pathlib import Path

BACKEND = Path("/home/deamon/Projects/employee-monitor/backend")
sys.path.insert(0, str(BACKEND))

import db

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"

import config as cfgmod
import counting

cfg = cfgmod.load()
cfg["counter_enabled"] = True
cfg["door_line"] = [[0.0, 0.5], [1.0, 0.5]]      # horizontal, middle of frame
cfg["door_invert"] = False
cfg["count_point"] = "foot"

W, H = 1280, 720
ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  ok    {label}")
    else:
        fail += 1
        print(f"  XATO  {label}: {got!r} != {want!r}")


def body(x, y):
    """A person whose feet are at (x, y)."""
    return {"box": (x - 40, y - 160, x + 40, y), "foot": (x, y), "conf": 0.9}


def walk(counter, x, ys, start=0.0, step=0.2):
    """Move one person down the frame through the given foot heights."""
    fired = []
    for i, y in enumerate(ys):
        fired += counter.update([body(x, y)], W, H, start + i * step)
    return fired


print("\n1. Yuqoridan pastga o'tish")
c = counting.Counter(cfg)
fired = walk(c, 640, [200, 300, 420, 500])       # crosses y=360
check("bitta o'tish", len(fired), 1)
direction_down = fired[0]["direction"]
check("yo'nalish aniqlandi", direction_down in ("kirdi", "chiqdi"), True)

print("\n2. Pastdan yuqoriga — teskari yo'nalish")
c = counting.Counter(cfg)
fired = walk(c, 640, [500, 420, 300, 200])
check("bitta o'tish", len(fired), 1)
check("teskari yo'nalish", fired[0]["direction"] != direction_down, True)

print("\n3. Chiziqqa yetmasdan qaytib ketsa — sanalmaydi")
c = counting.Counter(cfg)
fired = walk(c, 640, [200, 280, 320, 280, 200])
check("o'tish yo'q", len(fired), 0)

print("\n4. Chiziq ustida turib qolsa — bir marta")
c = counting.Counter(cfg)
# Steps across, then wobbles over the line for several seconds.
fired = walk(c, 640, [300, 420, 350, 400, 350, 400], step=0.2)
check("faqat bitta sanaldi", len(fired), 1)

print("\n5. Ikki odam yonma-yon")
c = counting.Counter(cfg)
fired = []
for i, y in enumerate([200, 300, 420, 500]):
    fired += c.update([body(400, y), body(900, y)], W, H, i * 0.2)
check("ikkitasi ham sanaldi", len(fired), 2)
check("ikki alohida iz", len({f["track"] for f in fired}), 2)

print("\n6. Qarama-qarshi yo'nalishda ikki odam")
c = counting.Counter(cfg)
fired = []
down = [200, 300, 420, 500]
up = [500, 420, 300, 200]
for i in range(4):
    fired += c.update([body(400, down[i]), body(900, up[i])], W, H, i * 0.2)
check("ikkitasi sanaldi", len(fired), 2)
check("yo'nalishlar qarama-qarshi",
      len({f["direction"] for f in fired}), 2)

print("\n7. door_invert yo'nalishni almashtiradi")
flipped = dict(cfg, door_invert=True)
c = counting.Counter(flipped)
fired = walk(c, 640, [200, 300, 420, 500])
check("almashdi", fired[0]["direction"] != direction_down, True)

print("\n8. Uzoqda turgan odam — chiziqqa tegmasa sanalmaydi")
c = counting.Counter(cfg)
fired = walk(c, 640, [100, 120, 110, 130])
check("o'tish yo'q", len(fired), 0)

print("\n9. Bazaga yozish va kunlik hisob")
db.init_db()
con = db.get_conn()
now = cfgmod.now_utc()
counting.record(con, cfg, "kirdi", now)
counting.record(con, cfg, "kirdi", now)
counting.record(con, cfg, "chiqdi", now)
totals = counting.totals_for_day(con, cfgmod.local_day(cfg, now))
check("kirdi", totals.get("kirdi"), 2)
check("chiqdi", totals.get("chiqdi"), 1)
check("ichkarida", totals.get("kirdi", 0) - totals.get("chiqdi", 0), 1)

print("\n10. Davomat jadvaliga tegmaydi")
rows = con.execute("SELECT COUNT(*) c FROM attendance_events").fetchone()["c"]
check("davomat yozuvlari o'zgarmadi", rows, 0)
con.close()

print("\n11. count_point: burun nuqtasi ishlatiladi")
nose_cfg = dict(cfg, count_point="nose")
c = counting.Counter(nose_cfg)
fired = []
for i, y in enumerate([200, 300, 420, 500]):
    # Body box stays put (as when a person is cut off by the frame edge);
    # only the nose moves. With count_point=foot this would never fire.
    person = body(640, 700)
    person["nose"] = (640.0, float(y))
    fired += c.update([person], W, H, i * 0.2)
check("burun kesib o-tdi", len(fired), 1)

c = counting.Counter(cfg)            # same frames, but count_point=foot
fired = []
for i, y in enumerate([200, 300, 420, 500]):
    person = body(640, 700)
    person["nose"] = (640.0, float(y))
    fired += c.update([person], W, H, i * 0.2)
check("oyoq nuqtasi qimirlamadi, sanalmadi", len(fired), 0)

print("\n12. Burun topilmasa oyoq nuqtasiga qaytadi")
c = counting.Counter(nose_cfg)
fired = walk(c, 640, [200, 300, 420, 500])    # no nose key at all
check("zaxira ishladi", len(fired), 1)

print(f"\n{'=' * 46}\n{ok} ok, {fail} xato\n")
sys.exit(1 if fail else 0)
