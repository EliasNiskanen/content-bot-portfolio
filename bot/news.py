"""Hakee tuoreet GTA 6 -uutisartikkelit RSS-syötteistä ja poimii niiden tekstin."""
import calendar
import html
import logging
import re
import time
from urllib.parse import urlparse

import feedparser
import requests

log = logging.getLogger(__name__)
logging.getLogger("trafilatura").setLevel(logging.CRITICAL)  # 403-rivit käsitellään itse

try:
    import trafilatura
    from trafilatura.settings import use_config as _tf_config
except ImportError:  # pragma: no cover
    trafilatura = None

FEED_TIMEOUT = (10, 20)   # (yhteys, luku) s: syötteen haku requestsilla, feedparser jäsentää rungon
ARTICLE_TIMEOUT = 20      # trafilatura DOWNLOAD_TIMEOUT
FEED_UA = feedparser.USER_AGENT  # sama tunniste kuin ennen (feedparser.parse(url))
_TF_CFG = None


def _tf():
    """trafilaturan asetukset: eksplisiittinen latausaikaraja (DOWNLOAD_TIMEOUT)."""
    global _TF_CFG
    if _TF_CFG is None and trafilatura is not None:
        c = _tf_config()
        c.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(ARTICLE_TIMEOUT))
        _TF_CFG = c
    return _TF_CFG


def fetch_feed(url: str, timeout=FEED_TIMEOUT):
    """Syöte aikarajalla: feedparser.parse(url) ei tunne aikarajaa -> haetaan runko itse."""
    r = requests.get(url, timeout=timeout, headers={"User-Agent": FEED_UA,
                                                    "Accept": "application/rss+xml, application/xml, */*"})
    r.raise_for_status()
    return feedparser.parse(r.content, response_headers={k.lower(): v for k, v in r.headers.items()})


def _source_name(entry, url: str) -> str:
    src = entry.get("source", {})
    if isinstance(src, dict) and src.get("title"):
        return src["title"]
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0].upper() if host else "News"


def _extract(url: str) -> tuple[str, str | None]:
    if trafilatura is None:
        return "", None
    try:
        page = trafilatura.fetch_url(url, config=_tf())
        if not page:
            return "", None
        text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        meta = trafilatura.extract_metadata(page)
        image = getattr(meta, "image", None) if meta else None
        return text, image
    except Exception as e:  # noqa: BLE001
        log.debug("extract failed %s: %s", url, e)
        return "", None


MIN_TEXT = 400        # tätä lyhyempi poiminta = maksumuuri/evästesivu, ei kelpaa lähteeksi
MAX_PER_SOURCE = 2    # ettei yksi julkaisu täytä koko listaa


def _is_aggregator(url: str) -> bool:
    """Google News -linkit ohjautuvat evästesivulle -> artikkelin tekstiä ei saa. Vain otsikko käytössä."""
    return urlparse(url).netloc.endswith("news.google.com")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _words(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", title.lower()))


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= 0.6


def _hits(text: str, kws: list[str]) -> int:
    t = text.lower()
    return sum(1 for k in kws if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t))


def _has_any(text: str, kws: list[str]) -> bool:
    """Aiheen avainsanat kokonaisina sanoina ("ai" ei osu sanaan "said", "car" ei sanaan "career")."""
    t = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t) for k in kws)


