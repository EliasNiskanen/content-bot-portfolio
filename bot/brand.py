"""Kanavabrändi: intro-sting, loppuruutu (end screen), brändiväri ja tekstitystyyli.

Kaikki asetukset `brand:`-osiosta. Jos osio puuttuu tai `brand.enabled: false`, settings() palauttaa
None ja botti toimii kuten ennen (pinkki aksentti, vanha tekstitys, ei introa/outroa).

  python -m bot.brand    # kirjoittaa assets/brand/endscreen_template.png (opas YouTube Studioon)
"""
import copy
import hashlib
import logging
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import proc
from .config import Cfg

log = logging.getLogger(__name__)
INTRO_VERSION = 3  # nosta, jos intron ulkoasu/ääni muuttuu koodissa -> välimuisti rakennetaan uudelleen
INTRO_TIMEOUT = 600.0  # intro-stingin renderöinti (3 s, kuvat Pythonista putkeen) enintään 10 min

DEFAULTS = {
    "enabled": False,
    "name": "",
    "tagline": "",
    "logo": "",
    "font": "",
    "colors": {"bg": "#050B1A", "panel": "#0A1630", "primary": "#1E9BFF", "glow": "#4FD0FF",
               "text": "#FFFFFF", "outline": "#04112B"},
    "intro": {"enabled": True, "seconds": 3.0, "sound": True},
    "outro": {"enabled": True, "seconds": 12.0, "tail_seconds": 4.0},
    # pitkän videon "uutissivu"-paneeli, lainauskortit (myös Shorts), osioiden otsikot ja niiden whoosh-ääni
    "news_panel": True,
    "quotes": True,
    "section_titles": True,
    "whoosh": True,
}


def features(cfg) -> dict:
    """Uudet ruutuelementit: kaikki pois, jos brändi ei ole käytössä (vanha ulkoasu)."""
    b = settings(cfg)
    keys = ("news_panel", "quotes", "section_titles", "whoosh", "hits", "risers", "music_dips")
    return {k: bool(b.get(k, True)) if b else False for k in keys}

# Tekstityksen värit (logon valkoinen "CATFISH" + sininen "GAMING")
SUB_ACTIVE = "#2FB4FF"
SUB_SHADOW = "#0B5ED7"

