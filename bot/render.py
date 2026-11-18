"""Taustapelikuva + kortit + tekstitys + ääni -> valmis MP4 ffmpegillä (kevyt, toimii Pillä)."""
import json
import logging
import random
import re
import time
from pathlib import Path

from . import brand, music, proc

log = logging.getLogger(__name__)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _run(cmd: list[str], timeout: float = proc.PIECE):
    proc.run(cmd, timeout)


def probe_duration(p: Path) -> float:
    try:
        out = proc.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)],
                       proc.PROBE, check=False, capture_output=True, text=True)
    except proc.ProcTimeout:
        return 0.0
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def _dims(cfg):
    return (1080, 1920) if cfg.format == "short" else (1920, 1080)


IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _probe_size(p: Path) -> tuple[int, int]:
    try:
        out = proc.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "json", str(p)],
                       proc.PROBE, check=False, capture_output=True, text=True)
    except proc.ProcTimeout:
        return 0, 0
    try:
        s = json.loads(out.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return 0, 0


def _letterbox(f: Path, start: float, size: tuple[int, int]) -> str:
    """Mustat palkit ylhäällä/alhaalla (traileri-letterbox) -> 'crop=...,' tai ''.
    Rajataan vain korkeutta ja vain jos palkit ovat selvät: tumma kohtaus ei saa leikata kuvaa."""
    w0, h0 = size
    if not w0 or not h0:
        return ""
    try:
        r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start + 0.3:.2f}", "-t", "1.5", "-i", str(f),
                      "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                     proc.PROBE, check=False, capture_output=True, text=True)
    except proc.ProcTimeout:
        return ""
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not m:
        return ""
    w, h, _x, y = map(int, m[-1])
    if w < w0 * 0.9 or h < h0 * 0.6 or h > h0 * 0.97:
        return ""
    return f"crop={w0}:{h}:0:{y},"


def _still_piece(cfg, img: Path, seg: float, out: Path, W: int, H: int, fps: int):
    """Virallinen still-kuva -> hidas zoom + liuku (Ken Burns). Skaalataan ensin isoksi, ettei zoom nyi."""
    n = max(int(seg * fps), fps)
    k = 2 if W >= H else 1.5
    BW, BH = int(W * k) // 2 * 2, int(H * k) // 2 * 2
    zin = random.random() < 0.6
    z = f"1+0.12*on/{n}" if zin else f"1.12-0.12*on/{n}"
    px, py = random.uniform(0.25, 0.75), random.uniform(0.3, 0.7)
    vf = (f"scale={BW}:{BH}:force_original_aspect_ratio=increase,crop={BW}:{BH},"
          f"zoompan=z='{z}':x='(iw-iw/zoom)*{px:.2f}':y='(ih-ih/zoom)*{py:.2f}':d={n}:s={W}x{H}:fps={fps},"
          f"setsar=1,eq=brightness=-{cfg.video.background_dim}:saturation=1.08,{_look(cfg)}{UNIFORM}")
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(img), "-vf", vf, "-frames:v", str(n),
          *_enc_args(), str(out)])
    return n / fps


# Kaikki taustan palat samaan muotoon (tavallinen värialue, bt709): JPEG-kuvat ovat täyttä aluetta (yuvj420p),
# ja muodon vaihtuminen kesken yhdistetyn videon jumittaa ffmpegin loppurenderöinnin.
UNIFORM = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"


def _is_landscape(f: Path) -> bool:
    """Leveys/korkeus >= 1.3 (16:9 = 1.78). Lukuvirhe -> True (ei pudoteta varmuuden vuoksi)."""
    try:
        w, h = _probe_size(f)
        return not (w and h) or w / h >= 1.3
    except Exception:  # noqa: BLE001
        return True


MOTION_ZOOM = 0.06        # hidas zoom taustapalassa (6 % palan aikana)


