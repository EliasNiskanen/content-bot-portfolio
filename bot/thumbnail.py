"""Thumbnail-tehdas.

1. Poimii kymmeniä ehdokasruutuja pelikuvaklipeistä ja pisteyttää ne (terävyys, värikkyys, valotus).
2. Irrottaa hahmon/auton taustasta (rembg) -> iso kohde + valkoinen ääriviiva + hehku.
3. Tekee 3 eri tyylistä varianttia (cutout / zoom+ympyrä / leima) -> YouTuben Test & Compare.
4. Tekee vertailukuvan, jossa variantit myös mobiilikoossa (luettavuuden tarkistus).
"""
import itertools
import logging
import os
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import proc
from .render import VIDEO_EXT, probe_duration

log = logging.getLogger(__name__)
TW, TH = 1280, 720
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
PINK, YELLOW, TEAL, RED = (236, 72, 153), (250, 204, 21), (20, 184, 166), (239, 68, 68)
BADGES = {"confirmed": ((34, 197, 94), "CONFIRMED"), "rumor": ((245, 158, 11), "RUMOR"),
          "leak": (RED, "LEAKED"), "opinion": ((99, 102, 241), "MY TAKE")}
STYLES = ["cutout", "zoom", "stamp"]


# ---------------------------------------------------------------- frames

def _grab(src: Path, t: float, out: Path) -> Image.Image | None:
    try:
        r = proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
                      "-frames:v", "1", "-vf", f"scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH}",
                      str(out)], proc.PROBE, check=False)
    except proc.ProcTimeout:
        return None
    if r.returncode or not out.exists():
        return None
    return Image.open(out).convert("RGB")


def sample_frames(cfg, work: Path, fallback: Path, n: int) -> list[tuple[Image.Image, str]]:
    files = []
    for d in cfg.paths.clip_dirs:
        if Path(d).is_dir():
            files += [f for f in Path(d).rglob("*") if f.suffix.lower() in VIDEO_EXT]
    pool = [(f, probe_duration(f)) for f in files]
    pool = [(f, d) for f, d in pool if d > 1.5] or [(fallback, probe_duration(fallback))]
    frames = []
    for k in range(n):
        f, dur = random.choice(pool)
        img = _grab(f, random.uniform(0.5, max(dur - 0.5, 0.6)), work / f"cand_{k:02d}.jpg")
        if img:
            frames.append((img, str(f)))
    return frames


def score_frame(img: Image.Image) -> float:
    a = np.asarray(img.resize((320, 180)), dtype=np.float32)
    g = a.mean(2)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    rg, yb = a[..., 0] - a[..., 1], 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    colorful = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
    exposure = abs(g.mean() - 115) / 115
    return float(np.log1p(lap.var()) + colorful / 40 - exposure * 1.5)


def _distinct(frames: list[tuple[Image.Image, str]], k: int) -> list[Image.Image]:
    """Parhaat ruudut, ensin yksi per lähdeklippi (vaihtelua), sitten loput erilaisuuden mukaan."""
    ranked = sorted(frames, key=lambda fs: score_frame(fs[0]), reverse=True)
    picked, sigs, srcs = [], [], set()
    for strict in (True, False):
        for f, src in ranked:
            if len(picked) >= k:
                break
            if strict and src in srcs:
                continue
            sig = np.asarray(f.convert("L").resize((32, 18)), dtype=np.float32)
            if all(np.abs(sig - s).mean() > 18 for s in sigs):
                picked.append(f); sigs.append(sig); srcs.add(src)
    return picked or [f for f, _ in ranked[:k]]


def _saliency(img: Image.Image) -> tuple[int, int]:
    e = np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES).resize((16, 9)), dtype=np.float32)
    yy, xx = np.mgrid[0:9, 0:16]
    e *= 1 - 0.5 * (np.abs(xx - 7.5) / 7.5) ** 2  # suositaan keskustaa
    e[:, :2] = e[:, -2:] = 0
    y, x = np.unravel_index(np.argmax(e), e.shape)
    return int((x + 0.5) * TW / 16), int((y + 0.5) * TH / 9)


# ---------------------------------------------------------------- subject cutout

class Cutter:
    def __init__(self, model: str):
        self.model, self.session, self.failed = model, None, False

    def cut(self, img: Image.Image) -> Image.Image | None:
        r = self.cut_with_box(img)
        return r[0] if r else None

    def _remove(self, img: Image.Image) -> Image.Image | None:
        if self.failed:
            return None
        try:
            from rembg import new_session, remove
            if self.session is None:
                self.session = new_session(self.model)
            return remove(img, session=self.session)
        except Exception as e:  # noqa: BLE001 - rembg on valinnainen
            log.warning("Hahmon irrotus ei käytössä (%s)", e)
            self.failed = True
            return None

    def mask(self, img: Image.Image) -> Image.Image | None:
        """Kohteen (hahmot/auto) maski L-kuvana samassa koossa kuin img, tai None jos rembg ei toimi."""
        rgba = self._remove(img)
        return rgba.split()[3] if rgba is not None else None

    def cut_with_box(self, img: Image.Image):
        rgba = self._remove(img)
        if rgba is None:
            return None
        alpha = np.asarray(rgba.split()[3]) > 128
        cover = alpha.mean()
        if not 0.04 < cover < 0.6:
            return None
        bbox = rgba.split()[3].point(lambda v: 255 if v > 128 else 0).getbbox()
        return (rgba.crop(bbox), bbox) if bbox else None


def _outlined(sub: Image.Image, width: int = 9, glow_rgb: tuple = PINK) -> Image.Image:
    pad = width * 4
    big = Image.new("RGBA", (sub.width + 2 * pad, sub.height + 2 * pad), (0, 0, 0, 0))
    big.paste(sub, (pad, pad), sub)
    # vain lähes läpinäkymättömät pikselit saavat ääriviivan (eleiden puoliläpinäkyvät efektit eivät)
    a = big.split()[3].point(lambda v: 255 if v > 200 else 0)
    dil = a.filter(ImageFilter.MaxFilter(2 * width + 1))
    glow = Image.new("RGBA", big.size, tuple(glow_rgb) + (0,))
    glow.putalpha(dil.filter(ImageFilter.GaussianBlur(width * 2)).point(lambda v: min(255, int(v * 1.6))))
    line = Image.new("RGBA", big.size, WHITE + (0,))
    line.putalpha(dil)
    out = Image.alpha_composite(glow, line)
    return Image.alpha_composite(out, big)


# ---------------------------------------------------------------- styling helpers

def _grade(img: Image.Image, dark: float = 1.0) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(1.45)
    img = ImageEnhance.Contrast(img).enhance(1.18)
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    img = ImageEnhance.Brightness(img).enhance(dark)
    grad = Image.linear_gradient("L").rotate(90).resize((TW, TH))
    tint = Image.composite(Image.new("RGB", (TW, TH), TEAL), Image.new("RGB", (TW, TH), PINK), grad)
    img = Image.blend(img, tint, 0.13)
    vign = Image.radial_gradient("L").resize((TW, TH)).point(lambda v: int(v * 0.75))
    return Image.composite(Image.new("RGB", (TW, TH), BLACK), img, vign)


def _left_shade(img: Image.Image, frac: float = 0.65, alpha: int = 190) -> Image.Image:
    g = Image.linear_gradient("L").rotate(90).resize((TW, TH))  # 0 vasen -> 255 oikea
    g = g.point(lambda v: int(alpha * max(0.0, 1 - v / (255 * frac))))
    return Image.composite(Image.new("RGB", (TW, TH), BLACK), img, g)


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def _tokens(text: str) -> list[tuple[str, bool]]:
    """'GTA 6 *LEAKED*?' -> korostetut sanat *tähtien* välissä."""
    out = []
    for part in re.split(r"(\*[^*]+\*)", text.upper()):
        hl = part.startswith("*") and part.endswith("*")
        for w in part.strip("*").split():
            if out and not re.search(r"[0-9A-ZÅÄÖ]", w):  # pelkkä välimerkki ("?", "!") kiinni edelliseen sanaan
                out[-1] = (out[-1][0] + w, out[-1][1])
            else:
                out.append((w, hl))
    return out


