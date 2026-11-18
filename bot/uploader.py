"""YouTube-lataus OAuthilla. Token luodaan kerran: python -m bot.auth_youtube

upload.auto_publish: false -> privacyStatus = upload.privacy (oletus private), kuten ennenkin.
upload.auto_publish: true  -> privacyStatus private + status.publishAt (ajastettu julkaisu, kutsuja antaa ajan).

Luotettavuus: jokaisella HTTP-kutsulla on aikaraja (HTTP_TIMEOUT); latauspalat uusitaan 5xx- ja yhteysvirheissä
(enintään MAX_CHUNK_RETRIES, koko lataus enintään upload.upload_timeout_minutes); latauksen jälkeen odotetaan
YouTuben käsittely (enintään upload.verify_minutes = 30 min); hylätty/epäonnistunut lataus uusitaan kerran;
thumbnail asetetaan ja vahvistetaan (enintään 3 yritystä).
"""
import datetime as dt
import logging
import re
import socket
import time
from pathlib import Path

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
LOCK_WARNING = (
    "YouTube lukitsi videon %s yksityiseksi: varmentamattoman Google API -projektin lataukset rajataan "
    "yksityisiksi, eikä ajastettu julkaisu (upload.auto_publish) toimi ennen kuin Google on hyväksynyt projektin "
    "YouTube API Services -auditoinnissa. Julkaise video itse YouTube Studiossa: https://studio.youtube.com/video/%s/edit")
HTTP_TIMEOUT = 120                     # s, yksi HTTP-kutsu (myös yksi 8 Mt:n latauspala)
MAX_CHUNK_RETRIES = 10                 # 5xx-/yhteysvirheitä per lataus ennen luovutusta
RETRY_STATUSES = (500, 502, 503, 504)
DONE_STATES = ("processed", "failed", "rejected", "deleted")


class UploadError(RuntimeError):
    """Lataus epäonnistui pysyvästi (liikaa uusintoja, aikaraja tai YouTube hylkäsi myös uusinnan)."""


def _service(secrets_dir: str, timeout: float = HTTP_TIMEOUT):
    """YouTube-asiakas, jonka jokaisella HTTP-kutsulla on aikaraja (httplib2 odottaisi muuten loputtomiin)."""
    import google_auth_httplib2
    import httplib2
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = Path(secrets_dir) / "token.json"
    if not token.exists():
        raise RuntimeError(f"{token} puuttuu. Aja ensin: python -m bot.auth_youtube")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    raw = httplib2.Http(timeout=timeout)
    # Kuten googleapiclient.http.build_http: YouTuben jatkettava lataus vastaa 308 Resume Incomplete, jota httplib2
    # pitää muuten uudelleenohjauksena (RedirectMissingLocation, lataus ei koskaan etene).
    if hasattr(raw, "redirect_codes"):
        raw.redirect_codes = raw.redirect_codes - {308}
    http = google_auth_httplib2.AuthorizedHttp(creds, http=raw)
    return build("youtube", "v3", http=http, cache_discovery=False)


def rfc3339(t: dt.datetime) -> str:
    """Aikavyöhykkeellinen aika -> RFC 3339 UTC (YouTube status.publishAt)."""
    if t.tzinfo is None:
        raise ValueError("publish_at tarvitsee aikavyöhykkeen")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_status(yt, ids: list[str]) -> dict[str, dict]:
    """videos.list part=status (1 kiintiöyksikkö / 50 videota). Poistettu video puuttuu tuloksesta."""
    out = {}
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="status", id=",".join(ids[i:i + 50])).execute()
        for it in resp.get("items", []):
            out[it["id"]] = it.get("status", {})
    return out


def _status_code(e) -> int | None:
    try:
        return int(getattr(getattr(e, "resp", None), "status", None))
    except (TypeError, ValueError):
        return None


def _retryable(e: Exception) -> bool:
    import httplib2
    from googleapiclient.errors import HttpError
    if isinstance(e, HttpError):
        return _status_code(e) in RETRY_STATUSES
    return isinstance(e, (ConnectionError, TimeoutError, socket.timeout, httplib2.HttpLib2Error, OSError))