def _motion(cfg, W: int, H: int, seg: float) -> str:
    """Taustavideopalan hidas liike: satunnaisesti zoom sisään tai ulos (scale eval=frame -> pehmeä, ei nykimistä).
    video.motion: false = ei liikettä."""
    if not cfg.video.get("motion", True) or seg <= 0.5:
        return ""
    z = float(cfg.video.get("motion_zoom", MOTION_ZOOM) or MOTION_ZOOM)
    # rajattu [1, 1+z]: viimeisen ruudun t voi ylittää palan pituuden, ja alle 1:n kerroin kaataisi rajauksen
    f = (f"min(1+{z},1+{z}*t/{seg:.2f})" if random.random() < 0.6 else f"max(1,1+{z}*(1-t/{seg:.2f}))")
    return (f"scale=w='trunc({W}*{f}/2)*2+2':h=-2:eval=frame:flags=bicubic,crop={W}:{H},")


def _look(cfg) -> str:
    """Värimaailma taustalle: kevyt vinjetti (video.vignette, 0 = pois). Paneelit ja kortit eivät saa tätä."""
    v = float(cfg.video.get("vignette", 0.55) or 0)
    return f"vignette=angle={v:.2f}," if v > 0 else ""


BLACK_TRIES = 6
TOPIC_MIN_PIECE = 2.0   # s: aihekohtauksesta vähintään (trailerin kohtaukset ovat lyhyitä)
BLACK_MAX_PIECE = 0.25  # s mustaa yhdessä taustapalassa (QA: koko videossa < 3 s, blackdetect pix_th 0.10)


def _black_s(path: Path) -> float:
    """Mustan kuvan kesto (s) palassa samalla mittarilla kuin laatuportti (bot/qa.py). Virhe -> 0 (ei estä)."""
    try:
        r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                      "scale=480:-2:flags=fast_bilinear,blackdetect=d=0.2:pix_th=0.10", "-f", "null", "-"],
                     60, check=False, capture_output=True, text=True)
        return sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr or ""))
    except Exception as e:  # noqa: BLE001
        log.warning("blackdetect epäonnistui (%s): %s", path.name, e)
        return 0.0


def _enc_args() -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


class TopicPicker:
    """Valitsee taustapalan sen mukaan, mistä kertoja puhuu (footage-indeksin tunnisteet), toistamatta samaa kohtausta.
    Pisteet: +4 per osuva tunniste, -6 per aiempi käyttö, -2 sama lähde kuin edellinen, still -1 (video liikkuu)."""

    def __init__(self, cands: list[dict], wants: list[tuple[float, float, set]], seed: int | None = None):
        self.cands, self.wants = cands, sorted(wants or [])
        self.used: dict[str, int] = {}
        self.last = None
        self.rng = random.Random(seed)
        self.matched = 0

    def want_at(self, t: float) -> set:
        for a, b, tags in self.wants:
            if a <= t < b:
                return tags
        return set()

    def pick(self, t: float, seg: float) -> dict | None:
        if not self.cands:
            return None
        want = self.want_at(t + seg / 2) | self.want_at(t)
        best, best_s = None, -1e9
        for c in self.cands:
            sc = 4.0 * len(want & c["tags"]) - 6.0 * self.used.get(c["key"], 0)
            if self.last is not None and c["path"] == self.last:
                sc -= 2.0
            if c["kind"] == "still":
                sc -= 1.0
            sc += self.rng.random() * 1.5  # tasapisteissä vaihtelua
            if sc > best_s:
                best, best_s = c, sc
        if want & best["tags"]:
            self.matched += 1
        self.used[best["key"]] = self.used.get(best["key"], 0) + 1
        self.last = best["path"]
        return best

    def ban(self, c: dict):
        self.used[c["key"]] = self.used.get(c["key"], 0) + 50


