"""Musiikki: taustamatto (bed), intron ja loppuruudun musiikki, äänekkyysmittaus (EBU R128).

Taustamatto: `video.music_file` (oletus: ensimmäinen äänitiedosto `paths.music_dir`-kansiossa). Kappaleen tasainen
osa `video.music_loop` [alku, loppu] toistetaan saumattomasti (3 s ristihäivytys kopioiden välissä) ja normalisoidaan
integroituun äänekkyyteen `video.music_lufs`. Valmis matto tallennetaan välimuistiin ${DATA_DIR}/brand_cache
(yksi tiedosto per kopioiden määrä = per pituusluokka).

Intro (`brand.intro.music`): kappale alkaa kohdasta `music_start`, taso `music_lufs`, häivytys `music_fade` s
ensimmäisten puhuttujen sanojen alle. Korvaa syntetisoidun whoosh/tömähdys-äänen (se jää varalle).

Loppuruutu (`brand.outro.music`): kappaleen viimeiset sekunnit niin, että `music_end` osuu videon loppuun.
Ristihäivytys taustamatosta `crossfade` s loppuruudun alussa; hiljaa (`music_under_lufs`) kun puhe vielä kuuluu,
nousee tasolle `music_lufs` hiljaisen hännän ajaksi ja loppuu kappaleen omaan loppuun.
"""
import hashlib
import json
import logging
import math
import random
import re
import subprocess
from pathlib import Path

from . import brand, proc