def _headline(canvas: Image.Image, text: str, box: tuple, font_path: str, align: str = "left", hl_col: tuple = YELLOW):
    x0, y0, x1, y1 = box
    toks = _tokens(text) or [("GTA 6", True)]
    if not any(h for _, h in toks):  # korosta pisin sana jos malli ei merkinnyt
        i = max(range(len(toks)), key=lambda k: len(toks[k][0]))
        toks[i] = (toks[i][0], True)
    d = ImageDraw.Draw(canvas)
    for size in range(200, 60, -8):
        f = _font(font_path, size)
        space = d.textlength(" ", font=f)
        lines, cur, cw = [], [], 0.0
        for w, hl in toks:
            ww = d.textlength(w, font=f)
            if cur and cw + space + ww > x1 - x0:
                lines.append(cur); cur, cw = [], 0.0
            cur.append((w, hl, ww)); cw += (space if cw else 0) + ww
        lines.append(cur)
        lh = int(size * 1.02)
        if len(lines) <= 3 and lh * len(lines) <= y1 - y0 and \
                max(sum(t[2] for t in ln) + space * (len(ln) - 1) for ln in lines) <= x1 - x0:
            break
    total_h = lh * len(lines)
    y = y0 + (y1 - y0 - total_h) // 2
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    draws = []
    for ln in lines:
        lw = sum(t[2] for t in ln) + space * (len(ln) - 1)
        x = x0 if align == "left" else x0 + (x1 - x0 - lw) / 2
        for w, hl, ww in ln:
            draws.append((x, y, w, hl))
            sd.text((x + 10, y + 12), w, font=f, fill=(0, 0, 0, 220), stroke_width=14, stroke_fill=(0, 0, 0, 220))
            x += ww + space
        y += lh
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(canvas)
    for x, y, w, hl in draws:
        d.text((x, y), w, font=f, fill=hl_col if hl else WHITE, stroke_width=12, stroke_fill=BLACK)
    return y0 + (y1 - y0 - total_h) // 2, total_h


def _badge_image(status: str, font_path: str, scale: float = 1.0, angle: float = -8, stroke: int = 3,
                 border: int | None = None, center_text: bool = False) -> Image.Image | None:
    """Tilamerkki (CONFIRMED / RUMOR / LEAKED / MY TAKE) kierrettynä läpinäkyvänä kuvana."""
    if status not in BADGES:
        return None
    color, label = BADGES[status]
    f = _font(font_path, int(70 * scale))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w, h = int(tmp.textlength(label, font=f)) + int(60 * scale), int(105 * scale)
    b = Image.new("RGBA", (w + 20, h + 20), (0, 0, 0, 0))
    bd = ImageDraw.Draw(b)
    bd.rounded_rectangle([10, 10, w + 10, h + 10], radius=int(18 * scale), fill=color + (255,),
                         outline=WHITE, width=border if border is not None else max(4, int(6 * scale)))
    if center_text:  # teksti tarkasti laatikon keskelle (Antonin korkeat ylämitat)
        l, t, r, btm = bd.textbbox((0, 0), label, font=f, stroke_width=stroke)
        bd.text((10 + (w - (r - l)) / 2 - l, 10 + (h - (btm - t)) / 2 - t), label, font=f, fill=WHITE,
                stroke_width=stroke, stroke_fill=BLACK)
    else:
        bd.text((10 + int(30 * scale), 10 + int(10 * scale)), label, font=f, fill=WHITE, stroke_width=stroke,
                stroke_fill=BLACK)
    return b.rotate(angle, expand=True, resample=Image.BICUBIC)


def _badge(canvas: Image.Image, status: str, center: tuple, font_path: str, scale: float = 1.0, angle: float = -8):
    b = _badge_image(status, font_path, scale, angle)
    if b is not None:
        canvas.alpha_composite(b, (int(center[0] - b.width / 2), int(center[1] - b.height / 2)))


# ---------------------------------------------------------------- styles

def style_cutout(frame, subject, text, badge, font_path) -> Image.Image:
    bg = _grade(frame.filter(ImageFilter.GaussianBlur(6)), dark=0.72)
    canvas = _left_shade(bg, 0.7, 160).convert("RGBA")
    if subject is not None:
        s = _outlined(subject)
        scale = min(TH * 1.1 / s.height, TW * 0.8 / s.width)
        s = s.resize((int(s.width * scale), int(s.height * scale)), Image.LANCZOS)
        # leveä kohde (auto) saa valua oikeasta reunasta yli -> näyttää isommalta
        x = TW - int(s.width * (0.88 if s.width > TW * 0.6 else 1.0)) + 20
        canvas.alpha_composite(s, (x, TH - s.height + 40))
    top = 40
    if badge:
        _badge(canvas, badge, (230, 95), font_path, 0.85)
        top = 160
    _headline(canvas, text, (45, top, int(TW * 0.52), TH - 40), font_path)
    return canvas.convert("RGB")


def style_zoom(frame, text, badge, font_path, box=None) -> Image.Image:
    if box:  # kohteen (hahmo/auto) keskikohta irrotusmallin maskista
        sx, sy = (box[0] + box[2]) // 2, int(box[1] + (box[3] - box[1]) * 0.4)
    else:
        sx, sy = _saliency(frame)
    z = 1.55
    cw, ch = TW / z, TH / z
    left = min(max(sx - cw * 0.72, 0), TW - cw)  # kohde oikealle kolmannekselle (tekstin ulkopuolelle)
    top = min(max(sy - ch / 2, 0), TH - ch)
    img = frame.crop((int(left), int(top), int(left + cw), int(top + ch))).resize((TW, TH), Image.LANCZOS)
    cx, cy = int((sx - left) * z), int((sy - top) * z)
    canvas = _left_shade(_grade(img), 0.6, 170).convert("RGBA")
    d = ImageDraw.Draw(canvas)
    r = 115
    for k, col in ((22, BLACK), (14, RED)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=k)
    # nuoli tekstistä kohti ympyrää
    ax, ay = cx - r - 20, cy + 10
    sx0, sy0 = int(TW * 0.46), int(TH * 0.62)
    if ax > sx0 + 60:
        d.line([(sx0, sy0), (ax, ay)], fill=BLACK, width=30)
        d.line([(sx0, sy0), (ax, ay)], fill=RED, width=18)
        v = np.array([ax - sx0, ay - sy0], dtype=float); v /= np.linalg.norm(v)
        n = np.array([-v[1], v[0]])
        tip = np.array([ax, ay]) + v * 30
        pts = [tuple(tip), tuple(tip - v * 60 + n * 38), tuple(tip - v * 60 - n * 38)]
        d.polygon(pts, fill=RED, outline=BLACK)
    if badge:
        _badge(canvas, badge, (230, 95), font_path, 0.85)
    _headline(canvas, text, (45, int(TH * 0.32), int(TW * 0.55), TH - 35), font_path)
    return canvas.convert("RGB")


def style_stamp(frame, text, badge, font_path) -> Image.Image:
    canvas = _left_shade(_grade(frame, 0.9), 0.75, 200).convert("RGBA")
    _badge(canvas, badge or "rumor", (TW - 300, 150), font_path, 1.5, angle=12)
    _headline(canvas, text, (45, 150, int(TW * 0.66), TH - 40), font_path)
    return canvas.convert("RGB")



# ---------------------------------------------------------------- v2: virallinen taustakuva + maskotin ele

