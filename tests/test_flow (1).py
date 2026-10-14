import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from alertas.config import Config
from alertas.engine import App, process_item
from alertas.web import make_server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cfg = Config(panel_password="clave-segura-123", ingest_token="tok123", dry_run=True,
                     db_path=os.path.join(cls.tmp, "t.db"), host="127.0.0.1", port=0)
        cls.app = App(cfg)
        cls.server = make_server(cls.app)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.jar = http.cookiejar.CookieJar()
        cls.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cls.jar), NoRedirect)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def req(self, path, data=None, headers=None, opener=None):
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
        r = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            with (opener or self.op).open(r) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_1_proceso_y_dedupe(self):
        item = {"id": "1", "source": "Prueba", "text": "¿Alguien sabe dónde compro una laptop?",
                "url": "https://ejemplo.com/p/1", "author": "ana"}
        r = process_item(self.app, dict(item))
        self.assertTrue(r["matched"] and r["notified"])
        self.assertFalse(process_item(self.app, dict(item))["matched"])  # duplicado
        r2 = process_item(self.app, {"id": "2", "source": "Prueba", "text": "Vendo laptop nueva"})
        self.assertFalse(r2["matched"])

    def test_2_limite_por_hora(self):
        self.app.db.set_setting("max_per_hour", 1)
        r = process_item(self.app, {"id": "9", "source": "Prueba", "text": "Busco un monitor, recomiendan alguno?"})
        self.assertTrue(r["matched"])
        self.assertFalse(r["notified"])
        self.app.db.set_setting("max_per_hour", 20)

    def test_3_login_y_paginas(self):
        st, _ = self.req("/")
        self.assertEqual(st, 303)  # sin sesión → redirige
        st, body = self.req("/login", {"password": "mala"})
        self.assertEqual(st, 401)
        st, _ = self.req("/login", {"password": "clave-segura-123"})
        self.assertEqual(st, 303)
        for p in ["/", "/alertas", "/palabras", "/fuentes", "/probar", "/ajustes"]:
            st, body = self.req(p)
            self.assertEqual(st, 200, p)
        self.assertIn("laptop", self.req("/alertas")[1])

    def test_4_palabras_y_probar(self):
        self.req("/login", {"password": "clave-segura-123"})
        st, _ = self.req("/palabras/agregar", {"kind": "producto", "terms": "dron, bocina bluetooth"})
        self.assertEqual(st, 303)
        st, body = self.req("/probar", {"text": "Busco un dron para grabar, alguien recomienda?"})
        self.assertIn("Sí generaría alerta", body)

    def test_5_xss_escapado(self):
        self.req("/login", {"password": "clave-segura-123"})
        process_item(self.app, {"id": "x", "source": "Prueba", "text": "Busco laptop <script>alert(1)</script> recomiendan?"})
        self.assertNotIn("<script>alert(1)</script>", self.req("/alertas")[1])

    def test_7_editar_palabras_y_fuentes(self):
        self.req("/login", {"password": "clave-segura-123"})
        self.app.db.add_keywords("dronn", "producto")
        kid = [k for k in self.app.db.list_keywords("producto") if k["term"] == "dronn"][0]["id"]
        st, page = self.req("/palabras?editar=%d" % kid)
        self.assertIn("Editar palabra", page)
        self.req("/palabras/editar", {"id": kid, "term": "dron profesional"})
        self.assertEqual(self.app.db.get_keyword(kid)["term"], "dron profesional")
        self.req("/palabras/editar", {"id": kid, "term": "laptop"})  # ya existe en productos: se rechaza
        self.assertEqual(self.app.db.get_keyword(kid)["term"], "dron profesional")
        self.app.db.add_source("Reddit", "https://ejemplo.com/a.rss")
        sid = self.app.db.list_sources()[0]["id"]
        self.assertIn("Editar fuente", self.req("/fuentes?editar=%d" % sid)[1])
        self.req("/fuentes/editar", {"id": sid, "name": "Reddit laptops", "url": "https://ejemplo.com/b.rss"})
        self.assertEqual(self.app.db.get_source(sid)["name"], "Reddit laptops")
        self.req("/fuentes/editar", {"id": sid, "name": "x", "url": "no-es-url"})
        self.assertEqual(self.app.db.get_source(sid)["url"], "https://ejemplo.com/b.rss")

    def test_6_ingest(self):
        payload = json.dumps({"text": "Necesito un teclado mecánico, ¿cuál comprar?", "url": "https://fb.example/1",
                              "source": "Make"}).encode()
        st, _ = self.req("/api/ingest", payload, {"X-Token": "mal", "Content-Type": "application/json"})
        self.assertEqual(st, 401)
        st, body = self.req("/api/ingest", payload, {"X-Token": "tok123", "Content-Type": "application/json"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["matched"])


if __name__ == "__main__":
    unittest.main()


class MailParseTests(unittest.TestCase):
    def test_correo_facebook(self):
        from alertas.mailbox import parse_fb_email
        raw = (b"From: Facebook <notification@facebookmail.com>\r\nSubject: Ana publico en Ofertas MX\r\n"
               b"Message-ID: <abc@fb>\r\nMIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
               b"<html><body><p>Busco una laptop, alguien recomienda?</p>"
               b"<a href=\"https://www.facebook.com/groups/123/posts/456/?x=1&amp;y=2\">Ver publicacion</a></body></html>")
        it = parse_fb_email(raw)
        self.assertIn("Busco una laptop", it["text"])
        self.assertEqual(it["url"], "https://www.facebook.com/groups/123/posts/456/?x=1&y=2")
        self.assertEqual(it["id"], "<abc@fb>")


class EmailLoginAndMuteTests(unittest.TestCase):
    def test_login_con_correo_y_silenciar_grupo(self):
        tmp = tempfile.mkdtemp()
        cfg = Config(panel_password="clave-segura-123", panel_email="Cliente@Ejemplo.com", dry_run=True,
                     db_path=os.path.join(tmp, "e.db"), host="127.0.0.1", port=0)
        app = App(cfg)
        server = make_server(app)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            jar = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)

            def post(path, data):
                r = urllib.request.Request(base + path, data=urllib.parse.urlencode(data).encode())
                try:
                    with op.open(r) as resp:
                        return resp.status, resp.read().decode()
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode()
            self.assertEqual(post("/login", {"email": "otro@x.com", "password": "clave-segura-123"})[0], 401)
            self.assertEqual(post("/login", {"email": "cliente@ejemplo.com", "password": "mala"})[0], 401)
            self.assertEqual(post("/login", {"email": "cliente@ejemplo.com", "password": "clave-segura-123"})[0], 303)
            # silenciar grupo de Telegram
            from alertas.engine import handle_tg_update
            upd = {"update_id": 1, "message": {"message_id": 5, "chat": {"id": -1001, "type": "supergroup", "title": "Ofertas"},
                                                "from": {"first_name": "Ana"}, "text": "Busco una laptop, alguien recomienda?"}}
            handle_tg_update(app, upd)
            self.assertEqual(len(app.db.recent_alerts()), 1)
            post("/fuentes/grupo", {"chat_id": "-1001"})
            upd["message"]["message_id"] = 6
            upd["message"]["text"] = "Necesito un monitor, cual me recomiendan?"
            handle_tg_update(app, upd)
            self.assertEqual(len(app.db.recent_alerts()), 1)  # silenciado: no hay alerta nueva
        finally:
            server.shutdown()