def build_background(cfg, total: float, work: Path, priority: Path | None = None,
                     moments: list[tuple[float, float]] | None = None,
                     wants: list[tuple[float, float, set]] | None = None) -> Path:
    """priority = breakdown-videon uusi virallinen video: sen kuva ensin (kohdat, joihin kertoja viittaa
    moments = [(kertojan aika, lähdevideon aika)], muuten järjestyksessä alusta), sitten muu virallinen materiaali."""
    W, H = _dims(cfg)
    fps = cfg.video.fps
    files = []
    for d in cfg.paths.clip_dirs:
        p = Path(d)
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXT]
    pool = [(f, probe_duration(f)) for f in files]
    pool = [(f, d) for f, d in pool if d >= cfg.video.clip_min_s + 1]
    # vain vaakakuva: Rockstarin pysty-/neliöversiot (480x854, 1080x1080) olisivat 16:9-rajattuina sumeita
    wide = [(f, d) for f, d in pool if _is_landscape(f)]
    if len(wide) < len(pool):
        log.info("Taustapooli: %d pysty-/neliövideota ohitettu", len(pool) - len(wide))
    pool = wide
    stills = []
    for d in cfg.paths.get("still_dirs") or []:
        if Path(d).is_dir():
            stills += [f for f in Path(d).rglob("*") if f.suffix.lower() in IMG_EXT]
    excluded = {str(x).lower() for x in (cfg.paths.get("exclude_images") or [])}
    stills = [f for f in stills if f.stem.lower() not in excluded]
    ratio = float(cfg.video.get("stills_ratio", 0.0) or 0.0) if stills else 0.0
    if not pool and stills:
        ratio = 1.0
    bg = work / "bg.mp4"
    if not pool and not stills:
        log.warning("Ei klippejä kansioissa %s -> käytetään testitaustaa", cfg.paths.clip_dirs)
        _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
              f"gradients=s={W}x{H}:r={fps}:speed=0.01:d={total + 1}", "-pix_fmt", "yuv420p",
              "-c:v", "libx264", "-preset", "ultrafast", str(bg)], proc.render_timeout(total, 1.0, proc.PIECE))
        return bg

    look = _look(cfg)

    def tail_for(seg: float) -> str:
        """Täyttö 16:9 -> hidas liike (zoom sisään/ulos, Learn By Leo: liikettä tylsiin kohtiin) -> himmennys,
        väri ja vinjetti -> yhtenäinen pikselimuoto."""
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{_motion(cfg, W, H, seg)}"
                f"fps={fps},setsar=1,eq=brightness=-{cfg.video.background_dim}:saturation=1.08,{look}{UNIFORM}")

    prio = None
    if priority and Path(priority).is_file():
        pd = probe_duration(Path(priority))
        if pd >= cfg.video.clip_min_s + 1:
            prio = (Path(priority), pd)
            pool = [x for x in pool if x[0].resolve() != prio[0].resolve()] or [prio]
    sizes = {f: _probe_size(f) for f, _ in pool + ([prio] if prio else [])}
    marks = sorted(moments or [])
    topic = None
    if wants is not None and not prio and cfg.video.get("topic_footage", True):
        from . import footage
        cands = footage.candidates(cfg)
        if len(cands) >= 20:
            topic = TopicPicker(cands, wants)
            log.info("Tausta aiheen mukaan: %d kohtausta/kuvaa indeksissä", len(cands))
        else:
            log.info("Taustaindeksi puuttuu tai on pieni (%d) -> satunnainen tausta", len(cands))
    cursor = 0.0
    pieces, t, i, last = [], 0.0, 0, None
    random.shuffle(pool)
    random.shuffle(stills)
    used_stills = 0
    while t < total + 0.5:
        out = work / f"piece_{i:03d}.mp4"
        mark = next((m for m in marks if t <= m[0] < t + cfg.video.clip_max_s), None)
        if prio and (mark or cursor < prio[1] - cfg.video.clip_min_s):
            f, dur = prio
            seg = min(random.uniform(cfg.video.clip_min_s, cfg.video.clip_max_s), dur - 0.5)
            if mark:  # kertoja puhuu tästä hetkestä -> näytetään se
                marks.remove(mark)
                start = min(max(mark[1] - 0.5, 0.0), max(dur - seg - 0.25, 0.0))
            else:
                start = min(cursor, max(dur - seg - 0.25, 0.0))
                cursor = start + seg
            vf = _letterbox(f, start, sizes[f]) + tail_for(seg)
            _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seg:.2f}",
                  "-i", str(f), "-an", "-vf", vf, *_enc_args(), str(out)])
            t += seg
        elif topic is not None:
            seg = random.uniform(cfg.video.clip_min_s, cfg.video.clip_max_s)
            for attempt in range(BLACK_TRIES):
                c = topic.pick(t, seg)
                if c["kind"] == "still":
                    seg = _still_piece(cfg, c["path"], seg, out, W, H, fps)
                    used_stills += 1
                    break
                f = c["path"]
                span = c["e"] - c["s"]
                seg_v = max(min(seg, span), min(span, TOPIC_MIN_PIECE))
                start = c["s"] + topic.rng.uniform(0, max(span - seg_v, 0))
                if f not in sizes:
                    sizes[f] = _probe_size(f)
                vf = _letterbox(f, start, sizes[f]) + tail_for(seg_v)
                _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seg_v:.2f}",
                      "-i", str(f), "-an", "-vf", vf, *_enc_args(), str(out)])
                if _black_s(out) < BLACK_MAX_PIECE:
                    seg = seg_v
                    break
                topic.ban(c)
                log.info("Taustapala %s: mustaa kohtauksessa %s -> toinen kohtaus", out.name, c["key"])
            t += seg
        elif stills and random.random() < ratio:
            img = stills[used_stills % len(stills)]
            used_stills += 1
            t += _still_piece(cfg, img, random.uniform(cfg.video.clip_min_s, cfg.video.clip_max_s), out, W, H, fps)
        else:
            for attempt in range(BLACK_TRIES):  # trailerien mustat häivytykset (taustan himmennys pahentaa) -> uusi kohta
                cand = [x for x in pool if x[0] != last] or pool
                # pidempi video (esim. 27 min Extended Look) valitaan useammin, mutta lyhyetkin pääsevät mukaan
                f, dur = random.choices(cand, weights=[max(d, 1.0) ** 0.5 for _, d in cand])[0]
                seg = random.uniform(cfg.video.clip_min_s, cfg.video.clip_max_s)
                seg = min(seg, dur - 0.5)
                start = random.uniform(0, max(dur - seg - 0.25, 0))
                vf = _letterbox(f, start, sizes[f]) + tail_for(seg)
                _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seg:.2f}",
                      "-i", str(f), "-an", "-vf", vf, *_enc_args(), str(out)])
                if _black_s(out) < BLACK_MAX_PIECE:
                    break
                log.info("Taustapala %s: mustaa kohdassa %s @ %.1f s -> uusi kohta", out.name, f.name, start)
            last = f
            t += seg
        pieces.append(out)
        i += 1
    if topic is not None:
        log.info("Tausta aiheen mukaan: %d palaa, %d osui puheen aiheeseen, %d eri kohtausta, %d still-kuvaa",
                 len(pieces), topic.matched, len(topic.used), used_stills)
    elif stills:
        log.info("Tausta: %d palaa, joista %d virallisia still-kuvia", len(pieces), used_stills)
    lst = work / "pieces.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in pieces), encoding="utf-8")
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", str(bg)], proc.render_timeout(total, 0.5, proc.PIECE))
    return bg


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(t: str) -> str:
    return t.replace("{", "").replace("}", "")


