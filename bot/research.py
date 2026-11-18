"""Trenditutkimus ennen käsikirjoitusta: mistä GTA 6 -yleisö puhuu juuri nyt (YouTube + Reddit).

Kaikki lähteet ovat valinnaisia: virhe kirjataan varoituksena ja jatketaan ilman sitä lähdettä.
Nämä ovat AIHESIGNAALEJA, eivät faktalähteitä: faktat tulevat vain uutisartikkeleista (news.py).
Reddit-otsikot ovat yhteisön puhetta (mielipiteitä, huhuja), niitä ei koskaan esitetä faktana.

  python -m bot.main research                      # mitä uutisvideolle kerättäisiin (YouTube, Reddit, RSS)
  python -m bot.main research --topic "GTA 6 police vs GTA 5"   # opasvideon aihetutkimus
"""
import html
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)
UA = "catfish-gaming-bot/1.0"
REDDIT = "https://www.reddit.com"

STOP = {"the", "and", "of", "in", "on", "to", "a", "an", "vs", "versus", "what", "is", "are", "not", "at", "with",
        "for", "its", "it", "how", "things", "do", "first", "day", "one", "gta", "gta6", "vi", "grand", "theft",
        "auto", "rockstar", "game", "games"}


def _rcfg(cfg):
    return cfg.get("research") or {}


def _get_json(url: str, params: dict | None = None):
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- YouTube

def youtube_signals(vids: list[dict], n: int = 8) -> list[dict]:
    """trending.find_trending-tulokset -> kevyet signaalit kehotteeseen (otsikko, kanava, katselut, outlier)."""
    return [{"title": v["title"], "channel": v["channel"], "views": int(v.get("views", 0)),
             "outlier": float(v.get("outlier", 1.0) or 1.0), "url": v.get("url")} for v in vids[:n]]


def youtube_topic(cfg, api_key: str, topic: str, n: int = 8) -> list[dict]:
    """Opasvideo: YouTube-haku aiheella (pidempi aikaikkuna), outlier-pisteytys kuten trendihaussa."""
    from . import trending
    if not api_key:
        return []
    r = _rcfg(cfg)
    q = topic if re.search(r"gta\s*(6|vi)", topic, re.I) else f"GTA 6 {topic}"
    try:
        vids = trending.find_trending(cfg, api_key, queries=[q],
                                      lookback_hours=float(r.get("topic_lookback_days", 30)) * 24,
                                      min_views=int(r.get("topic_min_views", 2000)))
    except Exception as e:  # noqa: BLE001 - signaalit ovat valinnaisia
        log.warning("YouTube-aihehaku epäonnistui: %s", e)
        return []
    return youtube_signals(vids, n)


# ---------------------------------------------------------------- Reddit

def _posts(listing: dict) -> list[dict]:
    out = []
    for ch in (listing or {}).get("data", {}).get("children", []):
        d = ch.get("data") or {}
        if d.get("stickied") or d.get("over_18"):
            continue
        out.append({"id": d.get("id"), "title": (d.get("title") or "").strip(), "sub": d.get("subreddit", ""),
                    "score": int(d.get("score") or 0), "comments": int(d.get("num_comments") or 0),
                    "flair": d.get("link_flair_text") or "", "text": (d.get("selftext") or "")[:500],
                    "url": REDDIT + (d.get("permalink") or "")})
    return out


def _rank(posts: list[dict], n: int) -> list[dict]:
    seen, out = set(), []
    for p in sorted(posts, key=lambda p: p["score"] + p["comments"], reverse=True):
        if p["id"] in seen or not p["title"]:
            continue
        seen.add(p["id"])
        out.append(p)
    return out[:n]


