"""GTA 6 -videobotti.

  python -m bot.main run            # yksi uutisvideo nyt
  python -m bot.main run --no-upload
  python -m bot.main run --mock     # ei API-kutsuja, testaa koko putken
  python -m bot.main run --format guide [--topic "GTA 6 police vs GTA 5"]   # vinkki-/opasvideo yhdestä aiheesta
  python -m bot.main run --script FILE.json  # valmis käsikirjoitus (+ "articles"), ei API-avaimia, ei latausta
  python -m bot.main research [--topic ...]  # tulostaa mitä trenditutkimus kerää (YouTube, Reddit, RSS)
  python -m bot.main schedule       # pyörii taustalla, ajaa schedule.jobs-ajot joka päivä (uutiset + opas)
  python -m bot.main schedule --dry-run   # tulostaa seuraavat ajot ja julkaisuajat
  python -m bot.main cleanup [--dry-run]  # siivoaa välitiedostot, vanhat työkansiot, outputit ja lokit
  python -m bot.main status         # tämän päivän työt (vaihe, linkit, QA), viimeiset virheet, levytila, seuraavat ajot

Ajastin ajaa jokaisen työn omana lapsiprosessinaan: python -m bot.main run --job NIMI --day YYYY-MM-DD
(idempotentti per työ ja päivä: uusinta jatkaa siitä mitä puuttuu, sama video ei koskaan nouse kahdesti).
"""
import argparse
import datetime as dt
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from . import cards as cardmod
from . import alerts, brand, housekeeping, logsetup, news, panels, preflight, proc, qa, render, research, scheduler
from . import scriptwriter, trending, tts
from .config import ROOT, env, load
from .state import State
from .shorts import make_shorts
from .thumbnail import make_thumbnails

log = logging.getLogger("bot")
DESC_LIMIT = 4900  # YouTube sallii 5 000 merkkiä; uploader katkaisee 4 900:aan -> hashtagit eivät saa jäädä yli
AI_LINE = "Voiceover is AI-generated. Gameplay footage and images belong to Rockstar Games."
DEFAULT_CTA = "If this helped, hit LIKE and SUBSCRIBE for more GTA 6 news and guides all the way to launch and after."
DEFAULT_DISCLAIMER = "Not affiliated with Rockstar Games or Take-Two Interactive."
DEFAULT_CREDIT = ('Music: "Pulsar" by The Grey Room / Density & Time, "Neon nights" by Patrick Patrikios '
                  "(YouTube Audio Library)")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "video"


def _tag(t) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z]", "", str(t))


def _hashtags(cfg, limit: int | None = None, lead: list[str] | None = None) -> str:
    """lead = 3 tarkinta aihetagia ensin (YouTube näyttää 3 ensimmäistä otsikon yläpuolella), sitten aihe- ja yleiset
    pelitagit. YouTube ohittaa kaikki hashtagit jos niitä on yli 60 -> raja configissa (upload.max_hashtags)."""
    up = cfg.upload
    tags, seen = [], set()
    for t in list(lead or []) + list(up.get("topic_hashtags") or []) + list(up.get("hashtags") or []):
        t = _tag(t)
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    n = min(limit or 999, int(up.get("max_hashtags", 30) or 30), 60)
    return " ".join(tags[:n])


def _lead_hashtags(cfg, fmt: str, title: str, topic: dict | None) -> list[str]:
    """3 tarkinta: muodon oletus (upload.lead_hashtags) ja aiheen oma tagi (guide.topics[].hashtag) kolmanneksi;
    GTA 5 -vertailu otsikossa -> #GTA6vsGTA5."""
    defaults = {"long": ["GTA6", "GTAVI", "GTA6News"], "guide": ["GTA6", "GTAVI", "GTA6Guide"],
                "breakdown": ["GTA6", "GTAVI", "GTA6Trailer"]}
    conf = cfg.upload.get("lead_hashtags") or {}
    lead = list(conf.get(fmt) or defaults.get(fmt) or defaults["long"])[:3]
    special = (topic or {}).get("hashtag")
    if not special and re.search(r"\bgta\s*(5|v)\b", title or "", re.I):
        special = "GTA6vsGTA5"
    if special and len(lead) >= 3:
        lead[2] = special
    return lead


def _stamp(t: float) -> str:
    s = int(t)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _chapters(script: dict, seg_times: list[dict], off: float, total: float, min_len: float = 10.0):
    """YouTube-kappaleet osioista: 0:00 Intro + osioiden alut. Jokaisen kappaleen oltava >= 10 s
    (lyhyt kappale yhdistetään edelliseen), ja niitä on oltava vähintään 3 -> muuten [] (ei lohkoa)."""
    chs = [(0.0, "Intro")]
    for seg, t in zip(script["segments"], seg_times):
        if seg.get("section"):
            st = float(int(t["s"] + off))
            if st > chs[-1][0]:
                chs.append((st, seg["section"]))
    i = 0
    while i < len(chs):
        end = chs[i + 1][0] if i + 1 < len(chs) else total
        if end - chs[i][0] < min_len and len(chs) > 1:
            del chs[1 if i == 0 else i]
            i = max(i - 1, 0)
            continue
        i += 1
    return chs if len(chs) >= 3 else []


def _no_angles(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")  # YouTube hylkää kuvauksen, jossa on < tai >


def _sub_link(cfg) -> str:
    h = (cfg.shorts.get("channel_handle") or "").strip()
    return f"https://www.youtube.com/{h}?sub_confirmation=1" if h.startswith("@") else ""


_LINKS_CACHE: dict = {}


def _channel_links(cfg) -> str:
    """Tilauslinkki (avaa tilausikkunan) + kanavan uusimmat julkiset pitkät videot "Watch next" -listaksi.
    Ei koskaan kaada ajoa: virhe -> pelkkä tilauslinkki."""
    n = int(cfg.upload.get("watch_next_count", 3) or 0)
    lines = []
    sub = _sub_link(cfg)
    if sub:
        lines.append(f"Subscribe for more: {sub}")
    if n and "videos" not in _LINKS_CACHE:
        vids = []
        try:
            from .uploader import _service
            yt = _service(cfg.paths.secrets_dir)
            up_id = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
                "contentDetails"]["relatedPlaylists"]["uploads"]
            items = yt.playlistItems().list(part="contentDetails", playlistId=up_id, maxResults=25).execute()["items"]
            ids = [i["contentDetails"]["videoId"] for i in items]
            try:
                from . import playlists
                member = playlists.membership(cfg, yt)
            except Exception as e:  # noqa: BLE001
                log.warning("Soittolistojen haku epäonnistui: %s", e)
                member = {}
            for v in yt.videos().list(part="snippet,status,contentDetails", id=",".join(ids)).execute()["items"]:
                dur = v["contentDetails"].get("duration", "")
                long_video = ("H" in dur) or bool(re.search(r"(\d+)M", dur) and int(re.search(r"(\d+)M", dur).group(1)) >= 3)
                if v["status"]["privacyStatus"] == "public" and long_video and \
                        "#shorts" not in v["snippet"]["title"].lower():
                    vids.append((v["snippet"]["title"], v["id"], member.get(v["id"])))
        except Exception as e:  # noqa: BLE001
            log.warning("Edellisten videoiden haku kuvaukseen epäonnistui: %s", e)
        _LINKS_CACHE["videos"] = vids[:n]
    if _LINKS_CACHE.get("videos"):
        from .playlists import watch_url
        lines.append("Watch next:\n" + "\n".join(f"- {t}: {watch_url(i, p)}" for t, i, p in _LINKS_CACHE["videos"]))
    return "\n\n".join(lines)


SOURCE_NAMES = {"pushsquare": "Push Square", "insider-gaming": "Insider Gaming", "insidergaming": "Insider Gaming",
                "ign": "IGN", "pcgamer": "PC Gamer", "videogameschronicle": "Video Games Chronicle", "vgc": "VGC",
                "gamesradar": "GamesRadar+", "gamespot": "GameSpot", "eurogamer": "Eurogamer", "kotaku": "Kotaku",
                "polygon": "Polygon", "theverge": "The Verge", "dexerto": "Dexerto", "forbes": "Forbes",
                "rockstargames": "Rockstar Games", "gameinformer": "Game Informer", "thegamer": "TheGamer",
                "gamerant": "Game Rant", "screenrant": "Screen Rant", "windowscentral": "Windows Central",
                "purexbox": "Pure Xbox", "gamesindustry": "GamesIndustry.biz", "bloomberg": "Bloomberg",
                "reuters": "Reuters", "theguardian": "The Guardian", "bbc": "BBC", "cnbc": "CNBC",
                "sensortower": "Sensor Tower", "dualshockers": "DualShockers"}