def _brand_subtitles(cfg, b, words: list[dict], work: Path) -> Path:
    """Logon tyyli: Bangers, isot kirjaimet, valkoinen + puhuttava sana sinisenä (yksi rivi per sana)."""
    W, H = _dims(cfg)
    short = cfg.format == "short"
    # enintään 3 sanaa kerrallaan (Learn By Leo: helpompi lukea); video.caption_words
    size, margin_v, chunk, outline = (124, 520, 3, 8) if short else (92, 60, int(cfg.video.get("caption_words", 3) or 3), 6)
    brand.install_font(cfg, work)
    white, active = brand.ass_color(b.colors.text), brand.ass_color(brand.SUB_ACTIVE)
    on, off = f"&H{active[4:]}&", f"&H{white[4:]}&"  # override-tagit: &HBBGGRR&
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{brand.font_family(b)},{size},{white},{active},{brand.ass_color(b.colors.outline)},{brand.ass_color(brand.SUB_SHADOW)},0,0,0,0,100,100,0,0,1,{outline},3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # ryhmät: enintään `chunk` sanaa, ja tauko (lauseen/segmentin raja) aloittaa aina uuden rivin
    groups = []
    for w in words:
        if groups and len(groups[-1]) < chunk and w["s"] - groups[-1][-1]["e"] <= 0.3:
            groups[-1].append(w)
        else:
            groups.append([w])
    lines = []
    for gi, grp in enumerate(groups):
        s, e = grp[0]["s"], grp[-1]["e"]
        if gi + 1 < len(groups):
            e = min(max(e, s + 0.4), groups[gi + 1][0]["s"])
        toks = [_clean(w["w"]).upper() for w in grp]
        prev = s
        for j in range(len(grp)):
            ws = max(s if j == 0 else grp[j]["s"], prev)
            we = grp[j + 1]["s"] if j + 1 < len(grp) else e
            we = min(max(we, ws), e)
            if j + 1 == len(grp):
                we = max(we, e)
            if we - ws < 0.005:
                continue
            txt = " ".join(f"{{\\1c{on}}}{t}{{\\1c{off}}}" if k == j else t for k, t in enumerate(toks))
            lines.append(f"Dialogue: 0,{_ass_time(ws)},{_ass_time(we)},Main,,0,0,0,,{txt}")
            prev = we
    p = work / "subs.ass"
    p.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return p


