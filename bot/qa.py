"""Laatuportti ennen jokaista latausta (config qa:, oletukset alla). Hylätty portti = EI latausta, tiedostot jäävät,
työ merkitään qa_failed ja omistajalle lähtee hälytys syineen. Portti ajetaan myös --no-upload- ja --mock-ajoissa
(raportti lokiin), mutta vain lataus pysähtyy siihen.

Tarkistukset:
  pitkä video   kesto qa.min_minutes (11,5) - qa.max_minutes (40) min, 1920x1080
  Short         10-60 s, 1080x1920
  kaikki        video- ja äänivirta, kestot alle 0,5 s erossa; ääni ei hiljainen eikä leikkaannu (volumedetect
                keskitaso -35...-10 dB, huippu <= 0 dB); mustaa yhteensä < 3 s (ilman 4 ensimmäistä ja 20 viimeistä
                sekuntia = intro ja loppuruutu); ei yli 8 s pysähtynyttä kuvaa samojen rajojen sisällä
  metatiedot    otsikko 10-100 merkkiä ja alkaa "GTA 6" (GTA-videot), kuvaus < 5 000 merkkiä ja <= 60 hashtagia,
                kirosanasuodatin (bot/clean.py) otsikolle, kuvaukselle ja käsikirjoitukselle
  thumbnailit   3 tiedostoa, kukin < 2 Mt ja 1280x720
  käsikirjoitus pitkä/opas >= script.min_words_long sanaa; jokainen lainauskortti löytyy artikkelista sanasta sanaan

--mock-ajot ovat lyhyitä testivideoita: pitkän videon kestoraja ja sanamäärä tarkistetaan vain oikeissa ajoissa
(raportti sanoo sen). Kaikki muu tarkistetaan myös mock-ajossa.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import clean, proc

log = logging.getLogger(__name__)

DEFAULTS = {
    "min_minutes": 11.5, "max_minutes": 40.0,
    "short_min_s": 10.0, "short_max_s": 60.0,
    "av_diff_max_s": 0.5,
    "mean_db_min": -35.0, "mean_db_max": -10.0, "max_db": 0.0,
    "black_max_s": 3.0, "skip_start_s": 4.0, "skip_end_s": 20.0,
    "freeze_max_s": 8.0,
    "title_min": 10, "title_max": 100, "title_prefix": "GTA 6",
    "desc_max": 5000, "max_hashtags": 60,
    "thumb_count": 3, "thumb_max_mb": 2.0, "thumb_size": [1280, 720],
    "min_free_gb": 8.0,
}
LONG_SIZE, SHORT_SIZE = (1920, 1080), (1080, 1920)


def settings(cfg) -> dict:
    s = dict(DEFAULTS)
    s.update({k: v for k, v in dict(cfg.get("qa") or {}).items() if v is not None})
    return s


@dataclass
class Report:
    checks: list = field(default_factory=list)   # [(kohde, nimi, ok, selite)]
    notes: list = field(default_factory=list)

    def add(self, target: str, name: str, ok: bool, detail: str = ""):
        self.checks.append((target, name, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(c[2] for c in self.checks)

    def failures(self) -> list[str]:
        return [f"{t}: {n} ({d})" if d else f"{t}: {n}" for t, n, ok, d in self.checks if not ok]

    def text(self) -> str:
        lines = [f"QA-raportti: {'HYVÄKSYTTY' if self.ok else 'HYLÄTTY'} "
                 f"({sum(c[2] for c in self.checks)}/{len(self.checks)} tarkistusta ok)"]
        for t, n, ok, d in self.checks:
            lines.append(f"  [{'ok' if ok else 'XX'}] {t}: {n}" + (f" - {d}" if d else ""))
        lines += [f"  huom: {x}" for x in self.notes]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "failures": self.failures(), "notes": self.notes,
                "checks": [{"target": t, "name": n, "ok": ok, "detail": d} for t, n, ok, d in self.checks]}


# ---------------------------------------------------------------- mittaukset

def probe(path: Path) -> dict:
    """ffprobe: {'duration', 'video': {...} | None, 'audio': {...} | None}."""
    r = proc.run(["ffprobe", "-v", "error", "-show_entries",
                  "stream=codec_type,width,height,duration:format=duration", "-of", "json", str(path)],
                 proc.PROBE, check=False, capture_output=True, text=True)
    try:
        js = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        js = {}
    out = {"duration": _f((js.get("format") or {}).get("duration")), "video": None, "audio": None}
    for s in js.get("streams") or []:
        kind = s.get("codec_type")
        if kind in ("video", "audio") and out[kind] is None:
            out[kind] = {"duration": _f(s.get("duration")), "width": int(s.get("width") or 0),
                         "height": int(s.get("height") or 0)}
    return out


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _intervals(stderr: str, start_key: str, end_key: str, total: float) -> list[tuple[float, float]]:
    """blackdetect/freezedetect-rivit -> [(alku, loppu)]; tiedoston loppuun asti jatkuva väli päättyy totaliin."""
    out, cur = [], None
    for m in re.finditer(rf"({start_key}|{end_key})[:=]\s*(-?[\d.]+)", stderr):
        key, val = m.group(1), float(m.group(2))
        if key == start_key:
            cur = val
        elif cur is not None:
            out.append((cur, val))
            cur = None
    if cur is not None:
        out.append((cur, total))
    return out


def analyze(path: Path, duration: float) -> dict:
    """Yksi ffmpeg-läpikäynti: blackdetect + freezedetect (pienennetty kuva) ja volumedetect."""
    vf = "scale=480:-2:flags=fast_bilinear,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2"
    r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:v:0", "-vf", vf,
                  "-map", "0:a:0?", "-af", "volumedetect", "-f", "null", "-"],
                 proc.analyze_timeout(duration), check=False, capture_output=True, text=True)
    err = r.stderr or ""
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", err)
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", err)

    def db(m):
        return None if not m else (-120.0 if m.group(1) == "-inf" else float(m.group(1)))

    black = [(s, e) for s, e in re.findall(r"black_start:(-?[\d.]+)\s+black_end:(-?[\d.]+)", err)]
    return {"returncode": r.returncode, "mean_db": db(mean), "max_db": db(peak),
            "black": [(float(s), float(e)) for s, e in black],
            "freeze": _intervals(err, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end", duration),
            "error": err.strip().splitlines()[-1] if r.returncode and err.strip() else ""}


def _clip(iv: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    out = []
    for s, e in iv:
        s, e = max(s, lo), min(e, hi)
        if e > s:
            out.append((s, e))
    return out


def _image_size(p: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(p) as im:
        return im.size


# ---------------------------------------------------------------- tarkistukset

def check_media(rep: Report, target: str, path: Path, kind: str, s: dict, mock: bool):
    """kind = 'long' (pitkä/opas) tai 'short'."""
    if not path or not Path(path).is_file() or Path(path).stat().st_size == 0:
        rep.add(target, "tiedosto olemassa", False, str(path))
        return
    try:
        info = probe(path)
    except proc.ProcTimeout as e:
        rep.add(target, "ffprobe", False, str(e))
        return
    v, a, dur = info["video"], info["audio"], info["duration"]
    rep.add(target, "videovirta", v is not None)
    rep.add(target, "äänivirta", a is not None)
    if v is None or a is None:
        return
    vd, ad = v["duration"] or dur, a["duration"] or dur
    rep.add(target, f"video/ääni kestoero <= {s['av_diff_max_s']} s", abs(vd - ad) <= float(s["av_diff_max_s"]),
            f"video {vd:.2f} s, ääni {ad:.2f} s")
    want = LONG_SIZE if kind == "long" else SHORT_SIZE
    rep.add(target, f"koko {want[0]}x{want[1]}", (v["width"], v["height"]) == want, f"{v['width']}x{v['height']}")
    if kind == "long":
        lo, hi = float(s["min_minutes"]) * 60, float(s["max_minutes"]) * 60
        if mock:
            rep.add(target, f"kesto <= {s['max_minutes']:g} min (mock: alaraja ohitettu)", dur <= hi,
                    f"{dur / 60:.2f} min")
        else:
            rep.add(target, f"kesto {s['min_minutes']:g}-{s['max_minutes']:g} min", lo <= dur <= hi,
                    f"{dur / 60:.2f} min")
    else:
        lo, hi = float(s["short_min_s"]), float(s["short_max_s"])
        rep.add(target, f"kesto {lo:g}-{hi:g} s", lo <= dur <= hi, f"{dur:.1f} s")
    try:
        an = analyze(path, dur)
    except proc.ProcTimeout as e:
        rep.add(target, "kuva- ja äänianalyysi", False, str(e))
        return
    if an["returncode"]:
        rep.add(target, "kuva- ja äänianalyysi", False, an["error"])
    mean, peak = an["mean_db"], an["max_db"]
    rep.add(target, f"äänen keskitaso {s['mean_db_min']:g}...{s['mean_db_max']:g} dB",
            mean is not None and float(s["mean_db_min"]) <= mean <= float(s["mean_db_max"]),
            "ei mitattu" if mean is None else f"{mean:.1f} dB")
    rep.add(target, f"äänen huippu <= {s['max_db']:g} dB", peak is not None and peak <= float(s["max_db"]),
            "ei mitattu" if peak is None else f"{peak:.1f} dB")
    if kind == "long":
        lo, hi = float(s["skip_start_s"]), max(dur - float(s["skip_end_s"]), 0.0)
        where = f"{lo:g} s - loppu-{s['skip_end_s']:g} s"
    else:
        lo, hi = 0.0, dur
        where = "koko video"
    black = sum(e - b for b, e in _clip(an["black"], lo, hi))
    rep.add(target, f"mustaa < {s['black_max_s']:g} s ({where})", black < float(s["black_max_s"]), f"{black:.1f} s")
    frozen = max([e - b for b, e in _clip(an["freeze"], lo, hi)] + [0.0])
    rep.add(target, f"pysähtynyt kuva <= {s['freeze_max_s']:g} s ({where})", frozen <= float(s["freeze_max_s"]),
            f"pisin {frozen:.1f} s")


def _is_gta(meta: dict, cfg) -> bool:
    text = " ".join([str(meta.get("guide_topic") or ""), " ".join((cfg.get("news") or {}).get("keywords") or [])])
    return bool(re.search(r"gta|grand theft auto", text, re.I))


def check_title(rep: Report, target: str, title: str, s: dict, need_prefix: bool):
    title = title or ""
    n = len(title)
    rep.add(target, f"otsikko {s['title_min']}-{s['title_max']} merkkiä", int(s["title_min"]) <= n <= int(s["title_max"]),
            f"{n} merkkiä")
    if need_prefix and s.get("title_prefix"):
        rep.add(target, f"otsikko alkaa '{s['title_prefix']}'", title.startswith(str(s["title_prefix"])), title[:40])
    hits = clean.find_profanity(title)
    rep.add(target, "otsikossa ei kirosanoja", not hits, ", ".join(hits))


def script_text(script: dict) -> str:
    parts = [x.get("text", "") for x in script.get("segments") or []]
    for sh in script.get("shorts") or []:
        parts += [x.get("text", "") for x in sh.get("script") or []]
        parts += [sh.get("hook", ""), sh.get("title", "")]
    return " ".join(p for p in parts if p)


def _quote_cards(script: dict):
    for seg in script.get("segments") or []:
        yield seg.get("card")
    for sh in script.get("shorts") or []:
        for seg in sh.get("script") or []:
            yield seg.get("card")


def check_script(rep: Report, cfg, script: dict, articles: list[dict], fmt: str, s: dict, mock: bool):
    from .scriptwriter import _spoken_words, norm_quote
    if fmt in ("long", "guide", "breakdown"):
        sc = cfg.get("script") or {}
        need = int((sc.get("min_words_breakdown", 1500) if fmt == "breakdown" else sc.get("min_words_long", 0)) or 0)
        n = _spoken_words(script)
        if mock:
            rep.notes.append(f"mock-ajo: käsikirjoituksen sanamäärää ({n}) ei verrata minimiin {need}")
        else:
            rep.add("käsikirjoitus", f"vähintään {need} sanaa", n >= need, f"{n} sanaa")
    hits = clean.find_profanity(script_text(script))
    rep.add("käsikirjoitus", "ei kirosanoja", not hits, ", ".join(sorted(set(hits)))[:200])
    bad, total = [], 0
    for card in _quote_cards(script):
        if not isinstance(card, dict) or card.get("type") != "quote":
            continue
        total += 1
        idx = card.get("article")
        body = norm_quote((articles[idx].get("text") or "")) if isinstance(idx, int) and 0 <= idx < len(articles) else ""
        if not body or not card.get("text") or norm_quote(card["text"]) not in body:
            bad.append(str(card.get("text", ""))[:40])
    rep.add("käsikirjoitus", "lainauskortit sanasta sanaan artikkeleista", not bad,
            f"{total} lainausta" + (f", virheelliset: {bad}" if bad else ""))


def gate(cfg, *, out: Path, meta: dict, thumbs: list[Path], shorts: list[dict], script: dict | None,
         articles: list[dict] | None, mock: bool = False) -> Report:
    """shorts: [{'path': Path, 'title': str}] (lataukseen menevät). script/articles: None = käsikirjoitusta ei
    tarkisteta (ei pitäisi tapahtua)."""
    s = settings(cfg)
    rep = Report()
    fmt = meta.get("format") or "long"
    if fmt == "breakdown":  # breakdown-video: vähintään qa.min_minutes_breakdown (8) min
        s = dict(s, min_minutes=float(s.get("min_minutes_breakdown", 8) or 8))
    if mock:
        rep.notes.append(f"mock-ajo: pitkän videon kestoa ei verrata minimiin {s['min_minutes']:g} min")
    check_media(rep, "video", Path(out), "long", s, mock)
    need_prefix = _is_gta(meta, cfg)
    check_title(rep, "video", meta.get("title", ""), s, need_prefix)
    desc = meta.get("description") or ""
    rep.add("video", f"kuvaus < {s['desc_max']} merkkiä", len(desc) < int(s["desc_max"]), f"{len(desc)} merkkiä")
    tags = re.findall(r"(?<![\w&])#\w+", desc)
    rep.add("video", f"hashtageja <= {s['max_hashtags']}", len(tags) <= int(s["max_hashtags"]), f"{len(tags)} kpl")
    hits = clean.find_profanity(desc)
    rep.add("video", "kuvauksessa ei kirosanoja", not hits, ", ".join(hits))
    fc = meta.get("first_chapter_s")
    if fc and fc > 90:  # ei estä latausta: palaute 17.11. (katsojat lähtivät ennen ensimmäistä käytännön lukua)
        rep.notes.append(f"ensimmäinen kappale alkaa vasta {int(fc) // 60}:{int(fc) % 60:02d} (tavoite alle 1:30)")

    want_n = int(s["thumb_count"])
    tw, th = (int(x) for x in s["thumb_size"])
    files = [Path(t) for t in thumbs or []]
    rep.add("thumbnailit", f"{want_n} tiedostoa", len(files) == want_n and all(f.is_file() for f in files),
            f"{sum(f.is_file() for f in files)} kpl")
    for f in files:
        if not f.is_file():
            continue
        size = f.stat().st_size
        try:
            dims = _image_size(f)
        except Exception as e:  # noqa: BLE001
            dims = (0, 0)
            log.warning("Thumbnailin %s luku epäonnistui: %s", f.name, e)
        rep.add(f"thumbnail {f.name}", f"< {s['thumb_max_mb']:g} Mt ja {tw}x{th}",
                size < float(s["thumb_max_mb"]) * 1024 * 1024 and dims == (tw, th),
                f"{size / 1e6:.2f} Mt, {dims[0]}x{dims[1]}")

    for k, sh in enumerate(shorts or [], 1):
        t = f"Short {k}"
        check_media(rep, t, Path(sh["path"]), "short", s, mock)
        # Shortsin otsikon alun hoitaa scriptwriter._short_title ("GTA 6"/"GTA VI") -> vain pituus ja kirosanat
        check_title(rep, t, sh.get("title", ""), s, False)

    if script is not None:
        check_script(rep, cfg, script, articles or [], fmt, s, mock)
    else:
        rep.add("käsikirjoitus", "script.json saatavilla", False)
    return rep
