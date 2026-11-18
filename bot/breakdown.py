"""Breakdown-video Rockstarin uudesta virallisesta GTA VI -videosta (format breakdown).

Vahti (ajastimessa breakdown.interval_minutes välein, alkaen breakdown.watch_from): Rockstarin uploads-soittolista
YouTube API:lla (channels.list forHandle -> playlistItems.list -> videos.list, 1 yksikkö per kutsu; EI search.list).
Uusi = julkaistu watch_from jälkeen, otsikossa "Grand Theft Auto VI"/"GTA VI", kesto >= 30 s, ei nähty (state.db).
Uusi video -> latautuu data/gta6_footage/rockstar_<id>.mp4 (kaikki myöhemmätkin videot käyttävät sitä), tekstitykset,
otsikko + kuvaus artikkeliksi, ~24 avainkuvaa (tasaisesti + kohtauksenvaihdot) käsikirjoittajalle kuvina.

  python -m bot.main breakdown --dry-run --watch-from 2026-08-01   # tunnistus, lataus, kuvat, kehotteen koko
"""
import datetime as dt
import json
import logging
import re
import sys
from pathlib import Path

import requests

from . import proc

log = logging.getLogger(__name__)
TITLE_RX = re.compile(r"grand theft auto vi\b|gta vi\b", re.I)
HANDLE = "RockstarGames"


def bcfg(cfg) -> dict:
    return dict(cfg.get("breakdown") or {})


def _api(path: str, params: dict) -> dict:
    from .trending import API
    r = requests.get(f"{API}/{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _since(s) -> dt.datetime:
    t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def rockstar_uploads(api_key: str, api=_api) -> list[dict]:
    """Rockstarin uusimmat lataukset: [{id, title, description, published, duration}] (3 kiintiöyksikköä)."""
    from .trending import _iso_duration_s
    ch = api("channels", dict(part="contentDetails", forHandle=HANDLE, key=api_key))["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items = api("playlistItems", dict(part="snippet,contentDetails", playlistId=up, maxResults=25,
                                      key=api_key)).get("items") or []
    ids = [i["contentDetails"]["videoId"] for i in items]
    if not ids:
        return []
    out = []
    for v in api("videos", dict(part="snippet,contentDetails", id=",".join(ids), key=api_key)).get("items") or []:
        sn = v.get("snippet") or {}
        out.append({"id": v["id"], "title": sn.get("title") or "", "description": sn.get("description") or "",
                    "published": sn.get("publishedAt"), "channel": sn.get("channelTitle") or "Rockstar Games",
                    "duration": _iso_duration_s((v.get("contentDetails") or {}).get("duration", "")),
                    "url": f"https://www.youtube.com/watch?v={v['id']}"})
    return out


def find_new(cfg, api_key: str, seen=lambda vid: False, watch_from=None, api=_api) -> list[dict]:
    since = _since(watch_from or bcfg(cfg).get("watch_from") or "2026-11-18T00:00:00+02:00")
    out = []
    for v in rockstar_uploads(api_key, api):
        pub = _since(v["published"]) if v.get("published") else None
        if pub and pub > since and TITLE_RX.search(v["title"]) and v["duration"] >= 30 and not seen(v["id"]):
            out.append(v)
    out.sort(key=lambda v: v["published"] or "")
    return out


def footage_dir(cfg) -> Path:
    return Path(cfg.paths.db).parent / "gta6_footage"


def download(cfg, v: dict, run=None) -> dict:
    """Video (<= 1080p, kuva) + englanninkieliset tekstitykset. Palauttaa {"video": Path|None, "transcript": str}."""
    from .research import vtt_to_text
    d = footage_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    run = run or (lambda cmd: proc.run(cmd, 1200, check=False, capture_output=True, text=True))
    have = [p for p in d.glob(f"rockstar_*{v['id']}*") if p.suffix.lower() in (".mp4", ".mkv", ".webm")]
    if not have:
        cmd = [sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]",
               "--restrict-filenames", "--no-playlist", "--no-progress", "--write-subs", "--write-auto-subs",
               "--sub-langs", "en,en-orig", "--sub-format", "vtt", "-o", str(d / f"rockstar_{v['id']}.%(ext)s"),
               v["url"]]
        try:
            r = run(cmd)
            if getattr(r, "returncode", 0):
                log.warning("yt-dlp: %s", ((getattr(r, "stderr", "") or "").strip().splitlines() or ["-"])[-1][:200])
        except Exception as e:  # noqa: BLE001
            log.warning("Rockstarin videon %s lataus epäonnistui: %s", v["id"], e)
        have = [p for p in d.glob(f"rockstar_*{v['id']}*") if p.suffix.lower() in (".mp4", ".mkv", ".webm")]
    subs = sorted(d.glob(f"rockstar_*{v['id']}*.vtt"), key=lambda p: (".en-orig." in p.name, p.name))
    text = vtt_to_text(subs[0].read_text(encoding="utf-8", errors="replace")) if subs else ""
    return {"video": have[0] if have else None, "transcript": text}