TOPIC_SIZE = 48          # px (1080p), Bangers
TOPIC_MARGIN_V = 26      # yläreunasta
TOPIC_MAX_W = 1600       # px, kaistan suurin leveys
TOPIC_DIM = "&H70&"      # muiden aiheiden läpinäkyvyys (00 = täysi, FF = näkymätön)
TOPIC_SEP = "  •  "


def _topic_width(names: list[str]) -> float:
    """Arvio kaistan leveydestä (Bangers ~0.22 x fonttikoko per merkki mitattuna, +varmuus)."""
    chars = sum(len(n) for n in names) + len(TOPIC_SEP) * max(len(names) - 1, 0)
    return chars * TOPIC_SIZE * 0.25


def topic_window(names: list[str], k: int, before: int = 1) -> tuple[int, int]:
    """Näytettävät aiheet [a, b): enintään `before` edellistä, nykyinen ja niin monta seuraavaa kuin leveyteen mahtuu
    (edellinen mahtuu aina ensin, jotta nykyinen ei ole reunassa)."""
    a, b = k, k + 1
    if a > 0 and _topic_width(names[a - 1:b]) <= TOPIC_MAX_W:
        a -= 1
    while b < len(names) and _topic_width(names[a:b + 1]) <= TOPIC_MAX_W:
        b += 1
    while a > max(k - before, 0) and _topic_width(names[a - 1:b]) <= TOPIC_MAX_W:
        a -= 1
    return a, b