def _insert(cfg, yt, video: Path, body: dict, sleep, clock) -> str:
    """Yksi resumable-lataus. Pala epäonnistuu 5xx-/yhteysvirheeseen -> sama pala uudelleen (kasvava odotus);
    4xx (esim. uploadLimitExceeded) nostetaan heti."""
    from googleapiclient.http import MediaFileUpload

    limit_s = float(cfg.upload.get("upload_timeout_minutes", 60) or 60) * 60
    deadline = clock() + limit_s
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True))
    resp, errors = None, 0
    while resp is None:
        if clock() > deadline:
            raise UploadError(f"Lataus {video.name} ylitti aikarajan {limit_s / 60:.0f} min")
        try:
            status_, resp = req.next_chunk()
        except Exception as e:  # noqa: BLE001
            if not _retryable(e):
                raise
            errors += 1
            if errors > MAX_CHUNK_RETRIES:
                raise UploadError(f"Lataus {video.name} epäonnistui {MAX_CHUNK_RETRIES} uusinnan jälkeen: "
                                  f"{type(e).__name__}: {e}") from e
            wait = min(2 ** errors, 60)
            log.warning("Latauspala epäonnistui (%s), uusinta %d/%d %d s päästä", type(e).__name__, errors,
                        MAX_CHUNK_RETRIES, wait)
            sleep(wait)
            continue
        if status_:
            log.info("Upload %d%%", int(status_.progress() * 100))
    return resp["id"]


def wait_processed(yt, vid: str, minutes: float, sleep=time.sleep, clock=time.monotonic) -> dict | None:
    """Odottaa enintään minutes, että uploadStatus on processed/failed/rejected. Palauttaa viimeisen statuksen."""
    end = clock() + float(minutes) * 60
    st = None
    while True:
        try:
            items = yt.videos().list(part="status,processingDetails", id=vid).execute().get("items") or []
            st = items[0].get("status", {}) if items else None
        except Exception as e:  # noqa: BLE001 - tilan luku ei saa kaataa latausta
            log.warning("Videon %s tilan luku epäonnistui: %s", vid, e)
        us = (st or {}).get("uploadStatus")
        if us in DONE_STATES:
            return st
        if clock() >= end:
            log.warning("YouTube ei käsitellyt videota %s %.0f minuutissa (tila %s) -> jatketaan", vid, minutes, us)
            return st
        sleep(max(min(30.0, end - clock()), 1.0))


def _thumb_confirmed(yt, vid: str) -> bool:
    try:
        items = yt.videos().list(part="snippet", id=vid).execute().get("items") or []
    except Exception as e:  # noqa: BLE001
        log.warning("Thumbnailin vahvistus epäonnistui: %s", e)
        return False
    th = ((items[0].get("snippet") or {}).get("thumbnails") or {}) if items else {}
    return bool({"maxres", "high"} & set(th))