def _stamp(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def keyframes(video: Path, out_dir: Path, n: int = 24, max_total: int = 32, scene: float = 0.35) -> list[dict]:
    """~n kuvaa tasaisesti + kohtauksenvaihdot (select='gt(scene,0.35)'), enintään max_total, <= 1280 px JPEG."""
    from .render import probe_duration
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(video)
    if dur <= 0:
        return []
    times = [dur * (k + 0.5) / n for k in range(n)]
    try:
        r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(video), "-vf",
                      f"scale=320:-2,select='gt(scene,{scene})',showinfo", "-an", "-f", "null", "-"],
                     proc.analyze_timeout(dur), check=False, capture_output=True, text=True)
        scenes = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr or "")]
    except proc.ProcTimeout:
        scenes = []
    gap = max(dur / (max_total * 1.5), 1.0)
    for s in scenes:
        if len(times) >= max_total:
            break
        if all(abs(s - t) > gap for t in times):
            times.append(s + 0.4)  # hetki vaihdon jälkeen (ei sekoittunutta ruutua)
    frames = []
    for k, t in enumerate(sorted(min(t, dur - 0.1) for t in times)):
        p = out_dir / f"frame_{k:02d}.jpg"
        try:
            proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                      "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", str(p)], proc.PROBE)
        except Exception as e:  # noqa: BLE001
            log.warning("Avainkuva %.1f s epäonnistui: %s", t, e)
            continue
        if p.is_file() and p.stat().st_size:
            frames.append({"t": round(t, 1), "stamp": _stamp(t), "path": str(p)})
    log.info("Avainkuvia %d (%d tasaisesti, %d kohtauksenvaihtoa löytyi)", len(frames), n, len(scenes))
    return frames


def official_article(v: dict, transcript: str) -> dict:
    """Videon otsikko + kuvaus + tekstitys artikkeliksi: lainaukset sanasta sanaan, lähde kuvaukseen."""
    text = v.get("description") or ""
    if transcript:
        text = (text + "\n\nVideo dialogue (subtitles): " + transcript).strip()
    return {"title": v["title"], "source": "Rockstar Games (YouTube)", "url": v["url"], "date": v.get("published")
            or "", "text": text, "summary": (v.get("description") or "")[:300], "kind": "official"}


def related_news(cfg, v: dict) -> list[dict]:
    from . import news, research
    kws = [w for w in research.topic_keywords(v["title"]) if w not in ("trailer", "official")] or ["trailer"]
    # Rockstar Newswirellä ei ole RSS-syötettä (404) -> Google News -haku videon otsikolla + news.feeds
    return news.fetch_news(cfg, extra_feeds=[research.google_news_feed(v["title"], 3)], topic_keywords=kws,
                           lookback_days=3)


def prepare(cfg, v: dict, work: Path) -> dict:
    """Lataus + kuvat + artikkelit (ennen käsikirjoitusta). Palauttaa {"video", "frames", "articles", "info"}."""
    got = download(cfg, v)
    frames = keyframes(got["video"], work / "frames", int(bcfg(cfg).get("frames", 24))) if got["video"] else []
    try:
        arts = related_news(cfg, v)
    except Exception as e:  # noqa: BLE001
        log.warning("Uutishaku epäonnistui: %s", e)
        arts = []
    articles = [official_article(v, got["transcript"])] + arts
    info = {k: v[k] for k in ("id", "title", "url", "published", "duration")}
    info["transcript_words"] = len(got["transcript"].split())
    return {"video": got["video"], "frames": frames, "articles": articles, "info": info}


def moments(script: dict, seg_times: list[dict]) -> list[tuple[float, float]]:
    """[(kertojan aika s, lähdevideon aika s)] segmenteistä, joissa on "moment": "M:SS"."""
    out = []
    for seg, t in zip(script.get("segments") or [], seg_times):
        m = re.fullmatch(r"\s*(\d+):(\d{2})\s*", str(seg.get("moment") or ""))
        if m:
            out.append((float(t["s"]), int(m.group(1)) * 60 + float(m.group(2))))
    return out


def dry_run(cfg, api_key: str, watch_from: str | None = None) -> dict:
    """Tunnistus (ohittaa nähty-tiedon), lataus, avainkuvat ja kehote ilman Claude-kutsua."""
    from . import scriptwriter
    from .config import Cfg
    vids = find_new(cfg, api_key, watch_from=watch_from)
    print(f"\n=== BREAKDOWN dry run (Rockstar uploads after {watch_from}) ===")
    print(f"New GTA VI videos found: {len(vids)}")
    for v in vids:
        print(f"  {v['published']}  {v['duration']:>4} s  {v['title']}  {v['url']}")
    if not vids:
        return {}
    v = vids[-1]
    work = Path(cfg.paths.work_dir) / f"breakdown_dry_{v['id']}"
    prep = prepare(cfg, v, work)
    c = Cfg(dict(cfg, format="breakdown"))
    prompt = scriptwriter.build_prompt(c, None, prep["articles"], [], breakdown=prep)
    content = scriptwriter.user_content(prompt, prep["frames"])
    img_bytes = sum(Path(f["path"]).stat().st_size for f in prep["frames"])
    print(f"Picked: {v['title']}")
    print(f"Downloaded: {prep['video']}  transcript words: {prep['info']['transcript_words']}")
    print(f"Articles: {len(prep['articles'])} (1 official + {len(prep['articles']) - 1} news)")
    print(f"Frames: {len(prep['frames'])} ({img_bytes / 1e6:.1f} MB JPEG) at "
          + ", ".join(f["stamp"] for f in prep["frames"]))
    print(f"Prompt: {len(prompt):,} characters text, {len(content)} content blocks (no Claude call)\n")
    (work / "prompt.txt").write_text(prompt, encoding="utf-8")
    return {"videos": vids, "prep": prep, "prompt_chars": len(prompt), "blocks": len(content)}
