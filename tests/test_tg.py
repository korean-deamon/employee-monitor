"""Exercise pairing, the outbox and message texts against a throwaway database."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path("/home/deamon/Projects/employee-monitor/backend")
sys.path.insert(0, str(BACKEND))

import db

db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"

import attendance
import config as cfgmod
import tg

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
    local = datetime.now(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


db.init_db()
con = db.get_conn()
ali = db.create_employee(con, "Ali", "Valiyev", "Sotuv")
vali = db.create_employee(con, "Vali", "Aliyev")

print("\n1. Kod formati")
issued = tg.new_code(con, cfg, ali)
check("uzunligi", len(issued["code"]), 8)
check("ko'rinishi XXXX-XXXX", issued["display"][4], "-")
check("adashtiradigan belgi yo'q",
      any(c in issued["code"] for c in "01OIL"), False)

print("\n2. Bog'lanish")
r = tg.redeem(con, cfg, issued["display"], 111222, "ali_v")
check("ulandi", r["ok"], True)
check("ismi", r["name"], "Ali Valiyev")
check("bazada bor", tg.link_for(con, ali)["telegram_id"], 111222)
check("teskari izlash", tg.employee_for(con, 111222)["employee_id"], ali)

print("\n3. Kod bir marta ishlaydi")
check("ikkinchi marta", tg.redeem(con, cfg, issued["code"], 999, "x")["reason"], "used")

print("\n4. Yangi kod eskisini bekor qiladi")
first = tg.new_code(con, cfg, vali)
second = tg.new_code(con, cfg, vali)
check("eski kod o'tmaydi", tg.redeem(con, cfg, first["code"], 333, "")["reason"], "expired")
check("yangi kod o'tadi", tg.redeem(con, cfg, second["code"], 333, "")["ok"], True)

print("\n5. Bir akkaunt bir odamga")
dil = db.create_employee(con, "Dilnoza", "Karimova")
dil_code = tg.new_code(con, cfg, dil)
check("boshqa odamning akkaunti",
      tg.redeem(con, cfg, dil_code["code"], 111222, "")["reason"], "account_taken")

print("\n6. Telefon almashtirish — o'sha xodimga yangi akkaunt")
new_code = tg.new_code(con, cfg, ali)
check("almashtirildi", tg.redeem(con, cfg, new_code["code"], 777888, "")["ok"], True)
check("yangi id", tg.link_for(con, ali)["telegram_id"], 777888)
check("eski id uzilgan", tg.employee_for(con, 111222), None)

print("\n7. Noto'g'ri kod va urinish cheklovi")
attacker = 555000
reasons = [tg.redeem(con, cfg, "AAAA-AAAA", attacker, "")["reason"] for _ in range(6)]
check("birinchi urinishlar", reasons[0], "unknown")
check("6-urinish to'xtatildi", reasons[-1], "rate_limited")
check("qisqa kod", tg.redeem(con, cfg, "ABC", 42, "")["reason"], "malformed")

print("\n8. Muddati o'tgan kod")
old = tg.new_code(con, cfg, dil)
con.execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?",
            ((cfgmod.now_utc() - timedelta(hours=1)).isoformat(), old["code"]))
con.commit()
check("eskirgan", tg.redeem(con, cfg, old["code"], 4444, "")["reason"], "expired")

print("\n9. Uzish")
check("uzildi", tg.unlink(con, vali), True)
check("qayta uzish", tg.unlink(con, vali), False)

print("\n10. Keldi xabari")
ev = attendance.register_stand(con, cfg, ali, at(9, 17))
tg.arrival_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"])
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
check("turi", row["kind"], "keldi")
check("vaqt matnda", "09:17" in row["body"], True)
check("soat+daqiqa", "17 daqiqa kech qoldingiz" in row["body"], True)
check("manzili bor", row["telegram_id"], 777888)
check("hali yuborilmagan", row["sent_at"], None)

print("\n11. Erta ketish — tugmalar bilan")
ev = attendance.register_stand(con, cfg, ali, at(15, 20))
check("sabab kerak", ev["grade"]["needs_reason"], True)
tg.early_departure_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"])
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
buttons = json.loads(row["buttons"])
check("uch tugma", [b["text"] for b in buttons[0]], ["Ha", "Yo'q", "Sabab"])
check("hodisaga bog'langan", buttons[0][0]["data"], f"leave:ha:{ev['id']}")
check("qolgan vaqt", "2 soat 40 daqiqa" in row["body"], True)

print("\n12. Sabab klaviaturasi")
prompt = tg.reason_prompt(ev["id"])
labels = [b["text"] for row_ in prompt["buttons"] for b in row_]
check("sabablar", labels, ["Obed", "Shifokor", "Shaxsiy ish", "Ish topshirig'i", "Boshqa"])

print("\n13. 18:00 dan keyin ketish — savol yo'q, hisob bor")
attendance.register_stand(con, cfg, ali, at(16, 0))
ev = attendance.register_stand(con, cfg, ali, at(18, 30))
check("sabab kerak emas", ev["grade"].get("needs_reason"), None)
summary = attendance.day_summary(con, cfg, ali, cfgmod.local_day(cfg, at(12, 0)))
tg.departure_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"], summary)
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
check("tugma yo'q", row["buttons"], None)
check("kun oralig'i", "09:17 – 18:30" in row["body"], True)
check("jami soat", "Jami: 8 soat" in row["body"], True)
check("tanaffus", "Tanaffus: 40 daqiqa" in row["body"], True)

print("\n14. Navbat — faqat manzili borlari yuboriladi")
tg.queue(con, "test", dil, "Dilnozaga xabar")     # dil is not linked
pending_ids = [r["employee_id"] for r in tg.pending(con)]
check("ulanmagan xodim navbatda yo'q", dil in pending_ids, False)
check("ulangan xodim navbatda", ali in pending_ids, True)

print("\n15. Yuborilgan deb belgilash")
first_pending = tg.pending(con)[0]
tg.mark_sent(con, first_pending["id"])
check("navbatdan chiqdi", first_pending["id"] in [r["id"] for r in tg.pending(con)], False)
tg.mark_failed(con, tg.pending(con)[0]["id"], "network down")
check("xatoli ham navbatda emas",
      con.execute("SELECT COUNT(*) c FROM telegram_outbox WHERE error IS NOT NULL"
                  ).fetchone()["c"], 1)

print("\n16. /bugun hisoboti")
report = tg.day_report(cfg, con, ali, cfgmod.local_day(cfg, at(12, 0)))
check("keldi bor", "keldi 09:17" in report, True)
check("ketdi bor", "ketdi 18:30" in report, True)
check("jami bor", "Jami: 8 soat" in report, True)

print("\n17. /hafta hisoboti")
week = tg.week_report(cfg, con, ali)
check("sarlavha", week.startswith("🗓 Shu hafta"), True)
check("jami bor", "Jami:" in week, True)

print("\n18. Takroriy xabar navbatni to'ldirmaydi")
before = con.execute("SELECT COUNT(*) c FROM telegram_outbox").fetchone()["c"]
for _ in range(10):
    tg.already_message(cfg, con, ali, at(9, 20))
after = con.execute("SELECT COUNT(*) c FROM telegram_outbox").fetchone()["c"]
check("10 urinishdan faqat 1 ta yozildi", after - before, 1)
check("ikkinchisi rad etildi", tg.already_message(cfg, con, ali, at(9, 21)), None)

con.close()
print(f"\n{'=' * 46}\n{ok} ok, {fail} xato\n")
sys.exit(1 if fail else 0)
