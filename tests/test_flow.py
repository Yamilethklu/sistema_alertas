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