def reddit_buzz(cfg, n: int = 15) -> list[dict]:
    """hot + top (päivä) jokaisesta `research.reddit_subs`-subista, vain news.keywords-osumat.
    `research.reddit_on_topic_subs` (esim. r/GTA6): koko subi on aiheesta -> kaikki postaukset kelpaavat."""
    r = _rcfg(cfg)
    subs = r.get("reddit_subs", ["GTA6", "GTA", "rockstar"]) or []
    on_topic = {s.lower() for s in (r.get("reddit_on_topic_subs") or [])}
    kws = [k.lower() for k in cfg.news.keywords]
    posts = []
    for sub in subs:
        for path, params in (("hot.json", {"limit": 50}), ("top.json", {"t": "day", "limit": 50})):
            try:
                got = _posts(_get_json(f"{REDDIT}/r/{sub}/{path}", params))
            except (requests.RequestException, ValueError) as e:
                log.warning("Reddit r/%s/%s epäonnistui: %s", sub, path, e)
                continue
            for p in got:
                text = (p["title"] + " " + p["text"] + " " + p["flair"]).lower()
                if sub.lower() in on_topic or any(k in text for k in kws):
                    posts.append(p)
            time.sleep(1.0)  # julkinen JSON: kohtuullinen tahti
    out = _rank(posts, n)
    log.info("Reddit: %d osumaa, %d mukaan", len(posts), len(out))
    return out


def reddit_topic(cfg, topic: str, n: int = 15) -> list[dict]:
    """Opasvideo: r/GTA6-haku aiheella (top, kuukausi)."""
    r = _rcfg(cfg)
    sub = r.get("reddit_search_sub", "GTA6")
    q = " ".join(w for w in re.findall(r"[A-Za-z0-9']+", topic) if w.lower() not in STOP and not w.isdigit())
    q = q or topic
    try:
        posts = _posts(_get_json(f"{REDDIT}/r/{sub}/search.json",
                                 {"q": q, "restrict_sr": 1, "sort": "top", "t": "month", "limit": 50}))
    except (requests.RequestException, ValueError) as e:
        log.warning("Reddit-haku %r epäonnistui: %s", q, e)
        return []
    out = _rank(posts, n)
    log.info("Reddit-haku %r: %d postausta, %d mukaan", q, len(posts), len(out))
    return out


# ---------------------------------------------------------------- opasvideon aihe

def topic_keywords(topic: str | dict) -> list[str]:
    """Aiheen avainsanat uutisten suodatukseen: configin `keywords` tai aiheen sisältösanat."""
    if isinstance(topic, dict) and topic.get("keywords"):
        return [str(k).lower() for k in topic["keywords"]]
    text = topic["topic"] if isinstance(topic, dict) else topic
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOP and len(w) >= 4 and not w.isdigit()]


def google_news_feed(topic: str, days: int = 30) -> str:
    q = topic if re.search(r"gta\s*(6|vi)", topic, re.I) else f"GTA 6 {topic}"
    return (f"https://news.google.com/rss/search?q={quote_plus(q)}+when:{int(days)}d"
            "&hl=en-US&gl=US&ceid=US:en")


# ---------------------------------------------------------------- kokonaisuus

def collect(cfg, yt_key: str = "", vids: list[dict] | None = None, topic: str | None = None) -> dict:
    """{"youtube": [...], "reddit": [...]} - uutisvideolle trendivideoista (vids) + subien kuumimmat,
    opasvideolle (topic) aiheen YouTube- ja Reddit-haut."""
    r = _rcfg(cfg)
    if not r.get("enabled", True):
        return {"youtube": youtube_signals(vids or []), "reddit": []}
    n_yt, n_rd = int(r.get("youtube_top", 8)), int(r.get("reddit_top", 15))
    if topic:
        yt = youtube_topic(cfg, yt_key, topic, n_yt)
        rd = reddit_topic(cfg, topic, n_rd)
        if len(rd) < 5:  # aihehaku antoi vähän -> yleinen pöhinä täydentää
            rd += [p for p in reddit_buzz(cfg, n_rd) if p["id"] not in {x["id"] for x in rd}][:n_rd - len(rd)]
    else:
        yt = youtube_signals(vids or [], n_yt)
        rd = reddit_buzz(cfg, n_rd)
    return {"youtube": yt, "reddit": rd}


