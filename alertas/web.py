"""Panel de control (HTTP + HTML, sin JavaScript, solo librería estándar)."""
import hashlib
import hmac
import html
import json
import logging
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import engine
from .creds import check_imap
from .notifier import tg_call
from .db import KINDS

log = logging.getLogger("alertas.web")


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


CSS = """
:root{--paper:#f2f4f1;--ink:#14201e;--panel:#fff;--line:#d3dad5;--accent:#0e6b5c;--signal:#d99a00;--bad:#b3261e;--mute:#5d6b67}
*{box-sizing:border-box}
body{margin:0;display:flex;min-height:100vh;background:var(--paper);color:var(--ink);font:16px/1.5 "Segoe UI","Helvetica Neue",Arial,sans-serif}
aside{width:220px;flex:none;background:var(--ink);color:#e8efec;padding:24px 16px;display:flex;flex-direction:column;gap:20px;position:sticky;top:0;height:100vh}
.brand{font-weight:700;font-size:20px;line-height:1.2;padding:0 10px}
nav{display:flex;flex-direction:column;gap:2px}
nav a,.out{color:#c4d1cc;text-decoration:none;padding:8px 10px;border-radius:6px}
nav a:hover,.out:hover{background:#22332f;color:#fff}
nav a.on{background:var(--accent);color:#fff}
.out{margin-top:auto;font-size:14px}
main{flex:1;min-width:0;padding:32px clamp(16px,4vw,48px);max-width:1000px}
h1{font-size:26px;margin:0 0 18px;font-weight:650}
h2{font-size:18px;margin:30px 0 10px;font-weight:650}
p.hint{color:var(--mute);margin:4px 0 12px;max-width:68ch}
.flash{background:#e3f1ec;border-left:4px solid var(--accent);padding:10px 14px;margin:0 0 18px}
.flash.err{background:#fbe9e7;border-color:var(--bad)}
.stats{display:flex;flex-wrap:wrap;border:1px solid var(--line);background:var(--panel);border-radius:8px}
.stats div{flex:1 1 140px;padding:14px 18px;border-right:1px solid var(--line)}
.stats div:last-child{border-right:0}
.stats b{display:block;font-size:28px;line-height:1.1}
.stats span{color:var(--mute);font-size:14px}
.status{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px 24px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;background:var(--bad)}
.dot.ok{background:var(--accent)}.dot.off{background:#9aa6a2}
article.alert{background:var(--panel);border:1px solid var(--line);border-left:5px solid #9aa6a2;border-radius:6px;padding:12px 16px;margin:0 0 10px}
article.alert.sent{border-left-color:var(--signal)}
.meta{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--mute);font-size:14px}
.meta strong{color:var(--ink)}
.q{margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.tag{background:#e8eeea;border-radius:4px;padding:1px 8px;font-size:13px}
.act{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;font-size:14px}
a{color:var(--accent)}
form.inline{display:inline;margin:0}
input[type=text],input[type=url],input[type=email],input[type=password],input[type=number],textarea{width:100%;padding:9px 10px;border:1px solid #b9c3be;border-radius:6px;font:inherit;background:#fff}
textarea{min-height:90px}
label{display:block;margin:12px 0 4px;font-weight:600}
label.chk{font-weight:400;display:flex;gap:8px;align-items:center}
button,.btn{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer}
button.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent);padding:3px 10px;font-size:14px}
button.del{background:transparent;color:var(--bad);border:1px solid var(--bad);padding:3px 10px;font-size:14px}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--signal);outline-offset:2px}
ul.kw{list-style:none;padding:0;margin:10px 0;display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:6px}
ul.kw li{display:flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
ul.kw li.off span{text-decoration:line-through;color:var(--mute)}
ul.kw li span{flex:1;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line)}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:15px;vertical-align:top}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 20px;max-width:560px}
.ok{color:var(--accent);font-weight:600}.bad{color:var(--bad);font-weight:600}
details{margin:10px 0}summary{cursor:pointer;color:var(--accent);font-weight:600}ol.steps{padding-left:22px}
.card.wide{max-width:none;margin:0 0 18px}
code{background:#e8eeea;padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
.login{margin:12vh auto;width:min(380px,92vw)}
@media(max-width:760px){body{flex-direction:column}aside{width:auto;height:auto;position:static;flex-direction:row;flex-wrap:wrap;align-items:center;padding:12px}.brand br{display:none}nav{flex-direction:row;flex-wrap:wrap}.out{margin:0 0 0 auto}}
"""

NAV = [("/", "Resumen"), ("/conexiones", "Conexiones"), ("/alertas", "Alertas"), ("/palabras", "Palabras clave"),
       ("/fuentes", "Fuentes"), ("/probar", "Probar texto"), ("/ajustes", "Ajustes")]

