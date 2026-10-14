"""Envío de alertas privadas por Telegram, correo y/o WhatsApp."""
import logging
import smtplib
import time
from email.message import EmailMessage

from .creds import AUTH_MSG, mail_hosts
from .httpc import post_json

log = logging.getLogger("alertas.notifier")


def tg_call(token, method, payload, timeout=20):
    return post_json(f"https://api.telegram.org/bot{token}/{method}", payload, timeout=timeout)


def format_alert(a, tz_offset=-6):
    when = time.strftime("%d/%m %H:%M", time.gmtime(a["created_at"] + tz_offset * 3600)) if a.get("created_at") else ""
    text = (a.get("text") or "").strip()
    if len(text) > 500:
        text = text[:500].rstrip() + "…"
    lines = ["🔔 Alerta de oportunidad", f"Fuente: {a.get('source') or '—'}"]
    if a.get("author"):
        lines.append(f"Autor: {a['author']}")
    if when:
        lines.append(f"Hora: {when}")
    lines += ["", f"“{text}”", ""]
    if a.get("matched"):
        lines.append(f"Coincidencias: {a['matched']}")
    if a.get("url"):
        lines.append(f"Publicación: {a['url']}")
    lines.append("Responde pronto: las primeras 24 h son las que más convierten.")
    return "\n".join(lines)


class Notifier:
    def __init__(self, cfg, db, cred=None):
        self.cfg, self.db = cfg, db
        self.cred = cred or (lambda k: getattr(cfg, k, ""))

    def channel_status(self):
        cr, d = self.cred, self.db
        return {
            "telegram": {"configurado": bool(cr("telegram_token") and cr("telegram_chat_id")),
                         "activo": d.get_setting("notify_telegram") == "1"},
            "email": {"configurado": bool(cr("imap_user") and cr("imap_password")),
                      "activo": d.get_setting("notify_email") == "1"},
            "whatsapp": {"configurado": bool(cr("wa_token") and cr("wa_phone_id") and cr("wa_to")),
                         "activo": d.get_setting("notify_whatsapp") == "1"},
        }

    def _telegram(self, text, subject=None):
        st, data = tg_call(self.cred("telegram_token"), "sendMessage",
                           {"chat_id": self.cred("telegram_chat_id"), "text": text})
        if st == 200 and isinstance(data, dict) and data.get("ok"):
            return True, "ok"
        err = data.get("description") if isinstance(data, dict) else data
        return False, f"{st} {err}"

    def _email(self, text, subject=None):
        cr = self.cred
        _, smtp = mail_hosts(cr, self.cfg)
        user, pw = cr("imap_user"), cr("imap_password")
        to = [x.strip() for x in (cr("email_to") or user).replace(";", ",").split(",") if "@" in x]
        if not (smtp and user and pw and to):
            return False, "correo sin configurar"
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject or "Alerta de oportunidad"
        msg.set_content(text)
        try:
            with smtplib.SMTP_SSL(smtp, 465, timeout=20) as s:
                s.login(user, pw)
                s.send_message(msg)
            return True, "ok"
        except smtplib.SMTPAuthenticationError:
            return False, AUTH_MSG
        except Exception as e:
            return False, str(e)[:150]

    def _whatsapp(self, text, subject=None):
        cr = self.cred
        url = f"https://graph.facebook.com/v20.0/{cr('wa_phone_id')}/messages"
        if cr("wa_template"):
            # Las plantillas aceptan variables sin saltos de línea.
            flat = " | ".join(p.strip() for p in text.splitlines() if p.strip())[:1000]
            payload = {"messaging_product": "whatsapp", "to": cr("wa_to"), "type": "template",
                       "template": {"name": cr("wa_template"), "language": {"code": self.cfg.wa_template_lang},
                                    "components": [{"type": "body",
                                                    "parameters": [{"type": "text", "text": flat}]}]}}
        else:
            payload = {"messaging_product": "whatsapp", "to": cr("wa_to"), "type": "text",
                       "text": {"preview_url": True, "body": text}}
        st, data = post_json(url, payload, headers={"Authorization": f"Bearer {cr('wa_token')}"})
        if st in (200, 201):
            return True, "ok"
        err = data.get("error", {}).get("message") if isinstance(data, dict) else data
        return False, f"{st} {err}"

    def send_channel(self, name, text, subject=None):
        """Envía por un canal concreto (sirve para probar), sin importar si está activo."""
        if self.cfg.dry_run:
            log.info("[DRY_RUN] %s:\n%s", name, text)
            return True, "dry-run"
        return {"telegram": self._telegram, "email": self._email, "whatsapp": self._whatsapp}[name](text, subject)

    def send_text(self, text, subject=None):
        """Devuelve (algún_canal_ok, nota)."""
        st = self.channel_status()
        if self.cfg.dry_run:
            log.info("[DRY_RUN] Notificación:\n%s", text)
            return True, "dry-run"
        results = []
        for name in ("telegram", "email", "whatsapp"):
            if st[name]["configurado"] and st[name]["activo"]:
                ok, n = self.send_channel(name, text, subject)
                results.append((name, ok, n))
        if not results:
            return False, "Sin canal de notificación configurado y activo"
        for name, ok, n in results:
            if not ok:
                log.warning("Fallo al notificar por %s: %s", name, n)
        return any(r[1] for r in results), "; ".join(f"{n}: {'ok' if ok else m}" for n, ok, m in results)

    def send_alert(self, alert):
        text = format_alert(alert, self.cfg.tz_offset)
        subject = "Alerta: " + (alert.get("text") or "oportunidad").strip().replace("\n", " ")[:60]
        return self.send_text(text, subject)
