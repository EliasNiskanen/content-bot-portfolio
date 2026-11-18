"""Kanavan soittolistat: uutiset omaansa, muut GTA 6 -videot (opas, julkaisupäivä, breakdown) omaansa.

Jokainen uusi pitkä video lisätään muodon mukaiseen listaan ensimmäiseksi, ja botin linkit (Watch next, Shortsien
"Full video") avaavat videon soittolistan kautta (watch?v=ID&list=PL...), jolloin YouTube ehdottaa seuraavaksi
saman listan videoita. Soittolistojen id:t: data/playlists.json (luodaan kerran, ei kahdesti).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT = {
    "news": {"title": "GTA 6 News", "formats": ["long"],
             "description": "Every GTA 6 (Grand Theft Auto VI) news story, checked against Rockstar's trailers, "
                            "interviews and previews: confirmed facts, rumors and leaks, newest first."},
    "guides": {"title": "GTA 6 Guides & Tips", "formats": ["guide", "breakdown"],
               "description": "GTA 6 (Grand Theft Auto VI) guides, tips and trailer breakdowns: what to do first, how "
                              "the map, police, cars and money work, and everything Rockstar showed, newest first."},
}
WATCH_RE = re.compile(r"(?:https://youtu\.be/|https://www\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})(?:&list=[A-Za-z0-9_-]+)?")


def settings(cfg) -> dict:
    pl = (cfg.get("upload") or {}).get("playlists")
    if pl is False:
        return {}
    return pl if isinstance(pl, dict) and pl else DEFAULT


def _store(cfg) -> Path:
    return Path(cfg.paths.db).parent / "playlists.json"


def _load_ids(cfg) -> dict:
    try:
        return json.loads(_store(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def name_for(cfg, fmt: str) -> str | None:
    for name, p in settings(cfg).items():
        if fmt in (p.get("formats") or []):
            return name
    return None


def ensure(cfg, yt) -> dict[str, str]:
    """{nimi: soittolistan id}. Tallennettu id tarkistetaan suoraan id:llä (luettelo päivittyy viiveellä, ja siksi
    pelkkä luettelo loi 18.11.2026 tuplalistan); puuttuva haetaan nimellä, vasta sitten luodaan ja tallennetaan heti."""
    want = settings(cfg)
    if not want:
        return {}
    ids = _load_ids(cfg)
    stored = [i for i in ids.values() if i]
    alive = set()
    if stored:
        r = yt.playlists().list(part="id", id=",".join(stored), maxResults=50).execute()
        alive = {p["id"] for p in r.get("items") or []}
    out = {}
    mine = None
    for name, p in want.items():
        pid = ids.get(name)
        if pid not in alive:
            if mine is None:
                mine, token = {}, None
                while True:
                    r = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
                    for x in r.get("items") or []:
                        mine[x["id"]] = x["snippet"]["title"]
                    token = r.get("nextPageToken")
                    if not token:
                        break
            pid = next((i for i, t in mine.items() if t.strip().lower() == p["title"].strip().lower()), None)
        if not pid:
            body = {"snippet": {"title": p["title"], "description": p.get("description", ""), "defaultLanguage": "en"},
                    "status": {"privacyStatus": "public"}}
            pid = yt.playlists().insert(part="snippet,status", body=body).execute()["id"]
            log.info("Soittolista luotu: %s (%s)", p["title"], pid)
        out[name] = pid
        if ids.get(name) != pid:
            ids[name] = pid
            _store(cfg).write_text(json.dumps(ids, indent=1), encoding="utf-8")
    return out


def items(yt, pid: str) -> list[str]:
    out, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pid, maxResults=50, pageToken=token).execute()
        out += [i["contentDetails"]["videoId"] for i in r.get("items") or []]
        token = r.get("nextPageToken")
        if not token:
            return out


def add(cfg, yt, vid: str, fmt: str) -> str | None:
    """Lisää videon muodon soittolistaan ensimmäiseksi (jos ei jo siellä). Palauttaa soittolistan id:n tai None."""
    name = name_for(cfg, fmt)
    if not name:
        return None
    pid = ensure(cfg, yt).get(name)
    if not pid:
        return None
    if vid not in items(yt, pid):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pid, "position": 0, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        log.info("Video %s lisätty soittolistaan %s", vid, settings(cfg)[name]["title"])
    return pid


def membership(cfg, yt) -> dict[str, str]:
    """{video_id: soittolistan id} kaikista botin soittolistoista."""
    out = {}
    for pid in ensure(cfg, yt).values():
        for vid in items(yt, pid):
            out.setdefault(vid, pid)
    return out


def watch_url(vid: str, pid: str | None = None) -> str:
    return f"https://www.youtube.com/watch?v={vid}&list={pid}" if pid else f"https://youtu.be/{vid}"