KIND_LABELS = {
    "producto": ("Productos, marcas y categorías",
                 "Lo que vendes o promocionas. Una alerta necesita que aparezca alguno (si 'Exigir producto' está activo)."),
    "intencion": ("Frases de intención de compra",
                  "Cómo pregunta la gente cuando quiere comprar: «busco», «dónde compro», «recomiendan»…"),
    "excluir": ("Palabras que descartan la publicación",
                "Si aparecen, no se alerta (por ejemplo «vendo»: el autor es vendedor, no comprador)."),
}


def layout(title, body, active="", flash="", err=False):
    nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class="on"' if h == active else "", t) for h, t in NAV)
    fl = '<p class="flash%s">%s</p>' % (" err" if err else "", esc(flash)) if flash else ""
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s · Alertas de oportunidad</title><style>%s</style></head><body>'
            '<aside><div class="brand">Alertas de<br>oportunidad</div><nav>%s</nav>'
            '<a class="out" href="/logout">Cerrar sesión</a></aside>'
            '<main><h1>%s</h1>%s%s</main></body></html>') % (esc(title), CSS, nav, esc(title), fl, body)


EMAIL_FIELD = '<label for="e">Correo</label><input id="e" type="email" name="email" autocomplete="username" required autofocus>'


def login_page(msg="", ask_email=False):
    fl = '<p class="flash err">%s</p>' % esc(msg) if msg else ""
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Entrar · Alertas de oportunidad</title><style>%s body{display:block}</style></head><body>'
            '<div class="login card"><h1>Alertas de oportunidad</h1>%s'
            '<form method="post" action="/login">%s<label for="p">Contraseña del panel</label>'
            '<input id="p" type="password" name="password" autofocus required>'
            '<p><button type="submit">Entrar</button></p></form></div></body></html>') % (CSS, fl, EMAIL_FIELD if ask_email else "")


def fmt_time(ts, off):
    return time.strftime("%d/%m %H:%M", time.gmtime((ts or 0) + off * 3600))


def alert_html(a, off):
    tags = "".join('<span class="tag">%s</span>' % esc(t.strip()) for t in (a["matched"] or "").split(",") if t.strip())
    link = ""
    if (a["url"] or "").startswith(("http://", "https://")):
        link = '<a href="%s" target="_blank" rel="noopener noreferrer">Abrir publicación</a>' % esc(a["url"])
    state = "Enviada a tu teléfono" if a["notified"] else "No enviada: " + esc(a["note"] or "sin detalle")
    author = '<span>%s</span>' % esc(a["author"]) if a["author"] else ""
    return ('<article class="alert %s"><div class="meta"><strong>%s</strong><span>%s</span>%s</div>'
            '<p class="q">%s</p><div class="tags">%s</div><div class="act">%s<span>%s</span></div></article>') % (
        "sent" if a["notified"] else "", esc(a["source"]), fmt_time(a["created_at"], off), author,
        esc(a["text"]), tags, link, state)


