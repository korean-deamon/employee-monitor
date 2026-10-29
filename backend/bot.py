#!/usr/bin/env python3
"""The only part of the system that talks to Telegram.

It does two things on a loop: read what employees send (a pairing code, a
command, a tapped button) and deliver what the outbox holds. Both are gated:

    telegram_enabled              — run at all
    telegram_send_notifications   — actually deliver attendance messages

That split exists so pairing and `/bugun` can be tried out while nobody's phone
buzzes. Everything it writes goes through `tg.py` and `attendance.py`; no
attendance rule lives here.

    python backend/bot.py
"""
import json
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attendance
import config as cfgmod
import tg
from db import get_conn, init_db

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30
OUTBOX_EVERY_SECONDS = 3

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def read_token() -> str:
    """Read the token from .env. Never logged, never echoed."""
    try:
        for line in ENV_PATH.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "TELEGRAM_BOT_TOKEN" and value.strip():
                return value.strip()
    except OSError:
        pass
    return ""


def call(token, method, **params):
    """One Bot API call. Returns the result, or None on failure."""
    url = API.format(token=token, method=method)
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as resp:
            payload = json.loads(resp.read())
        return payload.get("result") if payload.get("ok") else None
    except urllib.error.HTTPError as e:
        # Telegram explains refusals in the body; the status alone is not enough
        # to tell "blocked by user" from "bad token".
        detail = e.read().decode(errors="replace")[:200]
        print(f"[bot] {method} HTTP {e.code}: {detail}")
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"[bot] {method} failed: {e}")
        return None


def keyboard(buttons):
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]}
                                 for b in row] for row in buttons]}


def send(token, chat_id, text, buttons=None):
    params = {"chat_id": chat_id, "text": text}
    markup = keyboard(buttons)
    if markup:
        params["reply_markup"] = markup
    return call(token, "sendMessage", **params)


# ── Incoming ──────────────────────────────────────────────────────────────────

HELP = ("Buyruqlar:\n"
        "/bugun — bugungi yozuvingiz\n"
        "/hafta — shu haftaning hisobi\n"
        "/kechikish — kechikishlar tarixi\n"
        "/help — shu ro'yxat")

PAIR_PROMPT = ("Salom! Davomat tizimiga ulanish uchun\n"
               "admin bergan kodni yuboring.\n\n"
               "Masalan: K7M4-RPQX")

REDEEM_ERRORS = {
    "rate_limited": "Juda ko'p urinish. Bir soatdan keyin qayta urinib ko'ring.",
    "malformed": "Kod 8 belgidan iborat bo'ladi. Masalan: K7M4-RPQX",
    "unknown": "Bunday kod yo'q. Admindan qaytadan so'rang.",
    "used": "Bu kod allaqachon ishlatilgan. Admindan yangisini so'rang.",
    "expired": "Kodning muddati o'tgan. Admindan yangisini so'rang.",
    "account_taken": "Bu Telegram akkaunt boshqa xodimga ulangan.",
}


def handle_message(token, con, cfg, message):
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    from_user = message.get("from", {})
    telegram_id = from_user.get("id")
    if not chat_id or not telegram_id:
        return

    link = tg.employee_for(con, telegram_id)

    if not link:
        # Nothing is revealed to an unlinked account except that a code is
        # needed — not whether a code was close, not who is in the system.
        if text.startswith("/"):
            send(token, chat_id, PAIR_PROMPT)
            return
        result = tg.redeem(con, cfg, text, telegram_id,
                           from_user.get("username", ""))
        if result["ok"]:
            send(token, chat_id,
                 f"✅ Ulandingiz.\nSiz: {result['name']}\n\n{HELP}")
            print(f"[bot] ulandi: {result['name']}")
        else:
            send(token, chat_id, REDEEM_ERRORS.get(result["reason"], "Kod to'g'ri kelmadi."))
        return

    employee_id = link["employee_id"]
    command = text.split()[0].lower() if text else ""
    if command in ("/start", "/help"):
        send(token, chat_id,
             f"Siz: {tg.employee_name(con, employee_id)}\n\n{HELP}")
    elif command == "/bugun":
        send(token, chat_id,
             tg.day_report(cfg, con, employee_id,
                           cfgmod.local_day(cfg, cfgmod.now_utc())))
    elif command == "/hafta":
        send(token, chat_id, tg.week_report(cfg, con, employee_id))
    elif command == "/kechikish":
        send(token, chat_id, tg.late_report(cfg, con, employee_id))
    else:
        send(token, chat_id, HELP)


