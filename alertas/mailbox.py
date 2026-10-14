"""Alertas de Facebook SIN scraping: lee los correos de notificación que Facebook
le envía al propio cliente (grupos donde ya es miembro y tiene notificaciones activas).

Solo lectura (BODY.PEEK): no marca ni borra correos. Requiere IMAP con contraseña de aplicación.
"""
import email
import imaplib
import logging
import re
import time
from email.header import decode_header, make_header

from .creds import mail_hosts
from .engine_util import clean_html

log = logging.getLogger("alertas.mail")
FB_LINK = re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/[^\s\"'<>)]+")


def _h(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def parse_fb_email(raw: bytes):
    msg = email.message_from_bytes(raw)
    subject = _h(msg.get("Subject"))
    text, html_part = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        body = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/plain":
            text += body
        else:
            html_part += body
    raw_body = text or html_part
    link = ""
    for m in FB_LINK.finditer(html_part + " " + text):
        u = m.group(0).replace("&amp;", "&").rstrip(".,")
        if "/groups/" in u or "/posts/" in u or "permalink" in u:
            link = u
            break
    body = clean_html(raw_body)
    body = re.sub(r"https?://\S+", "", body).strip()
    full = (subject + ". " + body).strip(". ").strip()
    return {
        "id": msg.get("Message-ID") or "",
        "text": full[:1500],
        "url": link,
        "author": _h(msg.get("From")).split("<")[0].strip(' "'),
        "subject": subject,
    }


def poll_once(app, first_run):
    cfg = app.cfg
    from .engine import process_item
    host, _ = mail_hosts(app.cred, cfg)
    imap = imaplib.IMAP4_SSL(host, timeout=30)
    try:
        imap.login(app.cred("imap_user"), app.cred("imap_password"))
        imap.select(cfg.imap_folder, readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - 2 * 86400))
        typ, data = imap.search(None, "FROM", f'"{cfg.imap_from}"', "SINCE", since)
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for num in ids[-100:]:
            typ, msgd = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not msgd or not isinstance(msgd[0], tuple):
                continue
            item = parse_fb_email(msgd[0][1])
            item["source"] = "Facebook (correo)"
            if first_run:  # primera vez: solo memoriza lo que ya estaba en el buzón
                app.db.mark_seen(f"{item['source']}|{item['id']}")
            else:
                process_item(app, item)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def mail_loop(app):
    while not app.stop.is_set():
        user, pw = app.cred("imap_user"), app.cred("imap_password")
        host, _ = mail_hosts(app.cred, app.cfg)
        if not (user and pw and host):
            app.mail_wake.wait(15)
            app.mail_wake.clear()
            continue
        first = app.db.get_setting("imap_baseline", "") != user  # buzón nuevo: solo memoriza lo existente
        try:
            poll_once(app, first)
            if first:
                app.db.set_setting("imap_baseline", user)
        except Exception as e:
            log.warning("Error leyendo el correo: %s", e)
        app.mail_wake.wait(app.cfg.rss_minutes * 60)
        app.mail_wake.clear()
