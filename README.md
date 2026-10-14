# Sistema de Alertas de Oportunidad

Vigila fuentes públicas buscando personas con **intención de compra** (“busco laptop”, “¿dónde compro…?”, “recomiendan…”)
y te manda un **aviso privado** a Telegram y/o WhatsApp con el enlace directo a la publicación.
**Tú** entras y respondes de forma manual con tu enlace de afiliado: ningún bot publica ni comenta en tu nombre.

## Qué incluye
| Módulo | Qué hace |
|---|---|
| Rastreador | Lee grupos de Telegram (con el bot agregado), feeds RSS/Atom y publicaciones enviadas por webhook |
| Detector | Frases de intención + productos + palabras de exclusión (“vendo”), con puntaje ajustable |
| Notificador | Alerta privada a Telegram y/o WhatsApp Cloud API, con límite de avisos por hora |
| Panel web | Login, alertas, palabras clave, fuentes, ajustes y “Probar texto” para calibrar |
| Webhook | `POST /api/ingest` para conectar Make, n8n o Zapier |

## Inicio rápido (local)
```bash
cp .env.example .env      # edita PANEL_PASSWORD y, si quieres, Telegram
python3 run.py            # Python 3.10+, no requiere pip install
# abre http://localhost:8080
```
Para probar sin enviar mensajes reales: `DRY_RUN=1 python3 run.py`.

Pruebas automáticas: `python3 -m unittest discover -s tests -t .`

## Despliegue
- **Docker:** `docker compose up -d --build` (los datos quedan en el volumen `alertas-data`).
- **Railway / Render / VPS:** sube el repo, define las variables de `.env.example`, comando de inicio `python run.py`,
  y monta un volumen persistente en la ruta de `DB_PATH` (si no, la base se borra en cada despliegue).
- Detrás de HTTPS define `COOKIE_SECURE=1` y `TRUST_PROXY=1`.

## Fuentes
1. **Telegram:** crea un bot con @BotFather, desactiva su modo privacidad (`/setprivacy` → Disable), agrégalo a los grupos
   que quieras vigilar. Solo lee mensajes de grupos donde lo agregaron. Escríbele `/start` en privado para ver tu chat_id.
2. **RSS/Atom:** desde el panel (Fuentes). Ejemplo Reddit: `https://www.reddit.com/search.rss?q=busco+laptop&sort=new`.
   La primera lectura de cada feed solo memoriza lo existente, para no inundarte de alertas viejas.
3. **Facebook por correo (sin scraping):** el cliente activa en Facebook las notificaciones de los grupos donde ya es
   miembro (Grupo → Notificaciones → Todas las publicaciones). Facebook le manda un correo por publicación; el sistema lee ese
   buzón por IMAP (solo lectura) y genera la alerta con el enlace a la publicación. Define `IMAP_HOST`, `IMAP_USER` e
   `IMAP_PASSWORD` (contraseña de aplicación). Limitación: solo cubre los grupos y publicaciones que Facebook decida notificar.
4. **Webhook:**
   ```bash
   curl -X POST https://TU-DOMINIO/api/ingest -H "X-Token: TU_INGEST_TOKEN" -H "Content-Type: application/json" \
     -d '{"text":"Busco un taladro, ¿recomiendan alguno?","url":"https://...","source":"Make","author":"Ana"}'
   ```

## Lo que NO hace (a propósito)
- **No hace scraping de Facebook.** Meta no ofrece API para buscar publicaciones en grupos y rastrearlo viola sus términos
  (riesgo de bloqueo de cuentas). Facebook entra solo por las notificaciones por correo del propio cliente (ver Fuentes).
- **No publica ni comenta automáticamente** en ninguna plataforma.

## Estructura
```
run.py              arranque
alertas/config.py   variables de entorno
alertas/matcher.py  detector de intención
alertas/engine.py   procesamiento, dedupe, límite por hora, hilos de lectura
alertas/notifier.py envío a Telegram / WhatsApp
alertas/sources.py  lector RSS/Atom
alertas/mailbox.py  alertas de Facebook vía correo de notificaciones (IMAP)
alertas/web.py      panel y API
alertas/db.py       SQLite
tests/              pruebas
```
