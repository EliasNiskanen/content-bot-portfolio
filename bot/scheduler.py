"""Päivittäiset ajot (config schedule.jobs) ja julkaisuajat (upload.auto_publish).

- Ajot yhdessä silmukassa peräkkäin: jos edellinen ajo on yhä käynnissä seuraavan kellonaikana, seuraava alkaa
  heti sen perään. Jokainen ajo on oma lapsiprosessinsa (main.schedule), jolla on kova aikaraja
  schedule.job_timeout_minutes (150): ylitys -> koko prosessipuu tapetaan, hälytys ja uusinta. Epäonnistunut ajo
  kirjataan tracebackin kanssa ja yritetään kerran uudelleen schedule.retry_minutes (20) minuutin päästä (sama
  päivä -> state.db:n työtila: uusinta jatkaa siitä mitä puuttuu). Silmukka ei kuole koskaan, heartbeat jatkuu.
- Config luetaan uudelleen ennen jokaista ajoa (muutokset tulevat voimaan ilman uudelleenkäynnistystä).
- Ilman schedule.jobs-listaa schedule.time = yksi uutisajo (vanha muoto).
- Julkaisuajat: pitkä video tämän päivän publish_at (huomenna jos mennyt), Shortsit shorts.publish_times-paikoille
  järjestyksessä aikaisintaan pitkän videon jälkeen; jo varatut paikat (state.db) ohitetaan, ylimenevät seuraavalle
  päivälle.
"""
import copy
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from . import logsetup

log = logging.getLogger(__name__)

DEFAULT_JOBS = [{"name": "news", "time": "04:30", "format": "long", "publish_at": "08:00"},
                {"name": "guide", "time": "06:30", "format": "guide", "publish_at": "10:00"}]
DEFAULT_SHORT_TIMES = ["15:00", "19:00"]
FORMATS = ("long", "guide", "short", "breakdown")
MARGIN = dt.timedelta(minutes=15)      # julkaisuajan oltava näin paljon tulevaisuudessa latauksen alkaessa
EST_RUN = dt.timedelta(minutes=45)     # dry-run: arvioitu ajon kesto latauksen alkuun
CHECK_EVERY_S = 3600                   # ajastettujen videoiden lukitustarkistus (idle)


def tz(cfg) -> ZoneInfo:
    return ZoneInfo((cfg.get("schedule") or {}).get("timezone") or "Europe/Helsinki")


def _hm(s) -> tuple[int, int]:
    h, m = str(s).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"Virheellinen kellonaika: {s}")
    return h, m


def _at(day: dt.date, hhmm: str, zone: ZoneInfo) -> dt.datetime:
    h, m = _hm(hhmm)
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=zone)


def jobs(cfg) -> list[dict]:
    sc = cfg.get("schedule") or {}
    raw = sc.get("jobs")
    if raw is None:
        if sc.get("time"):  # vanha muoto: yksi uutisajo
            return [{"name": "news", "time": str(sc["time"]), "format": "long", "publish_at": sc.get("publish_at")}]
        return copy.deepcopy(DEFAULT_JOBS)
    out, names = [], set()
    for i, j in enumerate(raw or []):
        if not isinstance(j, dict) or not j.get("time"):
            raise ValueError(f"schedule.jobs[{i}]: 'time' puuttuu")
        _hm(j["time"])
        fmt = j.get("format") or "long"
        if fmt not in FORMATS:
            raise ValueError(f"schedule.jobs[{i}]: tuntematon format {fmt}")
        if j.get("publish_at"):
            _hm(j["publish_at"])
        name = str(j.get("name") or f"job{i + 1}")
        if name in names:
            raise ValueError(f"schedule.jobs: nimi {name} on kahdesti")
        names.add(name)
        out.append({"name": name, "time": str(j["time"]), "format": fmt, "publish_at": j.get("publish_at"),
                    "topic": j.get("topic")})
    return out