log = logging.getLogger(__name__)
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
BED_VERSION = 1
XFADE = 3.0          # ristihäivytys silmukan kopioiden välissä
BED_FADE_IN = 1.5    # matto nousee puheen alkaessa (intron musiikki häipyy samaan aikaan)
FMT = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _hash(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


def lufs(path: Path, start: float | None = None, dur: float | None = None) -> float | None:
    """Integroitu äänekkyys (LUFS) koko tiedostosta tai väliltä [start, start+dur]. None = ei mitattavissa."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-af", "ebur128", "-f", "null", "-"]
    try:
        r = proc.run(cmd, proc.analyze_timeout(dur if dur is not None else _probe(path)), check=False,
                     capture_output=True, text=True)
    except proc.ProcTimeout:
        return None
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr)
    if not m or m[-1] == "-inf":
        return None
    v = float(m[-1])
    return v if v > -70 else None


_LUFS_CACHE: dict[str, float | None] = {}


def _lufs_cached(cfg, path: Path, start: float, dur: float) -> float | None:
    """Mittaukset muistiin myös levylle (brand_cache/lufs.json), ettei samaa väliä pureta joka ajossa."""
    key = f"{_hash(path)}|{start:.2f}|{dur:.2f}"
    if key in _LUFS_CACHE:
        return _LUFS_CACHE[key]
    f = brand._cache_dir(cfg) / "lufs.json"
    try:
        disk = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disk = {}
    if key not in disk:
        disk[key] = lufs(path, start, dur)
        try:
            f.write_text(json.dumps(disk, indent=1), encoding="utf-8")
        except OSError:
            pass
    _LUFS_CACHE[key] = disk[key]
    return disk[key]


def _gain_db(measured: float | None, target: float, fallback: float = 0.0) -> float:
    return target - measured if measured is not None else fallback


# ---------------------------------------------------------------- taustamatto

def music_file(cfg) -> Path | None:
    v = cfg.video
    f = v.get("music_file")
    if f:
        p = Path(f)
        if not p.is_absolute() and not p.is_file():
            p = Path(cfg.paths.music_dir) / f
        if p.is_file():
            return p
        log.warning("Taustamusiikkia %s ei löydy -> ensimmäinen tiedosto kansiosta %s", f, cfg.paths.music_dir)
    d = Path(cfg.paths.music_dir)
    files = sorted(x for x in d.glob("*") if x.suffix.lower() in AUDIO_EXT) if d.is_dir() else []
    return files[0] if files else None


def _probe(p: Path) -> float:
    from .render import probe_duration
    return probe_duration(p)


def _loop_region(cfg, src: Path) -> tuple[float, float]:
    total = _probe(src)
    loop = cfg.video.get("music_loop") or [0.0, total]
    a, b = float(loop[0]), float(loop[1])
    if total and b > total:
        b = total
    if b - a < XFADE * 3:  # liian lyhyt silmukka -> koko kappale
        a, b = 0.0, total
    return a, b


def bed(cfg, seconds: float, random_start: bool = False) -> tuple[Path, float] | None:
    """Saumaton, normalisoitu taustamatto vähintään `seconds` pitkä. Palauttaa (tiedosto, aloituskohta).
    random_start (Shorts): aloitus satunnaisesta kohdasta silmukkaa."""
    src = music_file(cfg)
    if src is None:
        return None
    a, b = _loop_region(cfg, src)
    seg = b - a
    period = seg - XFADE
    start = random.uniform(0, period) if random_start else 0.0
    need = start + seconds + 1.0
    n = max(1, math.ceil((need - XFADE) / period))  # n kopiota = n*seg - (n-1)*XFADE sekuntia
    target = float(cfg.video.get("music_lufs", -30))
    key = hashlib.sha1(f"{BED_VERSION}|{_hash(src)}|{a:.2f}|{b:.2f}|{target}|{n}".encode()).hexdigest()[:16]
    out = brand._cache_dir(cfg) / f"bed_{key}.flac"
    if out.is_file() and out.stat().st_size > 0:
        return out, start
    measured = _lufs_cached(cfg, src, a, seg)
    gain = _gain_db(measured, target, fallback=20 * math.log10(float(cfg.video.get("music_volume", 0.07) or 0.07)))
    log.info("Rakennetaan taustamatto: %s, silmukka %.1f-%.1f s x %d, %.1f LUFS -> %.1f LUFS (%+.1f dB)",
             src.name, a, b, n, measured if measured is not None else float("nan"), target, gain)
    parts = [f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,{FMT},volume={gain:.2f}dB"]
    if n == 1:
        parts[0] += "[out]"
    else:
        parts[0] += f",asplit={n}" + "".join(f"[c{i}]" for i in range(n))
        prev = "[c0]"
        for i in range(1, n):
            lab = "[out]" if i == n - 1 else f"[x{i}]"
            parts.append(f"{prev}[c{i}]acrossfade=d={XFADE}:c1=qsin:c2=qsin{lab}")
            prev = lab
    tmp = out.with_suffix(".tmp.flac")
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex",
              ";".join(parts), "-map", "[out]", "-c:a", "flac", str(tmp)], proc.analyze_timeout(need))
    tmp.replace(out)
    return out, start


# ---------------------------------------------------------------- intro / loppuruutu

def _track(path) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return p
    log.warning("Brändimusiikkia %s ei löydy -> varaääni", p)
    return None


def intro_music(cfg) -> dict | None:
    b = brand.settings(cfg)
    if not b:
        return None
    p = _track(b.intro.get("music"))
    if p is None:
        return None
    start = float(b.intro.get("music_start", 0.0) or 0.0)
    measured = _lufs_cached(cfg, p, start + 0.4, 30.0)  # pudotuksen jälkeinen osa = intron taso
    return {"path": p, "start": start, "gain": _gain_db(measured, float(b.intro.get("music_lufs", -16))),
            "fade": float(b.intro.get("music_fade", 0.8) or 0.8)}


def outro_music(cfg) -> dict | None:
    b = brand.settings(cfg)
    if not b:
        return None
    p = _track(b.outro.get("music"))
    if p is None:
        return None
    end = float(b.outro.get("music_end", 0.0) or 0.0) or _probe(p)
    span = float(b.outro.get("seconds", 12) or 12)
    measured = _lufs_cached(cfg, p, max(end - span, 0.0), span)
    return {"path": p, "end": end, "gain": _gain_db(measured, float(b.outro.get("music_lufs", -16))),
            "under": float(b.outro.get("music_under_lufs", -26)) - float(b.outro.get("music_lufs", -16)),
            "crossfade": float(b.outro.get("crossfade", 2.0) or 2.0)}


# ---------------------------------------------------------------- render_final-osuus

DIP_DEPTH = 0.35     # taustamusiikki aiheen vaihtuessa (-9 dB)
DIP_PRE = 1.2        # s: hiljenee tämän ajan ennen osion otsikkoa (osio päättyy -> odotus uudesta)
DIP_BACK = 0.8       # s: palaa otsikon jälkeen
PAUSE_PRE = 1.9      # s: lunastusosio: musiikki katkeaa nousun (riser) alkaessa ja palaa otsikon jälkeen


def dip_expr(dips: list) -> str:
    """ffmpeg volume-lauseke (t = lopullisen videon aika): [(otsikon alku, loppu, lunastus?)]. Tavallinen osio:
    musiikki laskee DIP_DEPTH:iin ennen otsikkoa ja palaa sen jälkeen. Lunastus (viimeinen osio): musiikki
    katkeaa kokonaan nousun ajaksi (Learn By Leo: tauko nostaa hetken esiin) ja palaa otsikon jälkeen."""
    parts = []
    for s, e, payoff in sorted(dips):
        if payoff:
            a, d, a2 = s - PAUSE_PRE, 0.0, s - PAUSE_PRE + 0.15
        else:
            a, d, a2 = s - DIP_PRE, DIP_DEPTH, s
        h, r = max(e - 0.4, a2), e + DIP_BACK
        if a < 0:
            continue
        parts.append(f"if(lt(t,{a:.3f}),1,if(lt(t,{a2:.3f}),1-{1 - d:.3f}*(t-{a:.3f})/{a2 - a:.3f},"
                     f"if(lt(t,{h:.3f}),{d:.3f},if(lt(t,{r:.3f}),{d:.3f}+{1 - d:.3f}*(t-{h:.3f})/{r - h:.3f},1))))")
    return "*".join(parts)


def mix(cfg, plan: dict | None, total: float, voice_dur: float, idx: int) -> tuple[list[str], list[str], list[str], int, bool]:
    """ffmpeg-syötteet + suodattimet musiikille. Palauttaa (inputs, filters, mix_labels, next_idx, intro_music_used).

    plan = brand.long_plan (pitkä video / opas) tai None (Short: matto satunnaisesta kohdasta)."""
    inputs, filt, labels = [], [], []
    off = plan["offset"] if plan else 0.0
    es = plan.get("outro_start") if plan and plan.get("endscreen") else None
    om = outro_music(cfg) if es is not None else None
    if om and om["end"] - (total - es) < 0:
        log.warning("Loppumusiikki on lyhyempi kuin loppuruutu -> ei loppumusiikkia")
        om = None
    im = intro_music(cfg) if plan and plan.get("intro") else None

    # taustamatto: puheen alusta loppuun (tai loppuruudun ristihäivytyksen loppuun)
    bed_end = (es + om["crossfade"]) if om else total
    need = max(bed_end - off, 1.0)
    try:
        bd = bed(cfg, need, random_start=plan is None)
    except (subprocess.CalledProcessError, OSError, proc.ProcTimeout) as e:
        log.warning("Taustamaton rakentaminen epäonnistui: %s", e)
        bd = None
    if bd:
        path, start = bd
        inputs += ["-i", str(path.resolve())]
        f = (f"[{idx}:a]atrim={start:.3f}:{start + need:.3f},asetpts=PTS-STARTPTS,{FMT},"
             f"afade=t=in:st=0:d={BED_FADE_IN if off else 0.3}")
        if off:
            f += f",adelay={int(round(off * 1000))}:all=1"
        dips = dip_expr((plan or {}).get("music_dips") or [])
        if dips:
            f += f",volume='{dips}':eval=frame"
        if om:
            f += f",afade=t=out:st={es:.3f}:d={om['crossfade']:.3f}"
        else:
            f += f",afade=t=out:st={max(total - 2, 0):.3f}:d=2"
        filt.append(f + "[bed]")
        labels.append("[bed]")
        idx += 1

    if im:
        d = off + im["fade"]
        inputs += ["-i", str(im["path"].resolve())]
        filt.append(f"[{idx}:a]atrim={im['start']:.3f}:{im['start'] + d + 0.05:.3f},asetpts=PTS-STARTPTS,{FMT},"
                    f"volume={im['gain']:.2f}dB,afade=t=out:st={off:.3f}:d={im['fade']:.3f}[im]")
        labels.append("[im]")
        idx += 1

    if om:
        L = total - es
        voice_end = off + voice_dur - es  # loppuruudun aikajanalla: puhe loppuu tässä
        g_full, g_under = om["gain"], om["gain"] + om["under"]
        ramp = 1.0
        v1, v2 = 10 ** (g_under / 20), 10 ** (g_full / 20)
        if voice_end <= 0:
            vol = f"volume={g_full:.2f}dB"
        else:
            vol = (f"volume='if(lt(t,{voice_end:.3f}),{v1:.5f},"
                   f"if(lt(t,{voice_end + ramp:.3f}),{v1:.5f}+({v2:.5f}-{v1:.5f})*(t-{voice_end:.3f})/{ramp},"
                   f"{v2:.5f}))':eval=frame")
        inputs += ["-i", str(om["path"].resolve())]
        filt.append(f"[{idx}:a]atrim={om['end'] - L:.3f}:{om['end']:.3f},asetpts=PTS-STARTPTS,{FMT},{vol},"
                    f"afade=t=in:st=0:d={om['crossfade']:.3f},adelay={int(round(es * 1000))}:all=1[om]")
        labels.append("[om]")
        idx += 1
    return inputs, filt, labels, idx, im is not None
