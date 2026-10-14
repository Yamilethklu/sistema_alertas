import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import seeds

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords(
  id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT NOT NULL, kind TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, UNIQUE(term, kind));
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1, last_checked INTEGER, last_error TEXT);
CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, source TEXT, author TEXT,
  text TEXT, url TEXT, matched TEXT, score INTEGER, notified INTEGER NOT NULL DEFAULT 0, note TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tg_chats(
  chat_id TEXT PRIMARY KEY, title TEXT, kind TEXT, last_seen INTEGER,
  messages INTEGER NOT NULL DEFAULT 0, muted INTEGER NOT NULL DEFAULT 0);
"""

DEFAULT_SETTINGS = {
    "require_product": "1",
    "min_score": "3",
    "max_per_hour": "20",
    "notify_telegram": "1",
    "notify_whatsapp": "1",
    "notify_email": "0",
}

KINDS = ("producto", "intencion", "excluir")


class DB:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    @contextmanager
    def _c(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=30)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def init(self):
        with self._c() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            cols = [r[1] for r in c.execute("PRAGMA table_info(tg_chats)").fetchall()]
            if "muted" not in cols:  # migración para bases creadas antes de esta versión
                c.execute("ALTER TABLE tg_chats ADD COLUMN muted INTEGER NOT NULL DEFAULT 0")
            n = c.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
            if n == 0:
                now = int(time.time())
                for kind, items in (("intencion", seeds.INTENCION), ("producto", seeds.PRODUCTO),
                                    ("excluir", seeds.EXCLUIR)):
                    for t in items:
                        c.execute("INSERT OR IGNORE INTO keywords(term,kind,created_at) VALUES(?,?,?)",
                                  (t, kind, now))

    # ---- palabras clave
    def list_keywords(self, kind=None):
        with self._c() as c:
            if kind:
                rows = c.execute("SELECT * FROM keywords WHERE kind=? ORDER BY term", (kind,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM keywords ORDER BY kind, term").fetchall()
            return [dict(r) for r in rows]

    def active_terms(self, kind):
        with self._c() as c:
            rows = c.execute("SELECT term FROM keywords WHERE kind=? AND enabled=1", (kind,)).fetchall()
            return [r["term"] for r in rows]

    def add_keywords(self, raw, kind):
        if kind not in KINDS:
            return 0
        terms = [t.strip().lower() for t in raw.replace(",", "\n").splitlines()]
        added = 0
        with self._c() as c:
            for t in terms:
                if t and len(t) <= 80:
                    cur = c.execute("INSERT OR IGNORE INTO keywords(term,kind,created_at) VALUES(?,?,?)",
                                    (t, kind, int(time.time())))
                    added += cur.rowcount
        return added

    def get_keyword(self, kid):
        with self._c() as c:
            r = c.execute("SELECT * FROM keywords WHERE id=?", (kid,)).fetchone()
            return dict(r) if r else None

    def rename_keyword(self, kid, term):
        term = (term or "").strip().lower()
        if not term or len(term) > 80:
            return False
        try:
            with self._c() as c:
                return c.execute("UPDATE keywords SET term=? WHERE id=?", (term, kid)).rowcount == 1
        except sqlite3.IntegrityError:  # ya existe esa palabra en la misma lista
            return False

    def toggle_keyword(self, kid):
        with self._c() as c:
            c.execute("UPDATE keywords SET enabled = 1 - enabled WHERE id=?", (kid,))

    def delete_keyword(self, kid):
        with self._c() as c:
            c.execute("DELETE FROM keywords WHERE id=?", (kid,))

    # ---- fuentes RSS
    def list_sources(self, only_enabled=False):
        with self._c() as c:
            q = "SELECT * FROM sources" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY name"
            return [dict(r) for r in c.execute(q).fetchall()]

    def add_source(self, name, url):
        with self._c() as c:
            cur = c.execute("INSERT OR IGNORE INTO sources(name,url) VALUES(?,?)", (name.strip(), url.strip()))
            return cur.rowcount

    def get_source(self, sid):
        with self._c() as c:
            r = c.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
            return dict(r) if r else None

    def update_source(self, sid, name, url):
        old = self.get_source(sid)
        if not old:
            return False
        try:
            with self._c() as c:
                # si cambia la URL se vuelve a memorizar lo existente, para no inundar de alertas viejas
                last = old["last_checked"] if url.strip() == old["url"] else None
                return c.execute("UPDATE sources SET name=?, url=?, last_checked=?, last_error=NULL WHERE id=?",
                                 (name.strip(), url.strip(), last, sid)).rowcount == 1
        except sqlite3.IntegrityError:
            return False

    def toggle_source(self, sid):
        with self._c() as c:
            c.execute("UPDATE sources SET enabled = 1 - enabled WHERE id=?", (sid,))

    def delete_source(self, sid):
        with self._c() as c:
            c.execute("DELETE FROM sources WHERE id=?", (sid,))

    def source_checked(self, sid, error=None):
        with self._c() as c:
            c.execute("UPDATE sources SET last_checked=?, last_error=? WHERE id=?",
                      (int(time.time()), error, sid))

    # ---- deduplicación
    def mark_seen(self, key):
        with self._c() as c:
            cur = c.execute("INSERT OR IGNORE INTO seen(key,ts) VALUES(?,?)", (key, int(time.time())))
            return cur.rowcount == 1

    def prune_seen(self, days=30):
        with self._c() as c:
            c.execute("DELETE FROM seen WHERE ts < ?", (int(time.time()) - days * 86400,))

    # ---- alertas
    def add_alert(self, source, author, text, url, matched, score):
        with self._c() as c:
            cur = c.execute(
                "INSERT INTO alerts(created_at,source,author,text,url,matched,score) VALUES(?,?,?,?,?,?,?)",
                (int(time.time()), source, author, text, url, matched, score))
            return cur.lastrowid

    def set_alert_notified(self, aid, ok, note):
        with self._c() as c:
            c.execute("UPDATE alerts SET notified=?, note=? WHERE id=?", (1 if ok else 0, note, aid))

    def recent_alerts(self, limit=50):
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def count_notified_since(self, ts):
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM alerts WHERE notified=1 AND created_at>=?", (ts,)).fetchone()[0]

    def stats(self):
        now = int(time.time())
        with self._c() as c:
            q = lambda sql, *a: c.execute(sql, a).fetchone()[0]
            return {
                "alertas_24h": q("SELECT COUNT(*) FROM alerts WHERE created_at>=?", now - 86400),
                "alertas_7d": q("SELECT COUNT(*) FROM alerts WHERE created_at>=?", now - 7 * 86400),
                "enviadas_7d": q("SELECT COUNT(*) FROM alerts WHERE notified=1 AND created_at>=?", now - 7 * 86400),
                "palabras": q("SELECT COUNT(*) FROM keywords WHERE enabled=1"),
                "fuentes": q("SELECT COUNT(*) FROM sources WHERE enabled=1"),
                "grupos_tg": q("SELECT COUNT(*) FROM tg_chats WHERE kind != 'private'"),
            }

    # ---- ajustes
    def get_setting(self, key, default=None):
        with self._c() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r:
            return r["value"]
        return DEFAULT_SETTINGS.get(key, default)

    def set_setting(self, key, value):
        with self._c() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    # ---- grupos de Telegram vistos
    def touch_tg_chat(self, chat_id, title, kind):
        with self._c() as c:
            c.execute(
                "INSERT INTO tg_chats(chat_id,title,kind,last_seen,messages) VALUES(?,?,?,?,1) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, last_seen=excluded.last_seen, "
                "messages=messages+1", (chat_id, title, kind, int(time.time())))

    def toggle_tg_chat(self, chat_id):
        with self._c() as c:
            c.execute("UPDATE tg_chats SET muted = 1 - muted WHERE chat_id=?", (chat_id,))

    def is_tg_muted(self, chat_id):
        with self._c() as c:
            r = c.execute("SELECT muted FROM tg_chats WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(r and r["muted"])

    def list_tg_chats(self):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tg_chats ORDER BY last_seen DESC").fetchall()]