def once_jobs(cfg) -> list[dict]:
    """schedule.once: kertaluonteiset työt [{name, date YYYY-MM-DD, time, format, topic, research, privacy,
    research_retry_at, publish_at}]. Ajetaan kuten muutkin (lapsiprosessi, aikaraja, uusinta, QA)."""
    out = []
    for i, j in enumerate((cfg.get("schedule") or {}).get("once") or []):
        if not isinstance(j, dict) or not j.get("time") or not j.get("date") or not j.get("name"):
            raise ValueError(f"schedule.once[{i}]: name, date ja time vaaditaan")
        _hm(j["time"])
        dt.date.fromisoformat(str(j["date"]))
        fmt = j.get("format") or "guide"
        if fmt not in FORMATS:
            raise ValueError(f"schedule.once[{i}]: tuntematon format {fmt}")
        if j.get("privacy") and j["privacy"] not in ("private", "unlisted", "public", "none"):
            raise ValueError(f"schedule.once[{i}]: tuntematon privacy {j['privacy']}")
        out.append({"name": str(j["name"]), "date": str(j["date"]), "time": str(j["time"]), "format": fmt,
                    "publish_at": j.get("publish_at"), "topic": j.get("topic"), "research": j.get("research"),
                    "privacy": j.get("privacy"), "research_retry_at": list(j.get("research_retry_at") or []),
                    "once": True})
    return out


def skipped(cfg, name: str, day: str) -> bool:
    """schedule.skip: [{name, date}] -> säännöllinen työ ohitetaan sinä päivänä."""
    return any(str(s.get("name")) == name and str(s.get("date")) == day
               for s in (cfg.get("schedule") or {}).get("skip") or [] if isinstance(s, dict))


def find_job(cfg, name: str, day: str | None = None) -> dict | None:
    for j in jobs(cfg):
        if j["name"] == name:
            return j
    for j in once_jobs(cfg):
        if j["name"] == name and (day is None or j["date"] == day):
            return j
    return None


def once_at(j: dict, zone: ZoneInfo) -> dt.datetime:
    return _at(dt.date.fromisoformat(j["date"]), j["time"], zone)


def next_job_run(cfg, j: dict, after: dt.datetime, zone: ZoneInfo) -> dt.datetime:
    """Säännöllisen työn seuraava ajo, schedule.skip-päivät ohitetaan."""
    t = next_run(j["time"], after, zone)
    for _ in range(366):
        if not skipped(cfg, j["name"], t.astimezone(zone).date().isoformat()):
            break
        t = next_run(j["time"], t, zone)
    return t


def next_run(hhmm: str, after: dt.datetime, zone: ZoneInfo) -> dt.datetime:
    """Seuraava kellonaika hhmm aidosti ajan after jälkeen."""
    local = after.astimezone(zone)
    t = _at(local.date(), hhmm, zone)
    while t <= local:
        t = _at(t.date() + dt.timedelta(days=1), hhmm, zone)
    return t


def publish_time(cfg, hhmm: str, now: dt.datetime | None = None) -> dt.datetime:
    """Pitkän videon julkaisu: tämän päivän hhmm, tai huomenna jos se on jo mennyt (tai alle MARGIN päässä)."""
    zone = tz(cfg)
    now = (now or dt.datetime.now(zone)).astimezone(zone)
    t = _at(now.date(), hhmm, zone)
    while t <= now + MARGIN:
        t = _at(t.date() + dt.timedelta(days=1), hhmm, zone)
    return t


def short_times(cfg) -> list[str]:
    times = (cfg.get("shorts") or {}).get("publish_times") or DEFAULT_SHORT_TIMES
    return sorted((str(x) for x in times), key=_hm)


def short_slots(cfg, n: int, not_before: dt.datetime | None, taken: list[dt.datetime],
                now: dt.datetime | None = None) -> list[dt.datetime]:
    """n Shortsin julkaisuajat: shorts.publish_times järjestyksessä, aikaisintaan pitkän videon julkaisuhetkellä,
    ei varattuja paikkoja; kun päivän paikat loppuvat, seuraava päivä."""
    zone = tz(cfg)
    now = (now or dt.datetime.now(zone)).astimezone(zone)
    earliest = max(now + MARGIN, not_before or now)
    used = {t.timestamp() for t in taken}
    out, day = [], earliest.astimezone(zone).date()
    for _ in range(366):
        if len(out) >= n:
            break
        for hhmm in short_times(cfg):
            t = _at(day, hhmm, zone)
            if t >= earliest and t.timestamp() not in used and len(out) < n:
                out.append(t)
                used.add(t.timestamp())
        day += dt.timedelta(days=1)
    return out