# Loppuruudun mitat (1920x1080, origo vasen yläkulma)
# Loppuruudun mitat = YouTube Studion loppuruutuelementtien paikat (mitattu julkaistusta videosta, 1920x1080)
ES_LOGO_C, ES_LOGO_D, ES_RING = (1000, 288), 297, 6
ES_SUB_Y, ES_SUB_SIZE = 98, 60             # SUBSCRIBE-teksti tilausympyrän yläpuolella
ES_BOX_W, ES_BOX_H, ES_BOX_R, ES_BORDER = 750, 422, 24, 6
ES_BOXES = [((126, 482), "WATCH NEXT"), ((1036, 482), "MORE FROM THE CHANNEL")]
ES_LABEL_Y, ES_LABEL_SIZE = 446, 40


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings(cfg) -> Cfg | None:
    """Brändiasetukset oletuksineen, tai None jos brändi ei ole käytössä."""
    raw = cfg.get("brand") or {}
    if not raw.get("enabled"):
        return None
    return Cfg(_merge(DEFAULTS, raw))


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> ASS &HAABBGGRR"""
    r, g, b = rgb(hex_color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def accent(cfg, default: tuple[int, int, int]) -> tuple[int, int, int]:
    b = settings(cfg)
    return rgb(b.colors.primary) if b else default


def font(cfg, size: int):
    b = settings(cfg) or Cfg(DEFAULTS)
    for f in (b.get("font"), cfg.video.font):
        try:
            return ImageFont.truetype(f, size)
        except (OSError, TypeError, ValueError):
            continue
    return ImageFont.load_default(size)


def font_family(b: Cfg) -> str:
    try:
        return ImageFont.truetype(b.font, 20).getname()[0]
    except (OSError, TypeError, ValueError):
        return "Bangers"


def install_font(cfg, work: Path) -> Path | None:
    """Kopioi brändifontin työkansion fonts/-alikansioon (libass: ass=...:fontsdir=fonts)."""
    b = settings(cfg)
    if not b or not Path(b.font).is_file():
        if b:
            log.warning("Brändifonttia %s ei löydy -> libass käyttää korvaavaa fonttia", b.font)
        return None
    d = work / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / Path(b.font).name
    if not dst.exists() or dst.stat().st_size != Path(b.font).stat().st_size:
        shutil.copyfile(b.font, dst)
    return d


def _cache_dir(cfg) -> Path:
    base = os.environ.get("DATA_DIR") or str(Path(cfg.paths.work_dir).parent)
    d = Path(base) / "brand_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_hash(p: str) -> str:
    try:
        return hashlib.sha1(Path(p).read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


# ---------------------------------------------------------------- piirto

def _background(b: Cfg, W: int, H: int) -> Image.Image:
    """Tasainen tumma tausta + pehmeä sininen säteittäinen hehku keskellä (~25 %)."""
    bg = np.array(rgb(b.colors.bg), dtype=np.float32)
    glow = np.array(rgb(b.colors.primary), dtype=np.float32)
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    d2 = ((x - W / 2) ** 2 + (y - H / 2) ** 2) / (0.70 * H) ** 2
    a = (0.25 * np.exp(-2.2 * d2))[..., None]
    img = bg * (1 - a) + glow * a
    return Image.fromarray(np.clip(img + 0.5, 0, 255).astype(np.uint8), "RGB")


def _logo(b: Cfg) -> Image.Image | None:
    try:
        return Image.open(b.logo).convert("RGBA")
    except (OSError, AttributeError, TypeError):
        log.warning("Brändilogoa %s ei löydy", b.get("logo"))
        return None


def _feather(img: Image.Image, frac: float = 0.06) -> Image.Image:
    """Pehmentää neliölogon reunat, ettei logon oma tausta näy terävänä laatikkona."""
    w, h = img.size
    m = int(min(w, h) * frac)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([m, m, w - m, h - m], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(m * 0.6))
    out = img.copy()
    out.putalpha(Image.fromarray((np.asarray(img.getchannel("A"), np.float32)
                                  * np.asarray(mask, np.float32) / 255).astype(np.uint8), "L"))
    return out


def _with_alpha(img: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * max(a, 0.0) + 0.5)))
    return out


def _glyph_box(fnt, text: str, stroke: int = 0):
    return fnt.getbbox(text, anchor="la", stroke_width=stroke)


def _draw_text(d: ImageDraw.ImageDraw, text: str, fnt, fill, *, cx=None, left=None, cy=None, top=None,
               stroke: int = 0, stroke_fill=None):
    """Sijoittaa tekstin näkyvän glyyfilaatikon mukaan (keskitys tai vasen reuna / keskikohta tai yläreuna)."""
    l, t, r, bt = _glyph_box(fnt, text)
    x = cx - (l + r) / 2 if cx is not None else left - l
    y = cy - (t + bt) / 2 if cy is not None else top - t
    d.text((x, y), text, font=fnt, fill=fill, anchor="la", stroke_width=stroke, stroke_fill=stroke_fill)
    return x + l, y + t, x + r, y + bt


def render_endscreen(cfg, out: Path) -> Path:
    """Loppuruutu 1920x1080: logo ympyrässä (Subscribe-elementin paikka) + 2 videopaikkaa."""
    b = settings(cfg) or Cfg(DEFAULTS)
    W, H, ss = 1920, 1080, 3
    img = _background(b, W, H).convert("RGBA")
    primary, glow, panel = rgb(b.colors.primary), rgb(b.colors.glow), rgb(b.colors.panel)

    # videopaikkojen pehmeä ulkohehku
    halo = Image.new("RGBA", (W, H), glow + (0,))
    hd = ImageDraw.Draw(halo)
    for (x, y), _ in ES_BOXES:
        hd.rounded_rectangle([x - 2, y - 2, x + ES_BOX_W + 2, y + ES_BOX_H + 2], radius=ES_BOX_R + 2,
                             outline=glow + (200,), width=10)
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(9)))

    # terävät muodot ylinäytteistettynä (pehmeät reunat)
    big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    for (x, y), _ in ES_BOXES:
        bd.rounded_rectangle([x * ss, y * ss, (x + ES_BOX_W) * ss - 1, (y + ES_BOX_H) * ss - 1],
                             radius=ES_BOX_R * ss, fill=panel + (255,), outline=primary + (255,),
                             width=ES_BORDER * ss)
    cx, cy = ES_LOGO_C
    r = ES_LOGO_D // 2
    bd.ellipse([(cx - r) * ss, (cy - r) * ss, (cx + r) * ss - 1, (cy + r) * ss - 1], fill=b.colors.bg)
    img.alpha_composite(big.resize((W, H), Image.LANCZOS))

    logo = _logo(b)
    if logo is not None:
        logo = logo.resize((ES_LOGO_D, ES_LOGO_D), Image.LANCZOS)
        m = Image.new("L", (ES_LOGO_D * ss, ES_LOGO_D * ss), 0)
        ImageDraw.Draw(m).ellipse([0, 0, ES_LOGO_D * ss - 1, ES_LOGO_D * ss - 1], fill=255)
        logo.putalpha(m.resize((ES_LOGO_D, ES_LOGO_D), Image.LANCZOS))
        img.alpha_composite(logo, dest=(cx - r, cy - r))
    ring = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([(cx - r) * ss, (cy - r) * ss, (cx + r) * ss - 1, (cy + r) * ss - 1],
                                 outline=primary + (255,), width=ES_RING * ss)
    img.alpha_composite(ring.resize((W, H), Image.LANCZOS))

    d = ImageDraw.Draw(img)
    _draw_text(d, "SUBSCRIBE", font(cfg, ES_SUB_SIZE), rgb(b.colors.text), cx=cx, cy=ES_SUB_Y,
               stroke=4, stroke_fill=rgb(b.colors.outline))
    f_lab = font(cfg, ES_LABEL_SIZE)
    for (x, _), label in ES_BOXES:
        _draw_text(d, label, f_lab, glow, left=x, cy=ES_LABEL_Y)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out)
    return out


# ---------------------------------------------------------------- intro

def _ease_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _smooth(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def flash_seconds(b: Cfg) -> float:
    """Intron lopun syaani ruutu, joka ristihäivytetään videon ensimmäisiin ruutuihin (xfade)."""
    return 0.2 * float(b.intro.seconds) / 3.0


def _intro_frames(cfg, b: Cfg, W: int, H: int, fps: int):
    k = float(b.intro.seconds) / 3.0  # aikataulu on määritelty 3.0 s introlle, skaalataan
    n = int(round(float(b.intro.seconds) * fps))
    base = _background(b, W, H).convert("RGBA")
    cyan = Image.new("RGBA", (W, H), rgb(b.colors.glow) + (255,))
    LOGO_H = 760
    logo = _logo(b)
    src = None
    if logo is not None:
        src = _feather(logo.resize((800, 800), Image.LANCZOS)).convert("RGBa")
    tag = None
    if b.get("tagline"):
        f = font(cfg, 64)
        text = b.tagline.upper()
        l, t, r, bt = _glyph_box(f, text)
        tag = Image.new("RGBA", (r - l + 8, bt - t + 8), (0, 0, 0, 0))
        ImageDraw.Draw(tag).text((4 - l, 4 - t), text, font=f, fill=rgb(b.colors.glow), anchor="la")
        tag_xy = (int(round(W / 2 - tag.width / 2)), H // 2 + LOGO_H // 2 + 60 - 4)
    t_in, t_hold, t_flash, x_len = 0.5 * k, 2.6 * k, 2.6 * k, flash_seconds(b)
    t_end = float(b.intro.seconds)
    for i in range(n):
        t = i / fps
        if t < t_in:
            p = t / t_in
            scale, alpha = 0.88 + 0.12 * _ease_out(p), _ease_out(p)
        else:  # hidas ajelehtiminen 100 % -> 104 % (jatkuu samaa tahtia välähdyksen alle)
            scale, alpha = 1.0 + 0.04 * (t - t_in) / (t_hold - t_in), 1.0
        fr = base.copy()
        if src is not None:
            size = LOGO_H * scale
            sc = size / src.width
            fx, fy = W / 2 - size / 2, H / 2 - size / 2
            ix, iy = math.floor(fx), math.floor(fy)
            box = int(math.ceil(size)) + 2
            lay = src.transform((box, box), Image.AFFINE,
                                (1 / sc, 0, -(fx - ix) / sc, 0, 1 / sc, -(fy - iy) / sc),
                                resample=Image.BICUBIC).convert("RGBA")
            fr.alpha_composite(_with_alpha(lay, alpha), dest=(ix, iy))
        if tag is not None:
            ta = _smooth((t - 1.0 * k) / (0.4 * k))
            if ta > 0:
                fr.alpha_composite(_with_alpha(tag, ta), dest=tag_xy)
        if t >= t_flash:
            fa = _smooth((t - t_flash) / max(t_end - x_len - t_flash, 1e-3))
            fr = Image.blend(fr, cyan, fa)
        yield fr.convert("RGB").tobytes()


def _peak_db(p: Path) -> float:
    r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(p), "-af", "volumedetect",
                  "-f", "null", "-"], proc.PIECE, check=False, capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    return float(m.group(1)) if m and m.group(1) != "-inf" else -99.0


def _intro_sound(b: Cfg, out: Path, peak_db: float = -9.0) -> Path:
    """Pelkällä ffmpegillä: 0,5 s nouseva suodatettu kohina (whoosh) + matala pehmeä tömähdys 0,5 s kohdalla."""
    dur = float(b.intro.seconds)
    k = dur / 3.0
    w = 0.5 * k
    raw = out.with_suffix(".raw.wav")
    if not b.intro.sound:
        proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                  f"anullsrc=r=48000:cl=mono:d={dur}", "-c:a", "pcm_s16le", str(out)], proc.PIECE)
        return out
    # whoosh: alipäästö, jonka rajataajuus nousee 250 Hz -> 6 kHz (afftfilt, aika = pts)
    fc = f"(250*pow(24,min(pts/{w:.3f},1)))"
    gain = f"1/(1+pow((b*sr/(2*nb))/{fc},4))"
    graph = (
        f"anoisesrc=d={w:.3f}:c=pink:r=48000:a=0.6:seed=11,highpass=f=120,"
        f"afftfilt=real='re*{gain}':imag='im*{gain}':win_size=1024,"
        f"afade=t=in:st=0:d={w * 0.9:.3f}:curve=qua,afade=t=out:st={w * 0.86:.3f}:d={w * 0.14:.3f},"
        f"apad=whole_dur={dur}[wh];"
        # tömähdys ~55 Hz (80 -> 50 Hz pudotus), ~0,4 s vaimeneminen, 4 ms nousu ettei naksahda
        f"aevalsrc='(sin(2*PI*(50*t+1.8*(1-exp(-t/0.06))))+0.22*sin(4*PI*(50*t+1.8*(1-exp(-t/0.06)))))"
        f"*exp(-t/0.11)*min(1,t/0.004)':s=48000:d={0.6 * max(k, 1):.3f},"
        f"adelay={int(w * 1000)}:all=1,apad=whole_dur={dur}[th];"
        f"[wh][th]amix=inputs=2:normalize=0:weights='4 1',lowpass=f=9000,"
        f"aformat=sample_fmts=fltp:channel_layouts=mono,atrim=0:{dur}"
    )
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-filter_complex", graph,
              "-c:a", "pcm_f32le", str(raw)], proc.PIECE)
    gain_db = peak_db - _peak_db(raw)
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(raw), "-af", f"volume={gain_db:.2f}dB",
              "-ar", "48000", "-c:a", "pcm_s16le", str(out)], proc.PIECE)
    raw.unlink(missing_ok=True)
    return out


def intro_clip(cfg) -> Path | None:
    """Rakentaa (tai hakee välimuistista) intro-stingin: video + ääni samassa .mkv:ssä."""
    b = settings(cfg)
    if not b or not b.intro.enabled or float(b.intro.seconds) <= 0:
        return None
    W, H, fps = 1920, 1080, int(cfg.video.fps)
    key_src = "|".join(map(str, [INTRO_VERSION, W, H, fps, b.intro.seconds, b.intro.sound, b.tagline,
                                 sorted(b.colors.items()), _file_hash(b.logo), _file_hash(b.font)]))
    key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:16]
    cache = _cache_dir(cfg)
    clip = cache / f"intro_{key}.mkv"
    if clip.is_file() and clip.stat().st_size > 0:
        return clip
    log.info("Rakennetaan intro-sting (%s s) -> %s", b.intro.seconds, clip)
    wav = _intro_sound(b, cache / f"intro_{key}.wav")
    tmp = cache / f"intro_{key}.tmp.mkv"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
           "-c:a", "pcm_s16le", "-t", f"{float(b.intro.seconds):.3f}", str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        with proc.Watchdog(p, INTRO_TIMEOUT, cmd):  # kirjoitus putkeen voi jäädä jumiin, jos ffmpeg jumittuu
            try:
                for frame in _intro_frames(cfg, b, W, H, fps):
                    p.stdin.write(frame)
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
            rc = p.wait()
    except BaseException:
        proc.kill_tree(p)
        tmp.unlink(missing_ok=True)
        raise
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Intron renderöinti epäonnistui")
    tmp.replace(clip)
    wav.unlink(missing_ok=True)
    return clip


# ---------------------------------------------------------------- pitkän videon aikataulu

def long_plan(cfg, voice_dur: float, work: Path) -> dict | None:
    """Pitkän videon brändiaikataulu. None = ei brändiä (tai Short) -> vanha käytös.

    offset   intron pituus: ääni, kortit, tekstitys ja musiikki siirtyvät tämän verran
    total    lopullisen videon pituus = offset + puhe + tail
    """
    b = settings(cfg)
    if not b or cfg.format == "short":
        return None
    intro = intro_clip(cfg)
    off = float(b.intro.seconds) if intro else 0.0
    plan = {"offset": off, "intro": intro, "flash": flash_seconds(b) if intro else 0.0,
            "tail": 0.0, "endscreen": None, "outro_start": None}
    if b.outro.enabled and float(b.outro.seconds) > 0:
        tail = max(float(b.outro.tail_seconds), 0.0)
        # lyhyt puhe -> lyhennetään loppuruutua, ettei se peitä videon alkua (max puolet puheesta)
        length = min(float(b.outro.seconds), tail + voice_dur * 0.5)
        plan["tail"] = tail
        plan["outro_start"] = off + voice_dur + tail - length
        plan["endscreen"] = render_endscreen(cfg, work / "endscreen.png")
    plan["total"] = off + voice_dur + plan["tail"]
    return plan


def shift(items: list[dict], off: float) -> list[dict]:
    """Siirtää {s, e, ...}-ajoitukset intron verran eteenpäin."""
    return [{**x, "s": x["s"] + off, "e": x["e"] + off} for x in items] if off else list(items)


def place_cards(spans: list[dict], plan: dict | None) -> list[dict]:
    """Kortit lopulliselle aikajanalle: intro-siirto + katkaisu loppuruudun alkuun."""
    if not plan:
        return spans
    out = []
    for c in shift(spans, plan["offset"]):
        if plan["outro_start"] is not None:
            c["e"] = min(c["e"], plan["outro_start"])
        if c["e"] - c["s"] >= 0.3:
            out.append(c)
    return out


if __name__ == "__main__":
    from .config import ROOT, load

    logging.basicConfig(level=logging.INFO)
    c = load()
    print(render_endscreen(c, ROOT / "assets" / "brand" / "endscreen_template.png"))
