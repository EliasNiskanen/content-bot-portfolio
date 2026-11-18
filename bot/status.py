"""python -m bot.main status: tämän päivän työt (vaihe, YouTube-linkit, QA), viimeiset virheet, levytila,
heartbeat ja seuraavat ajot. Dockerissa: docker compose exec gta6-bot python -m bot.main status"""
import datetime as dt
import re
import time

from . import logsetup, preflight, qa, scheduler
from .state import State

STAGE_FI = {
    "started": "aloitettu (tutkimus/käsikirjoitus/renderöinti kesken)",
    "rendered": "renderöity, ei vielä tarkistettu",
    "qa_failed": "LAATUPORTTI HYLKÄSI (ei ladattu, tiedostot tallessa)",
    "qa_passed": "laatuportti ok, lataus ei vielä alkanut",
    "uploading_long": "pitkän videon lataus kesken",
    "long_uploaded": "pitkä video ladattu, Shortseja puuttuu",
    "upload_queued": "YouTuben latausraja täynnä: latausjonossa (uusinta tunnin välein)",
    "done": "valmis",
}


def _ago(ts: float | None) -> str:
    if not ts:
        return "-"
    s = max(time.time() - float(ts), 0)
    if s < 120:
        return f"{s:.0f} s sitten"
    if s < 7200:
        return f"{s / 60:.0f} min sitten"
    return f"{s / 3600:.1f} h sitten"


def _job_lines(r: dict) -> list[str]:
    out = [f"  [{r['name']}] {STAGE_FI.get(r['stage'], r['stage'])}  (yrityksiä {r.get('attempts') or 0}, "
           f"päivitetty {_ago(r.get('updated'))})"]
    if r.get("long_id"):
        out.append(f"      video:  https://youtu.be/{r['long_id']}")
    for f, v in (r.get("shorts") or {}).items():
        out.append(f"      Short:  {'https://youtu.be/' + v if v and v != 'pending' else 'lataus kesken'}  ({f})")
    q = r.get("qa")
    if q:
        out.append(f"      QA:     {'ok' if q.get('ok') else 'HYLÄTTY'}"
                   + ("" if q.get("ok") else ": " + "; ".join(q.get("failures") or [])[:300]))
    if r.get("output"):
        out.append(f"      tiedosto: {r['output']}")
    if r.get("error") and r["stage"] != "done":
        out.append(f"      virhe:  {r['error'][:300]}")
    return out


def _log_errors(cfg, n: int = 5) -> list[str]:
    d = logsetup.logs_dir(cfg)
    lines = []
    for p in sorted(d.glob("bot-*.log"))[-2:]:
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if re.search(r" (ERROR|CRITICAL) ", line):
                    lines.append(line)
        except OSError:
            pass
    return lines[-n:]


def report(cfg, now: dt.datetime | None = None) -> str:
    zone = scheduler.tz(cfg)
    now = (now or dt.datetime.now(zone)).astimezone(zone)
    day = now.date().isoformat()
    st = State(cfg.paths.db)
    out = [f"Catfish-botti: tila {now.strftime('%Y-%m-%d %H:%M %Z')}", "", f"Tämän päivän ({day}) työt:"]
    rows = st.jobs_on(day)
    known = {r["name"] for r in rows}
    for r in rows:
        out += _job_lines(r)
    try:
        js = scheduler.jobs(cfg)
    except ValueError as e:
        js = []
        out.append(f"  schedule.jobs virheellinen: {e}")
    try:
        ones = scheduler.once_jobs(cfg)
    except ValueError as e:
        ones = []
        out.append(f"  schedule.once virheellinen: {e}")
    for j in js + [o for o in ones if o["date"] == day]:
        if j["name"] in known:
            continue
        if not j.get("once") and scheduler.skipped(cfg, j["name"], day):
            out.append(f"  [{j['name']}] ohitetaan tänään (schedule.skip)")
            continue
        t = scheduler._at(now.date(), j["time"], zone)
        out.append(f"  [{j['name']}] {'ei ajettu tänään' if t <= now else 'ei vielä alkanut'} "
                   f"(ajoaika {j['time']})")
    yday = (now.date() - dt.timedelta(days=1)).isoformat()
    yrows = st.jobs_on(yday)
    if yrows:
        out += ["", f"Eilen ({yday}):"] + [f"  [{r['name']}] {r['stage']}"
                                           + (f"  https://youtu.be/{r['long_id']}" if r.get("long_id") else "")
                                           for r in yrows]

    out += ["", "Viimeiset virheet (state.db):"]
    errs = st.recent_job_errors(5)
    out += [f"  {r['day']} {r['name']} ({r['stage']}): {r['error'][:200]}" for r in errs] or ["  ei virheitä"]
    le = _log_errors(cfg)
    if le:
        out += ["Viimeiset ERROR-rivit lokissa:"] + [f"  {x[:220]}" for x in le]

    need = float(qa.settings(cfg)["min_free_gb"])
    try:
        free = preflight.free_gb(preflight.data_dir(cfg))
        out += ["", f"Levytila: {free:.1f} Gt vapaana (raja {need:.0f} Gt){'  <-- LIIAN VÄHÄN' if free < need else ''}"]
    except OSError as e:
        out += ["", f"Levytila: ei luettavissa ({e})"]
    hb = logsetup.logs_dir(cfg) / "heartbeat"
    if hb.is_file():
        age = time.time() - hb.stat().st_mtime
        out.append(f"Ajastin (heartbeat): {_ago(hb.stat().st_mtime)}"
                   + ("  <-- ajastin ei näytä olevan käynnissä" if age > 300 else ""))
    else:
        out.append("Ajastin (heartbeat): ei heartbeat-tiedostoa (ajastin ei ole käynnistynyt tällä datakansiolla)")

    from .main import _load_pending
    queue = _load_pending(cfg)
    out += ["", f"Latausjono (data/pending_uploads.json): {len(queue)} työtä"]
    for q in queue:
        what = f"työ {'/'.join(q['job'])}" if q.get("job") else "käsin ajettu"
        out.append(f"  {what}: {str(q.get('video', '-')).replace(chr(92), '/').split('/')[-1]}"
                   + (f"  (pitkä ladattu https://youtu.be/{q['long_id']})" if q.get("long_id") else "")
                   + (f"  epäonnistunut {q['failures']} kertaa" if q.get("failures") else ""))

    out += ["", "Seuraavat ajot:"]
    nxt = [(scheduler.next_job_run(cfg, j, now, zone), j) for j in js]
    nxt += [(scheduler.once_at(o, zone), o) for o in ones if scheduler.once_at(o, zone) > now]
    for t, j in sorted(nxt, key=lambda x: x[0]):
        out.append(f"  {j['name']:<9} {t.strftime('%a %Y-%m-%d %H:%M')}  format={j['format']}"
                   + (f"  kertaluonteinen, privacy={j.get('privacy') or cfg.upload.privacy}" if j.get("once") else "")
                   + (f"  julkaisu {j['publish_at']}" if j.get("publish_at") and scheduler.auto_publish(cfg) else ""))
    for sk in (cfg.get("schedule") or {}).get("skip") or []:
        if isinstance(sk, dict) and str(sk.get("date")) >= day:
            out.append(f"  ohitetaan: {sk.get('name')} {sk.get('date')} (schedule.skip)")
    tl = float((cfg.get("schedule") or {}).get("job_timeout_minutes", 150) or 150)
    out.append(f"  (aikaraja per työ {tl:.0f} min, uusinta {float((cfg.get('schedule') or {}).get('retry_minutes', 20) or 20):.0f} min päästä)")
    return "\n".join(out)
