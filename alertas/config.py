import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path=None):
    p = Path(path or os.environ.get("ENV_FILE", ".env"))
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Config:
    panel_password: str = ""
    secret_key: str = ""
    ingest_token: str = ""
    db_path: str = "data/alertas.db"
    host: str = "0.0.0.0"
    port: int = 8080
    telegram_token: str = ""
    telegram_chat_id: str = ""
    wa_token: str = ""
    wa_phone_id: str = ""
    wa_to: str = ""
    wa_template: str = ""
    wa_template_lang: str = "es_MX"
    dry_run: bool = False
    rss_minutes: int = 10
    tz_offset: int = -6
    cookie_secure: bool = False
    trust_proxy: bool = False


def load_config() -> Config:
    load_dotenv()
    e = os.environ.get
    cfg = Config(
        panel_password=e("PANEL_PASSWORD", ""),
        secret_key=e("SECRET_KEY", ""),
        ingest_token=e("INGEST_TOKEN", ""),
        db_path=e("DB_PATH", "data/alertas.db"),
        host=e("HOST", "0.0.0.0"),
        port=_int("PORT", 8080),
        telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=e("TELEGRAM_ALERT_CHAT_ID", ""),
        wa_token=e("WHATSAPP_TOKEN", ""),
        wa_phone_id=e("WHATSAPP_PHONE_ID", ""),
        wa_to=e("WHATSAPP_TO", ""),
        wa_template=e("WHATSAPP_TEMPLATE", ""),
        wa_template_lang=e("WHATSAPP_TEMPLATE_LANG", "es_MX"),
        dry_run=_bool("DRY_RUN"),
        rss_minutes=max(2, _int("RSS_MINUTES", 10)),
        tz_offset=_int("TZ_OFFSET", -6),
        cookie_secure=_bool("COOKIE_SECURE"),
        trust_proxy=_bool("TRUST_PROXY"),
    )
    if not cfg.secret_key:
        cfg.secret_key = hashlib.sha256(("alertas|" + cfg.panel_password).encode()).hexdigest()
    return cfg