def fetch_news(cfg, extra_feeds: list[str] | None = None, topic_keywords: list[str] | None = None,
               lookback_days: float | None = None) -> list[dict]:
    """Valinta: ensin julkaisijoiden omat syötteet (koko teksti saatavilla), otsikko-osumat ennen
    pelkkiä tiivistelmäosumia, tuoreimmat ensin. Google News täyttää vain jos muuta ei riitä.

    Opasvideo (topic_keywords): artikkelin pitää koskea GTA 6:ta (news.keywords) ja aiheosumat (otsikko,
    tiivistelmä tai poimittu teksti) menevät ensin; extra_feeds = aiheen omat hakusyötteet (Google News)."""
    n = cfg.news
    cutoff = time.time() - float(lookback_days or n.lookback_days) * 86400
    kws = [k.lower() for k in n.keywords]
    tkws = [k.lower() for k in (topic_keywords or []) if k.strip()]
    seen_titles, items = set(), []
    for feed_url in list(n.feeds) + list(extra_feeds or []):
        try:
            feed = fetch_feed(feed_url)
        except Exception as e:  # noqa: BLE001
            log.warning("Syöte epäonnistui %s: %s", feed_url, e)
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            log.warning("Syöte ei vastannut %s: %s", feed_url, getattr(feed, "bozo_exception", ""))
            continue
        hits = 0
        for e in feed.entries:
            title = (e.get("title") or "").strip()
            summary = _strip_html(e.get("summary", ""))
            in_title = any(k in title.lower() for k in kws)
            if not in_title and not any(k in summary.lower() for k in kws):
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = calendar.timegm(ts) if ts else time.time()
            if published < cutoff:
                continue
            url = e.get("link", "")
            source = _source_name(e, url)
            if title.endswith(" - " + source):  # Google News lisää julkaisijan otsikon perään
                title = title[: -len(source) - 3].strip()
            key = title.lower()[:70]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            hits += 1
            items.append(dict(title=title, url=url, source=source, published=published,
                              summary=summary[:600], _direct=not _is_aggregator(url), _in_title=in_title,
                              _topic=bool(tkws) and _has_any(title + " " + summary, tkws)))
        log.info("Syöte %s: %d osumaa", urlparse(feed_url).netloc + urlparse(feed_url).path[:30], hits)
    items.sort(key=lambda x: (x["_topic"], x["_direct"], x["_in_title"], x["published"]), reverse=True)

    picked, fallback, per_source, tries = [], [], {}, 0
    blocked: dict[str, int] = {}
    for it in items:
        if len(picked) >= n.max_articles:
            break
        w = _words(it["title"])
        if any(_similar(w, _words(p["title"])) and p["source"] == it["source"] for p in picked):
            continue
        if per_source.get(it["source"], 0) >= MAX_PER_SOURCE:
            continue
        text, image = "", None
        host = urlparse(it["url"]).netloc
        if it["_direct"] and tries < n.max_articles * 3 and blocked.get(host, 0) < 2:
            tries += 1
            text, image = _extract(it["url"])
            blocked[host] = 0 if text else blocked.get(host, 0) + 1  # 2 tyhjää peräkkäin = sivusto estää botit
        it["text"] = text[:4000]
        it["image"] = image if n.show_article_images else None
        # opasvideo: aihe otsikossa/tiivistelmässä tai vähintään 2 eri aiheen avainsanaa tekstissä
        if tkws and not it["_topic"] and _hits(text, tkws) < 2:
            fallback.append(it)  # opasvideo: GTA 6 -juttu, mutta ei aiheesta -> vain täytteeksi
        elif len(text) >= MIN_TEXT:
            picked.append(it)
            per_source[it["source"]] = per_source.get(it["source"], 0) + 1
        else:
            fallback.append(it)
    if len(picked) < n.max_articles:  # ei tarpeeksi kokotekstisiä -> täytetään otsikkotason uutisilla
        log.warning("Vain %d artikkelista saatiin teksti, täytetään otsikoilla", len(picked))
        for it in fallback:
            if len(picked) >= n.max_articles:
                break
            if any(_similar(_words(it["title"]), _words(p["title"])) for p in picked):
                continue
            picked.append(it)
    picked.sort(key=lambda x: x["published"], reverse=True)
    for it in picked:
        it.pop("_direct"), it.pop("_in_title"), it.pop("_topic")
        it["date"] = time.strftime("%b %d, %Y", time.gmtime(it["published"]))
    log.info("Uutisia: %d (tekstin kanssa %d)", len(picked), sum(1 for it in picked if it["text"]))
    return picked


def mock_news() -> list[dict]:
    now = time.time()
    return [
        dict(title="Rockstar confirms GTA 6 release window in latest earnings call", url="https://example.com/a",
             source="EXAMPLE NEWS", published=now, date=time.strftime("%b %d, %Y"), image=None,
             summary="", text="Take-Two reiterated the release window during its earnings call, and the company said "
                              "that development is \"on track\" for the planned launch date. Executives did not "
                              "share any new details about trailers or pre-orders."),
        dict(title="Fans spot new Vice City landmark in leaked footage", url="https://example.com/b",
             source="GAMEWIRE", published=now, date=time.strftime("%b %d, %Y"), image=None,
             summary="", text="An unverified clip shows what appears to be..."),
    ]
