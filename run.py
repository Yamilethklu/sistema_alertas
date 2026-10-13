import logging
import signal
import sys
import threading

from alertas.config import load_config
from alertas.engine import App
from alertas.web import make_server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if not cfg.panel_password or len(cfg.panel_password) < 8:
        sys.exit("Define PANEL_PASSWORD (mínimo 8 caracteres) en el archivo .env o en las variables de entorno.")
    app = App(cfg)
    app.start()
    server = make_server(app)
    logging.info("Panel en http://%s:%d  |  Telegram: %s  |  WhatsApp: %s  |  DRY_RUN: %s",
                 cfg.host, cfg.port, "sí" if cfg.telegram_token else "no",
                 "sí" if cfg.wa_token else "no", cfg.dry_run)

    def bye(*_):
        app.stop.set()
        # shutdown() bloquea si se llama desde el hilo que corre serve_forever(); por eso va en otro hilo
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    server.serve_forever()


if __name__ == "__main__":
    main()