def prompt_block(signals: dict | None) -> str:
    """WHAT PEOPLE ARE TALKING ABOUT -lohko käsikirjoituskehotteeseen ('' jos ei signaaleja)."""
    if not signals or not (signals.get("youtube") or signals.get("reddit")):
        return ""
    lines = ["WHAT PEOPLE ARE TALKING ABOUT (topic signals only, NOT sources of facts):"]
    if signals.get("youtube"):
        lines.append("YouTube videos doing far better than their channel's normal (outliers):")
        lines += [f"- \"{v['title']}\" ({v['channel']}, {v['views']:,} views, {v['outlier']:.1f}x the channel's "
                  "normal)" for v in signals["youtube"]]
    if signals.get("reddit"):
        subs = sorted({p["sub"] for p in signals["reddit"] if p.get("sub")})
        lines.append("Community buzz on Reddit (" + ", ".join(f"r/{s}" for s in subs) + "; fan opinions, questions "
                     "and rumors, never facts):")
        lines += [f"- [{p['score']:,} upvotes, {p['comments']:,} comments] {p['title']}" for p in signals["reddit"]]
    lines.append("Use these to pick the angle with the MOST heat (what several signals agree on, what people ask or "
                 "argue about) and to answer the community's real questions. Every fact, number, date and quote "
                 "still comes ONLY from ARTICLES. Never present a Reddit post or a video title as a fact; if you "
                 "mention a community rumor, call it a rumor and say whether the ARTICLES support it. Never copy "
                 "a video's title, structure or jokes.")
    return "\n".join(lines) + "\n\n"


def dry_run(cfg, yt_key: str, topic: str | None = None) -> dict:
    """Tulostaa mitä kerättäisiin: YouTube, Reddit ja RSS (määrät + muutama otsikko). Ei Claudea, ei renderöintiä."""
    from . import news, trending
    vids = []
    if not topic and yt_key:
        try:
            vids = trending.find_trending(cfg, yt_key)
        except Exception as e:  # noqa: BLE001
            log.warning("Trendihaku epäonnistui: %s", e)
    sig = collect(cfg, yt_key, vids=vids, topic=topic)
    if topic:
        days = int(_rcfg(cfg).get("topic_lookback_days", 30))
        arts = news.fetch_news(cfg, extra_feeds=[google_news_feed(topic, days)],
                               topic_keywords=topic_keywords(topic), lookback_days=days)
    else:
        arts = news.fetch_news(cfg)
    print(f"\n=== RESEARCH {'topic: ' + topic if topic else '(news video)'} ===")
    print(f"YouTube: {len(vids) if not topic else len(sig['youtube'])} relevant videos, {len(sig['youtube'])} as signals")
    for v in sig["youtube"][:5]:
        print(f"  {v['outlier']:5.1f}x  {v['views']:>9,}  {v['title']}  ({v['channel']})")
    print(f"Reddit: {len(sig['reddit'])} posts as community buzz")
    for p in sig["reddit"][:5]:
        print(f"  {p['score']:>6,} pts {p['comments']:>5,} c  r/{p['sub']}: {p['title'][:90]}")
    from .news import _hits
    kws = topic_keywords(topic) if topic else []

    def on_topic(a):
        return bool(kws) and (_hits(a["title"] + " " + a.get("summary", ""), kws) >= 1
                              or _hits(a.get("text", ""), kws) >= 2)
    if kws:
        arts_show = sorted(arts, key=on_topic, reverse=True)
        print(f"RSS articles: {len(arts)} picked, {sum(1 for a in arts if a.get('text'))} with full text, "
              f"{sum(1 for a in arts if on_topic(a))} on topic (* = on topic, keywords {kws})")
    else:
        arts_show = arts
        print(f"RSS articles: {len(arts)} picked, {sum(1 for a in arts if a.get('text'))} with full text")
    for a in arts_show[:8]:
        mark = "* " if on_topic(a) else "  "
        print(f"  {mark}[{a['source']}] {a['title'][:90]}{'' if a.get('text') else '  (headline only)'}")
    print()
    return {"signals": sig, "articles": arts}


