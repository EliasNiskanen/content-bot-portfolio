"""Etsii kuumimmat GTA 6 -videot YouTube Data API v3:lla.

Pisteytys = katselut tunnissa julkaisusta (velocity) * sitoutumiskerroin * outlier-kerroin
(outlier = videon katselut / kanavan keskimääräiset katselut per video).
Kiintiö: jokainen haku = 100 yksikköä, päivässä 10 000 -> 3 hakua/päivä on reilusti alle.
"""
import datetime as dt
import logging
import math
import re

import requests

API = "https://www.googleapis.com/youtube/v3"
log = logging.getLogger(__name__)


def _iso_duration_s(s: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def find_trending(cfg, api_key: str, queries: list[str] | None = None, lookback_hours: float | None = None,
                  min_views: int | None = None) -> list[dict]:
    """queries/lookback_hours/min_views: oletukset configista (trending.*); opasvideo hakee omalla aiheellaan."""
    t = cfg.trending
    now = dt.datetime.now(dt.timezone.utc)
    hours_back = float(lookback_hours or t.lookback_hours)
    after = (now - dt.timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    min_views = t.min_views if min_views is None else min_views
    ids: set[str] = set()
    for q in queries or t.queries:
        try:
            r = requests.get(f"{API}/search", timeout=20, params=dict(
                part="id", q=q, type="video", order="viewCount", publishedAfter=after,
                maxResults=t.max_results_per_query, regionCode=t.region_code,
                relevanceLanguage=t.relevance_language, key=api_key))
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("YouTube-haku %r epäonnistui: %s", q, e)
            continue
        ids |= {it["id"]["videoId"] for it in r.json().get("items", [])}
    # YouTuben Gaming-trendilista (1 yksikkö / maa): mukaan GTA-videot, jotka nousevat juuri nyt eniten
    for region in (t.get("chart_regions") or ["US", "GB"]):
        try:
            r = requests.get(f"{API}/videos", timeout=20, params=dict(
                part="id", chart="mostPopular", videoCategoryId="20", regionCode=region, maxResults=50, key=api_key))
            r.raise_for_status()
            ids |= {it["id"] for it in r.json().get("items", [])}
        except requests.RequestException as e:
            log.warning("YouTube-trendilista %s epäonnistui: %s", region, e)

    videos = []
    id_list = list(ids)
    for i in range(0, len(id_list), 50):
        r = requests.get(f"{API}/videos", timeout=20, params=dict(
            part="snippet,statistics,contentDetails", id=",".join(id_list[i:i + 50]), key=api_key))
        r.raise_for_status()
        videos += r.json().get("items", [])

    # kanavien keskimääräiset katselut -> "outlier" = video sai X kertaa kanavan normaalin (1 yksikkö / 50 kanavaa)
    ch_avg: dict[str, float] = {}
    ch_ids = sorted({v["snippet"]["channelId"] for v in videos})
    for i in range(0, len(ch_ids), 50):
        try:
            r = requests.get(f"{API}/channels", timeout=20, params=dict(
                part="statistics", id=",".join(ch_ids[i:i + 50]), key=api_key))
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("Kanavatilastot epäonnistuivat: %s", e)
            break
        for c in r.json().get("items", []):
            s = c.get("statistics", {})
            n = max(int(s.get("videoCount", 0) or 0), 1)
            ch_avg[c["id"]] = int(s.get("viewCount", 0) or 0) / n

    exclude = set(t.exclude_channel_ids or [])
    if cfg.channel.own_channel_id:
        exclude.add(cfg.channel.own_channel_id)
    must = [w.lower() for w in t.must_contain_any]

    out = []
    for v in videos:
        sn, st = v["snippet"], v.get("statistics", {})
        if sn["channelId"] in exclude:
            continue
        text = (sn["title"] + " " + sn.get("description", "")[:300]).lower()
        if must and not any(w in text for w in must):
            continue
        # otsikossa pitää olla itse aihe (ei riitä että kuvaus mainitsee GTA:n), ja videon pitää olla englanniksi
        title_kw = [w.lower() for w in t.get("title_must_contain_any", [])]
        if title_kw and not any(w in sn["title"].lower() for w in title_kw):
            continue
        lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").lower()
        want = (t.get("relevance_language") or "").lower()
        if want and lang and not lang.startswith(want):
            continue
        if want == "en" and sum(ord(c) > 0x24F for c in sn["title"]) > len(sn["title"]) * 0.3:
            continue
        views = int(st.get("viewCount", 0))
        if views < min_views:
            continue
        # Shortsit ja lyhyet klipit eivät ole uutisaiheita -> pois (Shorts voi olla jopa 3 min)
        dur = _iso_duration_s(v.get("contentDetails", {}).get("duration", ""))
        if dur < t.get("min_duration_s", 180) or "#shorts" in sn["title"].lower():
            continue
        likes, comments = int(st.get("likeCount", 0)), int(st.get("commentCount", 0))
        published = dt.datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        hours = max((now - published).total_seconds() / 3600, 1.0)
        engagement = (likes + 3 * comments) / max(views, 1)
        avg = ch_avg.get(sn["channelId"])
        outlier = views / max(avg, 1.0) if avg else 1.0
        # aihe kiinnostaa kun pieni kanava saa moninkertaisesti normaalin -> painotetaan outliereita
        score = views / hours * (1 + min(engagement * 10, 1.5)) * (1 + math.log10(1 + min(outlier, 100)))
        out.append(dict(
            id=v["id"], title=sn["title"], channel=sn["channelTitle"], channel_id=sn["channelId"],
            description=sn.get("description", "")[:1500], tags=sn.get("tags", [])[:20],
            views=views, likes=likes, comments=comments, hours_old=round(hours, 1),
            duration_s=_iso_duration_s(v.get("contentDetails", {}).get("duration", "")),
            url=f"https://www.youtube.com/watch?v={v['id']}", score=round(score, 1), outlier=round(outlier, 2),
        ))
    out.sort(key=lambda x: x["score"], reverse=True)
    log.info("Löytyi %d relevanttia trendivideota", len(out))
    for v in out[:5]:
        log.info("  %8.0f pts | %4.1fx | %s (%s, %d views)", v["score"], v["outlier"], v["title"], v["channel"],
                 v["views"])
    return out


def mock_trending() -> list[dict]:
    return [dict(
        id="MOCK123", title="GTA 6 Trailer 3 Leak? Everything We Know So Far", channel="SomeGTAChannel",
        channel_id="UCmock", description="Breaking down the latest GTA 6 rumors...", tags=["gta 6"],
        views=250000, likes=12000, comments=3400, hours_old=14.0, duration_s=612,
        url="https://www.youtube.com/watch?v=MOCK123", score=math.inf, outlier=12.0)]


def cached(cfg, api_key: str, max_age_h: float = 3.0) -> list[dict]:
    """find_trending, mutta saman päivän tuore tulos (data/trending_cache.json) käytetään uudelleen: uutis- ja
    opasvideo jakavat haun, eikä search.list-kiintiö kulu kahdesti."""
    import json
    import time
    from pathlib import Path
    p = Path(cfg.paths.db).parent / "trending_cache.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(d.get("t", 0)) < max_age_h * 3600 and d.get("videos"):
            return d["videos"]
    except (OSError, ValueError):
        pass
    vids = find_trending(cfg, api_key)
    try:
        p.write_text(json.dumps({"t": time.time(), "videos": vids}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return vids
