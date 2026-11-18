"""Esitarkistus ennen jokaista ajastettua työtä: vika löytyy heti (hälytys), ei puolivalmiin ajon jälkeen.

  levytila     vapaata data-levyllä >= qa.min_free_gb (8 Gt); jos vähemmän, ensin siivous (housekeeping), sitten
               uudelleenmittaus
  .env         YOUTUBE_API_KEY, ANTHROPIC_API_KEY (+ ELEVENLABS_API_KEY jos tts.engine: elevenlabs)
  YouTube      OAuth-tokenin uusinta toimii (_service) + halpa channels.list mine=true (1 kiintiöyksikkö)
  ffmpeg       ffmpeg -version ja ffprobe -version vastaavat
  verkko       YouTube API (yllä) + ensimmäinen news.feeds-syöte vastaa
"""
import logging
import shutil
from pathlib import Path

import requests

from . import proc, qa
from .config import env

log = logging.getLogger(__name__)


def required_keys(cfg) -> list[str]:
    keys = ["YOUTUBE_API_KEY", "ANTHROPIC_API_KEY"]
    if (cfg.get("tts") or {}).get("engine") == "elevenlabs":
        keys.append("ELEVENLABS_API_KEY")
    return keys


def free_gb(path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def data_dir(cfg) -> Path:
    return Path(cfg.paths.db).resolve().parent


def check_disk(cfg, cleanup=None, measure=free_gb) -> str | None:
    need = float(qa.settings(cfg)["min_free_gb"])
    d = data_dir(cfg)
    have = measure(d)
    if have >= need:
        return None
    log.warning("Levytilaa %.1f Gt (< %.0f Gt) -> siivotaan ensin", have, need)
    try:
        if cleanup is None:
            from . import housekeeping
            housekeeping.cleanup_all(cfg)
        else:
            cleanup(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("Siivous epäonnistui: %s", e)
    have = measure(d)
    if have >= need:
        log.info("Siivouksen jälkeen vapaata %.1f Gt", have)
        return None
    return f"levytilaa vain {have:.1f} Gt (tarvitaan {need:.0f} Gt) myös siivouksen jälkeen ({d})"


def check_keys(cfg) -> str | None:
    missing = [k for k in required_keys(cfg) if not env(k, required=False)]
    return f".env-avaimet puuttuvat: {', '.join(missing)}" if missing else None


def check_tools() -> str | None:
    bad = []
    for tool in ("ffmpeg", "ffprobe"):
        try:
            r = proc.run([tool, "-version"], 30, check=False, capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(f"{tool} (paluukoodi {r.returncode})")
        except (OSError, proc.ProcTimeout) as e:
            bad.append(f"{tool} ({type(e).__name__})")
    return f"ei toimi: {', '.join(bad)}" if bad else None


def check_youtube(cfg, service=None) -> str | None:
    """Tokenin uusinta + channels.list mine=true. Palauttaa virheen tai None."""
    try:
        if service is None:
            from .uploader import _service
            yt = _service(cfg.paths.secrets_dir)
        else:
            yt = service(cfg)
        resp = yt.channels().list(part="id", mine=True).execute()
    except Exception as e:  # noqa: BLE001
        return f"YouTube-tunnistus/yhteys epäonnistui: {type(e).__name__}: {e}"
    if not resp.get("items"):
        return "YouTube: token ei näe omaa kanavaa (channels.list mine=true tyhjä)"
    return None


def check_feed(cfg, get=requests.get) -> str | None:
    feeds = list((cfg.get("news") or {}).get("feeds") or [])
    if not feeds:
        return None
    url = feeds[0]
    try:
        from .news import FEED_TIMEOUT, FEED_UA
        r = get(url, timeout=FEED_TIMEOUT, headers={"User-Agent": FEED_UA})
        if r.status_code >= 400:
            return f"uutissyöte vastasi HTTP {r.status_code}: {url[:60]}"
    except requests.RequestException as e:
        return f"uutissyöte ei vastaa ({type(e).__name__}): {url[:60]}"
    return None


def run(cfg, *, youtube: bool = True, service=None, cleanup=None, measure=free_gb, get=requests.get) -> list[str]:
    """Palauttaa virheet (tyhjä = kaikki ok). youtube=False: ei token-/API-tarkistusta (--no-upload)."""
    problems = []
    checks = [("levytila", lambda: check_disk(cfg, cleanup, measure)), (".env", lambda: check_keys(cfg)),
              ("ffmpeg", check_tools)]
    if youtube:
        checks.append(("YouTube", lambda: check_youtube(cfg, service)))
    checks.append(("verkko", lambda: check_feed(cfg, get)))
    for name, fn in checks:
        try:
            err = fn()
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        if err:
            problems.append(f"{name}: {err}")
            log.error("Esitarkistus %s: %s", name, err)
        else:
            log.info("Esitarkistus %s: ok", name)
    return problems