# ---------------------------------------------------------------- julkaisupäivä: ensimmäisten pelaajien videot
# research: launch_streams (schedule.once). YouTube-haku julkaisuhetken jälkeen, englanninkieliset tekstitykset
# yt-dlp:llä, vinkkikohdat talteen. Nämä ovat LÄHTEITÄ (mitä pelaajat kertoivat, nimellä), mutta heidän
# pelikuvaansa ei koskaan käytetä: videon kuva on aina Rockstarin virallista materiaalia.

LAUNCH_AFTER = "2026-11-18T11:00:00Z"   # Uusi-Seelanti avautuu 13:00 Helsingin aikaa
LAUNCH_QUERIES = ["GTA 6 gameplay", "GTA 6 first hour", "GTA 6 tips", "GTA 6 things to do first",
                  "GTA 6 New Zealand", "GTA 6 live"]
TIP_WORDS = ["tip", "tips", "first", "make sure", "don't", "do not", "you can", "you should", "you need", "unlock",
             "unlocked", "money", "cash", "car", "cars", "cop", "cops", "police", "wanted", "phone", "map", "save",
             "atm", "bank", "weapon", "gun", "ammo", "store", "shop", "mission", "side", "skill", "upgrade", "fast",
             "free", "early", "start", "beginning", "recommend", "trick", "secret", "hidden", "gas", "fuel"]
MIN_CREATORS = 3
MIN_WORDS_KEPT = 150


def _lcfg(cfg) -> dict:
    return dict(_rcfg(cfg).get("launch") or {})


def _yt_api(path: str, params: dict) -> dict:
    from .trending import API
    r = requests.get(f"{API}/{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def launch_search(cfg, api_key: str, after: str, queries: list[str] | None = None, api=_yt_api,
                  n: int = 10) -> list[dict]:
    """search.list (publishedAfter=after, order=viewCount, relevanceLanguage=en) jokaiselle haulle sekä tavallisina
    videoina että päättyneinä livelähetyksinä (eventType=completed); oma kanava ja Shortsit pois; yksi video per
    tekijä; n katsotuinta."""
    from .trending import _iso_duration_s
    lc = _lcfg(cfg)
    own = (cfg.get("channel") or {}).get("own_channel_id")
    ids: list[str] = []
    for q in queries or lc.get("queries") or LAUNCH_QUERIES:
        for event in (None, "completed"):
            params = dict(part="id", q=q, type="video", order="viewCount", publishedAfter=after,
                          relevanceLanguage="en", maxResults=25, key=api_key)
            if event:
                params["eventType"] = event
            try:
                items = api("search", params).get("items", [])
            except (requests.RequestException, ValueError) as e:
                log.warning("Julkaisupäivän haku %r (%s) epäonnistui: %s", q, event or "uploads", e)
                continue
            ids += [it["id"]["videoId"] for it in items if (it.get("id") or {}).get("videoId")]
    ids = list(dict.fromkeys(ids))
    vids = []
    for i in range(0, len(ids), 50):
        try:
            items = api("videos", dict(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]),
                                       key=api_key)).get("items", [])
        except (requests.RequestException, ValueError) as e:
            log.warning("videos.list epäonnistui: %s", e)
            continue
        for v in items:
            sn, stt = v.get("snippet") or {}, v.get("statistics") or {}
            dur = _iso_duration_s((v.get("contentDetails") or {}).get("duration", ""))
            title = sn.get("title") or ""
            if sn.get("channelId") == own or "#shorts" in title.lower() or (dur and dur <= 180):
                continue
            if sn.get("liveBroadcastContent") in ("live", "upcoming"):
                continue  # käynnissä oleva lähetys: ei vielä tekstityksiä
            # otsikon on oltava GTA 6:sta (kuvauksen hakusanatäyte ei riitä); GTA 5 / San Andreas -striimit pois
            if not re.search(r"gta\s*(6|vi)\b|gta6|grand theft auto (vi|6)\b", title, re.I) \
                    or re.search(r"#gta5|#gtav\b|\bgta\s*(5|v)\s+live|san andreas", title, re.I):
                continue
            vids.append({"id": v["id"], "title": title, "channel": sn.get("channelTitle") or "",
                         "channel_id": sn.get("channelId"), "views": int(stt.get("viewCount") or 0),
                         "duration": dur, "published": sn.get("publishedAt"),
                         "url": f"https://www.youtube.com/watch?v={v['id']}"})
    vids.sort(key=lambda v: v["views"], reverse=True)
    out, seen = [], set()
    for v in vids:
        if v["channel_id"] in seen:
            continue
        seen.add(v["channel_id"])
        out.append(v)
    log.info("Julkaisupäivän haku: %d videota, %d tekijää, %d mukaan", len(vids), len(seen), min(n, len(out)))
    return out[:n]


