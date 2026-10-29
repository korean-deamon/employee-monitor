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

print("\n1. Формат кода")
issued = tg.new_code(con, cfg, ali)
check("длина", len(issued["code"]), 8)
check("вид XXXX-XXXX", issued["display"][4], "-")
check("нет путающих символов",
      any(c in issued["code"] for c in "01OIL"), False)

print("\n2. Привязка")
r = tg.redeem(con, cfg, issued["display"], 111222, "ali_v")
check("подключился", r["ok"], True)
check("имя", r["name"], "Ali Valiyev")
check("есть в базе", tg.link_for(con, ali)["telegram_id"], 111222)
check("обратный поиск", tg.employee_for(con, 111222)["employee_id"], ali)

print("\n3. Код работает один раз")
check("второй раз", tg.redeem(con, cfg, issued["code"], 999, "x")["reason"], "used")

print("\n4. Новый код отменяет старый")
first = tg.new_code(con, cfg, vali)
second = tg.new_code(con, cfg, vali)
check("старый код не проходит", tg.redeem(con, cfg, first["code"], 333, "")["reason"], "expired")
check("новый код проходит", tg.redeem(con, cfg, second["code"], 333, "")["ok"], True)

print("\n5. Один аккаунт — один человек")
dil = db.create_employee(con, "Dilnoza", "Karimova")
dil_code = tg.new_code(con, cfg, dil)
check("аккаунт другого человека",
      tg.redeem(con, cfg, dil_code["code"], 111222, "")["reason"], "account_taken")

print("\n6. Смена телефона — новый аккаунт тому же сотруднику")
new_code = tg.new_code(con, cfg, ali)
check("заменено", tg.redeem(con, cfg, new_code["code"], 777888, "")["ok"], True)
check("новый id", tg.link_for(con, ali)["telegram_id"], 777888)
check("старый id отвязан", tg.employee_for(con, 111222), None)

print("\n7. Неверный код и ограничение попыток")
attacker = 555000
reasons = [tg.redeem(con, cfg, "AAAA-AAAA", attacker, "")["reason"] for _ in range(6)]
check("первые попытки", reasons[0], "unknown")
check("6-я попытка остановлена", reasons[-1], "rate_limited")
check("короткий код", tg.redeem(con, cfg, "ABC", 42, "")["reason"], "malformed")

print("\n8. Код с истёкшим сроком")
old = tg.new_code(con, cfg, dil)
con.execute("UPDATE pairing_codes SET expires_at = ? WHERE code = ?",
            ((cfgmod.now_utc() - timedelta(hours=1)).isoformat(), old["code"]))
con.commit()
check("просрочен", tg.redeem(con, cfg, old["code"], 4444, "")["reason"], "expired")

print("\n9. Отвязка")
check("отвязан", tg.unlink(con, vali), True)
check("повторная отвязка", tg.unlink(con, vali), False)

print("\n10. Сообщение о приходе")
ev = attendance.register_stand(con, cfg, ali, at(9, 17))
tg.arrival_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"])
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
check("тип", row["kind"], "keldi")
check("время в тексте", "09:17" in row["body"], True)
check("часы и минуты", "Опоздание 17 мин" in row["body"], True)
check("адрес есть", row["telegram_id"], 777888)
check("ещё не отправлено", row["sent_at"], None)

print("\n11. Ранний уход — с кнопками")
ev = attendance.register_stand(con, cfg, ali, at(15, 20))
check("нужна причина", ev["grade"]["needs_reason"], True)
tg.early_departure_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"])
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
buttons = json.loads(row["buttons"])
check("три кнопки", [b["text"] for b in buttons[0]], ["Да", "Нет", "Причина"])
check("привязано к событию", buttons[0][0]["data"], f"leave:ha:{ev['id']}")
check("оставшееся время", "2 ч 40 мин" in row["body"], True)

print("\n12. Клавиатура причин")
prompt = tg.reason_prompt(ev["id"])
labels = [b["text"] for row_ in prompt["buttons"] for b in row_]
check("причины", labels, ["Обед", "Врач", "Личное дело", "Рабочее поручение", "Другое"])

print("\n13. Уход после 18:00 — без вопроса, с итогом")
attendance.register_stand(con, cfg, ali, at(16, 0))
ev = attendance.register_stand(con, cfg, ali, at(18, 30))
check("причина не нужна", ev["grade"].get("needs_reason"), None)
summary = attendance.day_summary(con, cfg, ali, cfgmod.local_day(cfg, at(12, 0)))
tg.departure_message(cfg, con, ali, ev["id"], ev["ts"], ev["grade"], summary)
row = con.execute("SELECT * FROM telegram_outbox ORDER BY id DESC LIMIT 1").fetchone()
check("кнопок нет", row["buttons"], None)
check("интервал дня", "09:17 – 18:30" in row["body"], True)
check("всего часов", "Итого: 8 ч" in row["body"], True)
check("перерыв", "Перерыв: 40 мин" in row["body"], True)

print("\n14. Очередь — отправляются только те, у кого есть адрес")
tg.queue(con, "test", dil, "Dilnozaga xabar")     # dil is not linked
pending_ids = [r["employee_id"] for r in tg.pending(con)]
check("непривязанный сотрудник не в очереди", dil in pending_ids, False)
check("привязанный сотрудник в очереди", ali in pending_ids, True)

print("\n15. Отметка об отправке")
first_pending = tg.pending(con)[0]
tg.mark_sent(con, first_pending["id"])
check("вышел из очереди", first_pending["id"] in [r["id"] for r in tg.pending(con)], False)
tg.mark_failed(con, tg.pending(con)[0]["id"], "network down")
check("сбойное тоже не в очереди",
      con.execute("SELECT COUNT(*) c FROM telegram_outbox WHERE error IS NOT NULL"
                  ).fetchone()["c"], 1)

print("\n16. Отчёт /bugun")
report = tg.day_report(cfg, con, ali, cfgmod.local_day(cfg, at(12, 0)))
check("приход есть", "вошёл 09:17" in report, True)
check("уход есть", "вышел 18:30" in report, True)
check("итог есть", "Итого: 8 ч" in report, True)

print("\n17. Отчёт /hafta")
week = tg.week_report(cfg, con, ali)
check("заголовок", week.startswith("🗓 Эта неделя"), True)
check("итог есть", "Итого:" in week, True)

print("\n18. Повторное сообщение не забивает очередь")
before = con.execute("SELECT COUNT(*) c FROM telegram_outbox").fetchone()["c"]
for _ in range(10):
    tg.already_message(cfg, con, ali, at(9, 20))
after = con.execute("SELECT COUNT(*) c FROM telegram_outbox").fetchone()["c"]
check("из 10 попыток записана только 1", after - before, 1)
check("вторая отклонена", tg.already_message(cfg, con, ali, at(9, 21)), None)

con.close()
print(f"\n{'=' * 46}\n{ok} ok, {fail} ошибок\n")
sys.exit(1 if fail else 0)
