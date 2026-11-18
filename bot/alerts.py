"""Hälytykset omistajalle (valinnainen): .env ALERT_WEBHOOK_URL + ALERT_WEBHOOK_KIND=discord|slack (oletus discord).

Discord-webhook: POST {"content": "..."}; Slackin incoming webhook: POST {"text": "..."}. Ilman osoitetta viesti
vain kirjataan lokiin. Lähetys ei koskaan kaada ajoa eikä jää jumiin (aikaraja 10 s, 2 yritystä).
Viestistä poistetaan salaisuudet: .env-avainten arvot, webhookin osoite, key=/token=-parametrit ja API-avaimen
näköiset merkkijonot.
"""
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)
TIMEOUT = 10
MAX_LEN = 1800          # Discordin raja on 2 000 merkkiä
SECRET_ENV = ("YOUTUBE_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ALERT_WEBHOOK_URL")
_PATTERNS = [
    (re.compile(r"(?i)\b(key|token|access_token|refresh_token|client_secret|api_key|apikey|password|sig)=[^&\s\"']+"),
     r"\1=***"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "***"),          # Google API -avain
    (re.compile(r"ya29\.[0-9A-Za-z_\-\.]{10,}"), "***"),       # Google OAuth -token
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{10,}"), "Bearer ***"),
    (re.compile(r"https://(discord(app)?\.com/api/webhooks|hooks\.slack\.com)/\S+"), "<webhook>"),
]


def redact(text: str) -> str:
    s = str(text or "")
    for name in SECRET_ENV:
        v = os.environ.get(name, "").strip()
        if len(v) >= 6:
            s = s.replace(v, "***")
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


def payload(text: str, kind: str | None = None) -> dict:
    kind = (kind or os.environ.get("ALERT_WEBHOOK_KIND") or "discord").strip().lower()
    body = redact(text)
    if len(body) > MAX_LEN:
        body = body[:MAX_LEN - 3] + "..."
    return {"text": body} if kind == "slack" else {"content": body}


def send(text: str, level: int = logging.WARNING) -> bool:
    """Kirjaa viestin aina; lähettää webhookiin, jos ALERT_WEBHOOK_URL on asetettu. True = lähetetty."""
    msg = redact(text)
    log.log(level, "HÄLYTYS: %s", msg)
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return False
    body = payload(text)
    for attempt in range(2):
        try:
            r = requests.post(url, json=body, timeout=TIMEOUT)
            if r.status_code < 300:
                return True
            if r.status_code == 429 and attempt == 0:  # Discordin rajoitin
                time.sleep(min(float(r.headers.get("Retry-After", 2) or 2), 5))
                continue
            log.warning("Hälytyksen lähetys epäonnistui: HTTP %s", r.status_code)
            return False
        except requests.RequestException as e:
            log.warning("Hälytyksen lähetys epäonnistui: %s", redact(type(e).__name__))
            if attempt == 0:
                time.sleep(1)
    return False
