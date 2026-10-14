"""Credenciales guardadas desde el panel (con respaldo en variables de entorno)."""
import imaplib


def mail_hosts(cred, cfg):
    """Devuelve (servidor_imap, servidor_smtp). Gmail se detecta solo por el correo."""
    user = cred("imap_user")
    imap = cred("imap_host")
    dom = user.split("@")[-1].lower() if "@" in user else ""
    if not imap and dom in ("gmail.com", "googlemail.com"):
        imap = "imap.gmail.com"
    smtp = cfg.smtp_host or ("smtp.gmail.com" if imap.endswith("gmail.com") else (imap.replace("imap", "smtp", 1) if imap else ""))
    return imap, smtp


AUTH_MSG = ("Google rechazó la contraseña. Revisa que sea una contraseña de aplicación de 16 letras "
            "(no tu contraseña normal) y que la verificación en 2 pasos esté activa.")


def check_imap(app):
    host, _ = mail_hosts(app.cred, app.cfg)
    user, pw = app.cred("imap_user"), app.cred("imap_password")
    if not (host and user and pw):
        return False, "Falta el correo o la contraseña de aplicación."
    try:
        m = imaplib.IMAP4_SSL(host, timeout=15)
        try:
            m.login(user, pw)
        finally:
            try:
                m.logout()
            except Exception:
                pass
        return True, "Conexión correcta: el sistema ya puede leer ese correo."
    except imaplib.IMAP4.error:
        return False, AUTH_MSG
    except Exception as e:
        return False, "No se pudo conectar: %s" % e
