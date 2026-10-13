"""Motor: procesa publicaciones, dedupe, límite por hora, y los hilos de lectura."""
import hashlib
import logging
import threading
import time

from . import matcher
from .db import DB
from .notifier import Notifier, tg_call
from .sources import fetch_feed

log = logging.getLogger("alertas.engine")


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = DB(cfg.db_path)
        self.db.init()
        self.notifier = Notifier(cfg, self.db)
        self.stop = threading.Event()
        self.threads = []

    def rules(self):
        d = self.db
        return matcher.build_rules(d.active_terms("intencion"), d.active_terms("producto"),
                                   d.active_terms("excluir"))

    def evaluate(self, text):
        d = self.db
        return matcher.evaluate(text, self.rules(), d.get_setting("require_product") == "1",
                                int(d.get_setting("min_score") or 3))

    def start(self):
        if self.cfg.telegram_token:
            self._spawn(telegram_loop, "telegram")
        self._spawn(rss_loop, "rss")

    def _spawn(self, fn, name):
        t = threading.Thread(target=self._guard, args=(fn,), name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def _guard(self, fn):
        while not self.stop.is_set():
            try:
                fn(self)
                return
            except Exception:
                log.exception("Hilo %s falló; reintentando en 30 s", fn.__name__)
                self.stop.wait(30)


def process_item(app, item):
    """item: id, source, text, url, author. Devuelve dict con el resultado."""
    d = app.db
    text = (item.get("text") or "").strip()
    if not text:
        return {"matched": False, "reason": "Sin texto"}
    source = item.get("source") or "Desconocida"
    uid = str(item.get("id") or hashlib.sha1((text + (item.get("url") or "")).encode()).hexdigest())
    if not d.mark_seen(f"{source}|{uid}"):
        return {"matched": False, "reason": "Duplicado"}
    if not d.mark_seen("txt|" + hashlib.sha1(matcher.normalize(text)[:300].encode()).hexdigest()):
        return {"matched": False, "reason": "Mismo texto ya visto (publicación repetida)"}

    m = app.evaluate(text)
    if not m.ok:
        return {"matched": False, "reason": m.reason}

    found = ", ".join(m.intents[:3] + m.products[:3])
    aid = d.add_alert(source, item.get("author") or "", text[:1500], item.get("url") or "", found, m.score)
    limit = int(d.get_setting("max_per_hour") or 20)
    if d.count_notified_since(int(time.time()) - 3600) >= limit:
        d.set_alert_notified(aid, False, "Límite por hora alcanzado (queda guardada en el panel)")
        return {"matched": True, "alert_id": aid, "notified": False, "reason": "Límite por hora"}
    alert = {"created_at": int(time.time()), "source": source, "author": item.get("author"),
             "text": text, "url": item.get("url"), "matched": found}
    ok, note = app.notifier.send_alert(alert)
    d.set_alert_notified(aid, ok, note)
    return {"matched": True, "alert_id": aid, "notified": ok, "reason": m.reason}


# ------------------------------------------------------------------ RSS
def check_source(app, src):
    d = app.db
    try:
        items = fetch_feed(src["url"])
    except Exception as e:
        d.source_checked(src["id"], str(e)[:200])
        log.warning("Fuente %s con error: %s", src["name"], e)
        return
    first = src["last_checked"] is None
    for it in items:
        it["source"] = f"RSS: {src['name']}"
        if first:  # primera lectura: solo memoriza lo existente para no inundar de alertas viejas
            d.mark_seen(f"{it['source']}|{it['id']}")
        else:
            process_item(app, it)
    d.source_checked(src["id"], None)


def rss_loop(app):
    last_prune = 0
    while not app.stop.is_set():
        now = time.time()
        for src in app.db.list_sources(only_enabled=True):
            if app.stop.is_set():
                return
            due = src["last_checked"] is None or now - src["last_checked"] >= app.cfg.rss_minutes * 60
            if due:
                check_source(app, src)
        if now - last_prune > 86400:
            app.db.prune_seen()
            last_prune = now
        app.stop.wait(30)


# ------------------------------------------------------------------ Telegram
def handle_tg_update(app, upd):
    cfg, d = app.cfg, app.db
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    chat = msg.get("chat", {})
    text = msg.get("text") or msg.get("caption") or ""
    cid = str(chat.get("id"))
    if chat.get("type") == "private":
        if text.strip().lower().split("@")[0] in ("/start", "/id"):
            tg_call(cfg.telegram_token, "sendMessage", {
                "chat_id": cid,
                "text": f"Hola. Tu chat_id es: {cid}\nCópialo en TELEGRAM_ALERT_CHAT_ID para recibir aquí las alertas."})
        return
    if cid == str(cfg.telegram_chat_id):
        return
    sender = msg.get("from") or {}
    if sender.get("is_bot"):
        return
    title = chat.get("title") or chat.get("username") or cid
    d.touch_tg_chat(cid, title, chat.get("type"))
    mid = msg.get("message_id")
    if chat.get("username"):
        url = f"https://t.me/{chat['username']}/{mid}"
    elif cid.startswith("-100"):
        url = f"https://t.me/c/{cid[4:]}/{mid}"
    else:
        url = ""
    author = ("@" + sender["username"]) if sender.get("username") else sender.get("first_name", "")
    process_item(app, {"id": f"{cid}:{mid}", "source": f"Telegram: {title}", "text": text,
                       "url": url, "author": author})


def telegram_loop(app):
    cfg, d = app.cfg, app.db
    tg_call(cfg.telegram_token, "deleteWebhook", {})  # getUpdates no funciona con webhook activo
    offset = int(d.get_setting("tg_offset", "0") or 0)
    backoff = 5
    while not app.stop.is_set():
        st, data = tg_call(cfg.telegram_token, "getUpdates",
                           {"offset": offset, "timeout": 25, "allowed_updates": ["message", "channel_post"]},
                           timeout=40)
        if st != 200 or not isinstance(data, dict) or not data.get("ok"):
            log.warning("Telegram getUpdates falló (%s): %s", st, str(data)[:200])
            app.stop.wait(backoff)
            backoff = min(backoff * 2, 120)
            continue
        backoff = 5
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            try:
                handle_tg_update(app, upd)
            except Exception:
                log.exception("Error procesando update de Telegram")
        d.set_setting("tg_offset", offset)
