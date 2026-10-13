"""Envío de alertas privadas por Telegram y/o WhatsApp."""
import logging
import time

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
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db

    def channel_status(self):
        c, d = self.cfg, self.db
        return {
            "telegram": {"configurado": bool(c.telegram_token and c.telegram_chat_id),
                         "activo": d.get_setting("notify_telegram") == "1"},
            "whatsapp": {"configurado": bool(c.wa_token and c.wa_phone_id and c.wa_to),
                         "activo": d.get_setting("notify_whatsapp") == "1"},
        }

    def _telegram(self, text):
        st, data = tg_call(self.cfg.telegram_token, "sendMessage",
                           {"chat_id": self.cfg.telegram_chat_id, "text": text})
        if st == 200 and isinstance(data, dict) and data.get("ok"):
            return True, "ok"
        err = data.get("description") if isinstance(data, dict) else data
        return False, f"{st} {err}"

    def _whatsapp(self, text):
        c = self.cfg
        url = f"https://graph.facebook.com/v20.0/{c.wa_phone_id}/messages"
        if c.wa_template:
            # Las plantillas aceptan variables sin saltos de línea.
            flat = " | ".join(p.strip() for p in text.splitlines() if p.strip())[:1000]
            payload = {"messaging_product": "whatsapp", "to": c.wa_to, "type": "template",
                       "template": {"name": c.wa_template, "language": {"code": c.wa_template_lang},
                                    "components": [{"type": "body",
                                                    "parameters": [{"type": "text", "text": flat}]}]}}
        else:
            payload = {"messaging_product": "whatsapp", "to": c.wa_to, "type": "text",
                       "text": {"preview_url": True, "body": text}}
        st, data = post_json(url, payload, headers={"Authorization": f"Bearer {c.wa_token}"})
        if st in (200, 201):
            return True, "ok"
        err = data.get("error", {}).get("message") if isinstance(data, dict) else data
        return False, f"{st} {err}"

    def send_text(self, text):
        """Devuelve (algún_canal_ok, nota)."""
        st = self.channel_status()
        results = []
        if self.cfg.dry_run:
            log.info("[DRY_RUN] Notificación:\n%s", text)
            return True, "dry-run"
        if st["telegram"]["configurado"] and st["telegram"]["activo"]:
            ok, n = self._telegram(text)
            results.append(("telegram", ok, n))
        if st["whatsapp"]["configurado"] and st["whatsapp"]["activo"]:
            ok, n = self._whatsapp(text)
            results.append(("whatsapp", ok, n))
        if not results:
            return False, "Sin canal de notificación configurado y activo"
        for name, ok, n in results:
            if not ok:
                log.warning("Fallo al notificar por %s: %s", name, n)
        return any(r[1] for r in results), "; ".join(f"{n}: {'ok' if ok else m}" for n, ok, m in results)

    def send_alert(self, alert):
        return self.send_text(format_alert(alert, self.cfg.tz_offset))