def set_thumbnail(yt, vid: str, thumb: Path, sleep=time.sleep, tries: int = 3) -> bool:
    """thumbnails.set + vahvistus videos.list-haulla, enintään tries yritystä. 4xx (tili ei vahvistettu, kuva
    hylätty) -> ei uusintaa. True = asetettu ja vahvistettu."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    for k in range(tries):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
            if _thumb_confirmed(yt, vid):
                log.info("Thumbnail asetettu ja vahvistettu: %s", vid)
                return True
            log.warning("Thumbnailia ei vielä näy videolla %s (yritys %d/%d)", vid, k + 1, tries)
        except HttpError as e:
            code = _status_code(e)
            if code is not None and code < 500:
                log.warning("Thumbnailin asetus epäonnistui (HTTP %s, onko tili vahvistettu?): %s", code, e)
                return False
            log.warning("Thumbnailin asetus epäonnistui (HTTP %s), yritys %d/%d", code, k + 1, tries)
        except Exception as e:  # noqa: BLE001
            if not _retryable(e):
                log.warning("Thumbnailin asetus epäonnistui: %s", e)
                return False
            log.warning("Thumbnailin asetus epäonnistui (%s), yritys %d/%d", type(e).__name__, k + 1, tries)
        if k + 1 < tries:
            sleep(10 * (k + 1))
    log.error("Thumbnailia ei saatu vahvistettua videolle %s %d yrityksellä", vid, tries)
    return False


def _check_lock(vid: str, st: dict | None, want_publish: dt.datetime | None):
    if not st:
        return
    log.info("YouTube-tila %s: privacyStatus=%s publishAt=%s uploadStatus=%s", vid, st.get("privacyStatus"),
             st.get("publishAt"), st.get("uploadStatus"))
    if want_publish:
        got = _parse(st.get("publishAt"))
        if got is None or abs((got - want_publish).total_seconds()) > 60 or st.get("privacyStatus") != "private":
            log.warning(LOCK_WARNING, vid, vid)


def upload(cfg, video: Path, meta: dict, thumb: Path | None, publish_at: dt.datetime | None = None,
           on_uploaded=None, yt=None, sleep=time.sleep, clock=time.monotonic) -> str:
    """publish_at (aikavyöhykkeellinen) -> ajastettu julkaisu: private + status.publishAt. None -> upload.privacy.
    on_uploaded(id) kutsutaan heti kun YouTube on antanut id:n (ennen käsittelyn odotusta), jotta kutsuja voi
    kirjata sen state.db:hen. Hylätty/epäonnistunut käsittely -> ladataan kerran uudelleen (on_uploaded uudella
    id:llä); toinen hylkäys -> UploadError."""
    from . import alerts

    yt = yt or _service(cfg.paths.secrets_dir)
    privacy = "private" if publish_at else cfg.upload.privacy
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": bool(cfg.upload.contains_synthetic_media)}
    if publish_at:
        status["publishAt"] = rfc3339(publish_at)
    body = {
        "snippet": {"title": meta["title"], "description": meta["description"][:4900],
                    "tags": list(meta.get("tags") or [])[:30], "categoryId": cfg.upload.category_id,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": status,
    }
    verify_min = float(cfg.upload.get("verify_minutes", 30) or 30)
    st, vid = None, None
    for attempt in (1, 2):
        vid = _insert(cfg, yt, Path(video), body, sleep, clock)
        when = f", julkaistaan {publish_at.isoformat(timespec='minutes')} ({status['publishAt']})" if publish_at else ""
        log.info("Ladattu: https://youtu.be/%s (%s%s)", vid, privacy, when)
        if on_uploaded:
            on_uploaded(vid)
        st = wait_processed(yt, vid, verify_min, sleep, clock)
        us = (st or {}).get("uploadStatus")
        if us not in ("failed", "rejected"):
            break
        why = (st or {}).get("failureReason") or (st or {}).get("rejectionReason") or "-"
        if attempt == 1:
            log.error("YouTube hylkäsi videon %s (%s: %s) -> ladataan kerran uudelleen", vid, us, why)
            alerts.send(f"Catfish-botti: YouTube hylkäsi latauksen {Path(video).name} ({us}: {why}), "
                        f"ladataan kerran uudelleen. https://studio.youtube.com/video/{vid}/edit")
            continue
        alerts.send(f"Catfish-botti: YouTube hylkäsi latauksen {Path(video).name} myös uusinnassa ({us}: {why}).")
        raise UploadError(f"YouTube hylkäsi videon {Path(video).name} myös uusinnassa ({us}: {why})")
    if thumb and cfg.upload.set_thumbnail:
        set_thumbnail(yt, vid, Path(thumb), sleep=sleep)
    _check_lock(vid, st, publish_at)
    if not publish_at and privacy == "public":
        ensure_public(yt, vid, st, sleep=sleep)
    return vid


def ensure_public(yt, vid: str, st: dict | None, sleep=time.sleep, tries: int = 3) -> bool:
    """Julkiseksi pyydetty lataus: jos YouTube näyttää sen muuna kuin public, vaihdetaan videos.update:lla ja
    tarkistetaan. Ei onnistu -> hälytys (video jää YouTubeen, omistaja julkaisee Studiossa)."""
    from . import alerts
    if (st or {}).get("privacyStatus") == "public":
        return True
    for k in range(tries):
        try:
            cur = yt.videos().list(part="status", id=vid).execute().get("items") or []
            if cur and cur[0]["status"].get("privacyStatus") == "public":
                return True
            if cur:
                status = dict(cur[0]["status"], privacyStatus="public")
                status.pop("publishAt", None)
                yt.videos().update(part="status", body={"id": vid, "status": status}).execute()
                log.info("Näkyvyys vaihdettu julkiseksi: https://youtu.be/%s", vid)
        except Exception as e:  # noqa: BLE001
            log.warning("Julkiseksi vaihto epäonnistui (%s, yritys %d): %s", vid, k + 1, e)
        sleep(10 * (k + 1))
    try:
        cur = yt.videos().list(part="status", id=vid).execute().get("items") or []
        if cur and cur[0]["status"].get("privacyStatus") == "public":
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("Näkyvyyden tarkistus epäonnistui (%s): %s", vid, e)
    log.error("Video %s ei ole julkinen, vaikka pyydettiin public", vid)
    alerts.send(f"Catfish-botti: video ei mennyt julkiseksi, julkaise Studiossa: https://studio.youtube.com/video/{vid}/edit")
    return False


def find_recent_upload(cfg, title: str, since: float, yt=None) -> str | None:
    """Kanavan uusimmista latauksista video, jonka otsikko on title ja joka lisättiin aikaisintaan since
    (unix-aika). Keskeytynyt lataus: ehtikö video nousta? -> ei ladata kahdesti."""
    yt = yt or _service(cfg.paths.secrets_dir)
    up = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="snippet", playlistId=up, maxResults=25).execute().get("items") or []
    for it in items:
        sn = it.get("snippet") or {}
        t = _parse(sn.get("publishedAt"))
        if (sn.get("title") or "").strip() == (title or "").strip() and t and t.timestamp() >= since:
            return (sn.get("resourceId") or {}).get("videoId")
    return None


def check_published(cfg, rows: list[dict]) -> list[str]:
    """Ajastetut videot, joiden julkaisuaika on jo mennyt: jos yhä private -> lukittu (varoitus).
    rows: [{"video_id", "publish_at" (ISO)}]. Palauttaa tarkistetut id:t."""
    if not rows:
        return []
    yt = _service(cfg.paths.secrets_dir)
    states = read_status(yt, [r["video_id"] for r in rows])
    done = []
    for r in rows:
        vid = r["video_id"]
        st = states.get(vid)
        done.append(vid)
        if st is None:
            log.info("Ajastettu video %s puuttuu YouTubesta (poistettu?)", vid)
        elif st.get("privacyStatus") == "public":
            log.info("Ajastettu video %s julkaistiin (%s)", vid, r["publish_at"])
            if r.get("kind") == "long" and cfg.upload.get("relink_shorts", True):
                try:  # ajastettu pitkä video julkaistiin -> Shortsit ohjaavat siihen
                    relink_shorts(cfg, vid, yt=yt)
                except Exception:  # noqa: BLE001
                    log.exception("Shortsien linkkien päivitys epäonnistui (%s)", vid)
        else:
            log.warning(LOCK_WARNING + " (julkaisuaika %s meni, tila yhä %s)", vid, vid, r["publish_at"],
                        st.get("privacyStatus"))
    return done


# "Full video: https://youtu.be/ID" tai "...youtube.com/watch?v=ID&list=PL..." (soittolistan kautta)
FULL_VIDEO_RE = re.compile(r"(Full video: )(?:https://youtu\.be/|https://www\.youtube\.com/watch\?v=)"
                           r"([A-Za-z0-9_-]{11})(?:&list=[A-Za-z0-9_-]+)?")


def _uploads(yt, limit: int) -> list[str]:
    """Kanavan uusimpien latausten id:t (uploads-soittolista, 1 yksikkö/sivu), uusin ensin."""
    up = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < limit:
        r = yt.playlistItems().list(part="contentDetails", playlistId=up, maxResults=50,
                                    pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items") or []]
        token = r.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def relink_shorts(cfg, new_id: str, only_from: str | None = None, yt=None, limit: int | None = None,
                  list_id: str | None = None) -> int:
    """Kanavan Shortsien kuvauksen "Full video: https://youtu.be/<id>" -> uusin pitkä video new_id (omistajan toive
    18.11.2026: kaikki Shortsit ohjaavat uusimpaan pitkään videoon). only_from = vaihdetaan vain tämä vanha id
    (jumiin jääneen videon korvaus). Palauttaa päivitettyjen määrän. Yksi epäonnistuminen ei estä muita."""
    yt = yt or _service(cfg.paths.secrets_dir)
    limit = int(limit or cfg.upload.get("relink_shorts_max", 100) or 100)
    ids = [i for i in _uploads(yt, limit) if i != new_id]
    changed = 0
    for k in range(0, len(ids), 50):
        items = yt.videos().list(part="snippet", id=",".join(ids[k:k + 50])).execute().get("items") or []
        for v in items:
            sn = v.get("snippet") or {}
            desc = sn.get("description") or ""
            m = FULL_VIDEO_RE.search(desc)
            from .playlists import watch_url
            url = watch_url(new_id, list_id)
            if not m or m.group(0) == m.group(1) + url or (only_from and m.group(2) != only_from):
                continue
            body = {"id": v["id"], "snippet": {
                "title": sn.get("title", ""), "description": FULL_VIDEO_RE.sub(lambda x: x.group(1) + url, desc),
                "categoryId": sn.get("categoryId") or cfg.upload.category_id, "tags": sn.get("tags") or [],
                "defaultLanguage": sn.get("defaultLanguage") or "en",
                "defaultAudioLanguage": sn.get("defaultAudioLanguage") or "en"}}
            try:
                yt.videos().update(part="snippet", body=body).execute()
                changed += 1
            except Exception as e:  # noqa: BLE001
                log.warning("Shortsin %s linkin päivitys epäonnistui: %s", v["id"], e)
                if _status_code(e) == 403 and "quota" in str(e).lower():
                    break
    log.info("Shortsien linkit -> https://youtu.be/%s: %d päivitetty", new_id, changed)
    return changed


def processing_state(yt, vid: str) -> dict | None:
    """{'privacy', 'publish_at', 'upload', 'processing', 'duration'} tai None (video puuttuu)."""
    items = yt.videos().list(part="status,processingDetails,contentDetails", id=vid).execute().get("items") or []
    if not items:
        return None
    v = items[0]
    return {"privacy": v["status"].get("privacyStatus"), "publish_at": v["status"].get("publishAt"),
            "upload": v["status"].get("uploadStatus"),
            "processing": (v.get("processingDetails") or {}).get("processingStatus"),
            "duration": (v.get("contentDetails") or {}).get("duration")}

