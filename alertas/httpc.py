"""Cliente HTTP mínimo (solo librería estándar)."""
import json
import urllib.error
import urllib.request

UA = "AlertasOportunidad/1.0 (+lectura de feeds publicos)"


def post_json(url, payload, headers=None, timeout=20):
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # red caída, DNS, timeout
        return 0, str(e)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def get_bytes(url, timeout=20, limit=2_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)