def taken_short_slots(st) -> list[dt.datetime]:
    out = []
    for s in st.scheduled_times("short"):
        try:
            out.append(dt.datetime.fromisoformat(s))
        except (TypeError, ValueError):
            pass
    return out


def publish_at_for_format(cfg, fmt: str) -> str | None:
    """Käsin ajettu `run`: saman muodon ensimmäisen ajon publish_at."""
    for j in jobs(cfg):
        if j["format"] == fmt and j.get("publish_at"):
            return j["publish_at"]
    return None


def auto_publish(cfg) -> bool:
    return bool((cfg.get("upload") or {}).get("auto_publish", False))


def _fmt(t: dt.datetime) -> str:
    return f"{t.strftime('%a %Y-%m-%d %H:%M %Z')} ({t.astimezone(dt.timezone.utc).strftime('%H:%MZ')})"


def dry_run(cfg, now: dt.datetime | None = None, taken: list[dt.datetime] | None = None, days: int = 3) -> str:
    """Seuraavat ajot ja niiden julkaisuajat (simuloitu: lataus EST_RUN ajon alun jälkeen)."""
    zone = tz(cfg)
    now = (now or dt.datetime.now(zone)).astimezone(zone)
    js = jobs(cfg)
    ap = auto_publish(cfg)
    n_short = int(cfg.shorts.count) if (cfg.get("shorts") or {}).get("enabled") else 0
    lines = [f"Nyt {_fmt(now)}, aikavyöhyke {zone.key}",
             f"upload.auto_publish: {str(ap).lower()}" + ("" if ap else
             f"  -> lataukset jäävät tilaan '{cfg.upload.privacy}', julkaisuajat alla vain tiedoksi"),
             f"Shortseja per video: {n_short}, Shorts-paikat {', '.join(short_times(cfg))}", ""]
    taken = list(taken or [])
    # seuraavat `days` päivää: säännölliset työt (schedule.skip-päivät merkitään) + kertaluonteiset (schedule.once)
    runs = []
    for k in range(days):
        d = now.date() + dt.timedelta(days=k)
        for j in js:
            t = _at(d, j["time"], zone)
            if t > now:
                runs.append((t, j, skipped(cfg, j["name"], d.isoformat())))
    for j in once_jobs(cfg):
        t = once_at(j, zone)
        if t > now and t.date() < now.date() + dt.timedelta(days=days):
            runs.append((t, j, False))
    for start, j, skip in sorted(runs, key=lambda r: r[0]):
        if skip:
            lines.append(f"[{j['name']}] {_fmt(start)}  OHITETAAN (schedule.skip)")
            continue
        extra = ""
        if j.get("once"):
            extra = (f"  kertaluonteinen, research={j.get('research') or '-'}, "
                     f"privacy={j.get('privacy') or cfg.upload.privacy}")
            if j.get("topic"):
                extra += f"\n    aihe: {j['topic']}"
        lines.append(f"[{j['name']}] format={j['format']}  ajo {_fmt(start)}{extra}")
        if not j.get("publish_at"):
            if not j.get("once"):
                lines.append("    publish_at puuttuu -> video jää yksityiseksi")
            continue
        up_at = start + EST_RUN
        long_t = publish_time(cfg, j["publish_at"], up_at)
        lines.append(f"    pitkä video julkaistaan {_fmt(long_t)}")
        for k, t in enumerate(short_slots(cfg, n_short, long_t, taken, up_at), 1):
            taken.append(t)
            lines.append(f"    Short {k} julkaistaan {_fmt(t)}")
    return "\n".join(lines)


def _check_scheduled(cfg):
    """Ajastetut videot, joiden julkaisuaika meni yli 30 min sitten: julkaistiinko vai lukittiinko yksityiseksi."""
    from .state import State
    st = State(cfg.paths.db)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for r in st.unchecked_scheduled():
        try:
            if dt.datetime.fromisoformat(r["publish_at"]) < now - dt.timedelta(minutes=30):
                rows.append(r)
        except (TypeError, ValueError):
            rows.append(r)
    if rows:
        from .uploader import check_published
        st.mark_checked(check_published(cfg, rows))


def job_day(t: dt.datetime, zone: ZoneInfo) -> str:
    """Työn päivä (state.db jobs-avain): ajastetun alkuhetken paikallinen päivämäärä. Uusinta käyttää samaa."""
    return t.astimezone(zone).date().isoformat()