class ConexionesTests(unittest.TestCase):
    def test_conexiones_desde_el_panel(self):
        from unittest import mock
        tmp = tempfile.mkdtemp()
        cfg = Config(panel_password="clave-segura-123", dry_run=True, db_path=os.path.join(tmp, "c.db"),
                     host="127.0.0.1", port=0)
        app = App(cfg)
        server = make_server(app)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)

        def call(path, data=None):
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            try:
                with op.open(urllib.request.Request(base + path, data=body)) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
        try:
            call("/login", {"password": "clave-segura-123"})
            st, page = call("/conexiones")
            self.assertEqual(st, 200)
            self.assertIn("Contraseña de aplicación", page)
            self.assertFalse(app.notifier.channel_status()["email"]["configurado"])

            # correo: quita espacios de la contraseña, prueba la conexión y activa el canal
            with mock.patch("alertas.web.check_imap", return_value=(True, "Conexión correcta")):
                call("/conexiones/correo", {"gmail": "cli@gmail.com", "app_password": "abcd efgh ijkl mnop", "notify_email": "1"})
            self.assertEqual(app.cred("imap_password"), "abcdefghijklmnop")
            self.assertTrue(app.notifier.channel_status()["email"]["configurado"])
            self.assertTrue(app.notifier.channel_status()["email"]["activo"])
            self.assertNotIn("abcdefghijklmnop", call("/conexiones")[1])  # nunca se vuelve a mostrar

            # telegram: token válido muestra el enlace del bot
            with mock.patch("alertas.web.tg_call", return_value=(200, {"ok": True, "result": {"username": "mi_bot"}})):
                call("/conexiones/telegram", {"token": "123:ABC"})
            self.assertIn("https://t.me/mi_bot", call("/conexiones")[1])

            # alguien toca Iniciar y el cliente lo elige con un clic
            from alertas.engine import handle_tg_update
            upd = {"update_id": 1, "message": {"message_id": 1, "text": "/start",
                   "chat": {"id": 777, "type": "private", "first_name": "Carlos"}, "from": {"first_name": "Carlos"}}}
            with mock.patch("alertas.engine.tg_call", return_value=(200, {"ok": True})):
                handle_tg_update(app, upd)
            self.assertIn("Usar este chat", call("/conexiones")[1])
            call("/conexiones/telegram/usar", {"chat_id": "999"})  # desconocido: se rechaza
            self.assertEqual(app.cred("telegram_chat_id"), "")
            call("/conexiones/telegram/usar", {"chat_id": "777"})
            self.assertEqual(app.cred("telegram_chat_id"), "777")
            self.assertTrue(app.notifier.channel_status()["telegram"]["configurado"])

            # quitar correo
            call("/conexiones/quitar", {"que": "correo"})
            self.assertFalse(app.notifier.channel_status()["email"]["configurado"])
        finally:
            server.shutdown()