def vtt_to_text(vtt: str) -> str:
    """WebVTT (myös YouTuben automaattinen, rullaava tekstitys) -> puhdas teksti ilman toistoja."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", vtt.replace("\r", "")):
        for line in block.split("\n"):
            line = line.strip()
            if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")) \
                    or re.fullmatch(r"\d+", line):
                continue
            line = re.sub(r"<[^>]+>", "", line)
            line = re.sub(r"\[(music|applause|laughter|__)\]", " ", line, flags=re.I)
            line = re.sub(r"\s+", " ", html.unescape(line).replace(" ", " ").replace(">>", " ")).strip()
            if not line:
                continue
            if out and (line == out[-1] or out[-1].endswith(line)):
                continue
            if out and line.startswith(out[-1]):
                out[-1] = line
                continue
            out.append(line)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def tip_windows(text: str, max_words: int = 2500, radius: int = 60, keywords: list[str] | None = None) -> str:
    """Vinkkikohdat tekstistä: +-radius sanaa jokaisen avainsanan ympäriltä, päällekkäiset yhdistetään, tiheimmin
    osumia sisältävät ikkunat ensin kunnes max_words täyttyy; tulos aikajärjestyksessä ja sanasta sanaan
    (lainauskortit tarkistetaan tätä tekstiä vasten)."""
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    kws = [k.lower().split() for k in (keywords or TIP_WORDS)]
    low = [re.sub(r"[^a-z0-9']", "", w.lower()) for w in words]
    hits = []
    for i in range(len(low)):
        for k in kws:
            if low[i:i + len(k)] == k:
                hits.append(i)
                break
    wins: list[list[int]] = []
    for i in hits:
        s, e = max(i - radius, 0), min(i + radius, len(words))
        if wins and s <= wins[-1][1]:
            wins[-1][1] = max(wins[-1][1], e)
            wins[-1][2] += 1
        else:
            wins.append([s, e, 1])
    picked, total = [], 0
    for s, e, _n in sorted(wins, key=lambda w: (-(w[2] / max(w[1] - w[0], 1)), w[0])):
        if total >= max_words:
            break
        e = min(e, s + (max_words - total))
        picked.append((s, e))
        total += e - s
    return " ... ".join(" ".join(words[s:e]) for s, e in sorted(picked))


def _impersonate_args() -> list[str]:
    """Selaimen TLS-sormenjälki (curl_cffi) tekstityshakuun: YouTube rajoittaa (HTTP 429) muuten yt-dlp:n
    oletusyhteyttä. Omistaja hyväksyi kiertämisen 17.11.2026. Ilman curl_cffi:tä haetaan tavallisesti."""
    import importlib.util
    return ["--impersonate", "chrome", "--sleep-subtitles", "3"] if importlib.util.find_spec("curl_cffi") else []


def _ytdlp_cmd(url: str, out_dir: Path) -> list[str]:
    import sys
    return [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-auto-subs", "--write-subs",
            "--sub-langs", "en,en-orig", "--sub-format", "vtt", "--no-playlist", "--no-progress",
            *_impersonate_args(), "-o", str(out_dir / "%(id)s.%(ext)s"), url]


class RateLimited(RuntimeError):
    """YouTube palautti tekstityshausta HTTP 429 kaikilla yrityksillä (IP rajoitettu)."""


def fetch_transcript(url: str, vid: str, out_dir: Path, run=None, sleep=time.sleep, tries: int = 3) -> str:
    """Englanninkieliset tekstitykset yt-dlp:llä (käsin tehdyt ensin, sitten automaattiset). HTTP 429 -> odotus ja
    uusinta (30 s, 60 s); jos 429 jatkuu, RateLimited. Haku tehdään selaimena (_impersonate_args). Palauttaa puhtaan tekstin tai ''."""
    from . import proc
    run = run or (lambda cmd: proc.run(cmd, 180, check=False, capture_output=True, text=True))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for k in range(tries):
        try:
            r = run(_ytdlp_cmd(url, out_dir))
        except proc.ProcTimeout as e:
            log.warning("yt-dlp aikaraja (%s): %s", vid, e)
            r = None
        files = sorted(out_dir.glob(f"{vid}*.vtt"), key=lambda p: (".en-orig." in p.name, p.name))
        if files:
            return vtt_to_text(files[0].read_text(encoding="utf-8", errors="replace"))
        err = (getattr(r, "stderr", "") or "") + (getattr(r, "stdout", "") or "")
        if "429" in err or "Too Many Requests" in err:
            if k + 1 >= tries:
                raise RateLimited(f"YouTube rajoitti tekstityshakua (HTTP 429) {tries} yrityksellä: {vid}")
            wait = 30 * (k + 1)
            log.warning("YouTube rajoitti tekstityshakua (429) -> odotetaan %d s (%s)", wait, vid)
            sleep(wait)
            continue
        if r is not None:
            log.info("Ei englanninkielisiä tekstityksiä: %s (%s)", vid, (err.strip().splitlines() or ["-"])[-1][:160])
        return ""
    return ""


def launch_streams(cfg, api_key: str, after: str | None = None, work_dir: Path | None = None, search=None,
                   transcript=None, n: int | None = None) -> dict:
    """Ensimmäisten pelaajien videot + vinkkikohdat tekstityksistä. Palauttaa
    {"after", "videos": [...], "creators": [{creator, title, url, views, words_total, words_kept, text}]}."""
    lc = _lcfg(cfg)
    after = after or lc.get("after") or LAUNCH_AFTER
    n = int(n or lc.get("top", 10))
    max_words = int(lc.get("max_words_per_creator", 2500))
    work_dir = Path(work_dir or Path(cfg.paths.work_dir) / "launch_subs")
    vids = (search or launch_search)(cfg, api_key, after, n=n)
    creators, limited = [], 0
    for v in vids:
        if limited >= 2:  # YouTube rajoittaa tätä IP:tä: ei jäädä odottelemaan jokaista videota
            v["words_total"] = v["words_kept"] = 0
            v["skipped"] = "rate-limited"
            continue
        try:
            text = (transcript or fetch_transcript)(v["url"], v["id"], work_dir)
            limited = 0
        except RateLimited as e:
            limited += 1
            log.warning("%s", e)
            text = ""
        except Exception as e:  # noqa: BLE001 - yksi video ei kaada tutkimusta
            log.warning("Tekstityksen haku epäonnistui (%s): %s", v["id"], e)
            text = ""
        kept = tip_windows(text, max_words) if text else ""
        nk = len(kept.split())
        v["words_total"], v["words_kept"] = len(text.split()), nk
        if nk >= MIN_WORDS_KEPT:
            creators.append({"creator": v["channel"], "title": v["title"], "url": v["url"], "views": v["views"],
                             "words_total": len(text.split()), "words_kept": nk, "text": kept})
    log.info("Julkaisupäivän tutkimus: %d videota, %d tekijää käyttökelpoisilla tekstityksillä", len(vids),
             len(creators))
    if limited >= 2:
        log.error("YouTube rajoittaa tekstityshakuja (HTTP 429) tältä koneelta -> tekstityksiä ei saatu; "
                  "käytetään uutisia")
    return {"after": after, "videos": vids, "creators": creators, "rate_limited": limited >= 2}


def launch_articles(launch: dict) -> list[dict]:
    """Tekijöiden tekstitykset artikkeleiksi (lainaukset tarkistetaan tätä tekstiä vasten, lähteet kuvaukseen)."""
    return [{"title": c["title"], "source": c["creator"], "url": c["url"], "date": "launch day",
             "text": c["text"], "summary": "", "kind": "transcript"} for c in launch.get("creators") or []]


def launch_research(cfg, api_key: str, retry_at: list[str] | None = None, now_fn=None, sleep=time.sleep,
                    run=None, **kw) -> dict:
    """launch_streams; jos alle MIN_CREATORS tekijää, odotetaan retry_at-kellonaikoihin (schedule.timezone,
    esim. 15:30 ja 16:00; menneet ohitetaan) ja haetaan uudelleen. Tulokseen early=True, jos tekijöitä jäi vähän."""
    import datetime as dt
    from . import scheduler
    run = run or launch_streams
    zone = scheduler.tz(cfg)
    now_fn = now_fn or (lambda: dt.datetime.now(zone))
    res = run(cfg, api_key, **kw)
    for hhmm in retry_at or []:
        if len(res["creators"]) >= MIN_CREATORS:
            break
        now = now_fn()
        t = scheduler._at(now.date(), hhmm, zone)
        if t <= now:
            continue
        log.warning("Vain %d tekijää tekstityksillä -> uusi haku %s (tekstitykset ilmestyvät viiveellä)",
                    len(res["creators"]), hhmm)
        sleep((t - now).total_seconds())
        res = run(cfg, api_key, **kw)
    res["early"] = len(res["creators"]) < MIN_CREATORS
    if res["early"]:
        log.warning("Julkaisupäivän tutkimus: vain %d tekijää -> uutiset + saatavilla olevat tekstitykset",
                    len(res["creators"]))
    return res


ROCKSTAR_HANDLE = "RockstarGames"
FOOTAGE_AFTER = "2026-11-18T00:00:00Z"


def rockstar_footage(cfg, api_key: str, after: str = FOOTAGE_AFTER, api=_yt_api, run=None,
                     out_dir: Path | None = None) -> list[Path]:
    """Rockstarin oman kanavan uudet GTA VI -videot (otsikossa "Grand Theft Auto VI" tai "GTA VI") taustaklipeiksi:
    data/gta6_footage/rockstar_<id>_<otsikko>.mp4, vain kuva, <= 1080p (avc1 ensin). Jo ladatut ohitetaan.
    Virheet kirjataan, eivät kaada ajoa. Palauttaa uudet tiedostot."""
    from . import proc
    import sys
    if not api_key:
        log.warning("Rockstarin videoita ei haeta: YOUTUBE_API_KEY puuttuu")
        return []
    out_dir = Path(out_dir or Path(cfg.paths.db).parent / "gta6_footage")
    out_dir.mkdir(parents=True, exist_ok=True)
    run = run or (lambda cmd: proc.run(cmd, 900, check=False, capture_output=True, text=True))
    try:
        items = api("channels", dict(part="id", forHandle=ROCKSTAR_HANDLE, key=api_key)).get("items") or []
        if not items:
            log.warning("Rockstarin kanavaa ei löytynyt (@%s)", ROCKSTAR_HANDLE)
            return []
        found = api("search", dict(part="snippet", channelId=items[0]["id"], publishedAfter=after, type="video",
                                   order="date", maxResults=25, key=api_key)).get("items") or []
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Rockstarin videoiden haku epäonnistui: %s", e)
        return []
    new = []
    for it in found:
        vid = (it.get("id") or {}).get("videoId")
        title = (it.get("snippet") or {}).get("title") or ""
        if not vid or not re.search(r"grand theft auto vi\b|gta vi\b", title, re.I):
            continue
        if list(out_dir.glob(f"rockstar_*{vid}*")) or list((out_dir.parent / "footage_vertical").glob(f"rockstar_*{vid}*")):
            log.info("Rockstarin video on jo ladattu: %s", title)
            continue
        cmd = [sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]",
               "--restrict-filenames", "--no-playlist", "--no-progress",
               "-o", str(out_dir / f"rockstar_{vid}_%(title).80s.%(ext)s"), f"https://www.youtube.com/watch?v={vid}"]
        try:
            r = run(cmd)
        except Exception as e:  # noqa: BLE001
            log.warning("Rockstarin videon %s lataus epäonnistui: %s", vid, e)
            continue
        got = [p for p in out_dir.glob(f"rockstar_*{vid}*") if not p.name.endswith((".part", ".ytdl"))]
        from .render import _is_landscape
        for g in [g for g in got if g.suffix.lower() in (".mp4", ".mkv", ".webm") and not _is_landscape(g)]:
            vdir = out_dir.parent / "footage_vertical"  # pysty-/neliöversio: ei taustapooliin (sumea 16:9-rajauksena)
            vdir.mkdir(exist_ok=True)
            g.replace(vdir / g.name)
            log.info("Rockstarin %s on pysty-/neliövideo -> %s", g.name, vdir)
        if got:
            log.info("Ladattu Rockstarin virallinen video: %s -> %s", title, got[0].name)
            new += got
        else:
            log.warning("Rockstarin videon %s lataus epäonnistui: %s", vid,
                        ((getattr(r, "stderr", "") or "").strip().splitlines() or ["-"])[-1][:200])
    log.info("Rockstarin uudet GTA VI -videot: %d ladattu (%s jälkeen)", len(new), after)
    return new


def launch_dry_run(cfg, yt_key: str, after: str | None = None) -> dict:
    res = launch_streams(cfg, yt_key, after=after)
    print(f"\n=== RESEARCH launch_streams (publishedAfter {res['after']}) ===")
    print(f"Videos (top by views, one per creator, no Shorts / own channel): {len(res['videos'])}")
    for v in res["videos"]:
        print(f"  {v['views']:>10,} views {v['duration'] // 60:>4} min  {v['title'][:70]}  ({v['channel']})  "
              f"transcript {v.get('words_total', 0):,} words, kept {v.get('words_kept', 0):,}")
    print(f"Transcripts fetched: {sum(1 for v in res['videos'] if v.get('words_total'))}/{len(res['videos'])}")
    print(f"Creators with usable transcripts (>= {MIN_WORDS_KEPT} words kept): {len(res['creators'])}"
          + ("" if len(res["creators"]) >= MIN_CREATORS else
             f"  (< {MIN_CREATORS}: launch job retries at 15:30/16:00, then news fallback)"))
    print(f"Words kept in total: {sum(c['words_kept'] for c in res['creators']):,}")
    if res.get("rate_limited"):
        print("YouTube answered HTTP 429 (Too Many Requests) to the subtitle downloads from this IP; the rest were "
              "skipped. The launch job then falls back to news articles.")
    print()
    return res


if __name__ == "__main__":
    import argparse
    import sys

    from .config import env, load
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    c = load()
    dry_run(c, env("YOUTUBE_API_KEY", required=False), a.topic)