def loop(load_cfg, run_job, sleep=time.sleep, clock=None, max_iterations: int | None = None,
         on_final_failure=None, on_day_done=None, catch_up=None, pending=None, once_seen=None, watch=None,
         watch_every_s: float = 1200):
    """load_cfg() -> cfg (luetaan joka kierroksella), run_job(cfg, job, day) ajaa yhden työn (main: lapsiprosessina
    aikarajalla, joten tämä silmukka ei jää jumiin). on_final_failure(cfg, job, day, err): työ epäonnistui myös
    uusinnassa. on_day_done(cfg, day): päivän viimeinen työ päättyi (yhteenveto). catch_up(cfg, now) -> [(t, job, day)]
    käynnistyksessä kesken jääneet työt. pending(cfg): latausjonon purku tunnin välein (main: lapsiprosessina).
    once_seen(cfg, job, day) -> True jos kertaluonteinen työ on jo aloitettu (state.db), jolloin sitä ei aloiteta
    uudelleen (kesken jääneet hoitaa catch_up). watch(cfg, now) -> [(job, day)] watch_every_s välein (breakdown-vahti):
    palautetut työt ajetaan heti (yksi kerrallaan, kesken olevan työn jälkeen). clock/max_iterations testejä varten."""
    cfg = load_cfg()
    zone = tz(cfg)
    clock = clock or (lambda: dt.datetime.now(zone))
    job_h = float((cfg.get("schedule") or {}).get("job_timeout_minutes", 150) or 150) / 60
    hb = (logsetup.Heartbeat(logsetup.logs_dir(cfg) / "heartbeat", max_job_hours=job_h + 0.5).start()
          if max_iterations is None else None)
    next_due: dict[str, tuple[str, dt.datetime]] = {}
    last_pending = 0.0
    retries: list[tuple[dt.datetime, dict, str]] = []
    failures: dict[tuple[str, str], int] = {}  # (työ, päivä) -> epäonnistuneet yritykset tässä ajastimessa
    if catch_up:
        try:
            retries += list(catch_up(cfg, clock()))
        except Exception:  # noqa: BLE001
            log.exception("Kesken jääneiden töiden tarkistus epäonnistui")
    finished_days: set[str] = set()
    fired_once: set[tuple[str, str]] = set()
    instant: list[tuple[dt.datetime, dict, str]] = []
    last_watch = -1e18
    last_check = 0.0
    announced = False
    it = 0

    def _hook(fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - hälytys/yhteenveto ei saa kaataa silmukkaa
                log.exception("Silmukan koukku %s epäonnistui", getattr(fn, "__name__", fn))

    while max_iterations is None or it < max_iterations:
        it += 1
        try:
            cfg = load_cfg()
            js = jobs(cfg)
        except Exception:  # noqa: BLE001 - rikkinäinen config ei kaada silmukkaa
            log.exception("Configin luku epäonnistui, käytetään edellistä")
            js = jobs(cfg)
        retry_min = float((cfg.get("schedule") or {}).get("retry_minutes", 20) or 20)
        now = clock()
        names = {j["name"] for j in js}
        for n in list(next_due):
            if n not in names:
                del next_due[n]
        for j in js:
            if j["name"] not in next_due or next_due[j["name"]][0] != j["time"]:
                next_due[j["name"]] = (j["time"], next_job_run(cfg, j, now, zone))
                announced = False
        if not announced:
            log.info("Seuraavat ajot: %s", ", ".join(f"{n} {t.strftime('%Y-%m-%d %H:%M')}"
                                                      for n, (_, t) in sorted(next_due.items(), key=lambda x: x[1][1])))
            announced = True

        due = [(t, j, False, job_day(t, zone)) for j in js for (_, t) in [next_due[j["name"]]] if t <= now]
        try:
            ones = once_jobs(cfg)
        except ValueError:
            log.exception("schedule.once virheellinen")
            ones = []
        today = now.astimezone(zone).date().isoformat()
        once_later = []
        for j in ones:
            t, key = once_at(j, zone), (j["name"], j["date"])
            if key in fired_once:
                continue
            if t > now:
                once_later.append(t)
            elif j["date"] == today:  # myöhästynyt kertaluonteinen työ ajetaan vain samana päivänä
                seen = False
                if once_seen:
                    try:
                        seen = bool(once_seen(cfg, j, j["date"]))
                    except Exception:  # noqa: BLE001
                        log.exception("once_seen epäonnistui")
                if seen:
                    fired_once.add(key)
                else:
                    due.append((t, j, False, j["date"]))
        due += [(t, j, True, d) for t, j, d in retries if t <= now]
        due += [(t, j, False, d) for t, j, d in instant if t <= now]
        if due:
            due.sort(key=lambda x: x[0])
            t, job, is_retry, day = due[0]
            if is_retry:
                retries = [r for r in retries if r[1] is not job]
            elif job.get("instant"):
                instant = [r for r in instant if r[1] is not job]
            elif job.get("once"):
                fired_once.add((job["name"], day))
            else:
                next_due[job["name"]] = (job["time"], next_job_run(cfg, job, now, zone))
            late = (now - t).total_seconds() / 60
            log.info("Ajo %s (%s) alkaa%s%s", job["name"], day, " (uusinta)" if is_retry else "",
                     f", {late:.0f} min myöhässä" if late >= 2 else "")
            if hb:
                hb.job_started = time.time()
            t0 = time.time()
            final = True
            try:
                run_job(copy.deepcopy(cfg), job, day)
                log.info("Ajo %s valmis (%.0f min)", job["name"], (time.time() - t0) / 60)
            except Exception as e:  # noqa: BLE001 - pidetään botti hengissä
                log.exception("Ajo %s epäonnistui (%.0f min)", job["name"], (time.time() - t0) / 60)
                failures[(job["name"], day)] = failures.get((job["name"], day), 0) + 1
                n_fail = failures[(job["name"], day)]
                max_retries = int((cfg.get("schedule") or {}).get("retry_count", 2) or 1)
                if n_fail > max_retries:
                    log.error("Ajo %s epäonnistui myös uusinnoissa; seuraava yritys normaalisti %s", job["name"],
                              next_due[job["name"]][1].strftime("%Y-%m-%d %H:%M") if job["name"] in next_due else "-")
                    _hook(on_final_failure, cfg, job, day, e)
                else:
                    rt = clock() + dt.timedelta(minutes=retry_min * n_fail)  # 20 min, sitten 40 min
                    retries.append((rt, job, day))
                    final = False
                    log.info("Ajo %s yritetään uudelleen %s", job["name"], rt.strftime("%H:%M"))
            finally:
                if hb:
                    hb.job_started = None
                    hb.beat()
            # päivän viimeinen työ päättyi (ei uusintoja jonossa, seuraavat ajot vasta myöhempinä päivinä)
            if (final and day not in finished_days and not any(d == day for _, _, d in retries)
                    and all(job_day(t2, zone) > day for _, t2 in next_due.values())
                    and not any(job_day(t2, zone) == day for t2 in once_later)):
                finished_days.add(day)
                _hook(on_day_done, cfg, day)
            announced = False
            continue

        if auto_publish(cfg) and time.time() - last_check > CHECK_EVERY_S and max_iterations is None:
            last_check = time.time()
            try:
                _check_scheduled(cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("Ajastettujen videoiden tarkistus epäonnistui: %s", e)
        if watch and time.monotonic() - last_watch >= watch_every_s:
            last_watch = time.monotonic()
            try:
                for j, d in watch(cfg, now) or []:
                    instant.append((now, dict(j, instant=True), d))
                    log.info("Vahti: uusi työ %s (%s) ajetaan heti", j["name"], d)
            except Exception:  # noqa: BLE001
                log.exception("Vahti epäonnistui")
            if instant:
                continue
        if pending and time.time() - last_pending > CHECK_EVERY_S:
            last_pending = time.time()
            _hook(pending, cfg)  # latausrajaan jääneet videot (data/pending_uploads.json) tunnin välein
        upcoming = [t for _, t in next_due.values()] + [t for t, _, _ in retries] + once_later
        if watch:
            upcoming.append(now + dt.timedelta(seconds=max(watch_every_s - (time.monotonic() - last_watch), 1)))
        wait = min([(t - now).total_seconds() for t in upcoming] + [60.0])
        sleep(max(wait, 1.0))