def handle_callback(token, con, cfg, callback):
    """A tapped button. The event id arrives from outside, so every write is
    scoped to the employee who tapped — see attendance.confirm_event.
    """
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    telegram_id = callback.get("from", {}).get("id")
    link = tg.employee_for(con, telegram_id) if telegram_id else None

    def answer(text=""):
        call(token, "answerCallbackQuery", callback_query_id=callback["id"], text=text)

    if not link or not chat_id:
        answer("Siz ulanmagansiz.")
        return

    employee_id = link["employee_id"]
    kind, _, rest = data.partition(":")
    choice, _, raw_id = rest.partition(":")
    try:
        event_id = int(raw_id)
    except ValueError:
        answer()
        return

    if kind == "leave":
        if choice == "sabab":
            prompt = tg.reason_prompt(event_id)
            send(token, chat_id, prompt["body"], prompt["buttons"])
            answer()
        elif choice == "yoq":
            if attendance.cancel_event(con, event_id, employee_id):
                send(token, chat_id, "↩️ Bekor qilindi. Siz ishxonada deb qoldingiz.")
                print(f"[bot] bekor: {tg.employee_name(con, employee_id)} #{event_id}")
            answer()
        else:
            attendance.confirm_event(con, event_id, employee_id, "ha")
            send(token, chat_id, "✅ Yozildi. Yaxshi dam oling!")
            answer()

    elif kind == "reason":
        label = tg.REASON_LABELS.get(choice, choice)
        if attendance.confirm_event(con, event_id, employee_id, "sabab", reason=choice):
            send(token, chat_id, f"✅ Yozildi · {label}")
            print(f"[bot] sabab: {tg.employee_name(con, employee_id)} — {label}")
        answer()

    elif kind == "close":
        if choice == "ok":
            attendance.confirm_event(con, event_id, employee_id, "ha")
            send(token, chat_id, "✅ Tasdiqlandi.")
        else:
            send(token, chat_id, "Adminga murojaat qiling — u vaqtni to'g'irlaydi.")
        answer()
    else:
        answer()


# ── Outgoing ──────────────────────────────────────────────────────────────────

def drain_outbox(token, con, cfg):
    """Deliver queued messages, if delivery is switched on.

    With it off the queue is deliberately left alone: the rows stay as the
    record of what would have been sent, and nothing is lost when it is
    switched on later.
    """
    if not cfg["telegram_send_notifications"]:
        return 0
    sent = 0
    for row in tg.pending(con):
        buttons = json.loads(row["buttons"]) if row["buttons"] else None
        if send(token, row["telegram_id"], row["body"], buttons) is None:
            # Most likely the employee blocked the bot. Marking it failed stops
            # the queue retrying forever behind one bad address.
            tg.mark_failed(con, row["id"], "sendMessage failed")
            continue
        tg.mark_sent(con, row["id"])
        sent += 1
    return sent


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    cfg = cfgmod.load()
    if not cfg["telegram_enabled"]:
        print("Telegram o'chirilgan. Yoqish uchun data/config.json da:")
        print('  "telegram_enabled": true')
        print("Xabar yuborish esa alohida kalit:")
        print('  "telegram_send_notifications": true')
        return

    token = read_token()
    if not token:
        print("ERROR: .env ichida TELEGRAM_BOT_TOKEN yo'q")
        return

    init_db()
    me = call(token, "getMe")
    if not me:
        print("ERROR: Telegram javob bermadi — token yoki internetni tekshiring")
        return
    print(f"Bot ulandi: @{me.get('username')}")
    print(f"Xabar yuborish: {'YOQILGAN' if cfg['telegram_send_notifications'] else 'o@chirilgan'}"
          .replace("@", "'"))

    offset = None
    last_outbox = 0.0
    while _running:
        cfg = cfgmod.load()        # picked up without a restart
        updates = call(token, "getUpdates", offset=offset,
                       timeout=POLL_TIMEOUT, allowed_updates=["message", "callback_query"])
        con = get_conn()
        try:
            for update in updates or []:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(token, con, cfg, update["message"])
                elif "callback_query" in update:
                    handle_callback(token, con, cfg, update["callback_query"])

            if time.time() - last_outbox >= OUTBOX_EVERY_SECONDS:
                last_outbox = time.time()
                count = drain_outbox(token, con, cfg)
                if count:
                    print(f"[bot] {count} ta xabar yuborildi")
        except Exception as e:
            print("[bot] xatolik, davom etamiz:", e)
        finally:
            con.close()

    print("Bot to'xtatildi")


if __name__ == "__main__":
    main()