def source_label(source: str, url: str = "") -> str:
    """Lähteen nimi kuvaukseen: "PUSHSQUARE" / pushsquare.com -> "Push Square"; tuntematon -> sellaisenaan."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower().replace("www.", "").split(".")[0] if url else ""
    for key in (source.strip().lower().replace(" ", ""), host):
        if key in SOURCE_NAMES:
            return SOURCE_NAMES[key]
    src = source.strip()
    if src and not src.isupper():
        return src  # Google Newsin oma nimi, esim. "Push Square"
    return (src or host).replace("-", " ").title()


def build_description(cfg, script: dict, articles: list[dict], chapters: list, used: list[int], src: dict | None,
                      fmt: str, topic: dict | None = None) -> str:
    """Isojen GTA-kanavien kuvauspohja: otsikko, tiivistelmä, kysymys, kappaleet, CTA, lähteet, krediitit, hashtagit.
    Yli 4 900 merkkiä -> lähteitä karsitaan lopusta, sitten tiivistelmää."""
    up = cfg.upload
    title = script["video_title"].strip()
    summary = re.sub(r"\s+", " ", script.get("description", "")).strip()
    question = re.sub(r"\s+", " ", script.get("comment_question", "") or "").strip()
    # Lähteet yhdellä rivillä julkaisujen niminä (omistaja 18.11.2026: ei pitkiä linkkejä, joita YouTube lyhentää,
    # eikä videoita, joista tekoon saatiin apua: pelaajien tekstitykset ja trendivideo eivät näy kuvauksessa)
    sources = []
    for i in used:
        if 0 <= i < len(articles) and articles[i].get("kind") != "transcript":
            name = source_label(articles[i].get("source", ""), articles[i].get("url", ""))
            if name and name not in sources:
                sources.append(name)
    credit = up.get("music_credit", DEFAULT_CREDIT)
    tags_line = _hashtags(cfg, lead=_lead_hashtags(cfg, fmt, title, topic))

    def assemble(summary_text: str, src_lines: list[str]) -> str:
        parts = [title, summary_text]
        if question:
            parts.append(question)
        if chapters:
            parts.append("\n".join(f"{_stamp(t)} {name}" for t, name in chapters))  # ei otsikkoriviä (kuten GrandGecko)
        parts.append(up.get("cta_line", DEFAULT_CTA))
        links = _channel_links(cfg)
        if links:
            parts.append(links)
        if src_lines:
            parts.append("Sources: " + ", ".join(src_lines))
        if cfg.channel.credit_inspiration and src and fmt != "guide":
            parts.append(f"Topic trending thanks to: {src['title']} by {src['channel']}")
        parts.append("\n".join(x for x in (credit, AI_LINE, up.get("disclaimer", DEFAULT_DISCLAIMER)) if x))
        if tags_line:
            parts.append(tags_line)
        return _no_angles("\n\n".join(p for p in parts if p)) + "\n"

    desc = assemble(summary, sources)
    while len(desc) > DESC_LIMIT and sources:
        sources = sources[:-1]
        desc = assemble(summary, sources)
    if len(desc) > DESC_LIMIT:
        desc = assemble(summary[: max(len(summary) - (len(desc) - DESC_LIMIT) - 3, 0)] + "...", sources)
    return desc


def _guide_topics(cfg) -> list[dict]:
    out = []
    for t in (cfg.get("guide") or {}).get("topics") or []:
        if isinstance(t, str):
            out.append({"topic": t})
        elif isinstance(t, dict) and t.get("topic"):
            out.append(dict(t))
    return out


def trend_topic(cfg, topics: list[dict], used: set[str], vids: list[dict]) -> dict | None:
    """Opasvideon aihe trendien mukaan (omistaja 18.11.2026: trendaavuus on tärkeintä monetisoinnille).
    Jokainen käyttämätön aihe saa pisteitä trendaavista GTA 6 -videoista, joiden otsikossa/kuvauksessa sen avainsanat
    ovat (painona videon trendipisteet ja outlier-kerroin). Jos vahvin trendi (outlier >= 3) ei osu mihinkään
    listan aiheeseen, aiheeksi tulee se trendi itse. None = ei trenditietoa -> listan järjestys."""
    import math
    if not vids:
        return None
    top = max(float(v.get("score") or 0) for v in vids) or 1.0
    best, best_s = None, 0.0
    for tp in topics:
        if tp["topic"] in used:
            continue
        kws = [k.lower() for k in (tp.get("keywords") or research.topic_keywords(tp))]
        sc = 0.0
        for v in vids:
            text = f" {v.get('title', '')} {v.get('description', '')[:400]} ".lower()
            hits = sum(1 for k in kws if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text))
            if hits:
                w = float(v.get("score") or 0) / top * (1 + math.log10(1 + min(float(v.get("outlier") or 1), 100)))
                sc += w * min(hits, 3)
        if sc > best_s:
            best, best_s = tp, sc
    lead = vids[0]
    lead_hits = any(re.search(r"(?<![a-z])" + re.escape(k.lower()) + r"(?![a-z])", lead.get("title", "").lower())
                    for tp in topics for k in (tp.get("keywords") or []))
    if float(lead.get("outlier") or 0) >= 3 and not lead_hits:
        title = re.sub(r"[#|].*$", "", lead.get("title", "")).strip()
        if title and title not in used:
            log.info("Opasvideon aihe suoraan trendistä: %s (%.1fx outlier, %s)", title, lead.get("outlier"), lead["url"])
            words = [w for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 3 and w not in ("grand", "theft", "auto")]
            return {"topic": title, "keywords": words[:8] or ["gta 6"], "trend": lead["url"]}
    if best:
        log.info("Opasvideon aihe trendien mukaan: %s (%.2f pistettä)", best["topic"], best_s)
    return best


def _guide_topic(cfg, st: State, explicit: str | None, vids: list[dict] | None = None) -> dict:
    topics = _guide_topics(cfg)
    if explicit:
        for t in topics:
            if t["topic"].lower() == explicit.strip().lower():
                return t
        return {"topic": explicit.strip()}
    if vids:
        used = {r[0] for r in st.db.execute("SELECT topic FROM guide_topics").fetchall()}
        tp = trend_topic(cfg, topics, used, vids)
        if tp:
            return tp
    name = st.next_guide_topic([t["topic"] for t in topics])
    if not name:
        raise RuntimeError("guide.topics on tyhjä: anna --topic")
    return next(t for t in topics if t["topic"] == name)


def _is_limit_error(e: Exception) -> bool:
    """YouTuben kanavakohtainen latausraja (uploadLimitExceeded): ei vika videossa, vaan odota ja yritä myöhemmin."""
    s = str(e)
    return "uploadLimitExceeded" in s or "exceeded the number of videos" in s


def _pending_path(cfg) -> Path:
    return Path(cfg.paths.db).parent / "pending_uploads.json"


def _save_pending(cfg, jobs: list[dict]):
    p = _pending_path(cfg)
    if jobs:
        p.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
    elif p.exists():
        p.unlink()


def _load_pending(cfg) -> list[dict]:
    p = _pending_path(cfg)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        log.exception("pending_uploads.json lukeminen epäonnistui")
        return []


def _queue(cfg, entry: dict, key: str):
    """Lisää jonoon (sama video/työ vain kerran)."""
    jobs = [j for j in _load_pending(cfg) if _entry_key(j) != key] + [entry]
    _save_pending(cfg, jobs)


def _entry_key(entry: dict) -> str:
    return "job:" + "/".join(entry["job"]) if entry.get("job") else str(entry.get("video"))


def _local_path(cfg, p: str | None) -> str | None:
    """Jonossa voi olla toisen ympäristön polku (Windows E:\\...\\data\\output\\x.mp4 vs. Docker /data/output/x.mp4):
    jos polkua ei ole, sama tiedostonimi output-kansiosta."""
    if not p or Path(p).exists():
        return p
    name = re.split(r"[\\/]", str(p))[-1]
    alt = Path(cfg.paths.output_dir) / name
    return str(alt) if alt.exists() else p


def _localize(cfg, job: dict) -> dict:
    job = dict(job)
    for k in ("video", "meta", "thumb"):
        job[k] = _local_path(cfg, job.get(k))
    job["shorts"] = [dict(x, path=_local_path(cfg, x["path"])) for x in job.get("shorts") or []]
    job["done_shorts"] = [_local_path(cfg, p) for p in job.get("done_shorts") or []]
    job["short_ids"] = {_local_path(cfg, k): v for k, v in (job.get("short_ids") or {}).items()}
    return job


def _upload_job(cfg, st, job: dict) -> str | None:
    """Käsin ajetun runin lataus: pitkä video ja sen Shortsit. Latausrajaan osuessa työ jää jonoon
    (data/pending_uploads.json) ja jatkuu myöhemmin siitä mihin jäi: mitään ei ladata kahdesti eikä renderöidä
    uudelleen. (Ajastetut työt käyttävät publish()-funktiota ja state.db:tä, ks. run_job.)"""
    import copy
    from .uploader import upload
    if job.get("privacy"):  # työkohtainen näkyvyys (esim. käsin jonoon lisätty julkinen Short)
        cfg = copy.deepcopy(cfg)
        cfg["upload"]["privacy"] = job["privacy"]
    meta = json.loads(Path(job["meta"]).read_text(encoding="utf-8"))
    long_at = dt.datetime.fromisoformat(job["publish_at"]) if job.get("publish_at") else None
    try:
        if not job.get("long_id"):
            job["long_id"] = upload(cfg, Path(job["video"]), meta, Path(job["thumb"]) if job.get("thumb") else None,
                                    publish_at=long_at)
            if long_at:
                st.add_scheduled(job["long_id"], "long", long_at.isoformat())
        yt_id = job["long_id"]
        list_id = _add_to_playlist(cfg, yt_id, meta.get("format") or cfg.format)
        todo = [x for x in job["shorts"] if x["path"] not in job["done_shorts"]]
        slots = scheduler.short_slots(cfg, len(todo), long_at, scheduler.taken_short_slots(st)) \
            if long_at else [None] * len(todo)
        for x, slot in zip(todo, slots):
            try:
                sid = upload(cfg, Path(x["path"]), _short_meta(cfg, x, yt_id, meta.get("tags") or [], list_id), None,
                             publish_at=slot)
            except Exception as e:  # noqa: BLE001
                if _is_limit_error(e):
                    raise
                log.exception("Shortsin lataus epäonnistui: %s", Path(x["path"]).name)
                job["done_shorts"].append(x["path"])  # rikkinäistä Shortsia ei yritetä loputtomiin
                continue
            job["done_shorts"].append(x["path"])
            job.setdefault("short_ids", {})[x["path"]] = sid
            if slot:
                st.add_scheduled(sid, "short", slot.isoformat())
        if not long_at and cfg.upload.privacy == "public" and cfg.upload.get("relink_shorts", True):
            try:  # kaikki vanhatkin Shortsit ohjaavat uusimpaan pitkään videoon
                from .uploader import relink_shorts
                relink_shorts(cfg, yt_id, list_id=list_id)
            except Exception:  # noqa: BLE001
                log.exception("Shortsien linkkien päivitys epäonnistui (%s)", yt_id)
        return yt_id
    except Exception as e:  # noqa: BLE001
        if not _is_limit_error(e):
            raise
        log.warning("YouTuben latausraja täynnä -> jonoon, yritetään myöhemmin uudelleen: %s", Path(job["video"]).name)
        _queue(cfg, job, _entry_key(job))
        alerts.send(f"Catfish-botti: YouTuben latausraja täynnä, {Path(job['video']).name} jäi jonoon "
                    "(yritetään tunnin välein uudelleen).")
        return job.get("long_id")


def _legacy_shorts_ok(cfg, job: dict) -> bool:
    """Vanhan jonomerkinnän lataamattomat Shortsit laatuportin läpi ennen latausta."""
    todo = [{"path": Path(x["path"]), "title": x["title"]} for x in job.get("shorts") or []
            if x["path"] not in (job.get("done_shorts") or [])]
    if not todo:
        return True
    rep = run_qa(cfg, {}, mock=False, shorts_only=todo)
    if not rep.ok:
        _qa_alert(f"jonossa ollut {Path(job['video']).name}", rep, Path(job["video"]))
    return rep.ok


def _stuck_minutes(cfg) -> float:
    return float(cfg.upload.get("stuck_minutes", 75) or 75)


def stuck_candidates(cfg, st: State | None = None, now: float | None = None) -> list[dict]:
    """Ajastetut työt (tänään/eilen), joiden pitkä video ladattiin yli stuck_minutes sitten eikä käsittelyä ole vielä
    todettu valmiiksi (meta.processed_ok). Ei API-kutsuja: tunnin välein ajastin katsoo tästä, tarvitaanko tarkistus."""
    st = st or State(cfg.paths.db)
    now = now or time.time()
    today = dt.datetime.fromtimestamp(now, scheduler.tz(cfg)).date()  # työn päivä = schedule.timezone
    out = []
    for day in (today.isoformat(), (today - dt.timedelta(days=1)).isoformat()):
        for r in st.jobs_on(day):
            m = r.get("meta") or {}
            started = m.get("long_upload_started")
            if (r.get("stage") == "done" and r.get("long_id") and started and not m.get("processed_ok")
                    and not m.get("stuck_recovered") and now - float(started) > _stuck_minutes(cfg) * 60):
                out.append(r)
    return out


def _verify_done(cfg, yt, row: dict, ps: dict):
    """Valmiin työn jälkitarkistus (tunnin välein, kerran per työ): pitkä video ja Shortsit julkisia jos niin
    pyydettiin, thumbnail asetettu ja video soittolistassa. Puuttuva korjataan, korjaus kirjataan ja hälytetään."""
    from . import uploader
    job = {}
    try:
        job = _find_job(cfg, row["name"], row["day"])
    except Exception:  # noqa: BLE001 - poistettu kertatyö: oletusasetukset
        pass
    want_public = (job.get("privacy") or cfg.upload.privacy) == "public"
    vid, fixed = row["long_id"], []
    if want_public and ps.get("privacy") != "public" and not ps.get("publish_at"):
        if uploader.ensure_public(yt, vid, {"privacyStatus": ps.get("privacy")}):
            fixed.append("julkiseksi")
    cd = (yt.videos().list(part="contentDetails", id=vid).execute().get("items") or [{}])[0].get("contentDetails") or {}
    out = Path(row.get("output") or "")
    if cd and not cd.get("hasCustomThumbnail") and cfg.upload.set_thumbnail and out.with_suffix(".json").is_file():
        meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
        th = [out.parent / t for t in meta.get("thumbnails") or []]
        if th and th[0].is_file() and uploader.set_thumbnail(yt, vid, th[0]):
            fixed.append("thumbnail")
    fmt = job.get("format") or "long"
    if _add_to_playlist(cfg, vid, fmt):
        pass
    if want_public:
        for sid in (row.get("shorts") or {}).values():
            if sid and sid != PENDING:
                st = uploader.processing_state(yt, sid)
                if st and st["privacy"] != "public" and not st.get("publish_at"):
                    if uploader.ensure_public(yt, sid, {"privacyStatus": st["privacy"]}):
                        fixed.append(f"Short {sid} julkiseksi")
    if fixed:
        log.warning("Jälkitarkistus korjasi videon %s: %s", vid, ", ".join(fixed))
        alerts.send(f"Catfish-botti: jälkitarkistus korjasi https://youtu.be/{vid}: {', '.join(fixed)}")
    else:
        log.info("Jälkitarkistus ok: https://youtu.be/%s", vid)


def recover_stuck(cfg, yt=None, st: State | None = None, now: float | None = None, upload_fn=None) -> int:
    """Pitkä video jäi YouTubessa käsittelyyn (processing, kesto 0) yli stuck_minutes: ladataan sama tiedosto
    uudelleen samalla näkyvyydellä, Shortsien linkit uuteen, jumiin jäänyt poistetaan (vain kun uusi on ylhäällä).
    Kerran per työ. Palauttaa korjattujen määrän."""
    import copy
    from . import uploader
    st = st or State(cfg.paths.db)
    rows = stuck_candidates(cfg, st, now)
    if not rows:
        return 0
    yt = yt or uploader._service(cfg.paths.secrets_dir)
    upload_fn = upload_fn or uploader.upload
    fixed = 0
    for r in rows:
        key, old = (r["name"], r["day"]), r["long_id"]
        ps = uploader.processing_state(yt, old)
        if ps is None:
            log.info("Työn %s/%s video %s puuttuu YouTubesta (poistettu?) -> ei tarkisteta enää", *key, old)
            _job_meta(st, key, processed_ok=True)
            continue
        if not (ps["processing"] in ("processing", "failed") and ps["duration"] in (None, "P0D")):
            try:
                _verify_done(cfg, yt, r, ps)
            except Exception:  # noqa: BLE001 - tarkistetaan uudelleen seuraavalla kierroksella
                log.exception("Videon %s jälkitarkistus epäonnistui", old)
                continue
            _job_meta(st, key, processed_ok=True)
            continue
        out = Path(r.get("output") or "")
        meta_file = out.with_suffix(".json")
        if not out.is_file() or not meta_file.is_file():
            log.error("Video %s on jumissa käsittelyssä, mutta tiedostoa %s ei ole -> ei voida ladata uudelleen",
                      old, out)
            alerts.send(f"Catfish-botti: video https://youtu.be/{old} on jumissa YouTuben käsittelyssä eikä "
                        f"alkuperäistä tiedostoa löydy. Tarkista Studiossa.")
            _job_meta(st, key, stuck_recovered=True)
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        thumbs = [out.parent / t for t in meta.get("thumbnails") or []]
        when = uploader._parse(ps.get("publish_at"))
        publish_at = when if when and when > dt.datetime.now(dt.timezone.utc) else None
        c2 = copy.deepcopy(cfg)
        c2["upload"]["privacy"] = ps["privacy"] if ps["privacy"] in ("public", "unlisted", "private") else             cfg.upload.privacy
        log.warning("Video %s on ollut YouTuben käsittelyssä yli %d min (kesto 0) -> ladataan uudelleen",
                    old, int(_stuck_minutes(cfg)))
        _job_meta(st, key, stuck_recovered=True, stuck_from=old)  # ennen latausta: ei koskaan kahta uusintaa
        new = upload_fn(c2, out, meta, thumbs[0] if thumbs and thumbs[0].is_file() else None, publish_at=publish_at)
        st.job_update(*key, long_id=new)
        if publish_at:
            st.add_scheduled(new, "long", publish_at.isoformat())
        list_id = _add_to_playlist(cfg, new, meta.get("format") or "long")
        try:
            uploader.relink_shorts(cfg, new, only_from=old, yt=yt, list_id=list_id)
        except Exception:  # noqa: BLE001
            log.exception("Shortsien linkit %s -> %s epäonnistui", old, new)
        try:
            yt.videos().delete(id=old).execute()
            log.info("Jumiin jäänyt video poistettu: %s (tilalla https://youtu.be/%s)", old, new)
        except Exception:  # noqa: BLE001
            log.exception("Jumiin jääneen videon %s poisto epäonnistui", old)
        alerts.send(f"Catfish-botti: video https://youtu.be/{old} jäi YouTuben käsittelyyn, ladattiin uudelleen: "
                    f"https://youtu.be/{new} (Shortsien linkit päivitetty, jumiin jäänyt poistettu).")
        fixed += 1
    return fixed


def upload_pending(cfg, run_job_fn=None) -> int:
    """Lataa latausrajaan jääneet työt jonosta. Ajastetut työt ({"job": [nimi, päivä]}) jatkavat run_jobilla
    (state.db: ei koskaan kahdesti), käsin ajetut (_upload_job). Palauttaa jonoon jääneiden määrän."""
    import copy
    run_job_fn = run_job_fn or run_job
    jobs = _load_pending(cfg)
    if not jobs:
        return 0
    st = State(cfg.paths.db)
    for entry in jobs:
        key = _entry_key(entry)
        _save_pending(cfg, [j for j in _load_pending(cfg) if _entry_key(j) != key])  # tämä pois jonosta
        try:
            if entry.get("job"):
                name, day = entry["job"]
                run_job_fn(copy.deepcopy(cfg), name, day, preflight_fn=lambda c, youtube=True: [])
            else:
                job = _localize(cfg, entry)
                if not Path(job["video"]).exists() and not job.get("long_id"):
                    log.error("Jonon video puuttuu, poistetaan jonosta: %s", job["video"])
                    continue
                if not _legacy_shorts_ok(cfg, job):
                    log.error("Jonon Shortsit hylättiin laatuportissa, poistetaan jonosta: %s", job["video"])
                    continue
                _upload_job(cfg, st, job)
        except Exception as e:  # noqa: BLE001
            entry["failures"] = int(entry.get("failures") or 0) + 1
            log.exception("Jonon työ %s epäonnistui (%d. kerta)", key, entry["failures"])
            if entry["failures"] < 5:
                _queue(cfg, entry, key)
            else:
                alerts.send(f"Catfish-botti: jonossa ollut {key} epäonnistui 5 kertaa, poistettiin jonosta: "
                            f"{alerts.redact(str(e))[:300]}")
            continue
        if any(_entry_key(j) == key for j in _load_pending(cfg)):
            log.info("Latausraja yhä täynnä -> loput odottavat seuraavaa yritystä")
            break
        log.info("Jonosta käsitelty: %s", key)
    return len(_load_pending(cfg))


class QAFailed(RuntimeError):
    """Laatuportti hylkäsi videon: ei latausta, tiedostot jäävät."""


EXIT_OK, EXIT_ERROR, EXIT_PREFLIGHT, EXIT_QA = 0, 1, 2, 3
PENDING = "pending"  # Shortsin lataus alkoi, id:tä ei vielä saatu (jobs.shorts)


def produce(cfg, st: State, mock: bool = False, script_file: str | None = None, topic: str | None = None,
            reuse_script: str | None = None, on_script=None, job: dict | None = None) -> dict:
    """Tutkimus -> käsikirjoitus -> ääni -> renderöinti -> thumbnailit -> Shortsit -> metadata (output/<nimi>.json).
    reuse_script = ajastetun työn kesken jääneen yrityksen script.json: ei uutta Claude-kutsua, lataus sallittu.
    on_script(work) kutsutaan heti kun script.json on tallennettu. job = ajastettu työ (schedule.jobs/once):
    job.research == "launch_streams" -> julkaisupäivän tutkimus (ensimmäisten pelaajien tekstitykset) ja Rockstarin
    uusin virallinen video taustaksi. Palauttaa tuotoksen tiedot (prod)."""
    launch = None
    if job and job.get("research") == "launch_streams" and not mock:
        try:  # Rockstarin julkaisupäivän viralliset videot taustamateriaaliksi (ei koskaan muiden pelikuvaa)
            research.rockstar_footage(cfg, env("YOUTUBE_API_KEY", required=False))
        except Exception:  # noqa: BLE001
            log.exception("Rockstarin uusien videoiden haku epäonnistui (jatketaan vanhalla materiaalilla)")
    _log_own_clips(cfg)
    guide = cfg.format == "guide"
    bd = cfg.format == "breakdown"
    src, related, signals, gtopic, bdprep = None, [], None, None, None

    if reuse_script:
        script = json.loads(Path(reuse_script).read_text(encoding="utf-8"))
        articles = script.pop("articles", None) or []
        signals = script.pop("research", None)
        src = script.pop("source", None)
        if script.get("format") == "guide" and not guide:
            cfg["format"], guide = "guide", True
        if script.get("format") == "breakdown":
            cfg["format"], bd, bdprep = "breakdown", True, script.get("breakdown")
        if guide:
            gtopic = script.get("guide_topic") or ({"topic": topic} if topic else None)
        log.info("Jatketaan kesken jääneen yrityksen käsikirjoituksesta %s (ei uutta Claude-kutsua)", reuse_script)
    elif script_file:  # valmis käsikirjoitus: ei trendihakua, ei Claudea, ei latausta
        script = json.loads(Path(script_file).read_text(encoding="utf-8"))
        articles = script.pop("articles", None)
        script.pop("research", None)
        script.pop("source", None)
        if articles is None:
            articles = news.fetch_news(cfg)
        if script.get("format") == "guide" and not guide:
            cfg["format"], guide = "guide", True
        if guide:
            gtopic = script.get("guide_topic") or ({"topic": topic} if topic else None)
    elif bd:
        from . import breakdown
        if mock:
            articles = news.mock_news()
        else:
            v = (job or {}).get("video")
            if not v:
                raise RuntimeError("breakdown-työltä puuttuu Rockstarin video (breakdown-vahti)")
            log.info("Breakdown: %s (%s)", v["title"], v["url"])
            bdprep = breakdown.prepare(cfg, v, Path(cfg.paths.work_dir) / f"breakdown_{v['id']}")
            articles = bdprep["articles"]
    elif guide:
        tvids = []
        if not mock and not topic and not (job and job.get("topic")) and cfg.trending.get("drive_guide_topics", True):
            try:  # trendit ensin (sama haku kuin uutisvideolla, välimuistista)
                tvids = trending.cached(cfg, env("YOUTUBE_API_KEY"))
            except Exception as e:  # noqa: BLE001 - aihelistan järjestys varalla
                log.warning("Trendihaku opasvideolle epäonnistui: %s", e)
        gtopic = _guide_topic(cfg, st, topic, tvids)
        log.info("Opasvideon aihe: %s", gtopic["topic"])
        if mock:
            articles = news.mock_news()
        else:
            days = int((cfg.get("research") or {}).get("topic_lookback_days", 30))
            if job and job.get("research") == "launch_streams":
                launch = research.launch_research(cfg, env("YOUTUBE_API_KEY", required=False),
                                                  retry_at=job.get("research_retry_at"))
                news_arts = news.fetch_news(cfg, extra_feeds=[research.google_news_feed("GTA 6 launch", 3)],
                                            lookback_days=3)
                # tekstitykset ensin (lähteet), uutiset tueksi; vähän tekstityksiä -> kaikki uutiset mukaan
                articles = research.launch_articles(launch) + (news_arts if launch["early"] else news_arts[:5])
                signals = {"youtube": [], "reddit": [],
                           "launch": [{k: c[k] for k in ("creator", "title", "url", "views", "words_kept")}
                                      for c in launch["creators"]], "early": launch["early"]}
            else:
                signals = research.collect(cfg, env("YOUTUBE_API_KEY", required=False), topic=gtopic["topic"])
                articles = news.fetch_news(cfg, extra_feeds=[research.google_news_feed(gtopic["topic"], days)],
                                           topic_keywords=research.topic_keywords(gtopic), lookback_days=days)
    else:
        # 1) trendit
        vids = trending.mock_trending() if mock else trending.cached(cfg, env("YOUTUBE_API_KEY"))
        vids = [v for v in vids if mock or not st.is_used(v["id"])]
        if not vids:
            raise RuntimeError("Ei uusia trendaavia GTA 6 -videoita")
        src, related = vids[0], vids[1:6]
        log.info("Aihe: %s (%s)", src["title"], src["url"])
        # 2) trenditutkimus (YouTube-outlierit + Reddit) ja uutiset
        if not mock:
            signals = research.collect(cfg, env("YOUTUBE_API_KEY", required=False), vids=vids[1:])
        articles = news.mock_news() if mock else news.fetch_news(cfg)

    # 3) käsikirjoitus
    if reuse_script:
        pass  # jo tarkistettu (_validate) edellisessä yrityksessä
    elif script_file:
        script = scriptwriter._validate(script, len(articles), articles)
    elif mock:
        script = scriptwriter.mock_guide_script(articles, gtopic["topic"]) if guide else \
            scriptwriter.mock_breakdown_script(articles) if bd else scriptwriter.mock_script(articles)
    else:
        script = scriptwriter.write_script(cfg, src, articles, st.recent_titles(), env("ANTHROPIC_API_KEY"),
                                           related=related, research=None if launch else signals,
                                           topic=gtopic["topic"] if gtopic else None, launch=launch,
                                           breakdown=bdprep)
    log.info("Otsikko: %s | lisäarvo: %s", script["video_title"], script.get("value_add"))

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    name = f"{stamp}_{_slug(script['video_title'])}"
    work = Path(cfg.paths.work_dir) / name
    work.mkdir(parents=True, exist_ok=True)
    # käsikirjoitus + artikkelit samassa tiedostossa -> sama video voidaan renderöidä uudelleen: run --script <tämä>
    extra = {"format": "guide", "guide_topic": gtopic} if guide else {}
    if bd:
        extra = {"format": "breakdown",
                 "breakdown": {"info": (bdprep or {}).get("info"), "video": str((bdprep or {}).get("video") or "")}}
    if src:
        extra["source"] = src
    (work / "script.json").write_text(json.dumps({**script, **extra, "research": signals, "articles": articles},
                                                 indent=2, ensure_ascii=False), encoding="utf-8")
    if on_script:
        on_script(work)

    # 4) voiceover
    if mock:
        cfg["tts"]["engine"] = "mock"
    eleven = env("ELEVENLABS_API_KEY", required=cfg.tts.engine == "elevenlabs")
    # jaettu välimuisti: epäonnistunut tai uusittu ajo ei tee samoja TTS-segmenttejä uudelleen
    voice = tts.synthesize(cfg, script["segments"], work, eleven,
                           cache_dir=Path(cfg.paths.work_dir) / "tts_cache")
    want = float(cfg.script.get("target_minutes_long", 0) or 0) * 60
    if cfg.format != "short" and want and voice["duration"] < want - 30:
        log.warning("Puhetta %.1f min, tavoite %.0f min", voice["duration"] / 60, want / 60)

    # 5) kortit, tausta, tekstitys, renderöinti
    # brändi (pitkä video ja opas): intro siirtää kaiken offsetin verran, loppuruutu + häntä lopussa
    plan = brand.long_plan(cfg, voice["duration"], work)
    off = plan["offset"] if plan else 0.0
    spans = brand.place_cards(cardmod.build_cards(cfg, script, articles, voice["segments"], work), plan)
    # tausta (myös thumbnailien lähde) on pelkkää pelikuvaa: intro/loppuruutu lisätään vasta renderöinnissä
    prio, marks = None, None
    if bd:  # uusi virallinen video ensin, kertojan mainitsemat hetket (segment "moment") kohdalleen
        from . import breakdown
        prio = (bdprep or {}).get("video") or None
        if not prio and mock:
            prio = next(iter(sorted(breakdown.footage_dir(cfg).glob("rockstar_*.mp4"))), None)
        marks = breakdown.moments(script, voice["segments"])
        log.info("Breakdown-tausta: %s, %d viitattua hetkeä", Path(prio).name if prio else "-", len(marks))
    wants = None
    if cfg.format != "short" and cfg.video.get("topic_footage", True):
        from . import footage
        try:  # uudet kohtaukset/kuvat indeksiin (jo merkityt ohitetaan); mock: ei Claude-kutsuja
            footage.build(cfg, tag=not mock)
        except Exception:  # noqa: BLE001 - tausta tehdään silti (satunnaisesti)
            log.exception("Taustaindeksin päivitys epäonnistui")
        flash = (plan or {}).get("flash") or 0.0 if (plan or {}).get("intro") else 0.0
        wants = footage.wants_from_script(script, voice["segments"], shift=flash)
    bg = render.build_background(cfg, voice["duration"] + (plan["tail"] if plan else 0.0), work,
                                 priority=Path(prio) if prio else None, moments=marks, wants=wants)
    subs = render.write_subtitles(cfg, brand.shift(voice["words"], off), work) if cfg.video.subtitles else None
    total = plan["total"] if plan else voice["duration"]
    chapters = _chapters(script, voice["segments"], off, total)
    out_dir = Path(cfg.paths.output_dir)
    out = out_dir / f"{name}.mp4"
    # uutispaneelit, lainaukset ja osioiden otsikot omiksi paloikseen (thumbnailit käyttävät yhä puhdasta bg:tä)
    spans, sfx = panels.prepare(cfg, script, articles, spans, voice["segments"], bg, plan, work,
                                total=plan["total"] if plan else voice["duration"])
    # yläreunan aihekaista (nykyinen aihe korostettuna), ei uutispaneelien aikana eikä loppuruudussa
    render.add_topic_bar(cfg, subs, chapters, (plan or {}).get("outro_start") or total,
                         hide=[(c["s"], c["e"]) for c in spans if c.get("kind") == "panel"])
    render.render_final(cfg, bg, voice["wav"], voice["duration"], spans, subs, out, work, plan=plan, sfx=sfx)
    thumbs = make_thumbnails(cfg, bg, script, out_dir, name, work)
    shorts = make_shorts(cfg, script, articles, work, out_dir, name, eleven)

    # 6) metadata
    used = sorted({s["card"]["article"] for s in script["segments"]
                   if s.get("card") and s["card"].get("type") == "news"}
                  | {i for i, a in enumerate(articles) if a.get("kind") == "transcript"})  # pelaajien videot lähteinä
    fmt = "guide" if guide else "breakdown" if bd else "long"
    desc = build_description(cfg, script, articles, chapters, used, src, fmt, gtopic)
    meta = {"title": script["video_title"], "description": desc, "tags": script.get("tags", []),
            "format": fmt, "guide_topic": gtopic["topic"] if gtopic else None,
            "source_video": src["url"] if src else None, "source_id": src["id"] if src else None,
            "value_add": script.get("value_add"),
            "first_chapter_s": chapters[1][0] if len(chapters) > 1 else None}
    if meta["first_chapter_s"] and meta["first_chapter_s"] > 90:
        log.warning("Ensimmäinen kappale alkaa vasta %s (tavoite alle 1:30): katsojat lähtevät ennen lupausta",
                    _stamp(meta["first_chapter_s"]))

    meta["thumbnails"] = [p.name for p in thumbs]
    meta["shorts"] = [{"file": x["path"].name, "title": x["title"], "hook": x.get("hook", ""),
                       "duration": round(x["duration"], 1)} for x in shorts]
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"out": out, "meta": meta, "thumbs": list(thumbs),
            "shorts": [{"path": x["path"], "title": x["title"], "hook": x.get("hook", ""), "duration": x["duration"]}
                       for x in shorts],
            "script": script, "articles": articles, "work": work, "name": name, "src": src, "gtopic": gtopic}


def _log_own_clips(cfg):
    """Omistajan omat klipit (paths.clip_dirs, esim. data/clips) ovat aina taustamateriaalia: kirjataan määrä."""
    exts = {".mp4", ".mov", ".mkv", ".webm"}
    for d in cfg.paths.get("clip_dirs") or []:
        p = Path(d)
        n = sum(1 for f in p.rglob("*") if f.suffix.lower() in exts) if p.is_dir() else 0
        log.info("Taustaklipit %s: %d videota", p, n)


def load_saved(cfg, output: str | None, work_dir: str | None) -> dict | None:
    """Aiemmin renderöity tuotos levyltä (uusinta ilman uudelleenrenderöintiä). None = video tai metatiedot puuttuvat."""
    if not output:
        return None
    out = Path(output)
    meta_path = out.with_suffix(".json")
    if not out.is_file() or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out_dir = out.parent
    script, articles = None, []
    sj = Path(work_dir) / "script.json" if work_dir else None
    if sj and sj.is_file():
        script = json.loads(sj.read_text(encoding="utf-8"))
        articles = script.pop("articles", None) or []
        script.pop("research", None)
        script.pop("source", None)
    src = {"id": meta["source_id"], "url": meta.get("source_video")} if meta.get("source_id") else None
    return {"out": out, "meta": meta, "thumbs": [out_dir / t for t in meta.get("thumbnails") or []],
            "shorts": [{"path": out_dir / x["file"], "title": x["title"], "hook": x.get("hook") or "",
                        "duration": x.get("duration")} for x in meta.get("shorts") or []],
            "script": script, "articles": articles, "work": Path(work_dir) if work_dir else None,
            "name": out.stem, "src": src,
            "gtopic": {"topic": meta["guide_topic"]} if meta.get("guide_topic") else None}


def run_qa(cfg, prod: dict, mock: bool, shorts_only: list[dict] | None = None) -> qa.Report:
    """Laatuportti + raportti lokiin. shorts_only: vain nämä Shortsit (pitkä video on jo ladattu)."""
    if shorts_only is not None:
        rep = qa.Report()
        s = qa.settings(cfg)
        for sh in shorts_only:
            t = f"Short {Path(sh['path']).name}"
            qa.check_media(rep, t, Path(sh["path"]), "short", s, mock)
            qa.check_title(rep, t, sh.get("title", ""), s, False)
        rep.notes.append("pitkä video on jo ladattu: tarkistettiin vain lataamatta olevat Shortsit"
                         + ("" if shorts_only else " (ei yhtään)"))
    else:
        rep = qa.gate(cfg, out=prod["out"], meta=prod["meta"], thumbs=prod["thumbs"], shorts=prod["shorts"],
                      script=prod["script"], articles=prod["articles"], mock=mock)
    (log.info if rep.ok else log.error)("%s", rep.text())
    return rep


def _short_meta(cfg, x: dict, long_id: str, tags: list, list_id: str | None = None) -> dict:
    from .playlists import watch_url
    credit = cfg.upload.get("music_credit", DEFAULT_CREDIT)
    sdesc = _no_angles(f"{x.get('hook') or ''}\n\nFull video: {watch_url(long_id, list_id)}\n"
                       + (f"Subscribe: {_sub_link(cfg)}\n" if _sub_link(cfg) else "") + "\n"
                       + (f"{credit}\n" if credit else "") + f"{AI_LINE}\n\n#Shorts {_hashtags(cfg, 14)}")
    title = x["title"] if "#shorts" in x["title"].lower() else f"{x['title'][:88]} #Shorts"
    return {"title": title, "description": sdesc, "tags": list(tags) + ["shorts"]}


def _add_to_playlist(cfg, vid: str, fmt: str) -> str | None:
    """Pitkä video muodon soittolistaan (uutiset / oppaat). Virhe ei kaada latausta. Palauttaa listan id:n."""
    try:
        from . import playlists
        from .uploader import _service
        return playlists.add(cfg, _service(cfg.paths.secrets_dir), vid, fmt)
    except Exception as e:  # noqa: BLE001
        log.warning("Videon %s lisäys soittolistaan epäonnistui: %s", vid, e)
        return None


def _job_meta(st: State, job: tuple[str, str], **kw):
    row = st.job(*job) or {}
    m = dict(row.get("meta") or {})
    m.update(kw)
    st.job_update(*job, meta=m)


def publish(cfg, st: State, prod: dict, publish_at: str | None, job: tuple[str, str] | None = None) -> str:
    """Lataus: pitkä video + variantti 1 thumbnailiksi, sitten Shortsit linkillä pitkään videoon.
    upload.auto_publish: true -> ajastettu julkaisu (private + publishAt), muuten upload.privacy kuten ennen.
    job = (nimi, päivä): jokainen ladattu id kirjataan heti state.db:hen; uusinta ohittaa jo ladatut, ja jos edellinen
    yritys keskeytyi kesken latauksen, YouTubesta katsotaan ensin ehtikö video nousta (sama otsikko) -> ei kahdesti."""
    from . import uploader

    meta, out, thumbs = prod["meta"], prod["out"], prod["thumbs"]
    row = (st.job(*job) or {}) if job else {}
    jm = row.get("meta") or {}
    long_at = None
    if jm.get("long_publish_at"):
        long_at = dt.datetime.fromisoformat(jm["long_publish_at"])
    elif scheduler.auto_publish(cfg):
        if publish_at:
            long_at = scheduler.publish_time(cfg, publish_at)
        else:
            log.warning("upload.auto_publish on päällä, mutta tälle ajolle ei ole publish_at-aikaa "
                        "-> video jää yksityiseksi")

    def got_long(vid: str):
        if job:
            st.job_update(*job, long_id=vid, stage="long_uploaded")

    yt_id = row.get("long_id")
    list_id = None
    if yt_id:
        log.info("Pitkä video on jo ladattu tälle työlle (https://youtu.be/%s) -> ei ladata uudelleen", yt_id)
        list_id = _add_to_playlist(cfg, yt_id, meta.get("format") or cfg.format)
    else:
        if job and jm.get("long_upload_started"):
            yt_id = uploader.find_recent_upload(cfg, meta["title"], since=float(jm["long_upload_started"]) - 300)
            if yt_id:
                log.warning("Edellinen yritys ehti ladata pitkän videon (https://youtu.be/%s) ennen keskeytystä "
                            "-> otetaan se käyttöön, ei ladata uudelleen", yt_id)
                try:  # keskeytynyt lataus ei ehtinyt asettaa thumbnailia eikä tarkistaa näkyvyyttä
                    yt = uploader._service(cfg.paths.secrets_dir)
                    if thumbs and cfg.upload.set_thumbnail:
                        uploader.set_thumbnail(yt, yt_id, Path(thumbs[0]))
                    if not long_at and cfg.upload.privacy == "public":
                        uploader.ensure_public(yt, yt_id, None)
                except Exception:  # noqa: BLE001 - video on jo YouTubessa; Shortsit jatkuvat
                    log.exception("Käyttöön otetun videon %s thumbnail/näkyvyys epäonnistui", yt_id)
        if not yt_id:
            if job:
                _job_meta(st, job, long_upload_started=time.time(), long_title=meta["title"],
                          long_publish_at=long_at.isoformat() if long_at else None)
                st.job_update(*job, stage="uploading_long")
            yt_id = uploader.upload(cfg, out, meta, thumbs[0] if thumbs else None, publish_at=long_at,
                                    on_uploaded=got_long)
        got_long(yt_id)
        if long_at:
            st.add_scheduled(yt_id, "long", long_at.isoformat())
        list_id = _add_to_playlist(cfg, yt_id, meta.get("format") or cfg.format)

    failed = []
    if cfg.shorts.upload and prod["shorts"]:
        done = dict(((st.job(*job) or {}).get("shorts") or {})) if job else {}
        todo = []
        for x in prod["shorts"]:
            sid = done.get(x["path"].name)
            if sid and sid != PENDING:
                log.info("Short %s on jo ladattu (https://youtu.be/%s) -> ohitetaan", x["path"].name, sid)
            else:
                todo.append(x)
        slots = scheduler.short_slots(cfg, len(todo), long_at, scheduler.taken_short_slots(st)) \
            if long_at else [None] * len(todo)
        for x, slot in zip(todo, slots):
            file = x["path"].name
            smeta = _short_meta(cfg, x, yt_id, meta.get("tags") or [], list_id)
            sid = None
            if job and done.get(file) == PENDING:
                sid = uploader.find_recent_upload(cfg, smeta["title"], since=float(row.get("started") or 0) - 300)
                if sid:
                    log.warning("Edellinen yritys ehti ladata Shortsin %s (https://youtu.be/%s) -> ei ladata "
                                "uudelleen", file, sid)
            try:  # yksi epäonnistunut Short ei kaada muita (uusinta lataa vain puuttuvat)
                if not sid:
                    if job:
                        st.job_short_uploaded(*job, file, PENDING)
                    sid = uploader.upload(cfg, x["path"], smeta, None, publish_at=slot,
                                          on_uploaded=(lambda v, f=file: st.job_short_uploaded(*job, f, v))
                                          if job else None)
                if job:
                    st.job_short_uploaded(*job, file, sid)
            except Exception as e:  # noqa: BLE001
                if _is_limit_error(e):
                    raise
                log.exception("Shortsin lataus epäonnistui: %s", file)
                failed.append(file)
                continue
            x["youtube_id"] = sid
            if slot:
                st.add_scheduled(sid, "short", slot.isoformat())
    if not long_at and cfg.upload.privacy == "public" and cfg.upload.get("relink_shorts", True):
        try:  # kaikki vanhatkin Shortsit ohjaavat uusimpaan pitkään videoon
            uploader.relink_shorts(cfg, yt_id, list_id=list_id)
        except Exception:  # noqa: BLE001 - video ja Shortsit ovat jo ylhäällä
            log.exception("Shortsien linkkien päivitys epäonnistui (%s)", yt_id)
    if failed and job:
        raise RuntimeError(f"{len(failed)} Shortsia jäi lataamatta ({', '.join(failed)}); uusinta lataa vain ne")
    return yt_id


def finish(cfg, st: State, prod: dict, yt_id: str | None, mock: bool, script_file: str | None = None):
    src, gtopic = prod.get("src"), prod.get("gtopic")
    if src and src.get("id") and not mock:
        st.mark_used(src["id"])
    if gtopic and not mock and not script_file:
        st.mark_guide_topic(gtopic["topic"])
    st.log_run(title=prod["meta"]["title"], source_video=src["id"] if src else None, output=str(prod["out"]),
               youtube_id=yt_id, status="uploaded" if yt_id else "rendered")
    log.info("Valmis: %s", prod["out"])
    # 8) siivous: tämän ajon isot välitiedostot; oikeissa ajoissa myös vanhat työkansiot/outputit/lokit
    if prod.get("work"):
        housekeeping.after_run(cfg, prod["work"], prune_old=not mock)


def _qa_alert(what: str, rep: qa.Report, out: Path | None):
    reasons = "\n".join(f"- {x}" for x in rep.failures()[:12])
    alerts.send(f"Catfish-botti: laatuportti hylkäsi videon ({what}), EI ladattu. Tiedostot tallessa: "
                f"{Path(out).name if out else '-'}\nSyyt:\n{reasons}")


def run_once(cfg, mock: bool = False, do_upload: bool = True, script_file: str | None = None,
             topic: str | None = None, publish_at: str | None = None) -> Path:
    """Käsin ajettu `run` (ei työtilaa). publish_at = julkaisun kellonaika (HH:MM, schedule.timezone); käytössä vain
    kun upload.auto_publish: true. Laatuportti ajetaan aina; lataus vain jos se hyväksyy."""
    st = State(cfg.paths.db)
    if script_file:
        do_upload = False  # valmis käsikirjoitus: ei latausta
    uploading = do_upload and not mock and cfg.upload.privacy != "none"
    if uploading:
        try:  # aiemmin latausrajaan jääneet ensin
            upload_pending(cfg)
        except Exception:  # noqa: BLE001
            log.exception("Jonon lataus epäonnistui")
    prod = produce(cfg, st, mock=mock, script_file=script_file, topic=topic)
    rep = run_qa(cfg, prod, mock)
    if not uploading and not rep.ok:
        log.warning("Laatuportti hylkäsi videon (tässä ajossa ei ladata, raportti yllä)")
    yt_id = None
    if uploading:
        if not rep.ok:
            _qa_alert("käsin ajettu run", rep, prod["out"])
            raise QAFailed("Laatuportti hylkäsi videon: " + "; ".join(rep.failures()))
        long_at = scheduler.publish_time(cfg, publish_at) if publish_at and scheduler.auto_publish(cfg) else None
        job = {"video": str(prod["out"]), "meta": str(prod["out"].with_suffix(".json")),
               "thumb": str(prod["thumbs"][0]) if prod["thumbs"] else None,
               "publish_at": long_at.isoformat() if long_at else None,
               "shorts": [{"path": str(x["path"]), "title": x["title"], "hook": x["hook"]} for x in prod["shorts"]]
               if cfg.shorts.upload else [], "long_id": None, "done_shorts": []}
        yt_id = _upload_job(cfg, st, job)  # latausraja -> data/pending_uploads.json, tunnin välein uusinta
    finish(cfg, st, prod, yt_id, mock, script_file)
    return prod["out"]


def breakdown_job(cfg, name: str, video: dict, day: str) -> dict:
    from . import breakdown
    b = breakdown.bcfg(cfg)
    return {"name": name, "format": "breakdown", "date": day, "time": "00:00", "once": True, "video": video,
            "privacy": b.get("privacy") or "public", "topic": None, "publish_at": None}


def _find_job(cfg, name: str, day: str | None = None) -> dict:
    if name.startswith("breakdown") and day:
        for r in State(cfg.paths.db).breakdown_jobs(day):
            if r["job"] == name:
                return breakdown_job(cfg, name, r["video"], day)
    j = scheduler.find_job(cfg, name, day)
    if j is None:
        raise RuntimeError(f"Tuntematon työ {name} ({day}) (schedule.jobs / schedule.once)")
    return j


def _picture_failure(f: str) -> bool:
    """QA-hylkäys, jonka uusi satunnainen tausta korjaa (pitkän videon musta tai pysähtynyt kuva)."""
    return f.startswith("video: ") and ("mustaa <" in f or "pysähtynyt kuva" in f)


RESUMABLE = ("rendered", "qa_passed", "uploading_long", "long_uploaded", "upload_queued")


def run_job(cfg, name: str, day: str, *, preflight_fn=None) -> int:
    """Ajastettu työ (lapsiprosessi): idempotentti per (nimi, päivä). Palauttaa poistumiskoodin.
    Jatkaa siitä mitä puuttuu: valmis -> ei mitään; pitkä ladattu -> vain puuttuvat Shortsit; renderöity -> QA ja
    lataus ilman uudelleenrenderöintiä; käsikirjoitus valmis -> ei uutta Claude-kutsua; QA hylätty -> alusta."""
    job = _find_job(cfg, name, day)
    cfg["format"] = job["format"]
    if job.get("privacy"):  # schedule.once[].privacy: vain tämä työ (laatuportti pätee silti)
        cfg["upload"] = dict(cfg["upload"], privacy=job["privacy"])
        log.info("Työn %s näkyvyys: %s", name, job["privacy"])
    st = State(cfg.paths.db)
    row = st.job(name, day)
    if row and row["stage"] == "done":
        log.info("Työ %s (%s) on jo valmis -> ei tehdä mitään (https://youtu.be/%s)", name, day, row.get("long_id"))
        return EXIT_OK
    row = st.job_begin(name, day)
    key = (name, day)
    log.info("Työ %s päivälle %s: yritys %d, vaihe %s", name, day, row["attempts"], row["stage"])
    uploading = cfg.upload.privacy != "none"
    problems = (preflight_fn or preflight.run)(cfg, youtube=uploading)
    if problems:
        st.job_update(*key, error="esitarkistus: " + "; ".join(problems))
        alerts.send(f"Catfish-botti: työn {name} ({day}) esitarkistus epäonnistui, työ ei alkanut:\n- "
                    + "\n- ".join(problems))
        return EXIT_PREFLIGHT
    try:
        prod = None
        if row["stage"] in RESUMABLE:
            prod = load_saved(cfg, row.get("output"), row.get("work_dir"))
            if prod is None and row["stage"] == "uploading_long" and (row.get("meta") or {}).get("long_title"):
                # tiedostot hävinneet kesken latauksen: ehtikö video silti nousta?
                from . import uploader
                jm = row["meta"]
                vid = uploader.find_recent_upload(cfg, jm["long_title"], since=float(jm["long_upload_started"]) - 300)
                if vid:
                    st.job_update(*key, long_id=vid, stage="long_uploaded")
                    row = st.job(*key)
            if prod is None:
                if row.get("long_id"):
                    raise RuntimeError(f"Pitkä video on jo ladattu (https://youtu.be/{row['long_id']}), mutta "
                                       f"tallennettua tuotosta {row.get('output')} ei löydy -> ei ladata mitään")
                log.warning("Tallennettua tuotosta %s ei löydy -> video tehdään alusta", row.get("output"))
            else:
                log.info("Jatketaan tallennetusta tuotoksesta %s (ei uudelleenrenderöintiä)", prod["out"].name)
        if prod is None:
            reuse = None
            if row["stage"] == "started" and row.get("work_dir") and (Path(row["work_dir"]) / "script.json").is_file():
                reuse = str(Path(row["work_dir"]) / "script.json")
            if row["stage"] == "qa_failed":
                fails = (row.get("qa") or {}).get("failures") or []
                wd = Path(row["work_dir"]) if row.get("work_dir") else None
                if fails and all(_picture_failure(f) for f in fails) and wd and (wd / "script.json").is_file():
                    # vain kuvavirhe (musta/pysähtynyt tausta): sama hyväksytty käsikirjoitus, uusi satunnainen tausta
                    reuse = str(wd / "script.json")
                    log.info("Edellinen yritys hylättiin vain kuvan takia (%s) -> sama käsikirjoitus, uusi tausta",
                             "; ".join(fails)[:200])
                else:
                    log.info("Edellinen yritys hylättiin laatuportissa -> uusi video alusta")
            prod = produce(cfg, st, topic=job.get("topic"), reuse_script=reuse, job=job,
                           on_script=lambda w: st.job_update(*key, work_dir=str(w)))
            st.job_update(*key, stage="rendered", output=str(prod["out"]), work_dir=str(prod["work"]),
                          long_id=None, shorts={}, meta={})
        row = st.job(*key)
        if row["stage"] == "upload_queued" and (row.get("qa") or {}).get("ok"):
            rep = qa.Report()
            rep.add("laatuportti", "hyväksytty jo ennen jonoon menoa (tiedostot ennallaan)", True)
            log.info("Latausjonosta: laatuportti hyväksyi tämän tuotoksen jo aiemmin")
        elif row.get("long_id"):
            remaining = [x for x in prod["shorts"]
                         if (row.get("shorts") or {}).get(x["path"].name) in (None, PENDING)]
            rep = run_qa(cfg, prod, mock=False, shorts_only=remaining if cfg.shorts.upload else [])
        else:
            rep = run_qa(cfg, prod, mock=False)
        st.job_update(*key, qa=rep.as_dict())
        if not rep.ok:
            if not row.get("long_id"):
                st.job_update(*key, stage="qa_failed")
            st.job_update(*key, error="QA: " + "; ".join(rep.failures())[:800])
            _qa_alert(f"työ {name} {day}", rep, prod["out"])
            return EXIT_QA
        if not row.get("long_id") and row["stage"] != "uploading_long":
            st.job_update(*key, stage="qa_passed")
        yt_id = None
        if uploading:
            try:
                yt_id = publish(cfg, st, prod, job.get("publish_at"), job=key)
            except Exception as e:  # noqa: BLE001
                if not _is_limit_error(e):
                    raise
                _queue(cfg, {"job": [name, day], "video": str(prod["out"]), "queued": time.time()},
                       f"job:{name}/{day}")
                st.job_update(*key, stage="upload_queued",
                              error="YouTuben latausraja täynnä: latausjonossa, uusinta tunnin välein")
                log.warning("YouTuben latausraja täynnä -> työ %s (%s) latausjonoon", name, day)
                alerts.send(f"Catfish-botti: YouTuben kanavan latausraja täynnä, työn {name} ({day}) video jäi "
                            f"latausjonoon ({prod['out'].name}); yritetään tunnin välein uudelleen.")
                return EXIT_OK
        else:
            log.info("upload.privacy: none -> ei latausta")
        finish(cfg, st, prod, yt_id, mock=False)
        st.job_update(*key, stage="done", error=None)
        return EXIT_OK
    except Exception as e:
        st.job_update(*key, error=alerts.redact(f"{type(e).__name__}: {e}")[:800])
        raise


def job_command(job: dict, day: str, config_path: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "bot.main", "run", "--job", job["name"], "--day", day]
    if config_path:
        cmd += ["--config", str(Path(config_path).resolve())]
    return cmd


class JobTimeout(RuntimeError):
    pass


def spawn_job(cmd: list[str], limit_s: float, name: str = "job", popen=subprocess.Popen, cwd=None) -> int:
    """Käynnistää työn lapsiprosessina ja odottaa enintään limit_s. Aikaraja -> koko prosessipuu (myös ffmpeg)
    tapetaan ja nostetaan JobTimeout. Palauttaa paluukoodin."""
    kw = {"cwd": str(cwd or ROOT)}
    if not proc.IS_WINDOWS:
        kw["start_new_session"] = True  # oma prosessiryhmä -> killpg tappaa myös ffmpegit
    p = popen(cmd, **kw)
    try:
        rc = proc.wait_child(p, limit_s)
    except BaseException:
        proc.kill_tree(p, group=not proc.IS_WINDOWS)
        raise
    if rc is None:
        lim = f"{limit_s / 60:.0f} min" if limit_s >= 120 else f"{limit_s:.0f} s"
        log.error("Ajo %s ylitti aikarajan %s -> tapetaan prosessipuu", name, lim)
        proc.kill_tree(p, group=not proc.IS_WINDOWS)
        raise JobTimeout(f"Ajo {name} ylitti aikarajan {lim} ja tapettiin (myös ffmpeg-prosessit)")
    return rc


def job_timeout_s(cfg) -> float:
    return float((cfg.get("schedule") or {}).get("job_timeout_minutes", 150) or 150) * 60


def daily_summary(cfg, day: str) -> str:
    st = State(cfg.paths.db)
    lines = [f"Catfish-botti: päivän {day} yhteenveto"]
    rows = st.jobs_on(day)
    if not rows:
        lines.append("- ei ajastettuja töitä")
    for r in rows:
        line = f"- {r['name']}: {r['stage']}"
        if r.get("long_id"):
            line += f", video https://youtu.be/{r['long_id']}"
        shorts = [v for v in (r.get("shorts") or {}).values() if v and v != PENDING]
        if shorts:
            line += ", Shorts " + " ".join(f"https://youtu.be/{v}" for v in shorts)
        q = r.get("qa") or {}
        if q:
            line += ", QA " + ("ok" if q.get("ok") else "HYLÄTTY")
        if r.get("error") and r["stage"] != "done":
            line += f", virhe: {r['error'][:160]}"
        lines.append(line)
    return "\n".join(lines)


def schedule(cfg, config_path: str | None = None, dry_run: bool = False):
    """Päivittäiset ajot (schedule.jobs) peräkkäin yhdessä silmukassa, ks. bot/scheduler.py. Jokainen työ ajetaan
    omana lapsiprosessinaan kovalla aikarajalla (schedule.job_timeout_minutes)."""
    if dry_run:
        print(scheduler.dry_run(cfg, taken=scheduler.taken_short_slots(State(cfg.paths.db))))
        print(f"\nJokainen työ ajetaan lapsiprosessina (run --job NIMI --day PÄIVÄ), aikaraja "
              f"{job_timeout_s(cfg) / 60:.0f} min (schedule.job_timeout_minutes)")
        return

    def run_job_child(job_cfg, job, day):
        limit = job_timeout_s(job_cfg)
        cmd = job_command(job, day, config_path)
        log.info("Käynnistetään työ %s (%s) lapsiprosessina, aikaraja %.0f min", job["name"], day, limit / 60)
        try:
            rc = spawn_job(cmd, limit, job["name"])
        except JobTimeout as e:
            State(job_cfg.paths.db).job_update(job["name"], day, error=str(e))
            alerts.send(f"Catfish-botti: {e} ({day}).")
            raise
        if rc != 0:
            row = State(job_cfg.paths.db).job(job["name"], day) or {}
            raise RuntimeError(f"Työ {job['name']} päättyi koodilla {rc}: {row.get('error') or 'ks. loki'}")

    def on_final_failure(job_cfg, job, day, err):
        alerts.send(f"Catfish-botti: työ {job['name']} ({day}) epäonnistui myös uusinnassa: {err}")

    def on_day_done(job_cfg, day):
        st = State(job_cfg.paths.db)
        if st.summary_sent(day):
            return
        alerts.send(daily_summary(job_cfg, day), level=logging.INFO)
        st.mark_summary(day)

    def catch_up(job_cfg, now):
        """Käynnistyksessä: tämän päivän työt, jotka alkoivat mutta eivät valmistuneet (kone tai kontti käynnistyi
        uudelleen kesken ajon) ja joilla on yritys jäljellä -> jatketaan heti."""
        st = State(job_cfg.paths.db)
        day = now.date().isoformat()
        out = []
        bds = [breakdown_job(job_cfg, r["job"], r["video"], day) for r in st.breakdown_jobs(day)]
        for j in scheduler.jobs(job_cfg) + [o for o in scheduler.once_jobs(job_cfg) if o["date"] == day] + bds:
            r = st.job(j["name"], day)
            if r and r["stage"] != "done" and int(r.get("attempts") or 0) < 2:
                log.warning("Työ %s (%s) jäi kesken (vaihe %s) -> jatketaan heti", j["name"], day, r["stage"])
                out.append((now, j, day))
        return out

    def watch(job_cfg, now):
        """Breakdown-vahti: Rockstarin uudet GTA VI -videot -> heti ajettava työ (max breakdown.max_per_day)."""
        from . import breakdown
        b = breakdown.bcfg(job_cfg)
        if not b.get("enabled") or now < breakdown._since(b.get("watch_from") or "2026-11-18T00:00:00+02:00"):
            return []
        st = State(job_cfg.paths.db)
        day = now.date().isoformat()
        out = []
        found = breakdown.find_new(job_cfg, env("YOUTUBE_API_KEY"), seen=st.breakdown_seen)
        for v in sorted(found, key=lambda x: -x["duration"]):  # pääversio (pisin) ennen lyhyitä mainoksia
            n = len(st.breakdown_jobs(day))
            if n >= int(b.get("max_per_day", 1) or 1):
                st.add_breakdown(v, day, "skipped_max_per_day")
                log.warning("Uusi Rockstarin video %s, mutta päivän breakdown-raja täynnä -> ohitetaan", v["title"])
                continue
            name = "breakdown" if n == 0 else f"breakdown{n + 1}"
            st.add_breakdown(v, day, "queued", job=name)
            alerts.send(f"Catfish-botti: Rockstar julkaisi uuden videon \"{v['title']}\" ({v['url']}), "
                        f"breakdown-video tehdään heti.", level=logging.INFO)
            out.append((breakdown_job(job_cfg, name, v, day), day))
        return out

    def once_seen(job_cfg, job, day):
        return State(job_cfg.paths.db).job(job["name"], day) is not None

    def pending(job_cfg):
        """Latausjono (data/pending_uploads.json) tunnin välein omana lapsiprosessinaan aikarajalla."""
        if not _load_pending(job_cfg) and not stuck_candidates(job_cfg):
            return
        cmd = [sys.executable, "-m", "bot.main", "upload-pending"]
        if config_path:
            cmd += ["--config", str(Path(config_path).resolve())]
        log.info("Latausjonossa %d työtä, käsittelyn tarkistus %d videolle", len(_load_pending(job_cfg)),
                 len(stuck_candidates(job_cfg)))
        try:
            spawn_job(cmd, job_timeout_s(job_cfg), "upload-pending")
        except JobTimeout as e:
            alerts.send(f"Catfish-botti: {e}.")

    scheduler.loop(lambda: load(config_path), run_job_child, on_final_failure=on_final_failure,
                   on_day_done=on_day_done, catch_up=catch_up, pending=pending, once_seen=once_seen, watch=watch,
                   watch_every_s=float((cfg.get("breakdown") or {}).get("interval_minutes", 20) or 20) * 60)


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "schedule", "research", "cleanup", "status", "upload-pending",
                                    "breakdown", "index-footage"])
    ap.add_argument("--watch-from", help="breakdown --dry-run: Rockstarin videot tämän jälkeen (esim. 2026-08-01)")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--script", help="valmis käsikirjoitus-JSON (+ valinnainen 'articles'); ei API-kutsuja eikä latausta")
    ap.add_argument("--format", choices=["long", "short", "guide", "breakdown"])
    ap.add_argument("--topic", help="opasvideon aihe (--format guide); oletus: seuraava käyttämätön guide.topics")
    ap.add_argument("--dry-run", action="store_true",
                    help="schedule: tulosta seuraavat ajot ja julkaisuajat; cleanup: näytä mitä poistettaisiin")
    ap.add_argument("--job", help=argparse.SUPPRESS)   # ajastimen lapsiprosessi: schedule.jobs[].name
    ap.add_argument("--day", help=argparse.SUPPRESS)   # ... ja työn päivä YYYY-MM-DD
    ap.add_argument("--mode", choices=["default", "launch_streams"], default="default",
                    help="research: launch_streams = julkaisupäivän pelaajavideot ja tekstitykset")
    ap.add_argument("--after", help="research --mode launch_streams: publishedAfter (RFC 3339, esim. "
                                    "2026-11-18T11:00:00Z)")
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # Windows-konsoli: cp1252 ei osaa ä/ö kaikkialla
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logsetup.setup(a.verbose)
    cfg = load(a.config)
    if not (a.cmd == "schedule" and a.dry_run) and a.cmd != "status":
        logsetup.add_file_log(cfg)  # data/logs/bot-YYYY-MM-DD.log
    if a.format:
        cfg["format"] = a.format
    if a.cmd == "run" and a.job:
        log.info("Komento: %s", " ".join(sys.argv[1:]))
        sys.exit(run_job(cfg, a.job, a.day or dt.date.today().isoformat()))
    if a.cmd == "run":
        log.info("Komento: %s", " ".join(sys.argv[1:]))
        pub = scheduler.publish_at_for_format(cfg, cfg.get("format", "long")) if scheduler.auto_publish(cfg) else None
        try:
            run_once(cfg, mock=a.mock, do_upload=not a.no_upload, script_file=a.script, topic=a.topic,
                     publish_at=pub)
        except QAFailed as e:
            log.error("%s", e)
            sys.exit(EXIT_QA)
    elif a.cmd == "research":
        if a.mode == "launch_streams":
            research.launch_dry_run(cfg, env("YOUTUBE_API_KEY", required=False), a.after)
        else:
            research.dry_run(cfg, env("YOUTUBE_API_KEY", required=False), a.topic)
    elif a.cmd == "cleanup":
        housekeeping.cleanup_all(cfg, dry_run=a.dry_run)
    elif a.cmd == "breakdown":
        from . import breakdown
        wf = a.watch_from or (cfg.get("breakdown") or {}).get("watch_from")
        if wf and len(wf) == 10:
            wf += "T00:00:00+00:00"
        breakdown.dry_run(cfg, env("YOUTUBE_API_KEY"), wf)
    elif a.cmd == "upload-pending":
        try:
            recover_stuck(cfg)
        except Exception:  # noqa: BLE001 - jono ladataan silti
            log.exception("Jumiin jääneiden videoiden tarkistus epäonnistui")
        left = upload_pending(cfg)
        log.info("Latausjonoon jäi %d työtä", left)
    elif a.cmd == "index-footage":
        from . import footage
        idx = footage.build(cfg, tag=not a.mock)
        n_sc = sum(len(v.get("scenes") or []) for v in idx["videos"].values())
        print(f"Taustaindeksi: {len(idx['videos'])} videota, {n_sc} kohtausta, {len(idx['stills'])} kuvaa, "
              f"{len(footage.candidates(cfg, idx))} käyttökelpoista -> {footage.index_path(cfg)}")
    elif a.cmd == "status":
        from . import status
        print(status.report(cfg))
    else:
        schedule(cfg, a.config, dry_run=a.dry_run)


if __name__ == "__main__":
    cli()