# ele -> milloin käytetään (näytetään myös käsikirjoitusmallille)
POSES = {
    "default": "neutral gaming pose, general news",
    "detective": "investigating, fact-checking, analysing a leak or rumor",
    "shocked": "huge surprising news, jaw-dropping reveal",
    "hype": "great news, celebration, confirmed release or feature",
    "thinking": "open question, speculation, something unconfirmed",
    "angry": "bad news, delay, controversy, backlash",
    "laughing": "funny or absurd story",
    "pointing": "pointing at one specific detail or number",
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def background_index(cfg) -> dict[str, list[Path]]:
    """Viralliset kuvat kategorioittain: kansion nimi = kategoria (esim. 'vice city', 'lucia caminos')."""
    root = Path(cfg.thumbnail.get("backgrounds_dir", "") or "")
    idx: dict[str, list[Path]] = {}
    if not root.is_dir():
        return idx
    excluded = {str(x).lower() for x in (cfg.paths.get("exclude_images") or [])}
    for f in root.rglob("*"):
        if f.suffix.lower() not in IMG_EXT or f.parent == root or "_zips" in f.parts:
            continue
        if f.stem.lower() in excluded:
            continue
        name = f.stem.lower()
        if "artwork" in [x.lower() for x in f.parts]:
            # taidekuvista vain vaakakuvat ilman logoja/kansia (logot ja ikärajamerkit eivät kuulu thumbnailiin)
            if "landscape" not in name or "logo" in name or "cover" in name or "postcard" in name:
                continue
        cat = f.parent.name.replace("_", " ").lower().strip()
        idx.setdefault(cat, []).append(f)
    return idx


def available_poses(cfg) -> list[str]:
    d = Path(cfg.thumbnail.get("poses_dir", "") or "")
    return [k for k in POSES if (d / f"{k}.png").is_file()] if d.is_dir() else []


def _pick_background(idx: dict, want, used: set):
    want = (want or "").replace("_", " ").lower().strip()
    cats = [c for c in idx if want and (want == c or want in c or c in want)] or list(idx)
    random.shuffle(cats)
    for c in cats:
        free = [f for f in idx[c] if f not in used]
        if free:
            f = random.choice(free)
            used.add(f)
            return f
    return None


def _cover(img: Image.Image) -> Image.Image:
    r = max(TW / img.width, TH / img.height)
    img = img.resize((int(img.width * r + 0.5), int(img.height * r + 0.5)), Image.LANCZOS)
    x, y = (img.width - TW) // 2, (img.height - TH) // 2
    return img.crop((x, y, x + TW, y + TH))


def style_brand(bg: Image.Image, mascot, text: str, badge, font_path: str,
                primary: tuple, glow: tuple, shade: tuple) -> Image.Image:
    """Teksti vasemmalla, maskotti oikealla alhaalla, taustalla virallinen pelikuva."""
    img = ImageEnhance.Contrast(ImageEnhance.Color(_cover(bg.convert("RGB"))).enhance(1.15)).enhance(1.08)
    g = Image.linear_gradient("L").rotate(90).resize((TW, TH))            # 0 vasen -> 255 oikea
    g = g.point(lambda v: int(215 * max(0.0, 1 - v / (255 * 0.68))))
    img = Image.composite(Image.new("RGB", (TW, TH), shade), img, g)
    canvas = img.convert("RGBA")
    if mascot is not None:
        m = _outlined(mascot, width=7, glow_rgb=primary)
        sc = min(TH * 1.0 / m.height, TW * 0.56 / m.width)
        m = m.resize((int(m.width * sc), int(m.height * sc)), Image.LANCZOS)
        canvas.alpha_composite(m, (TW - m.width + 24, TH - m.height + 34))
    top = 40
    if badge:
        _badge(canvas, badge, (230, 95), font_path, 0.85)
        top = 160
    _headline(canvas, text, (45, top, int(TW * 0.54), TH - 40), font_path, hl_col=glow)
    return canvas.convert("RGB")


def _make_brand_thumbnails(cfg, script: dict, variants: list[dict], out_dir: Path, name: str) -> list[Path]:
    from . import brand
    b = brand.settings(cfg)
    idx = background_index(cfg)
    poses = available_poses(cfg)
    if not b or not idx or not poses:
        return []
    font_path = b.font if Path(b.font).exists() else cfg.thumbnail.font
    primary, glow, shade = brand.rgb(b.colors.primary), brand.rgb(b.colors.glow), brand.rgb(b.colors.bg)
    pdir, used, outs = Path(cfg.thumbnail.poses_dir), set(), []
    for i, v in enumerate(variants):
        pose = v.get("mascot") if v.get("mascot") in poses else "default" if "default" in poses else poses[0]
        bgf = _pick_background(idx, v.get("background"), used)
        if bgf is None:
            return []
        img = style_brand(Image.open(bgf), Image.open(pdir / f"{pose}.png").convert("RGBA"), v["text"], v["badge"],
                          font_path, primary, glow, shade)
        p = out_dir / f"{name}_thumb{i + 1}_{pose}.jpg"
        q = 92
        img.save(p, quality=q)
        while p.stat().st_size > 1_900_000 and q > 60:
            q -= 8
            img.save(p, quality=q)
        log.info("Thumbnail %d: ele=%s (pyydetty %s), tausta=%s", i + 1, pose, v.get("mascot"), bgf.parent.name)
        outs.append(p)
    return outs


# ---------------------------------------------------------------- v3: YouTube-tyyli
# Kirkas tausta (paikkakuva tai puhdas taustalevy), iso sankarihahmo (läpinäkyvä cutout) tekstin alaosan edessä,
# iso Anton-teksti ja pieni GTA VI -logo kulmassa. Kolme asettelua: A "bigword", B "banner", C "stack".

LAYOUTS = ["bigword", "banner", "stack"]
LAYOUT_OF = {"cutout": "bigword", "zoom": "banner", "stamp": "stack",
             "bigword": "bigword", "banner": "banner", "stack": "stack"}
OLD_STYLE_OF = {"bigword": "cutout", "banner": "zoom", "stack": "stamp"}
YT_YELLOW = (255, 210, 31)                       # #FFD21F voimasana
VC_PINK, VC_ORANGE, VC_YELLOW = (255, 79, 216), (255, 138, 61), (255, 216, 74)   # Vice City -liukuväri
VC_CYAN, VC_OUTLINE = (79, 208, 255), (42, 10, 58)                               # #4FD0FF, #2A0A3A
BANNER_RGBA = (11, 11, 11, 235)                  # #0B0B0B 92 %
MARGIN = 28
AV_D, AV_RING, AV_OUTER = 190, 7, 3              # avatarin halkaisija, sininen rengas, valkoinen ulkorengas
AV_FILL, AV_BLUE = (10, 22, 48), (30, 155, 255)  # #0A1630, #1E9BFF
HEAD_BOX = (410, 250, 1010, 740)                 # maskotin pää ele-kankaalla (ks. poses.PAD_L/PAD_T)
LOGO_W = {"bigword": 230, "banner": 230, "stack": 200}
SHADOW = (153, 8, (6, 8))                        # alfa 60 %, sumennus, siirtymä
DURATION_BOX = (TW - 220, TH - 90, TW, TH)       # YouTuben keston merkki: ei mitään tärkeää tähän
# sankarin sijoittelu: keskikohta x (osuus leveydestä), korkeus (osuus ruudun korkeudesta), suurin leveys
# (osuus leveydestä, leveä kohde sovitetaan leveyteen) ja vähintään näkyvä osuus, kun hahmoa siirretään alas
HERO_X = {"bigword": 0.50, "banner": 0.66, "stack": 0.70}
HERO_H = {"bigword": 1.00, "banner": 1.05, "stack": 1.05}
HERO_MAXW = {"bigword": 0.95, "banner": 0.80, "stack": 0.62}
HERO_VISIBLE = {"bigword": 0.70, "banner": 0.58, "stack": 0.60}
HEAD_FRAC, TORSO_FRAC = 0.30, 0.65              # pää = cutoutin ylin 30 %, A-asettelu rajaa ylimpään 65 %:iin
# paikkakuvat, joissa ei ole ihmisiä etualalla (sankari erottuu paremmin); config thumbnail.scenic_backgrounds
SCENIC = ["Ambrosia_04", "Grassrivers_02", "Grassrivers_04", "Grassrivers_05", "Leonida_Keys_01",
          "Leonida_Keys_04", "Leonida_Keys_06", "Leonida_Keys_07", "Mount_Kalaga_National_Park_02",
          "Mount_Kalaga_National_Park_04", "Mount_Kalaga_National_Park_05", "Mount_Kalaga_National_Park_06",
          "Port_Gellhorn_06", "Vice_City_01", "Vice_City_03", "Vice_City_10", "Vice_City_11", "Vice_City_15",
          "Vice_City_22", "Vice_City_26", "Vice_City_27"]
# perheystävällisyys: strippiklubin sisä- ja ulkokuvat eivät käy koskaan (lisäksi config paths.exclude_images)
ALWAYS_EXCLUDE = {"boobie_ike__plate", "drequan_priest__plate"}
WIDE_RATIO, WIDE_KEEP = 1.3, 1.15                # tätä leveämpi cutout rajataan pään ympäriltä (leveys = 1.15 x korkeus)
NAME_STOP = {"and", "the", "artwork", "rockstar", "real", "lock", "load", "beach", "rooftop", "robbery", "motel",
             "plate", "01", "02", "03"}


def _comp(dst: Image.Image, im: Image.Image, xy):
    """alpha_composite, joka sallii negatiivisen sijainnin (leikkaa reunan yli menevän osan)."""
    x, y = int(round(xy[0])), int(round(xy[1]))
    if x < 0 or y < 0:
        im = im.crop((max(0, -x), max(0, -y), im.width, im.height))
        x, y = max(x, 0), max(y, 0)
    if im.width > 0 and im.height > 0 and x < dst.width and y < dst.height:
        dst.alpha_composite(im, (x, y))


def _drop_shadow(canvas: Image.Image, alpha_img: Image.Image, xy, alpha: int, blur: int, offset):
    """Pehmeä musta varjo annetun alfan muotoon."""
    blk = Image.new("RGBA", alpha_img.size, (0, 0, 0, 0))
    blk.putalpha(alpha_img.point(lambda v: v * alpha // 255))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    _comp(layer, blk, (xy[0] + offset[0], xy[1] + offset[1]))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _gradient(canvas: Image.Image, alpha: int, until: int, axis: str):
    """Musta liukuvärjäys alpha -> 0 ylhäältä (axis='y') tai vasemmalta (axis='x') pikseliin until asti."""
    n = TH if axis == "y" else TW
    ramp = np.clip(1 - np.arange(n) / until, 0, 1) * alpha
    a = np.repeat(ramp[:, None], TW, 1) if axis == "y" else np.repeat(ramp[None, :], TH, 0)
    layer = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    layer.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
    canvas.alpha_composite(layer)


def _power_tokens(text: str) -> list[tuple[str, bool]]:
    toks = _tokens(text) or [("GTA 6", True)]
    if not any(h for _, h in toks):  # voimasana = pisin sana, jos mallin tekstissä ei ole *tähtiä*
        i = max(range(len(toks)), key=lambda k: len(toks[k][0]))
        toks[i] = (toks[i][0], True)
    return toks


def _split(widths: list[float], space: float, n: int):
    """Sanat n riville niin, että pisin rivi on mahdollisimman lyhyt."""
    k, best = len(widths), None
    for cuts in itertools.combinations(range(1, k), n - 1):
        b = (0, *cuts, k)
        m = max(sum(widths[b[i]:b[i + 1]]) + space * (b[i + 1] - b[i] - 1) for i in range(n))
        if best is None or m < best[0]:
            best = (m, b)
    return best


def _fit_text(toks, width: float, height: float, font_path: str, cap: int, lines: tuple, stroke: int,
              min_size: int = 36, one_line_down_to: int = 0) -> dict:
    """Suurin fonttikoko (<= cap), jolla teksti mahtuu laatikkoon lines[0]..lines[1] rivillä.
    Mitat ovat mustekokoja (versaalien korkeus + ääriviiva), joten iso teksti täyttää laatikon tarkasti."""
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lo, hi = min(lines[0], len(toks)), min(lines[1], len(toks))
    lay = None
    for size in range(cap, min_size - 1, -2):
        f = _font(font_path, size)
        _, ct, _, cb = f.getbbox("H")
        cap_h, gap = cb - ct, max(int(size * 0.12), 2 * stroke + 4)
        space = d.textlength(" ", font=f)
        widths = [d.textlength(w, font=f) for w, _ in toks]
        for n in range(lo, hi + 1):
            if n > lo and size >= one_line_down_to > 0:
                break  # banneri: yksi rivi niin kauan kuin fontti pysyy riittävän isona
            m, b = _split(widths, space, n)
            lay = dict(font=f, cap_top=ct, cap_h=cap_h, gap=gap, space=space, stroke=stroke, size=size,
                       w=m + 2 * stroke, h=n * cap_h + (n - 1) * gap + 2 * stroke,
                       lines=[[(toks[j][0], toks[j][1], widths[j]) for j in range(b[i], b[i + 1])] for i in range(n)])
            if lay["w"] <= width and lay["h"] <= height:
                return lay
    return lay  # ei mahdu edes pienimmällä koolla -> pienin kokeiltu


def _text_positions(lay: dict, box: tuple, align: str):
    """Sanojen piirtopisteet (laatikon keskellä pystysuunnassa) ja rivien mustelaatikot."""
    x0, y0, x1, y1 = box
    s = lay["stroke"]
    y = y0 + (y1 - y0 - lay["h"]) / 2 + s
    draws, rects = [], []
    for ln in lay["lines"]:
        lw = sum(t[2] for t in ln) + lay["space"] * (len(ln) - 1)
        x = x0 + s if align == "left" else x0 + (x1 - x0 - lw) / 2
        rects.append((x - s, y - s, x + lw + s, y + lay["cap_h"] + s))
        for w, hl, ww in ln:
            draws.append((x, y - lay["cap_top"], w, hl))
            x += ww + lay["space"]
        y += lay["cap_h"] + lay["gap"]
    return draws, rects


def _text_gradient(lay: dict, box: tuple):
    """A: Vice City -liukuväri (pinkki -> oranssi -> keltainen) joka riville, tumma ääriviiva ja pinkki hehku."""
    draws, rects = _text_positions(lay, box, "center")
    f, s = lay["font"], lay["stroke"]
    fill, ink = Image.new("L", (TW, TH), 0), Image.new("L", (TW, TH), 0)
    df, di = ImageDraw.Draw(fill), ImageDraw.Draw(ink)
    for x, y, w, _ in draws:
        di.text((x, y), w, font=f, fill=255, stroke_width=s, stroke_fill=255)
        df.text((x, y), w, font=f, fill=255)
    stops = np.array([VC_PINK, VC_ORANGE, VC_YELLOW], dtype=np.float32)
    rows = np.repeat(stops[2][None, :], TH, 0)
    for _, ry0, _, ry1 in rects:
        a, b = int(max(ry0 + s, 0)), int(min(ry1 - s, TH))
        t = np.clip((np.arange(a, b) - a) / max(1, b - a - 1), 0, 1)[:, None] * 2
        rows[a:b] = np.where(t < 1, stops[0] + (stops[1] - stops[0]) * t, stops[1] + (stops[2] - stops[1]) * (t - 1))
    grad = Image.fromarray(np.repeat(rows[:, None, :], TW, 1).astype(np.uint8), "RGB").convert("RGBA")
    grad.putalpha(fill)
    glow = Image.new("RGBA", (TW, TH), VC_PINK + (0,))
    glow.putalpha(ink.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(14))
                  .point(lambda v: int(min(255, v * 1.2) * 0.7)))
    outline = Image.new("RGBA", (TW, TH), VC_OUTLINE + (255,))
    outline.putalpha(ink)
    layer = Image.alpha_composite(glow, outline)
    layer.alpha_composite(grad)
    return layer, np.asarray(ink) > 64, rects


def _text_plain(lay: dict, box: tuple, align: str, shadow: bool):
    """B/C: valkoinen teksti, voimasana keltainen, musta ääriviiva (+ pehmeä varjo)."""
    draws, rects = _text_positions(lay, box, align)
    f, s = lay["font"], lay["stroke"]
    layer = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    ink = Image.new("L", (TW, TH), 0)
    di = ImageDraw.Draw(ink)
    for x, y, w, _ in draws:
        di.text((x, y), w, font=f, fill=255, stroke_width=s, stroke_fill=255)
    if shadow:
        a, blur, (ox, oy) = SHADOW
        sh = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
        sh.putalpha(ImageChops.offset(ink, ox, oy).point(lambda v: v * a // 255).filter(ImageFilter.GaussianBlur(blur)))
        layer.alpha_composite(sh)
    d = ImageDraw.Draw(layer)
    for x, y, w, hl in draws:
        d.text((x, y), w, font=f, fill=YT_YELLOW if hl else WHITE, stroke_width=s, stroke_fill=BLACK)
    return layer, np.asarray(ink) > 64, rects


def _sticker(status, font_path: str) -> Image.Image | None:
    return _badge_image(status, font_path, scale=0.8, angle=-6, stroke=5, border=4, center_text=True)


def _avatar(canvas: Image.Image, pose: Image.Image, center: tuple):
    """Maskotti ympyrässä; pään yläpuolen tarvikkeet (hattu, !!, ajatuskupla ...) saavat ylittää kehyksen."""
    cx, cy = center
    R = AV_D / 2
    r_in = R - AV_OUTER - AV_RING
    ss = 4
    S = AV_D * ss
    disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    for inset, col in ((0, WHITE), (AV_OUTER, AV_BLUE), (AV_OUTER + AV_RING, AV_FILL)):
        dd.ellipse([inset * ss, inset * ss, S - 1 - inset * ss, S - 1 - inset * ss], fill=col + (255,))
    disc = disc.resize((AV_D, AV_D), Image.LANCZOS)
    dxy = (cx - R, cy - R)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    if pose is not None:
        hx0, hy0, hx1, hy1 = HEAD_BOX
        sc = 2 * r_in / (hx1 - hx0)
        m = pose.resize((round(pose.width * sc), round(pose.height * sc)), Image.LANCZOS)
        _comp(layer, m, (cx - (hx0 + hx1) / 2 * sc, cy - (hy0 + hy1) / 2 * sc))
        # maski = ympyrä ∪ ympyrän vaakasuoran keskilinjan yläpuoli (pehmeä ympyrän reuna)
        yy, xx = np.mgrid[0:TH, 0:TW]
        circ = np.clip(r_in + 0.5 - np.hypot(xx + 0.5 - cx, yy + 0.5 - cy), 0, 1)
        above = (yy + 0.5 <= cy).astype(np.float32)
        mask = np.maximum(circ, above)
        a = np.asarray(layer.split()[3], dtype=np.float32) * mask
        layer.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
    full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    _comp(full, disc, dxy)
    sh_alpha = Image.fromarray(np.maximum(np.asarray(full.split()[3]), np.asarray(layer.split()[3])), "L")
    a, blur, off = SHADOW
    _drop_shadow(canvas, sh_alpha, (0, 0), a, blur, off)
    canvas.alpha_composite(full)
    canvas.alpha_composite(layer)


def _load_logo(cfg) -> Image.Image | None:
    p = os.path.expandvars(str(cfg.thumbnail.get("logo", "${DATA_DIR}/brand_assets/gta6_logo.png") or ""))
    if not p or not Path(p).is_file():
        return None
    try:
        return Image.open(p).convert("RGBA")
    except OSError:
        return None


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _grow(r, m: float):
    return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)


def _save_jpg(img: Image.Image, p: Path):
    q = 92
    img.save(p, quality=q)
    while p.stat().st_size > 1_900_000 and q > 60:  # YouTube-raja 2 MB
        q -= 8
        img.save(p, quality=q)


# ---- kuvat: hahmot ja taustat

def _excluded(cfg) -> set[str]:
    return {str(x).lower() for x in (cfg.paths.get("exclude_images") or [])} | ALWAYS_EXCLUDE


def _cfg_dir(cfg, key: str, default: str) -> Path:
    return Path(os.path.expandvars(str(cfg.thumbnail.get(key, default) or default)))


def _cutouts_dir(cfg) -> Path:
    return _cfg_dir(cfg, "cutouts_dir", "${DATA_DIR}/promo/cutouts")


def available_characters(cfg) -> list[str]:
    """Hahmocutoutien tiedostonimet ilman päätettä (esim. 'Jason_and_Lucia_Beach__artwork', 'Boobie_Ike__rockstar')."""
    d = _cutouts_dir(cfg)
    if not d.is_dir():
        return []
    ex = _excluded(cfg)
    return sorted(f.stem for f in d.glob("*.png") if f.stem.lower() not in ex)


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split()


def _is_duo(stem: str) -> bool:
    w = _words(stem)
    return "jason" in w and "lucia" in w


def _match_character(want, stems: list[str], used: set) -> str | None:
    """Sumea osuma variantin character-kenttään: 'lucia' -> jokin Jason_and_Lucia_*-kuva, 'boobie' -> Boobie_Ike."""
    ww = [w for w in _words(want) if w not in ("and", "the", "artwork", "rockstar")]
    if not ww:
        return None
    exact = [s for s in stems if _words(s) == _words(want) or s.lower() == str(want).lower()]
    if exact:
        return exact[0]
    scores = {}
    for s in stems:
        sw = _words(s)
        sc = sum(1 for w in ww if w in sw or (len(w) >= 4 and any(x.startswith(w) or w.startswith(x) for x in sw
                                                                     if len(x) >= 4)))
        if sc:
            scores[s] = sc
    if not scores:
        return None
    best = max(scores.values())
    top = [s for s in stems if scores.get(s) == best]
    free = [s for s in top if s not in used]
    return random.choice(free or top)


def _character_from_text(text: str, stems: list[str], used: set) -> str | None:
    """Hahmon nimi variantin tekstissä (esim. 'BOOBIE IS BACK' -> Boobie_Ike), muuten None."""
    tw = set(_words(text))
    hits = [s for s in stems if any(w in tw for w in _words(s) if w not in NAME_STOP and not w.isdigit())]
    free = [s for s in hits if s not in used]
    return random.choice(free) if free else None


def _pick_character(v: dict, stems: list[str], used: set) -> str | None:
    if not stems:
        return None
    pick = _match_character(v.get("character"), stems, used) if v.get("character") else None
    if pick in used:
        pick = None  # sama kuva jo toisessa variantissa -> valitaan muu (tekstistä tai Jason & Lucia)
    pick = pick or _character_from_text(v.get("text") or "", stems, used)
    if pick is None:
        duo = [s for s in stems if _is_duo(s) and s not in used]
        rest = [s for s in stems if s not in used]
        pick = random.choice(duo or rest or stems)
    used.add(pick)
    return pick


def _plates(cfg) -> list[Path]:
    d = _cfg_dir(cfg, "plates_dir", "${DATA_DIR}/promo/backgrounds")
    ex = _excluded(cfg)
    return sorted(f for f in d.glob("*") if f.suffix.lower() in IMG_EXT and f.stem.lower() not in ex) \
        if d.is_dir() else []


def _places(cfg) -> dict[str, list[Path]]:
    """Viralliset paikkakuvat (screenshots/Places/<paikka>/*) kategorioittain (background_index rajattuna)."""
    out = {}
    for cat, files in background_index(cfg).items():
        fs = [f for f in files if "places" in [x.lower() for x in f.parts]]
        if fs:
            out[cat] = fs
    return out


def _pick_bg(cfg, places: dict, plates: list[Path], want, hero: str | None, used: set) -> Path | None:
    """Pyydetyn kategorian paikkakuva -> sankarin oma taustalevy -> satunnainen maisemakuva tai taustalevy."""
    scenic = {s.lower() for s in (cfg.thumbnail.get("scenic_backgrounds") or SCENIC)}
    want = (want or "").replace("_", " ").lower().strip()
    cats = [c for c in places if want and (want == c or want in c or c in want)]

    def choose(files):
        free = [f for f in files if f not in used]
        good = [f for f in free if f.stem.lower() in scenic]
        if good or free:
            f = random.choice(good or free)
            used.add(f)
            return f
        return None

    f = choose([f for c in cats for f in places[c]]) if cats else None
    if f is None and hero:
        own = [p for p in plates if p.stem.split("__")[0].lower() == hero.split("__")[0].lower()]
        f = choose(own)
    if f is None:
        allp = [f for fs in places.values() for f in fs]
        pool = [f for f in allp if f.stem.lower() in scenic] + plates
        f = choose(pool) or choose(allp) or choose(plates)
    if f is None:  # kaikki käytetty -> sallitaan toisto
        allp = [f for fs in places.values() for f in fs] + plates
        f = random.choice(allp) if allp else None
    return f


def _prep_bg(f: Path) -> Image.Image:
    src = Image.open(f)
    src.draft("RGB", (TW * 2, TH * 2))  # JPEG: nopea pienennetty dekoodaus
    img = _cover(src.convert("RGB")).filter(ImageFilter.GaussianBlur(4))
    img = ImageEnhance.Color(img).enhance(1.3)
    img = ImageEnhance.Contrast(img).enhance(1.1)
    return ImageEnhance.Brightness(img).enhance(1.05)


# ---- sankarihahmo

class _Hero:
    """Rajattu cutout + pään laatikko (osuuksina kuvan koosta)."""

    def __init__(self, img: Image.Image, head: tuple, name: str):
        self.img, self.head, self.name = img, head, name


def _soften_cut_edges(im: Image.Image) -> Image.Image:
    """Alkuperäisen taidekuvan rajaus (pitkä läpinäkymätön suora reuna vasemmalla, oikealla tai ylhäällä)
    häivytetään, jotta ruutuun ei jää suoraa leikkausta valkoisella reunuksella."""
    a = np.asarray(im.split()[3], np.float32)
    h, w = a.shape
    edges = {"left": a[:, 0], "right": a[:, -1], "top": a[0, :]}
    ramp_x, ramp_y = np.ones(w, np.float32), np.ones(h, np.float32)
    changed = False
    for side, line in edges.items():
        if np.count_nonzero(line > 128) < 0.06 * len(line):
            continue  # luonnollinen siluetti koskettaa reunaa vain vähän
        changed = True
        if side == "top":
            n = max(2, int(0.04 * h))
            ramp_y[:n] = np.minimum(ramp_y[:n], np.linspace(0, 1, n))
        else:
            n = max(2, int(0.06 * w))
            r = np.linspace(0, 1, n)
            if side == "left":
                ramp_x[:n] = np.minimum(ramp_x[:n], r)
            else:
                ramp_x[-n:] = np.minimum(ramp_x[-n:], r[::-1])
    if not changed:
        return im
    im = im.copy()
    im.putalpha(Image.fromarray((a * ramp_y[:, None] * ramp_x[None, :]).astype(np.uint8), "L"))
    return im


def _hero_from_rgba(im: Image.Image, layout: str, name: str) -> _Hero | None:
    bb = im.split()[3].point(lambda v: 255 if v > 16 else 0).getbbox()
    if not bb:
        return None
    im = _soften_cut_edges(im.crop(bb))
    small = im.split()[3].copy()
    small.thumbnail((400, 400))
    hard = np.asarray(small) > 128
    rows = np.where(hard.any(1))[0]
    if not len(rows):
        return None
    band = hard[: max(1, int(hard.shape[0] * HEAD_FRAC))]
    cov = band.mean(0)
    cols = np.where(cov > 0.25)[0]
    if not len(cols):
        cols = np.where(band.any(0))[0]
    hx0, hx1 = cols[0] / hard.shape[1], (cols[-1] + 1) / hard.shape[1]
    if im.width > WIDE_RATIO * im.height:  # hyvin leveä (pöytä, vene): rajataan pään ympäriltä, pehmeät reunat
        keep = WIDE_KEEP * im.height / im.width
        c = (hx0 + hx1) / 2
        l = min(max(c - keep / 2, 0.0), 1 - keep)
        w0 = im.width
        L, R = int(l * w0), int((l + keep) * w0)
        im = im.crop((L, 0, R, im.height))
        fade = int(0.08 * im.width)
        ramp = np.ones(im.width, np.float32)
        if L > 0:
            ramp[:fade] = np.linspace(0, 1, fade)
        if R < w0 - 1:
            ramp[-fade:] = np.minimum(ramp[-fade:], np.linspace(1, 0, fade))
        a = (np.asarray(im.split()[3], np.float32) * ramp[None, :]).astype(np.uint8)
        im.putalpha(Image.fromarray(a, "L"))
        hx0, hx1 = (hx0 - l) / keep, (hx1 - l) / keep
    head = (hx0, 0.0, hx1, HEAD_FRAC)
    if layout == "bigword":  # pää ja ylävartalo
        im = im.crop((0, 0, im.width, int(im.height * TORSO_FRAC)))
        head = (hx0, 0.0, hx1, HEAD_FRAC / TORSO_FRAC)
    return _Hero(im, head, name)


def _load_hero(path: Path, layout: str) -> _Hero | None:
    try:
        return _hero_from_rgba(Image.open(path).convert("RGBA"), layout, path.stem)
    except OSError:
        return None


def _overlap_count(block: np.ndarray, am: np.ndarray, x: int, y: int) -> int:
    bh, bw = block.shape
    h, w = am.shape
    X0, Y0, X1, Y1 = max(0, x), max(0, y), min(bw, x + w), min(bh, y + h)
    if X1 <= X0 or Y1 <= Y0:
        return 0
    return int(np.count_nonzero(block[Y0:Y1, X0:X1] & am[Y0 - y:Y1 - y, X0 - x:X1 - x]))


def _place_hero(hero: _Hero, layout: str, forbid: list, block: np.ndarray | None, tol: float):
    """Suurin sijoitus (koko, x, y), jossa pää on ruudussa eikä minkään kiellon (teksti, logo, tarra, keston merkki)
    alla ja sankari peittää tekstiä enintään tol-osuuden. Ensin siirretään alas/sivulle, sitten pienennetään."""
    Q = 4
    w, h = hero.img.size
    s0 = min(HERO_H[layout] * TH / h, HERO_MAXW[layout] * TW / w)
    blk = block[::Q, ::Q] if block is not None else None
    n_blk = max(1, int(np.count_nonzero(blk))) if blk is not None else 1
    alpha = hero.img.split()[3]
    hx0, hy0, hx1, hy1 = hero.head
    last = None
    for f in np.arange(1.0, 0.499, -0.05):
        s = s0 * f
        W, H = w * s, h * s
        am = np.asarray(alpha.resize((max(1, round(W / Q)), max(1, round(H / Q))), Image.BILINEAR)) > 128
        hh = (hy1 - hy0) * H
        dmax = (1 - HERO_VISIBLE[layout]) * H
        cands = sorted((dy + 3 * abs(off), dy, off) for dy in range(0, int(dmax) + 1, 10)
                       for off in range(-320, 321, 20))
        for _, dy, off in cands:
            x = HERO_X[layout] * TW + off - W / 2
            y = TH - H + dy
            head = (x + hx0 * W, y + hy0 * H, x + hx1 * W, y + hy1 * H)
            if head[0] < 10 or head[2] > TW - 10 or head[1] < -0.2 * hh or head[3] > TH - 40:
                continue
            if any(_overlaps(head, r) for r in forbid):
                continue
            if blk is not None and _overlap_count(blk, am, round(x / Q), round(y / Q)) > tol * n_blk:
                continue
            return s, x, y, head
        last = (s, HERO_X[layout] * TW - W / 2, TH - H, None)
    log.warning("Sankarille %s ei löytynyt sijoitusta, jossa kasvot ovat vapaina", hero.name)
    s, x, y, _ = last
    W, H = w * s, h * s
    return s, x, y, (x + hx0 * W, y + hy0 * H, x + hx1 * W, y + hy1 * H)


def _rimmed(img: Image.Image):
    """3 px valkoinen reunus + pehmeä hehku (pinkki vasemmalla -> syaani oikealla), vain terävillä reunoilla
    (häivytetyt alareunat eivät saa viivaa). Palauttaa (kuva, reunuksen leveys)."""
    pad = 48
    big = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    big.paste(img, (pad, pad))
    a = big.split()[3]
    hard = a.point(lambda v: 255 if v > 160 else 0)
    dil = hard
    for _ in range(3):
        dil = dil.filter(ImageFilter.MaxFilter(3))
    outside = np.asarray(a) < 40
    ring = np.where(outside, np.asarray(dil), 0).astype(np.uint8)
    line = Image.new("RGBA", big.size, WHITE + (0,))
    line.putalpha(Image.fromarray(ring, "L").filter(ImageFilter.GaussianBlur(0.6)))
    g = np.asarray(hard.filter(ImageFilter.GaussianBlur(18)), dtype=np.float32) * 1.6
    g = np.where(outside, np.minimum(g, 235), 0).astype(np.uint8)
    t = np.linspace(0, 1, big.width, dtype=np.float32)[None, :, None]   # pinkki vasemmalla -> syaani oikealla
    col = np.array(VC_PINK, np.float32) * (1 - t) + np.array(VC_CYAN, np.float32) * t
    rgb = np.repeat(col, big.height, 0).astype(np.uint8)
    glow = Image.fromarray(np.dstack([rgb, g]), "RGBA")
    out = Image.alpha_composite(glow, line)
    out.alpha_composite(big)
    return out, pad


def _draw_hero(canvas: Image.Image, hero: _Hero, s: float, x: float, y: float):
    W, H = max(1, round(hero.img.width * s)), max(1, round(hero.img.height * s))
    im = hero.img.resize((W, H), Image.LANCZOS)
    # vain ruutuun osuva osa (+ reunus) käsitellään -> nopeampi
    cx0, cy0 = max(0, int(-x) - 60), max(0, int(-y) - 60)
    cx1, cy1 = min(W, int(TW - x) + 60), min(H, int(TH - y) + 60)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    im = im.crop((cx0, cy0, cx1, cy1))
    r, pad = _rimmed(im)
    _comp(canvas, r, (x + cx0 - pad, y + cy0 - pad))


# ---- asettelut

def _render_youtube(layout: str, bg: Image.Image, hero: _Hero | None, text: str, badge, font_path: str,
                    logo: Image.Image | None, pose: Image.Image | None, avatar: bool) -> Image.Image:
    toks = _power_tokens(text)
    canvas = bg.convert("RGBA")
    lg = None
    if logo is not None:
        w = LOGO_W[layout]
        lg = logo.resize((w, round(logo.height * w / logo.width)), Image.LANCZOS)
    forbid = [DURATION_BOX]
    st = _sticker(badge, font_path) if layout != "bigword" else None
    st_xy, av = None, None

    if layout == "bigword":
        if logo is not None:  # iso haalea VI-logo taustalla tekstin ja sankarin takana
            bh = 620
            big = logo.resize((round(logo.width * bh / logo.height), bh), Image.LANCZOS)
            big.putalpha(big.split()[3].point(lambda v: v * 35 // 100))
            _comp(canvas, big, ((TW - big.width) / 2, (TH - bh) / 2))
        lay = _fit_text(toks, TW - 2 * 40, 250, font_path, 230, (1, 2), 8)
        text_layer, ink, rects = _text_gradient(lay, (40, 10, TW - 40, 260))
        # sankari saa peittää vain viimeisen rivin kirjainten alimman ~15 %:n
        limit = int(rects[-1][3] - lay["stroke"] - 0.15 * lay["cap_h"])
        block = ink.copy()
        block[max(0, limit):] = False
        tol = 0.002
    elif layout == "banner":
        pad_x, pad_y, b_top = 36, 22, 24
        lay = _fit_text(toks, 1200 - 2 * pad_x, 260, font_path, 120, (1, 2), 4, one_line_down_to=84)
        bh = lay["h"] + 2 * pad_y
        banner = (40, b_top, 1240, b_top + bh)
        text_layer = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
        ImageDraw.Draw(text_layer).rounded_rectangle(banner, radius=26, fill=BANNER_RGBA)
        tl, _, rects = _text_plain(lay, (40 + pad_x, b_top + pad_y, 1240 - pad_x, b_top + pad_y + lay["h"]),
                                   "center", shadow=False)
        text_layer.alpha_composite(tl)
        block = np.zeros((TH, TW), bool)
        block[: int(banner[3]) + 6, 40:1240] = True
        forbid.append(_grow(banner, 8))
        tol = 0.0
    else:  # stack
        _gradient(canvas, 140, 760, "x")                       # musta 55 % -> 0 vasemmalta
        lay = _fit_text(toks, 640 - 45, 690 - 200, font_path, 170, (2, 3), 12)
        text_layer, ink, rects = _text_plain(lay, (45, 200, 640, 690), "left", shadow=True)
        block = ink
        forbid += [_grow(r, 10) for r in rects]
        tol = 0.01

    if lg is not None:
        lx, ly = (MARGIN, 24) if layout == "stack" else (MARGIN, TH - MARGIN - lg.height)
        forbid.append(_grow((lx, ly, lx + lg.width, ly + lg.height), 8))
        if avatar:
            av = (TW - MARGIN - AV_D / 2, MARGIN + AV_D / 2) if layout == "stack" else \
                (lx + lg.width + 18 + AV_D / 2, ly + lg.height / 2)
    elif avatar:
        av = (TW - MARGIN - AV_D / 2, MARGIN + AV_D / 2) if layout == "stack" else \
            (MARGIN + AV_D / 2, TH - MARGIN - AV_D / 2)
    if av is not None:
        forbid.append((av[0] - AV_D / 2 - 8, av[1] - AV_D / 2 - 40, av[0] + AV_D / 2 + 8, av[1] + AV_D / 2 + 8))
    if st is not None and layout == "stack":
        sx = (MARGIN + lg.width + 20) if lg is not None else MARGIN + 10
        sy = (24 + lg.height / 2 - st.height / 2) if lg is not None else 40
        st_xy = (sx, sy)
        forbid.append(_grow((sx, sy, sx + st.width, sy + st.height), 8))

    placed = _place_hero(hero, layout, forbid, block, tol) if hero is not None else None
    head = placed[3] if placed else None

    if st is not None and layout == "banner":  # tarra bannerin alle vasemmalle tai oikeaan reunaan, ei kasvoille
        top = rects[-1][3] + 22 + 22
        opts = [(52, top), (TW - 40 - st.width, top)]
        if lg is not None:
            opts.append((52, ly - 24 - st.height))
        free = [o for o in opts if head is None or not _overlaps(_grow((*o, o[0] + st.width, o[1] + st.height), 6),
                                                                 head)]
        st_xy = (free or opts)[0]

    canvas.alpha_composite(text_layer)
    if placed:
        _draw_hero(canvas, hero, *placed[:3])
    if lg is not None:
        _drop_shadow(canvas, lg.split()[3], (lx, ly), 128, 8, SHADOW[2])
        canvas.alpha_composite(lg, (lx, ly))
    if st is not None and st_xy is not None:
        _drop_shadow(canvas, st.split()[3], st_xy, 110, 8, SHADOW[2])
        _comp(canvas, st, st_xy)
    if av is not None:
        _avatar(canvas, pose, av)
    return canvas.convert("RGB")


def _rembg_hero(cfg, cutter, want, used: set, layout: str, tries: int = 6):
    """Varatapa ilman cutouteja: ihmisiä sisältävä virallinen kuva (People -> taide -> paikat, pyydetty kategoria
    ensin), josta rembg irrottaa kohteen (4-60 % kuvasta). Palauttaa (sankari, kuva) tai None."""
    idx = background_index(cfg)
    want = (want or "").replace("_", " ").lower().strip()

    def tier(f: Path) -> int:
        parts = [x.lower() for x in f.parts]
        return 0 if "people" in parts else 1 if "artwork" in parts else 2

    files = [f for c in idx for f in idx[c] if f not in used]
    random.shuffle(files)
    files.sort(key=lambda f: (0 if want and want in f.parent.name.replace("_", " ").lower() else 1, tier(f)))
    for f in files[:tries]:
        try:
            src = Image.open(f)
            src.draft("RGB", (1600, 1600))
            src = src.convert("RGB")
        except OSError:
            continue
        src.thumbnail((1600, 1600))
        cut = cutter.cut(src)
        if cut is not None:
            used.add(f)
            hero = _hero_from_rgba(cut, layout, f.stem)
            if hero is not None:
                return hero, f
        if cutter.failed:
            break
    return None


def _make_youtube_thumbnails(cfg, variants: list[dict], out_dir: Path, name: str) -> list[Path]:
    tc = cfg.thumbnail
    font_path = tc.font if Path(tc.font).exists() else cfg.video.font
    places, plates = _places(cfg), _plates(cfg)
    stems = available_characters(cfg)
    cdir = _cutouts_dir(cfg)
    logo = _load_logo(cfg)
    avatar = bool(tc.get("avatar", False))
    poses, pdir = available_poses(cfg), Path(tc.get("poses_dir", "") or "")
    cutter = Cutter(tc.get("cutout_model", "isnet-general-use")) if not stems and tc.get("cutout", True) else None
    if not places and not plates:
        return []
    used_h, used_bg, outs = set(), set(), []
    for i, v in enumerate(variants):
        layout = LAYOUT_OF.get(str(v.get("style") or "").lower(), LAYOUTS[i % len(LAYOUTS)])
        stem = _pick_character(v, stems, used_h)
        bgf = _pick_bg(cfg, places, plates, v.get("background"), stem, used_bg)
        if bgf is None:
            return []
        hero = _load_hero(cdir / f"{stem}.png", layout) if stem else None
        if hero is None and cutter is not None:  # ei cutouteja: irrotetaan kohde virallisesta kuvasta (rembg)
            hit = _rembg_hero(cfg, cutter, v.get("background"), used_bg, layout)
            if hit is not None:
                hero, bgf = hit
        pose = None
        if avatar and poses:
            pn = v.get("mascot") if v.get("mascot") in poses else "default" if "default" in poses else poses[0]
            pose = Image.open(pdir / f"{pn}.png").convert("RGBA")
        img = _render_youtube(layout, _prep_bg(bgf), hero, v.get("text") or "GTA 6", v.get("badge"), font_path,
                              logo, pose, avatar)
        p = out_dir / f"{name}_thumb{i + 1}_{layout}.jpg"
        _save_jpg(img, p)
        log.info("Thumbnail %d: %s, hahmo=%s (pyydetty %s), tausta=%s", i + 1, layout,
                 hero.name if hero else None, v.get("character"), bgf.name)
        outs.append(p)
    return outs


# ---------------------------------------------------------------- main entry

def _label_font(font_path, size: int):
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for f in (font_path, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", windir / "arialbd.ttf",
              windir / "segoeuib.ttf"):
        if f and Path(f).is_file():
            try:
                return ImageFont.truetype(str(f), size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _contact_sheet(paths: list[Path], out: Path, font_path: str | None = None):
    big, small = (640, 360), (246, 138)  # mobiilin kotisyötteen kokoluokka
    sheet = Image.new("RGB", (big[0] * len(paths) + 20 * (len(paths) + 1), 360 + 138 + 60), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    f = _label_font(font_path, 30)
    for i, p in enumerate(paths):
        im = Image.open(p)
        x = 20 + i * (big[0] + 20)
        sheet.paste(im.resize(big, Image.LANCZOS), (x, 20))
        sheet.paste(im.resize(small, Image.LANCZOS), (x, 400))
        d.text((x + small[0] + 20, 450), f"#{i + 1}  ({p.stem.split('_')[-1]})", fill=WHITE, font=f)
    sheet.save(out, quality=88)


def make_thumbnails(cfg, bg: Path, script: dict, out_dir: Path, name: str, work: Path) -> list[Path]:
    tc = cfg.thumbnail
    font_path = tc.font if Path(tc.font).exists() else cfg.video.font
    variants = list(script.get("thumbnail_variants") or [])[: tc.variants]
    base_text = script.get("thumbnail_text") or script["video_title"]
    while len(variants) < tc.variants:
        variants.append({"text": base_text})
    for i, v in enumerate(variants):
        st = OLD_STYLE_OF.get(str(v.get("style") or "").lower(), v.get("style"))  # uudet nimet -> vanhat
        v["style"] = st if st in STYLES else STYLES[i % len(STYLES)]
        if v.get("badge") not in BADGES:
            v["badge"] = None

    layout = str(tc.get("layout", "youtube") or "youtube").lower()
    outs = []
    if layout != "classic":
        try:
            outs = _make_youtube_thumbnails(cfg, variants, out_dir, name)
        except Exception:  # noqa: BLE001 - thumbnail ei saa kaataa koko ajoa -> vanha tyyli
            log.exception("YouTube-tyylin thumbnailit epäonnistuivat, käytetään classic-tyyliä")
    if not outs:  # "classic" (tai YouTube-tyyli ei onnistunut): virallinen taustakuva + iso maskotti
        outs = _make_brand_thumbnails(cfg, script, variants, out_dir, name)
    if outs:
        _contact_sheet(outs, out_dir / f"{name}_thumbs_compare.jpg", cfg.video.font)
        log.info("Thumbnailit: %s", ", ".join(p.name for p in outs))
        return outs

    thumb_work = work / "thumbs"
    thumb_work.mkdir(exist_ok=True)
    frames = sample_frames(cfg, thumb_work, bg, tc.candidate_frames)
    best = _distinct(frames, len(variants) + 3)
    cutter = Cutter(tc.cutout_model) if tc.cutout else None
    used, outs = set(), []
    for i, v in enumerate(variants):
        frame_idx = next((k for k in range(len(best)) if k not in used), 0)
        style, subject = v["style"], None
        if style == "cutout":
            for k in [x for x in range(len(best)) if x not in used][:4]:
                subject = cutter.cut(best[k]) if cutter else None
                if subject is not None:
                    frame_idx = k
                    break
            if subject is None:
                style = "zoom"
                log.info("Variantti %d: ei selkeää kohdetta irrotukseen -> zoom-tyyli", i + 1)
        used.add(frame_idx)
        frame = best[frame_idx]
        if style == "cutout":
            img = style_cutout(frame, subject, v["text"], v["badge"], font_path)
        elif style == "zoom":
            hit = cutter.cut_with_box(frame) if cutter else None
            img = style_zoom(frame, v["text"], v["badge"], font_path, hit[1] if hit else None)
        else:
            img = style_stamp(frame, v["text"], v["badge"], font_path)
        p = out_dir / f"{name}_thumb{i + 1}_{style}.jpg"
        q = 92
        img.save(p, quality=q)
        while p.stat().st_size > 1_900_000 and q > 60:  # YouTube-raja 2 MB
            q -= 8
            img.save(p, quality=q)
        outs.append(p)
    _contact_sheet(outs, out_dir / f"{name}_thumbs_compare.jpg", cfg.video.font)
    log.info("Thumbnailit: %s", ", ".join(p.name for p in outs))
    return outs