def _minus(s: float, e: float, holes: list[tuple[float, float]], min_len: float = 0.8) -> list[tuple[float, float]]:
    """Väli [s, e) ilman reikiä; alle min_len sekunnin palat pois (ei välkettä)."""
    out = [(s, e)]
    for hs, he in sorted(holes):
        nxt = []
        for a, b in out:
            if he <= a or hs >= b:
                nxt.append((a, b))
                continue
            if hs > a:
                nxt.append((a, hs))
            if he < b:
                nxt.append((he, b))
        out = nxt
    return [(a, b) for a, b in out if b - a >= min_len]


def add_topic_bar(cfg, subs: Path | None, chapters: list[tuple[float, str]], end: float,
                  hide: list[tuple[float, float]] | None = None) -> int:
    """Uutis-/opasvideon yläreunaan aihekaista: käsiteltävä aihe korostettuna (brändin sininen), edelliset ja
    seuraavat himmeinä. Ei introssa (ennen ensimmäistä aihetta), loppuruudussa eikä hide-väleillä (uutispaneelin
    oma yläpalkki). Palauttaa rivien määrän."""
    b = brand.settings(cfg)
    if not subs or not b or cfg.format == "short" or not cfg.video.get("topic_bar", True):
        return 0
    items = [(float(t), str(n)) for t, n in chapters if str(n).strip().lower() != "intro"]
    if len(items) < 2:
        return 0
    names = [_clean(n).upper() for _, n in items]
    white, active = brand.ass_color(b.colors.text), brand.ass_color(brand.SUB_ACTIVE)
    on, off = f"&H{active[4:]}&", f"&H{white[4:]}&"
    # kaksi kerrosta: TopicBox = yksi yhtenäinen tausta (sama teksti näkymättömänä, BorderStyle 3), Topic = värit
    # (jos laatikko piirrettäisiin väritettyyn tekstiin, libass piirtää sen paloina päällekkäin)
    fnt = brand.font_family(b)
    style = (f"Style: TopicBox,{fnt},{TOPIC_SIZE},&HFF000000,&HFF000000,&H70000000,&H00000000,"
             f"0,0,0,0,100,100,1,0,3,14,0,8,60,60,{TOPIC_MARGIN_V},1\n"
             f"Style: Topic,{fnt},{TOPIC_SIZE},{white},{white},&H90000000,&H80000000,"
             f"0,0,0,0,100,100,1,0,1,2,1,8,60,60,{TOPIC_MARGIN_V},1")
    lines = []
    for k, (t, _) in enumerate(items):
        e = min(items[k + 1][0] if k + 1 < len(items) else end, end)
        if e - t < 1.0:
            continue
        a, z = topic_window(names, k)
        parts = []
        for j in range(a, z):
            parts.append((f"{{\\1c{on}\\1a&H00&}}" if j == k else f"{{\\1c{off}\\1a{TOPIC_DIM}}}") + names[j])
        txt = f"{{\\1a{TOPIC_DIM}}}{TOPIC_SEP}".join(parts)
        if a > 0:
            txt = f"{{\\1c{off}\\1a{TOPIC_DIM}}}…  " + txt
        if z < len(names):
            txt += f"{{\\1c{off}\\1a{TOPIC_DIM}}}  …"
        plain = TOPIC_SEP.join(names[a:z])
        plain = ("…  " if a > 0 else "") + plain + ("  …" if z < len(names) else "")
        for s0, e0 in _minus(t, e, hide or []):
            lines.append(f"Dialogue: 1,{_ass_time(s0)},{_ass_time(e0)},TopicBox,,0,0,0,,{plain}")
            lines.append(f"Dialogue: 2,{_ass_time(s0)},{_ass_time(e0)},Topic,,0,0,0,,{txt}")
    if not lines:
        return 0
    text = Path(subs).read_text(encoding="utf-8")
    text = text.replace("\n\n[Events]", "\n" + style + "\n\n[Events]", 1)
    Path(subs).write_text(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    log.info("Aihekaista: %d aihetta yläreunassa", len(items))
    return len(lines)


def write_subtitles(cfg, words: list[dict], work: Path) -> Path:
    b = brand.settings(cfg)
    if b:
        return _brand_subtitles(cfg, b, words, work)
    W, H = _dims(cfg)
    short = cfg.format == "short"
    size, align, margin_v, chunk = (84, 2, 520, 3) if short else (60, 2, 70, 6)
    font = cfg.video.subtitle_font_name
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},&H00FFFFFF,&H0015CCFA,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i in range(0, len(words), chunk):
        grp = words[i:i + chunk]
        s, e = grp[0]["s"], grp[-1]["e"]
        if i + chunk < len(words):
            e = min(max(e, s + 0.4), words[i + chunk]["s"])
        txt = " ".join(w["w"] for w in grp).replace("{", "").replace("}", "")
        if short:
            txt = txt.upper()
        lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Main,,0,0,0,,{txt}")
    p = work / "subs.ass"
    p.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def render_final(cfg, bg: Path, voice: Path, duration: float, cards: list[dict],
                 subs: Path | None, out: Path, work: Path, plan: dict | None = None,
                 sfx: Path | None = None) -> Path:
    """cards ja subs ovat jo lopullisella aikajanalla. plan (brand.long_plan) lisää intron alkuun
    (ääni + musiikki siirtyvät plan['offset'] verran), loppuruudun ja hiljaisen hännän.

    cards: {png} = läpinäkyvä kortti (häivytys 0,25 s), {video} = valmis klippi (panels.prepare: uutispaneeli,
    lainaus), {seq} = läpinäkyvä kuvasarja (osion otsikko). Järjestys: klipit, kortit, otsikot -> Shortsin
    koukkupalkki jää lainauksen päälle. sfx: whoosh-raita, sekoitetaan puheen alle."""
    fps = cfg.video.fps
    off = plan["offset"] if plan else 0.0
    total = plan["total"] if plan else duration
    inputs = ["-i", bg.name, "-i", voice.name]
    filt, vlabel, idx = [], "[0:v]", 2
    if plan and plan.get("intro"):
        # intro + pelikuva yhdeksi taustaksi; intron syaani loppu ristihäivytetään pelikuvaan
        inputs += ["-i", str(Path(plan["intro"]).resolve())]
        intro_idx, idx = idx, idx + 1
        norm = f"fps={fps},format=yuv420p,setsar=1,settb=AVTB"
        filt.append(f"[{intro_idx}:v]{norm}[iv]")
        filt.append(f"[0:v]{norm}[bv]")
        x = plan["flash"]
        filt.append(f"[iv][bv]xfade=transition=fade:duration={x:.3f}:offset={off - x:.3f}[base]")
        vlabel = "[base]"
    for k, c in enumerate(c for c in cards if c.get("video")):
        inputs += ["-i", str(Path(c["video"]).resolve())]
        filt.append(f"[{idx}:v]setpts=PTS-STARTPTS+{c['s']:.4f}/TB[x{k}]")
        filt.append(f"{vlabel}[x{k}]overlay=0:0:eof_action=pass:"
                    f"enable='between(t,{c['s']:.4f},{c['e']:.4f})'[vx{k}]")
        vlabel = f"[vx{k}]"
        idx += 1
    for k, c in enumerate(c for c in cards if c.get("png")):
        d = c["e"] - c["s"]
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.2f}", "-i", c["png"].name]
        filt.append(f"[{idx}:v]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,"
                    f"fade=t=out:st={max(d - 0.25, 0):.2f}:d=0.25:alpha=1,"
                    f"setpts=PTS-STARTPTS+{c['s']:.3f}/TB[c{k}]")
        filt.append(f"{vlabel}[c{k}]overlay=0:0:eof_action=pass:"
                    f"enable='between(t,{c['s']:.3f},{c['e']:.3f})'[v{k}]")
        vlabel = f"[v{k}]"
        idx += 1
    for k, c in enumerate(c for c in cards if c.get("seq")):
        q, d = c["seq"], c["e"] - c["s"]
        rest = max(d - q["n"] / fps, 1.0 / fps)
        inputs += ["-framerate", str(fps), "-start_number", "0", "-i", q["pattern"],
                   "-loop", "1", "-framerate", str(fps), "-t", f"{rest:.3f}", "-i", q["static"]]
        filt.append(f"[{idx}:v]setsar=1,format=rgba[q{k}a]")
        filt.append(f"[{idx + 1}:v]setsar=1,format=rgba[q{k}b]")
        filt.append(f"[q{k}a][q{k}b]concat=n=2:v=1:a=0,fade=t=out:st={q['fade_st']:.3f}:d={q['fade_d']:.3f}:alpha=1,"
                    f"setpts=PTS-STARTPTS+{c['s']:.4f}/TB[q{k}]")
        filt.append(f"{vlabel}[q{k}]overlay={q['x']}:{q['y']}:eof_action=pass:"
                    f"enable='between(t,{c['s']:.4f},{c['e']:.4f})'[vq{k}]")
        vlabel = f"[vq{k}]"
        idx += 2
    if plan and plan.get("endscreen"):
        es, d = plan["outro_start"], total - plan["outro_start"]
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.2f}", "-i", str(Path(plan["endscreen"]).resolve())]
        filt.append(f"[{idx}:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1,setpts=PTS-STARTPTS+{es:.3f}/TB[es]")
        filt.append(f"{vlabel}[es]overlay=0:0:eof_action=pass:enable='gte(t,{es:.3f})'[ves]")
        vlabel = "[ves]"
        idx += 1
    if subs is not None:
        fdir = ":fontsdir=fonts" if brand.settings(cfg) and (work / "fonts").is_dir() else ""
        filt.append(f"{vlabel}ass={subs.name}{fdir}[vs]")
        vlabel = "[vs]"
    filt.append(f"{vlabel}format=yuv420p[vout]")

    # ääni: puhe (+ intron jälkeen), intron musiikki (tai syntetisoitu sting), taustamatto, loppuruudun musiikki
    vo = "[1:a]" + music.FMT
    if off:
        vo += f",adelay={int(round(off * 1000))}:all=1"
    if total > duration:
        vo += f",apad=whole_dur={total:.3f}"
    filt.append(f"{vo}[vo]")
    mix = ["[vo]"]
    m_inputs, m_filt, m_labels, idx, intro_music = music.mix(cfg, plan, total, duration, idx)
    inputs += m_inputs
    filt += m_filt
    if plan and plan.get("intro") and not intro_music:
        filt.append(f"[{intro_idx}:a]{music.FMT},atrim=0:{off:.3f}[is]")
        mix.append("[is]")
    mix += m_labels
    if sfx is not None:
        inputs += ["-i", str(Path(sfx).resolve())]
        filt.append(f"[{idx}:a]{music.FMT}[fx]")
        mix.append("[fx]")
        idx += 1
    if len(mix) > 1:
        filt.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[aout]")
    else:
        filt.append("[vo]anull[aout]")

    codec = cfg.video.codec
    venc = ["-c:v", codec]
    if codec == "libx264":
        venc += ["-preset", cfg.video.preset, "-crf", str(cfg.video.crf)]
    else:
        venc += ["-b:v", "8M"]
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-stats", *inputs,
           "-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "[aout]",
           *venc, "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}",
           "-movflags", "+faststart", str(out.resolve())]
    limit = proc.render_timeout(total)
    log.info("Renderöidään %s (%.0f s, %d korttia, aikaraja %.0f min)", out.name, total, len(cards), limit / 60)
    t0 = time.monotonic()
    proc.run(cmd, limit, cwd=work)
    log.info("Renderöinti valmis %.0f s:ssa", time.monotonic() - t0)
    return out