def make_handler(app):
    cfg, db = app.cfg, app.db
    fails = {}

    class H(BaseHTTPRequestHandler):
        server_version = "Alertas/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # ---------- utilidades
        def _ip(self):
            if cfg.trust_proxy:
                xf = self.headers.get("X-Forwarded-For", "")
                if xf:
                    return xf.split(",")[0].strip()
            return self.client_address[0]

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to, extra=None):
            self.send_response(303)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()

        def _sign(self, exp):
            return hmac.new(cfg.secret_key.encode(), str(exp).encode(), hashlib.sha256).hexdigest()

        def _authed(self):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sess":
                    exp, _, sig = v.partition(".")
                    if exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, self._sign(exp)):
                        return True
            return False

        def _body(self, limit=1_000_000):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                return b""
            return self.rfile.read(n) if n else b""

        def _form(self):
            raw = parse_qs(self._body().decode("utf-8", "replace"), keep_blank_values=True)
            return {k: v[0] for k, v in raw.items()}

        def _flash(self, q):
            return parse_qs(q).get("m", [""])[0]

        # ---------- GET
        def do_GET(self):
            u = urlparse(self.path)
            path, flash = u.path, self._flash(u.query)
            if path == "/health":
                return self._send(200, "ok", "text/plain")
            if path == "/login":
                return self._send(200, login_page(ask_email=bool(cfg.panel_email)))
            if path == "/logout":
                return self._redirect("/login", [("Set-Cookie", "sess=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
            if not self._authed():
                return self._redirect("/login")
            pages = {"/": self.p_home, "/alertas": self.p_alerts, "/palabras": self.p_keywords,
                     "/conexiones": self.p_connections, "/fuentes": self.p_sources, "/probar": self.p_probe, "/ajustes": self.p_settings}
            fn = pages.get(path)
            if not fn:
                return self._send(404, layout("No encontrada", "<p>Esa página no existe.</p>"))
            return self._send(200, fn(flash))

        # ---------- POST
        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/ingest":
                return self.api_ingest()
            if path == "/login":
                return self.do_login()
            if not self._authed():
                return self._redirect("/login")
            routes = {
                "/palabras/agregar": self.a_kw_add, "/palabras/alternar": self.a_kw_toggle,
                "/palabras/borrar": self.a_kw_del, "/fuentes/agregar": self.a_src_add,
                "/conexiones/correo": self.a_conn_mail, "/conexiones/correo/prueba": self.a_conn_mail_test,
                "/conexiones/telegram": self.a_conn_tg, "/conexiones/telegram/usar": self.a_conn_tg_use,
                "/conexiones/whatsapp": self.a_conn_wa, "/conexiones/probar": self.a_conn_test,
                "/conexiones/quitar": self.a_conn_clear,
                "/fuentes/grupo": self.a_tg_toggle, "/fuentes/alternar": self.a_src_toggle, "/fuentes/borrar": self.a_src_del,
                "/ajustes": self.a_settings, "/prueba": self.a_test, "/probar": self.a_probe,
            }
            fn = routes.get(path)
            if not fn:
                return self._send(404, layout("No encontrada", "<p>Esa acción no existe.</p>"))
            return fn(self._form())

        def do_login(self):
            ip, now = self._ip(), time.time()
            fails[ip] = [t for t in fails.get(ip, []) if now - t < 600]
            if len(fails[ip]) >= 5:
                return self._send(429, login_page("Demasiados intentos. Espera 10 minutos.", bool(cfg.panel_email)))
            f = self._form()
            pw = f.get("password", "")
            email_ok = (not cfg.panel_email) or hmac.compare_digest(
                f.get("email", "").strip().lower().encode(), cfg.panel_email.lower().encode())
            if cfg.panel_password and email_ok and hmac.compare_digest(pw.encode(), cfg.panel_password.encode()):
                exp = int(now) + 7 * 86400
                ck = "sess=%d.%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (
                    exp, self._sign(exp), 7 * 86400, "; Secure" if cfg.cookie_secure else "")
                return self._redirect("/", [("Set-Cookie", ck)])
            fails[ip].append(now)
            return self._send(401, login_page("Correo o contraseña incorrectos." if cfg.panel_email else "Contraseña incorrecta.", bool(cfg.panel_email)))

        # ---------- páginas
        def p_home(self, flash):
            s = db.stats()
            ch = app.notifier.channel_status()

            def li(name, c):
                cls = "ok" if c["configurado"] and c["activo"] else ("off" if c["configurado"] else "")
                txt = ("Activo" if c["activo"] else "Apagado en Ajustes") if c["configurado"] else "Falta configurar"
                return '<li><span class="dot %s"></span>%s: %s</li>' % (cls, name, txt)
            stats = "".join('<div><b>%s</b><span>%s</span></div>' % (v, k) for k, v in [
                (s["alertas_24h"], "alertas en 24 h"), (s["alertas_7d"], "alertas en 7 días"),
                (s["enviadas_7d"], "enviadas a tu teléfono (7 d)"), (s["palabras"], "palabras activas"),
                (s["fuentes"] + s["grupos_tg"], "fuentes y grupos vigilados")])
            recent = "".join(alert_html(a, cfg.tz_offset) for a in db.recent_alerts(8)) or \
                '<p class="hint">Aún no hay alertas. Cuando alguien pregunte por un producto de tu lista en una fuente vigilada, aparecerá aquí y en tu Telegram/WhatsApp.</p>'
            body = ('<div class="stats">%s</div><h2>Canales de aviso</h2><ul class="status">%s%s</ul>'
                    '<p class="hint"><a href="/conexiones">Configurar conexiones</a> (correo, Telegram, WhatsApp)</p>'
                    '<form method="post" action="/prueba"><p><button>Enviar mensaje de prueba</button></p></form>'
                    '<h2>Últimas alertas</h2>%s<p><a href="/alertas">Ver todas</a></p>') % (
                stats, li("Telegram", ch["telegram"]) + li("Correo", ch["email"]), li("WhatsApp", ch["whatsapp"]), recent)
            return layout("Resumen", body, "/", flash)

        def p_alerts(self, flash):
            items = "".join(alert_html(a, cfg.tz_offset) for a in db.recent_alerts(100)) or \
                '<p class="hint">Todavía no hay alertas registradas.</p>'
            return layout("Alertas", '<p class="hint">Las últimas 100 alertas. Entra a la publicación y responde tú mismo con tu enlace de afiliado.</p>' + items, "/alertas", flash)

        def p_keywords(self, flash):
            out = ['<p class="hint">Aquí controlas qué detecta el sistema. Los cambios aplican de inmediato.</p>']
            for kind in KINDS:
                title, hint = KIND_LABELS[kind]
                rows = ""
                for k in db.list_keywords(kind):
                    rows += ('<li class="%s"><span>%s</span>'
                             '<form class="inline" method="post" action="/palabras/alternar"><input type="hidden" name="id" value="%d"><button class="ghost">%s</button></form>'
                             '<form class="inline" method="post" action="/palabras/borrar"><input type="hidden" name="id" value="%d"><button class="del">Borrar</button></form></li>') % (
                        "" if k["enabled"] else "off", esc(k["term"]), k["id"],
                        "Pausar" if k["enabled"] else "Activar", k["id"])
                out.append('<h2>%s</h2><p class="hint">%s</p>'
                           '<form method="post" action="/palabras/agregar"><input type="hidden" name="kind" value="%s">'
                           '<label for="t-%s">Agregar (una por línea o separadas por coma)</label>'
                           '<textarea id="t-%s" name="terms"></textarea><p><button>Agregar</button></p></form>'
                           '<ul class="kw">%s</ul>' % (esc(title), esc(hint), kind, kind, kind, rows))
            return layout("Palabras clave", "".join(out), "/palabras", flash)

        def p_sources(self, flash):
            rows = ""
            for s in db.list_sources():
                chk = fmt_time(s["last_checked"], cfg.tz_offset) if s["last_checked"] else "pendiente"
                err = '<br><span style="color:var(--bad)">%s</span>' % esc(s["last_error"]) if s["last_error"] else ""
                rows += ('<tr><td>%s<br><code>%s</code></td><td>%s%s</td><td>'
                         '<form class="inline" method="post" action="/fuentes/alternar"><input type="hidden" name="id" value="%d"><button class="ghost">%s</button></form> '
                         '<form class="inline" method="post" action="/fuentes/borrar"><input type="hidden" name="id" value="%d"><button class="del">Borrar</button></form></td></tr>') % (
                    esc(s["name"]), esc(s["url"]), chk, err, s["id"], "Pausar" if s["enabled"] else "Activar", s["id"])
            rss = ('<table><tr><th>Fuente</th><th>Última lectura</th><th></th></tr>%s</table>' % rows) if rows else \
                '<p class="hint">No hay feeds RSS todavía.</p>'
            chats = "".join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>'
                '<form class="inline" method="post" action="/fuentes/grupo"><input type="hidden" name="chat_id" value="%s"><button class="%s">%s</button></form></td></tr>' % (
                esc(c["title"]), esc(c["kind"]), c["messages"], fmt_time(c["last_seen"], cfg.tz_offset),
                esc(c["chat_id"]), "ghost" if c["muted"] else "del", "Reactivar" if c["muted"] else "Silenciar")
                for c in db.list_tg_chats())
            tg = ('<table><tr><th>Grupo</th><th>Tipo</th><th>Mensajes leídos</th><th>Último</th><th>Vigilancia</th></tr>%s</table>' % chats) if chats else \
                '<p class="hint">Ningún grupo de Telegram ha enviado mensajes todavía. Agrega el bot a un grupo (ver instructivo).</p>'
            body = ('<h2>Grupos de Telegram vigilados</h2>'
                    '<p class="hint">Aparecen solos cuando agregas el bot a un grupo y alguien escribe en él. Con Silenciar dejas de recibir alertas de ese grupo sin sacar el bot (para quitarlo del todo, sácalo del grupo en Telegram).</p>%s'
                    '<h2>Feeds RSS / Atom</h2><p class="hint">Ejemplos: búsquedas de Reddit en formato RSS, Google Alerts (entregar como feed), foros públicos.</p>%s'
                    '<h2>Agregar feed</h2><form method="post" action="/fuentes/agregar">'
                    '<label for="n">Nombre</label><input id="n" type="text" name="name" required>'
                    '<label for="u">URL del feed</label><input id="u" type="url" name="url" required placeholder="https://…">'
                    '<p><button>Agregar feed</button></p></form>'
                    '<h2>Facebook por correo</h2><p class="hint">Lee los correos de notificación que Facebook envía a la cuenta de correo del cliente (grupos donde ya es miembro). Estado: <strong>%s</strong>.</p>'
                    '<h2>Webhook para otras herramientas</h2><p class="hint">Make, n8n o Zapier pueden enviar publicaciones al sistema con '
                    '<code>POST /api/ingest</code> y el encabezado <code>X-Token</code>. Estado del token: <strong>%s</strong>.</p>') % (
                tg, rss, "conectado a " + esc(app.cred("imap_user")) if (app.cred("imap_user") and app.cred("imap_password")) else 'sin configurar: <a href="/conexiones">configurar ahora</a>', "configurado" if cfg.ingest_token else "no configurado (INGEST_TOKEN)")
            return layout("Fuentes", body, "/fuentes", flash)

        def p_connections(self, flash):
            cr = app.cred
            user, pwset = cr("imap_user"), bool(cr("imap_password"))
            ms = db.get_setting("mail_status", "")
            ms_html = ('<p class="%s">%s</p>' % ("ok" if ms.startswith("OK") else "bad", esc(ms.split(": ", 1)[-1]))) if ms else ""
            estado_mail = ('<p class="ok">Correo conectado: %s</p>' % esc(user)) if (user and pwset) else '<p class="bad">Falta conectar el correo.</p>'
            mail = (
                '<p class="hint">Facebook te manda un correo por cada publicación de los grupos que sigues. Conectando ese correo, el sistema lo lee y te avisa. También puede mandarte cada alerta por correo.</p>%s%s'
                '<details><summary>¿Cómo consigo la contraseña de aplicación? (2 minutos)</summary><ol class="steps">'
                '<li>Abre <a href="https://myaccount.google.com/security" target="_blank" rel="noopener noreferrer">tu cuenta de Google, sección Seguridad</a> y activa la Verificación en 2 pasos.</li>'
                '<li>Abre <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Contraseñas de aplicaciones</a>, escribe «Alertas» como nombre y presiona Crear.</li>'
                '<li>Google te muestra un código de 16 letras. Cópialo y pégalo aquí abajo, con o sin espacios.</li></ol>'
                '<p class="hint">No uses tu contraseña normal de Gmail: no funciona y no es segura. Si algún día quieres cortar el acceso, borra este código en la misma página de Google.</p></details>'
                '<form method="post" action="/conexiones/correo"><label for="g">Tu correo de Gmail</label>'
                '<input id="g" type="email" name="gmail" value="%s" required placeholder="tucorreo@gmail.com">'
                '<label for="ap">Contraseña de aplicación</label>'
                '<input id="ap" type="password" name="app_password" autocomplete="off" placeholder="%s">'
                '<label class="chk"><input type="checkbox" name="notify_email" value="1" %s> Mandarme también cada alerta por correo</label>'
                '<label for="to">Mandar las alertas a otro correo (opcional)</label>'
                '<input id="to" type="text" name="email_to" value="%s" placeholder="si lo dejas vacío, llegan a tu mismo correo">'
                '<p><button>Guardar y probar conexión</button></p></form>'
                '<form class="inline" method="post" action="/conexiones/correo/prueba"><button class="ghost">Enviarme un correo de prueba</button></form> '
                '<form class="inline" method="post" action="/conexiones/quitar"><input type="hidden" name="que" value="correo"><button class="del">Quitar correo</button></form>') % (
                estado_mail, ms_html, esc(user),
                "Guardada. Déjalo vacío para conservarla" if pwset else "16 letras",
                "checked" if (db.get_setting("notify_email") == "1" or not user) else "", esc(cr("email_to")))

            tok = bool(cr("telegram_token"))
            bot_user = db.get_setting("c_telegram_bot_username", "")
            chat = cr("telegram_chat_id")
            link = ('<p class="ok">Bot conectado: <a href="https://t.me/%s" target="_blank" rel="noopener noreferrer">@%s</a>. Pásale este enlace al dueño del teléfono: <code>https://t.me/%s</code></p>' % (esc(bot_user), esc(bot_user), esc(bot_user))) if (tok and bot_user) else ('<p class="ok">Token guardado.</p>' if tok else '<p class="bad">Falta el token del bot.</p>')
            people = [c for c in db.list_tg_chats() if c["kind"] == "private"]
            rows = "".join(
                '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                    esc(p["title"]), esc(p["chat_id"]),
                    ('<span class="ok">En uso</span>' if str(p["chat_id"]) == str(chat) else
                     '<form class="inline" method="post" action="/conexiones/telegram/usar"><input type="hidden" name="chat_id" value="%s"><button>Usar este chat</button></form>' % esc(p["chat_id"])))
                for p in people)
            tabla = ('<table><tr><th>Persona</th><th>chat_id</th><th></th></tr>%s</table>' % rows) if rows else \
                '<p class="hint">Todavía nadie. Abre el enlace de tu bot en Telegram, toca <strong>Iniciar</strong> y recarga esta página.</p>'
            tg = ('%s<form method="post" action="/conexiones/telegram"><label for="tk">Token del bot (te lo da @BotFather)</label>'
                  '<input id="tk" type="password" name="token" autocomplete="off" placeholder="%s">'
                  '<p><button>Guardar y comprobar</button></p></form>'
                  '<h2>¿Quién recibe las alertas?</h2>'
                  '<p class="hint">Quien toque Iniciar en el bot aparece aquí. Elígelo para que las alertas lleguen a su Telegram.</p>%s'
                  '<p class="hint">Chat actual: <strong>%s</strong></p>'
                  '<details><summary>Escribir el chat_id a mano</summary><form method="post" action="/conexiones/telegram">'
                  '<label for="ci">chat_id (solo el número)</label><input id="ci" type="text" name="chat_id" placeholder="123456789">'
                  '<p><button class="ghost">Guardar chat_id</button></p></form></details>'
                  '<form class="inline" method="post" action="/conexiones/probar"><input type="hidden" name="canal" value="telegram"><button class="ghost">Enviar mensaje de prueba por Telegram</button></form> '
                  '<form class="inline" method="post" action="/conexiones/quitar"><input type="hidden" name="que" value="telegram"><button class="del">Quitar Telegram</button></form>') % (
                link, "Guardado. Déjalo vacío para conservarlo" if tok else "123456789:AAE...", tabla, esc(chat) if chat else "sin elegir")

            wa = ('<p class="hint">Opcional. Requiere una cuenta de WhatsApp Cloud API de Meta (ver instructivo). Si no la tienes, ignora esta sección.</p>'
                  '<form method="post" action="/conexiones/whatsapp">'
                  '<label for="wt">Token de acceso</label><input id="wt" type="password" name="wa_token" autocomplete="off" placeholder="%s">'
                  '<label for="wp">Phone Number ID</label><input id="wp" type="text" name="wa_phone_id" value="%s">'
                  '<label for="wn">Número que recibe las alertas (con lada, solo números)</label><input id="wn" type="text" name="wa_to" value="%s" placeholder="521XXXXXXXXXX">'
                  '<label for="wl">Nombre de la plantilla aprobada (opcional)</label><input id="wl" type="text" name="wa_template" value="%s">'
                  '<p><button>Guardar WhatsApp</button></p></form>'
                  '<form class="inline" method="post" action="/conexiones/probar"><input type="hidden" name="canal" value="whatsapp"><button class="ghost">Enviar mensaje de prueba por WhatsApp</button></form> '
                  '<form class="inline" method="post" action="/conexiones/quitar"><input type="hidden" name="que" value="whatsapp"><button class="del">Quitar WhatsApp</button></form>') % (
                "Guardado. Déjalo vacío para conservarlo" if cr("wa_token") else "", esc(cr("wa_phone_id")), esc(cr("wa_to")), esc(cr("wa_template")))

            body = ('<p class="hint">Aquí pegas tus datos y el sistema los guarda y los prueba al momento. No necesitas tocar archivos ni el servidor. Los datos quedan guardados en la base del sistema, detrás de tu contraseña del panel, y nunca se muestran de nuevo.</p>'
                    '<section class="card wide"><h2>1. Correo (Facebook y avisos)</h2>%s</section>'
                    '<section class="card wide"><h2>2. Telegram</h2>%s</section>'
                    '<section class="card wide"><h2>3. WhatsApp (opcional)</h2><details><summary>Mostrar</summary>%s</details></section>') % (mail, tg, wa)
            return layout("Conexiones", body, "/conexiones", flash)

        def p_probe(self, flash, text="", result=None):
            res = ""
            if result is not None:
                res = ('<div class="card"><p><strong>%s</strong></p><p>Puntaje: %d. Intención: %s. Productos: %s.</p><p class="hint">%s</p></div>') % (
                    "Sí generaría alerta" if result.ok else "No generaría alerta", result.score,
                    esc(", ".join(result.intents) or "ninguna"), esc(", ".join(result.products) or "ninguno"),
                    esc(result.reason))
            body = ('<p class="hint">Pega el texto de una publicación real para ver si el sistema la detectaría y por qué. Sirve para calibrar las palabras.</p>'
                    '<form method="post" action="/probar"><label for="x">Texto de la publicación</label>'
                    '<textarea id="x" name="text" required>%s</textarea><p><button>Probar</button></p></form>%s') % (esc(text), res)
            return layout("Probar texto", body, "/probar", flash)

        def p_settings(self, flash):
            g = db.get_setting
            body = ('<form method="post" action="/ajustes" class="card">'
                    '<label class="chk"><input type="checkbox" name="require_product" value="1" %s> Exigir producto de mi lista (recomendado)</label>'
                    '<p class="hint">Apagado = modo amplio: basta una frase de intención como «busco». Llegarán muchas más alertas, con ruido.</p>'
                    '<label for="ms">Puntaje mínimo</label><input id="ms" type="number" name="min_score" min="1" max="12" value="%s">'
                    '<p class="hint">Más alto = menos alertas pero más certeras. Empieza en 3 y ajusta.</p>'
                    '<label for="mh">Máximo de avisos por hora</label><input id="mh" type="number" name="max_per_hour" min="1" max="500" value="%s">'
                    '<p class="hint">Protege tu teléfono de avalanchas. Lo que exceda se guarda en el panel sin avisar.</p>'
                    '<label class="chk"><input type="checkbox" name="notify_telegram" value="1" %s> Avisar por Telegram</label>'
                    '<label class="chk"><input type="checkbox" name="notify_whatsapp" value="1" %s> Avisar por WhatsApp</label>'
                    '<label class="chk"><input type="checkbox" name="notify_email" value="1" %s> Avisar por correo</label>'
                    '<p><button>Guardar ajustes</button></p></form>') % (
                "checked" if g("require_product") == "1" else "", esc(g("min_score")), esc(g("max_per_hour")),
                "checked" if g("notify_telegram") == "1" else "", "checked" if g("notify_whatsapp") == "1" else "",
                "checked" if g("notify_email") == "1" else "")
            return layout("Ajustes", body, "/ajustes", flash)

        # ---------- acciones
        def _back(self, path, msg):
            return self._redirect("%s?m=%s" % (path, quote(msg)))

        def _int(self, f, k):
            try:
                return int(f.get(k, ""))
            except ValueError:
                return 0

        def a_kw_add(self, f):
            n = db.add_keywords(f.get("terms", ""), f.get("kind", ""))
            return self._back("/palabras", "Agregadas: %d" % n if n else "No se agregó nada (¿ya existían?)")

        def a_kw_toggle(self, f):
            db.toggle_keyword(self._int(f, "id"))
            return self._back("/palabras", "Actualizada")

        def a_kw_del(self, f):
            db.delete_keyword(self._int(f, "id"))
            return self._back("/palabras", "Borrada")

        def a_src_add(self, f):
            url = f.get("url", "").strip()
            if not url.startswith(("http://", "https://")) or not f.get("name", "").strip():
                return self._back("/fuentes", "Escribe un nombre y una URL que empiece con http")
            n = db.add_source(f["name"], url)
            return self._back("/fuentes", "Feed agregado: se leerá en menos de un minuto" if n else "Ese feed ya existe")

        def a_conn_mail(self, f):
            user = f.get("gmail", "").strip()
            if "@" not in user:
                return self._back("/conexiones", "Escribe un correo válido")
            pw = "".join(f.get("app_password", "").split())
            db.set_setting("c_imap_user", user)
            if pw:
                db.set_setting("c_imap_password", pw)
            db.set_setting("c_email_to", f.get("email_to", "").strip())
            db.set_setting("notify_email", "1" if f.get("notify_email") else "0")
            ok, msg = check_imap(app)
            db.set_setting("mail_status", ("OK " if ok else "ERROR ") + fmt_time(int(time.time()), cfg.tz_offset) + ": " + msg)
            if ok:
                app.mail_wake.set()
            note = "" if (not pw or len(pw) == 16) else " Ojo: la contraseña de aplicación suele tener 16 letras."
            return self._back("/conexiones", ("Correo guardado. " if ok else "Guardado, pero falló la prueba. ") + msg + note)

        def a_conn_mail_test(self, f):
            ok, note = app.notifier.send_channel("email", "Prueba del Sistema de Alertas de Oportunidad. Si ves este correo, las alertas llegarán aquí.", "Prueba: Alertas de oportunidad")
            return self._back("/conexiones", "Correo de prueba enviado. Revisa tu bandeja (y spam)." if ok else "No se pudo enviar: %s" % note)

        def a_conn_tg(self, f):
            token = "".join(f.get("token", "").split())
            chat = f.get("chat_id", "").strip()
            msgs = []
            if token:
                db.set_setting("c_telegram_token", token)
                st, data = tg_call(token, "getMe", {})
                if st == 200 and isinstance(data, dict) and data.get("ok"):
                    db.set_setting("c_telegram_bot_username", data["result"].get("username", ""))
                    msgs.append("Bot conectado: @%s." % data["result"].get("username", ""))
                else:
                    db.set_setting("c_telegram_bot_username", "")
                    msgs.append("Telegram no reconoce ese token. Cópialo completo, con los dos puntos.")
            if chat:
                if re.fullmatch(r"-?\d{3,20}", chat):
                    db.set_setting("c_telegram_chat_id", chat)
                    msgs.append("chat_id guardado.")
                else:
                    msgs.append("El chat_id debe ser solo un número.")
            return self._back("/conexiones", " ".join(msgs) or "No escribiste nada nuevo")

        def a_conn_tg_use(self, f):
            cid = f.get("chat_id", "")
            if cid not in [str(c["chat_id"]) for c in db.list_tg_chats() if c["kind"] == "private"]:
                return self._back("/conexiones", "Ese chat no ha tocado Iniciar en el bot")
            db.set_setting("c_telegram_chat_id", cid)
            ok, note = app.notifier.send_channel("telegram", "Listo: las alertas de oportunidad llegarán a este chat.")
            return self._back("/conexiones", "Chat elegido. Mensaje de prueba enviado." if ok else "Chat elegido, pero la prueba falló: %s" % note)

        def a_conn_wa(self, f):
            if f.get("wa_token", "").strip():
                db.set_setting("c_wa_token", "".join(f["wa_token"].split()))
            db.set_setting("c_wa_phone_id", f.get("wa_phone_id", "").strip())
            db.set_setting("c_wa_to", re.sub(r"\D", "", f.get("wa_to", "")))
            db.set_setting("c_wa_template", f.get("wa_template", "").strip())
            return self._back("/conexiones", "WhatsApp guardado. Usa el botón de prueba para comprobarlo.")

        def a_conn_test(self, f):
            canal = f.get("canal", "")
            if canal not in ("telegram", "whatsapp"):
                return self._back("/conexiones", "Canal desconocido")
            ok, note = app.notifier.send_channel(canal, "Prueba del Sistema de Alertas de Oportunidad. Si ves este mensaje, las alertas llegarán aquí.")
            return self._back("/conexiones", "Mensaje de prueba enviado." if ok else "No se pudo enviar: %s" % note)

        def a_conn_clear(self, f):
            keys = {"correo": ["imap_user", "imap_password", "email_to"], "telegram": ["telegram_token", "telegram_chat_id", "telegram_bot_username"],
                    "whatsapp": ["wa_token", "wa_phone_id", "wa_to", "wa_template"]}.get(f.get("que", ""))
            if not keys:
                return self._back("/conexiones", "Nada que quitar")
            for k in keys:
                db.set_setting("c_" + k, "")
            if f.get("que") == "correo":
                db.set_setting("mail_status", "")
            return self._back("/conexiones", "Quitado. Si venía del servidor (.env), seguirá usándose ese valor.")

        def a_tg_toggle(self, f):
            db.toggle_tg_chat(f.get("chat_id", ""))
            return self._back("/fuentes", "Grupo actualizado")

        def a_src_toggle(self, f):
            db.toggle_source(self._int(f, "id"))
            return self._back("/fuentes", "Actualizada")

        def a_src_del(self, f):
            db.delete_source(self._int(f, "id"))
            return self._back("/fuentes", "Borrada")

        def a_settings(self, f):
            db.set_setting("require_product", "1" if f.get("require_product") else "0")
            db.set_setting("notify_telegram", "1" if f.get("notify_telegram") else "0")
            db.set_setting("notify_whatsapp", "1" if f.get("notify_whatsapp") else "0")
            db.set_setting("notify_email", "1" if f.get("notify_email") else "0")
            db.set_setting("min_score", max(1, min(12, self._int(f, "min_score") or 3)))
            db.set_setting("max_per_hour", max(1, min(500, self._int(f, "max_per_hour") or 20)))
            return self._back("/ajustes", "Ajustes guardados")

        def a_test(self, f):
            ok, note = app.notifier.send_text("✅ Prueba del Sistema de Alertas de Oportunidad. Si ves este mensaje, las alertas llegarán aquí.")
            return self._back("/", ("Mensaje enviado (%s)" % note) if ok else ("No se pudo enviar: %s" % note))

        def a_probe(self, f):
            text = f.get("text", "")
            return self._send(200, self.p_probe("", text, app.evaluate(text)))

        def api_ingest(self):
            tok = self.headers.get("X-Token") or self.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
            if not cfg.ingest_token or not hmac.compare_digest(tok.encode(), cfg.ingest_token.encode()):
                return self._send(401, json.dumps({"error": "token inválido"}), "application/json")
            try:
                data = json.loads(self._body(262144) or b"{}")
                text = str(data.get("text", "")).strip()
            except (ValueError, AttributeError):
                return self._send(400, json.dumps({"error": "JSON inválido"}), "application/json")
            if not text:
                return self._send(400, json.dumps({"error": "falta 'text'"}), "application/json")
            res = engine.process_item(app, {
                "id": data.get("id"), "source": str(data.get("source") or "Webhook")[:80],
                "text": text, "url": str(data.get("url") or "")[:500], "author": str(data.get("author") or "")[:80]})
            return self._send(200, json.dumps(res, ensure_ascii=False), "application/json")

    return H


def make_server(app):
    return ThreadingHTTPServer((app.cfg.host, app.cfg.port), make_handler(app))
