"""Pitkän videon uutispaneeli ("uutissivu"), lainauskortit (myös Shorts) ja osioiden otsikot + whoosh-ääni.

Kaikki rakennetaan lyhyinä paloina ennen loppurenderöintiä, jotta loppurenderöinti pysyy kevyenä:
  panel / quote -> oma läpinäkymätön klippi: sumennettu + tummennettu pelikuva ja staattiset PNG-kerrokset,
                   joiden liuku ja häivytys tehdään ffmpegin overlay-/fade-lausekkeilla (ei ruutu kerrallaan PIL:llä)
  title         -> läpinäkyvä kuvasarja (vain 0,5 s animaatio PIL:llä + staattinen ruutu), pelikuvan päälle
  whoosh        -> yksi ääniraita, jossa lyhyt suodatettu kohina jokaisen paneelin ja otsikon alussa

Kaikki videopalat ovat yuv420p / tv / bt709 (render.UNIFORM), muuten loppurenderöinti voi jumittua.
"""
import logging
import random
import re
import wave
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import brand, proc
from .render import UNIFORM, _enc_args

log = logging.getLogger(__name__)
WHOOSH_VERSION = 1

# lisävärit (brändin perusvärit tulevat brand.colors-asetuksista)
ROW, CARD, CARD_HI, CARD_META, CHEVRON = "#13244A", "#F4F7FF", "#33405C", "#5B6B8C", "#85B7EB"

# ajoitus (s)
SLIDE, XF0, XF1, FADE_OUT, BLUR_IN = 0.35, 0.35, 0.6, 0.25, 0.25
Q_IN, Q_SWEEP0, Q_SWEEP = 0.3, 0.5, 0.6
TITLE_S, TITLE_IN, TITLE_LINE, TITLE_OUT = 2.4, 0.3, 0.5, 0.4
MIN_SPAN = 1.5          # otsikon perään siirretty paneeli/lainaus, joka jäisi tätä lyhyemmäksi, jätetään pois

# uutispaneelin mitat (1920x1080)
COL_X, COL_Y, COL_W, COL_BOTTOM = 60, 140, 700, 900
IMG_X, IMG_Y, IMG_W, IMG_H, IMG_R, IMG_BORDER = 820, 140, 1040, 585, 28, 6
STRIP_Y, TH_W, TH_H, TH_GAP, TH_R = 750, 160, 90, 20, 12
ROW_H, ROW_R, ROW_GAP, ROW_ALPHA = 84, 18, 14, 235
CTA_GAP, CTA_H = 20, 70
PILL_Y, PILL_H, PILL_RIGHT = 50, 56, 1860
EXP_PAD, EXP_R = 32, 24
MAX_ROWS = 4  # 5 riviä ei jätä avatulle kortille tilaa tiivistelmälle (y < 900)
SS = 2                  # ylinäytteistys: pyöristetyt kulmat pehmeiksi

# laajennetun kortin sovitus: (otsikkorivejä, korostusrivejä, fonttikerroin), ensimmäinen joka mahtuu
LADDER = [(3, 3, 1.0), (3, 2, 1.0), (2, 2, 1.0), (2, 1, 1.0), (2, 2, 0.9), (2, 1, 0.9), (2, 1, 0.82),
          (2, 0, 1.0), (2, 0, 0.9), (1, 1, 0.82), (2, 0, 0.82), (1, 0, 0.82), (1, 0, 0.7)]


# ---------------------------------------------------------------- apurit

def _rgb(h):
    return brand.rgb(h)


def _ui(cfg, size):
    try:
        return ImageFont.truetype(cfg.video.font, max(int(round(size)), 6))
    except OSError:
        return ImageFont.load_default(max(int(round(size)), 6))


def _fit(font, text: str, max_w: float) -> str:
    text = text.strip()
    if font.getlength(text) <= max_w:
        return text
    while text and font.getlength(text.rstrip() + "…") > max_w:
        text = text[:-1]
    return text.rstrip(" ,.;:-") + "…"


def _wrap(font, text: str, max_w: float, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for w in (text or "").split():
        t = (cur + " " + w).strip()
        if not cur or font.getlength(t) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if max_lines <= 0:
        return []
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [_fit(font, " ".join(lines[max_lines - 1:]), max_w)]
    return [_fit(font, ln, max_w) for ln in lines]


def _pill(d, x, y, h, text, font, fill, color, pad, right=False):
    """Täysin pyöreä pilleri; x = vasen reuna (right=True: oikea reuna). Palauttaa (x0, x1)."""
    w = font.getlength(text) + 2 * pad
    x0 = x - w if right else x
    d.rounded_rectangle([x0, y, x0 + w, y + h], radius=h / 2, fill=fill)
    d.text((x0 + w / 2, y + h / 2), text, font=font, fill=color, anchor="mm")
    return x0, x0 + w


def _down(img: Image.Image) -> Image.Image:
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


def _domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _norm_words(s: str) -> list[str]:
    from .scriptwriter import norm_quote
    return [re.sub(r"^\W+|\W+$", "", norm_quote(w)) for w in (s or "").split()]


def _still(i: int, n: int, fps: int) -> str:
    """Yksi PNG -> n ruutua (dekoodataan kerran, ei joka ruudulla)."""
    return f"[{i}:v]loop=loop={max(n - 1, 0)}:size=1:start=0,setpts=N/{fps}/TB,format=rgba"


def _clip_enc() -> list[str]:
    enc = _enc_args()
    enc[enc.index("-crf") + 1] = "18"
    return enc


def _run(cmd: list[str], seconds: float = 0.0):
    """Yksi paneeli-/lainausklippi: aikaraja max(120 s, 10 x klipin kesto)."""
    proc.run(cmd, max(proc.PIECE, 10.0 * float(seconds or 0.0)))


def _blur_chain(W: int, H: int, b) -> str:
    """Vahva sumennus (≈ sigma 24 täysikokoisena) pienennettynä + 70 % tummennus brändin taustaväriin."""
    bg = b.colors.bg.lstrip("#")
    return (f"scale={W // 4}:{H // 4}:flags=area,gblur=sigma=6,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{bg}@0.70:t=fill,scale={W}:{H}:flags=bilinear,format=yuv420p")


def _bg_input(bg: Path, start: float, d: float, fps: int) -> list[str]:
    # puolen ruudun varaus: tarkka haku ei saa pudottaa juuri haluttua ruutua pyöristyksen takia
    return ["-ss", f"{max(start - 0.5 / fps, 0):.4f}", "-t", f"{d + 0.5:.3f}", "-i", str(bg)]


def _grid(c: dict, fps: int) -> tuple[float, int]:
    s = round(c["s"] * fps) / fps
    n = max(int(round((c["e"] - s) * fps)), 1)
    return s, n


# ---------------------------------------------------------------- uutispaneeli: kerrokset

def _row_png(cfg, b, title: str, source: str, big: bool = False) -> Image.Image:
    """Suljettu rivi 700x84 (big=True: ylinäytteistetty koko)."""
    k = SS
    img = Image.new("RGBA", (COL_W * k, ROW_H * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, COL_W * k - 1, ROW_H * k - 1], radius=ROW_R * k, fill=_rgb(ROW) + (ROW_ALPHA,))
    fs = _ui(cfg, 22 * k)
    ph = 40 * k
    x0, _ = _pill(d, (COL_W - 22) * k, (ROW_H * k - ph) / 2, ph, (source or "").upper()[:24], fs,
                  _rgb(b.colors.panel), _rgb(b.colors.glow), 16 * k, right=True)
    ft = _ui(cfg, 30 * k)
    d.text((24 * k, ROW_H * k / 2), _fit(ft, title, x0 - 40 * k), font=ft, fill=_rgb(b.colors.text), anchor="lm")
    return img if big else _down(img)


def _expanded(cfg, b, art: dict, highlight, head_n: int, hi_n: int, sc: float, draw: bool):
    """Laajennettu (valkoinen) kortti. draw=False -> vain korkeus (sama rivitys kuin piirrettäessä)."""
    k = SS
    inner = (COL_W - 2 * EXP_PAD) * k
    f_pill, f_head = _ui(cfg, 22 * sc * k), _ui(cfg, 46 * sc * k)
    f_hi, f_meta = _ui(cfg, 30 * sc * k), _ui(cfg, 24 * sc * k)
    head = _wrap(f_head, art.get("title", ""), inner, head_n)
    hi = _wrap(f_hi, highlight, inner, hi_n) if highlight else []
    pill_h, head_lh, hi_lh, meta_h = 40 * sc, 46 * sc * 1.18, 30 * sc * 1.3, 24 * sc * 1.25
    h = EXP_PAD + pill_h + 16 + len(head) * head_lh + (12 + len(hi) * hi_lh if hi else 0) + 14 + meta_h + EXP_PAD
    h = int(round(h))
    if not draw:
        return h, None
    img = Image.new("RGBA", (COL_W * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, COL_W * k - 1, h * k - 1], radius=EXP_R * k, fill=_rgb(CARD) + (255,))
    x, y = EXP_PAD * k, EXP_PAD * k
    _pill(d, x, y, pill_h * k, "NEWS", f_pill, _rgb(b.colors.primary), (255, 255, 255), 16 * sc * k)
    y += (pill_h + 16) * k
    for ln in head:
        d.text((x, y), ln, font=f_head, fill=_rgb(b.colors.outline), anchor="la")
        y += head_lh * k
    if hi:
        y += 12 * k
        for ln in hi:
            d.text((x, y), ln, font=f_hi, fill=_rgb(CARD_HI), anchor="la")
            y += hi_lh * k
    y += 14 * k
    meta = " · ".join(p for p in ((art.get("source") or "").upper(), (art.get("date") or "").upper()) if p)
    d.text((x, y), _fit(f_meta, meta, inner), font=f_meta, fill=_rgb(CARD_META), anchor="la")
    return h, _down(img)


def _column_layout(cfg, b, n_rows: int, art: dict, highlight):
    """Valitsee laajennetun kortin koon niin, että sarake + CTA päättyy y=900 yläpuolelle."""
    room = COL_BOTTOM - COL_Y - 1
    for rows in range(n_rows, 0, -1):
        rest = (rows - 1) * (ROW_H + ROW_GAP) + CTA_GAP + CTA_H
        for head_n, hi_n, sc in LADDER:
            h, _ = _expanded(cfg, b, art, highlight, head_n, hi_n if highlight else 0, sc, draw=False)
            if rest + h <= room:
                return rows, (head_n, hi_n if highlight else 0, sc)
    return 1, (1, 0, 0.7)


def _panel_layers(cfg, b, rows_arts: list[dict], cur: int, card: dict, out: Path) -> dict:
    """-> {'static','col','exp','pills'} PNG-polut + nykyisen rivin y."""
    art = rows_arts[cur]
    n, (head_n, hi_n, sc) = _column_layout(cfg, b, len(rows_arts), art, card.get("highlight"))
    lo = min(max(cur - (n - 1) // 2, 0), len(rows_arts) - n)  # n rivin ikkuna, jossa nykyinen on mukana
    win = list(range(lo, lo + n))
    exp_h, exp = _expanded(cfg, b, art, card.get("highlight"), head_n, hi_n, sc, draw=True)

    k = SS
    col_h = COL_BOTTOM - COL_Y
    static = Image.new("RGBA", (COL_W * k, col_h * k), (0, 0, 0, 0))
    y, ycur = 0, 0
    for j in win:
        if j == cur:
            ycur = y
            y += exp_h + ROW_GAP
        else:
            static.alpha_composite(_row_png(cfg, b, rows_arts[j].get("title", ""), rows_arts[j].get("source", ""),
                                            big=True), (0, y * k))
            y += ROW_H + ROW_GAP
    y += CTA_GAP - ROW_GAP
    d = ImageDraw.Draw(static)
    d.rounded_rectangle([0, y * k, COL_W * k - 1, (y + CTA_H) * k - 1], radius=CTA_H * k / 2,
                        fill=_rgb(b.colors.primary) + (255,))
    f = _ui(cfg, 28 * k)
    label = _fit(f, f"Source: {_domain(art.get('url')) or art.get('source', '')}", (COL_W - 60) * k)
    d.text((COL_W * k / 2, (y + CTA_H / 2) * k), label, font=f, fill=_rgb(b.colors.outline), anchor="mm")
    _down(static).save(out / "col_static.png")
    _row_png(cfg, b, art.get("title", ""), art.get("source", "")).save(out / "row_col.png")
    exp.save(out / "row_exp.png")

    pills = Image.new("RGBA", (1920 * k, 130 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(pills)
    fp = _ui(cfg, 28 * k)
    name = (str(b.get("name") or "Catfish").split() or ["Catfish"])[0].upper()
    _pill(d, COL_X * k, PILL_Y * k, PILL_H * k, f"← {name} NEWS", fp, _rgb(ROW), (255, 255, 255), 26 * k)
    if art.get("date"):
        _pill(d, PILL_RIGHT * k, PILL_Y * k, PILL_H * k, art["date"].upper(), fp, _rgb(b.colors.primary),
              _rgb(b.colors.outline), 26 * k, right=True)
    _down(pills).save(out / "pills.png")
    return {"ycur": COL_Y + ycur, "rows": n}


def _round_mask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w * 4, h * 4), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * 4 - 1, h * 4 - 1], radius=r * 4, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def _image_layers(cfg, b, img: Path | None, strip: list[Path], out: Path) -> bool:
    """Kuva-alueen kerrokset: src.jpg (2x, zoomia varten), mask.png ja deco.png (reunus + pikkukuvarivi)."""
    primary = _rgb(b.colors.primary)
    deco_h = STRIP_Y + TH_H - IMG_Y
    k = 4
    deco = Image.new("RGBA", (IMG_W * k, deco_h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(deco)
    if img is None:  # ei virallisia kuvia: tasainen paneeli reunuksella
        d.rounded_rectangle([0, 0, IMG_W * k - 1, IMG_H * k - 1], radius=IMG_R * k, fill=_rgb(b.colors.panel) + (255,))
    d.rounded_rectangle([0, 0, IMG_W * k - 1, IMG_H * k - 1], radius=IMG_R * k, outline=primary + (255,),
                        width=IMG_BORDER * k)
    deco = deco.resize((IMG_W, deco_h), Image.LANCZOS)
    if img is not None and strip:
        x0 = (IMG_W - (5 * TH_W + 4 * TH_GAP)) // 2
        y0 = STRIP_Y - IMG_Y
        mask = _round_mask(TH_W, TH_H, TH_R)
        for i, p in enumerate(strip[:5]):
            try:
                th = ImageOps.fit(Image.open(p).convert("RGB"), (TH_W, TH_H), Image.LANCZOS).convert("RGBA")
            except OSError:
                continue
            a = mask if i == 1 else mask.point(lambda v: int(v * 0.7 + 0.5))
            th.putalpha(a)
            x = x0 + i * (TH_W + TH_GAP)
            deco.alpha_composite(th, (x, y0))
            if i == 1:  # nykyinen kuva: sininen reunus
                ring = Image.new("RGBA", (TH_W * 4, TH_H * 4), (0, 0, 0, 0))
                ImageDraw.Draw(ring).rounded_rectangle([0, 0, TH_W * 4 - 1, TH_H * 4 - 1], radius=TH_R * 4,
                                                       outline=primary + (255,), width=4 * 4)
                deco.alpha_composite(ring.resize((TH_W, TH_H), Image.LANCZOS), (x, y0))
        # nuolet ‹ › rivin molemmin puolin
        cd = ImageDraw.Draw(deco)
        cy, hh, ww = y0 + TH_H / 2, 16, 9
        for cx, sgn in ((x0 / 2, -1), (IMG_W - x0 / 2, 1)):
            pts = [(cx - sgn * ww / 2, cy - hh), (cx + sgn * ww / 2, cy), (cx - sgn * ww / 2, cy + hh)]
            cd.line(pts, fill=_rgb(CHEVRON) + (255,), width=5, joint="curve")
    deco.save(out / "deco.png")
    if img is None:
        return False
    ImageOps.fit(Image.open(img).convert("RGB"), (IMG_W * 2, IMG_H * 2), Image.LANCZOS).save(out / "src.jpg",
                                                                                              quality=93)
    _round_mask(IMG_W, IMG_H, IMG_R).save(out / "mask.png")
    return True


def _panel_clip(cfg, b, c: dict, rows_arts, cur, img, strip, bg: Path, shift: float, out_dir: Path) -> Path:
    fps = int(cfg.video.fps)
    s, n = _grid(c, fps)
    d = n / fps
    c["s"], c["e"] = s, s + d
    out_dir.mkdir(parents=True, exist_ok=True)
    lay = _panel_layers(cfg, b, rows_arts, cur, c["card"], out_dir)
    has_img = _image_layers(cfg, b, img, strip, out_dir)
    W, H = 1920, 1080
    sl = f"pow(1-min(t/{SLIDE},1),3)"
    col_x = f"'{COL_X}-{COL_X + 740}*{sl}'"
    img_x = f"'{IMG_X}+200*{sl}'"
    inputs = _bg_input(bg, s - shift, d, fps)
    for name in ("pills.png", "col_static.png", "row_col.png", "row_exp.png", "deco.png"):
        inputs += ["-framerate", str(fps), "-i", str(out_dir / name)]
    f = [f"[0:v]setpts=PTS-STARTPTS,format=yuv420p,split[sh][b0]",
         f"[b0]{_blur_chain(W, H, b)}[bl]",
         f"{_still(1, n, fps)},fade=t=in:st=0:d={SLIDE}:alpha=1[pl]",
         "[bl][pl]overlay=0:0[a1]",
         f"{_still(2, n, fps)}[cs]",
         f"[a1][cs]overlay=x={col_x}:y={COL_Y}[a2]",
         f"{_still(3, n, fps)},fade=t=out:st={XF0}:d={XF1 - XF0:.2f}:alpha=1[cc]",
         f"[a2][cc]overlay=x={col_x}:y={lay['ycur']}:enable='lt(t,{XF1})'[a3]",
         f"{_still(4, n, fps)},fade=t=in:st={XF0}:d={XF1 - XF0:.2f}:alpha=1[ce]",
         f"[a3][ce]overlay=x={COL_X}:y={lay['ycur']}:enable='gte(t,{XF0})'[a4]"]
    last = "[a4]"
    if has_img:
        inputs += ["-i", str(out_dir / "src.jpg"), "-framerate", str(fps), "-i", str(out_dir / "mask.png")]
        f += [f"[6:v]zoompan=z='1+0.06*on/{n}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={n}:s={IMG_W}x{IMG_H}"
              f":fps={fps},format=rgba[zi]",
              f"{_still(7, n, fps)},format=gray[mk]",
              "[zi][mk]alphamerge,fade=t=in:st=0:d=" f"{SLIDE}:alpha=1[im]",
              f"[a4][im]overlay=x={img_x}:y={IMG_Y}[a5]"]
        last = "[a5]"
    f += [f"{_still(5, n, fps)},fade=t=in:st=0:d={SLIDE}:alpha=1[dc]",
          f"{last}[dc]overlay=x={img_x}:y={IMG_Y}[a6]"]
    f += _fade_back("[a6]", d)
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(f),
          "-map", "[v]", "-frames:v", str(n), "-an", *_clip_enc(), str(out_dir / "clip.mp4")], n / fps)
    return out_dir / "clip.mp4"


def _fade_back(top: str, d: float) -> list[str]:
    """Alussa terävä pelikuva häivyttää pois paneelin alta, lopussa takaisin päälle (kaikki häivyy pois)."""
    e = max(d - FADE_OUT, BLUR_IN)
    return [f"[sh]format=yuva420p,fade=t=out:st=0:d={BLUR_IN}:alpha=1,fade=t=in:st={e:.3f}:d={FADE_OUT}:alpha=1[sf]",
            f"{top}[sf]overlay=0:0:enable='lt(t,{BLUR_IN})+gte(t,{e:.3f})',{UNIFORM}[v]"]


# ---------------------------------------------------------------- lainauskortti

def _quote_layout(cfg, text: str, highlight, short: bool):
    max_w, size, pad = (960, 60, 56) if short else (1400, 52, 56)
    max_layer_h = 830 if short else 900 - 120
    words = text.split()
    words = ["“" + words[0]] + words[1:]
    words[-1] = words[-1] + "”"
    hl_idx = set()
    if highlight:
        tw, hw = _norm_words(text), [w for w in _norm_words(highlight) if w]
        for i in range(len(tw) - len(hw) + 1):
            if hw and tw[i:i + len(hw)] == hw:
                hl_idx = set(range(i, i + len(hw)))
                break
    while True:
        f = _ui(cfg, size)
        space = f.getlength(" ")
        lines, cur, cw = [], [], 0.0
        for i, w in enumerate(words):
            ww = f.getlength(w)
            if cur and cw + space + ww > max_w - 2 * pad:
                lines.append(cur)
                cur, cw = [], 0.0
            cur.append((i, w, ww))
            cw += (space if len(cur) > 1 else 0) + ww
        lines.append(cur)
        asc, desc = f.getmetrics()
        lh = size * 1.3
        box_h = int(round(2 * pad + (len(lines) - 1) * lh + asc + desc))
        line_w = max(sum(t[2] for t in ln) + space * (len(ln) - 1) for ln in lines)
        box_w = int(min(max_w, line_w + 2 * pad))
        layer_h = box_h + 20 + 38  # + lähderivi (30 px)
        if layer_h <= max_layer_h or size <= 30:
            return dict(f=f, size=size, pad=pad, lines=lines, space=space, lh=lh, asc=asc, desc=desc,
                        box_w=box_w, box_h=box_h, layer_h=layer_h, hl=hl_idx)
        size -= 2


def _quote_frames(cfg, b, c: dict, article: dict, short: bool, out: Path) -> tuple[int, int, int, int]:
    """Kirjoittaa f_000.png ... (0..1,1 s animaatio) ja static.png. -> (x, y, n_anim, layer_w)"""
    card = c["card"]
    L = _quote_layout(cfg, card["text"], card.get("highlight"), short)
    k = SS
    cap_text = "— " + " · ".join(p for p in ((article.get("source") or "").upper(),
                                                       (article.get("date") or "").upper()) if p)
    f_cap = _ui(cfg, 30 * k)
    cap_w = int(f_cap.getlength(cap_text) / k) + 4
    lw = max(L["box_w"], min(cap_w, 1800 if not short else 1040))
    lh_total = L["layer_h"]
    box = Image.new("RGBA", (lw * k, lh_total * k), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle([0, 0, L["box_w"] * k - 1, L["box_h"] * k - 1], radius=20 * k,
                                          fill=_rgb(b.colors.panel) + (240,))
    box = _down(box)
    txt = Image.new("RGBA", (lw * k, lh_total * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    f = _ui(cfg, L["size"] * k)
    bars = []  # (x0, y0, x1, y1) 1x-koordinaatit, järjestyksessä vasemmalta oikealle
    for li, ln in enumerate(L["lines"]):
        y = L["pad"] + li * L["lh"]
        x = L["pad"]
        cur_bar = None
        for i, w, ww in ln:
            d.text((x * k, y * k), w, font=f, fill=_rgb(b.colors.text), anchor="la")
            if i in L["hl"]:
                x0, x1 = x - 6, x + ww + 6
                cur_bar = [cur_bar[0] if cur_bar else x0, y + L["size"] * 0.06, x1, y + L["size"] * 1.16]
            elif cur_bar:
                bars.append(cur_bar)
                cur_bar = None
            x += ww + L["space"]
        if cur_bar:
            bars.append(cur_bar)
    d.text((0, (L["box_h"] + 20) * k), cap_text, font=f_cap, fill=_rgb(b.colors.glow), anchor="la")
    txt = _down(txt)
    total = sum(x1 - x0 for x0, _, x1, _ in bars)
    fps = int(cfg.video.fps)
    n_anim = int(round((Q_SWEEP0 + Q_SWEEP) * fps)) + 1
    hl_rgba = _rgb(b.colors.primary) + (140,)
    cx, cy = L["box_w"] / 2, L["box_h"] / 2

    def frame(t: float) -> Image.Image:
        fr = box.copy()
        p = min(max((t - Q_SWEEP0) / Q_SWEEP, 0.0), 1.0)
        p = p * p * (3 - 2 * p)
        if bars and p > 0:
            lay = Image.new("RGBA", fr.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            left = p * total
            for x0, y0, x1, y1 in bars:
                if left <= 0:
                    break
                w = min(x1 - x0, left)
                ld.rectangle([x0, y0, x0 + w, y1], fill=hl_rgba)
                left -= w
            fr.alpha_composite(lay)
        fr.alpha_composite(txt)
        a = brand._ease_out(t / Q_IN)
        if a < 0.999:
            sc = 0.96 + 0.04 * a
            sm = fr.resize((max(int(fr.width * sc), 1), max(int(fr.height * sc), 1)), Image.BICUBIC)
            fr = Image.new("RGBA", fr.size, (0, 0, 0, 0))
            fr.alpha_composite(sm, (int(round(cx - cx * sc)), int(round(cy - cy * sc))))
            fr = brand._with_alpha(fr, a)
        return fr

    out.mkdir(parents=True, exist_ok=True)
    for i in range(n_anim):
        frame(i / fps).save(out / f"f_{i:03d}.png", compress_level=1)
    frame(10.0).save(out / "static.png", compress_level=1)
    W, H = (1080, 1920) if short else (1920, 1080)
    x = (W - L["box_w"]) // 2
    if short:  # koukkupalkin ja tekstityksen väliin (~420..1250)
        y = int(420 + (830 - lh_total) / 2)
    else:      # keskellä, mutta kortti + lähderivi päättyvät y=900 yläpuolelle (tekstitys alla)
        y = int(min((H - L["box_h"]) / 2, 900 - lh_total))
    return x, max(y, 40), n_anim, lw


def _quote_clip(cfg, b, c: dict, article: dict, bg: Path, shift: float, out_dir: Path) -> Path:
    fps = int(cfg.video.fps)
    short = cfg.format == "short"
    W, H = (1080, 1920) if short else (1920, 1080)
    s, n = _grid(c, fps)
    d = n / fps
    c["s"], c["e"] = s, s + d
    x, y, n_anim, _ = _quote_frames(cfg, b, c, article, short, out_dir)
    rest = max(n - n_anim, 1) / fps
    inputs = _bg_input(bg, s - shift, d, fps)
    inputs += ["-framerate", str(fps), "-start_number", "0", "-i", str(out_dir / "f_%03d.png"),
               "-framerate", str(fps), "-i", str(out_dir / "static.png")]
    f = [f"[0:v]setpts=PTS-STARTPTS,format=yuv420p,split[sh][b0]",
         f"[b0]{_blur_chain(W, H, b)}[bl]",
         "[1:v]setsar=1,format=rgba[qa]",
         f"{_still(2, max(int(round(rest * fps)), 1), fps)},setsar=1[qb]",
         "[qa][qb]concat=n=2:v=1:a=0[q]",
         f"[bl][q]overlay=x={x}:y={y}:eof_action=pass[a1]"]
    f += _fade_back("[a1]", d)
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(f),
          "-map", "[v]", "-frames:v", str(n), "-an", *_clip_enc(), str(out_dir / "clip.mp4")], n / fps)
    return out_dir / "clip.mp4"


# ---------------------------------------------------------------- osion otsikko

TITLE_BAND_Y, TITLE_BAND_H, TITLE_CY = 270, 400, 470


def _title_frames(cfg, b, text: str, out: Path) -> int:
    """Läpinäkyvä 1920x400-kaista: Bangers 120 px, ääriviiva, syaani hehku, kasvava sininen viiva."""
    W = 1920
    text = text.upper()
    size = 120
    while True:
        f = brand.font(cfg, size)
        l, t, r, bt = f.getbbox(text, anchor="la", stroke_width=6)
        if r - l <= W - 200 or size <= 60:
            break
        size -= 6
    m = 40  # hehkun tila
    tw, th = r - l + 2 * m, bt - t + 2 * m
    lay = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    glow = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((m - l, m - t), text, font=f, fill=_rgb(b.colors.glow) + (255,), anchor="la",
                              stroke_width=10, stroke_fill=_rgb(b.colors.glow) + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    glow.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.85)))
    lay.alpha_composite(glow)
    ImageDraw.Draw(lay).text((m - l, m - t), text, font=f, fill=_rgb(b.colors.text), anchor="la",
                             stroke_width=6, stroke_fill=_rgb(b.colors.outline))
    cy = TITLE_CY - TITLE_BAND_Y
    line_y = cy + (bt - t) / 2 + 26
    fps = int(cfg.video.fps)
    n_anim = int(round(TITLE_LINE * fps)) + 1

    def frame(tt: float) -> Image.Image:
        fr = Image.new("RGBA", (W, TITLE_BAND_H), (0, 0, 0, 0))
        q = brand._ease_out(tt / TITLE_LINE)
        lw = 600 * q
        if lw >= 1:
            ImageDraw.Draw(fr).rounded_rectangle([W / 2 - lw / 2, line_y, W / 2 + lw / 2, line_y + 6], radius=3,
                                                 fill=_rgb(b.colors.primary) + (255,))
        a = brand._ease_out(tt / TITLE_IN)
        sc = 1.15 - 0.15 * a
        img = lay if abs(sc - 1) < 1e-3 else lay.resize((int(tw * sc), int(th * sc)), Image.BICUBIC)
        img = brand._with_alpha(img, a)
        fr.alpha_composite(img, (int(round(W / 2 - img.width / 2)), int(round(cy - img.height / 2))))
        return fr

    out.mkdir(parents=True, exist_ok=True)
    for i in range(n_anim):
        frame(i / fps).save(out / f"f_{i:03d}.png", compress_level=1)
    frame(10.0).save(out / "static.png", compress_level=1)
    return n_anim


# ---------------------------------------------------------------- whoosh

def _whoosh(cfg, b) -> Path:
    """~0,35 s suodatettu kohina (kuten intron whoosh, hiljaisempi): huippu -24 dBFS."""
    out = brand._cache_dir(cfg) / f"whoosh_v{WHOOSH_VERSION}.wav"
    if out.is_file() and out.stat().st_size > 0:
        return out
    dur = 0.35
    raw = out.with_suffix(".raw.wav")
    fc = f"(400*pow(12,min(pts/{dur:.3f},1)))"
    gain = f"1/(1+pow((b*sr/(2*nb))/{fc},4))"
    graph = (f"anoisesrc=d={dur}:c=pink:r=48000:a=0.6:seed=23,highpass=f=180,"
             f"afftfilt=real='re*{gain}':imag='im*{gain}':win_size=1024,"
             f"afade=t=in:st=0:d={dur * 0.55:.3f}:curve=qua,afade=t=out:st={dur * 0.55:.3f}:d={dur * 0.45:.3f}:curve=qua,"
             f"lowpass=f=7000,aformat=sample_fmts=fltp:channel_layouts=mono,atrim=0:{dur}")
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-filter_complex", graph,
              "-c:a", "pcm_f32le", str(raw)], proc.PIECE)
    g = -24.0 - brand._peak_db(raw)
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(raw), "-af", f"volume={g:.2f}dB",
              "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out)], proc.PIECE)
    raw.unlink(missing_ok=True)
    return out


SR = 48000
HIT_PEAK_DB = -16.0      # isku osion otsikkoon (whoosh -24 dBFS, puhe ~-21 LUFS)
RISER_PEAK_DB = -22.0    # nousu ennen lunastusosiota
RISER_S = 1.8


def _write_wav(path: Path, x: np.ndarray, peak_db: float):
    x = x / max(float(np.max(np.abs(x))), 1e-9) * (10 ** (peak_db / 20)) * 32767
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())


def _hit(cfg) -> Path:
    """Itse syntetisoitu "isku" (Learn By Leo: hit korostaa hetken): matala sini 95->42 Hz nopealla vaimenemisella
    + lyhyt suodatettu kohinaisku. Ei tekijänoikeuksia."""
    out = brand._cache_dir(cfg) / "hit_v1.wav"
    if out.is_file() and out.stat().st_size > 0:
        return out
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    f = 42 + 53 * np.exp(-t / 0.08)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.28)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n) * np.exp(-t / 0.012)
    noise = np.convolve(noise, np.ones(24) / 24, mode="same")  # pehmeä alkuisku
    x = body + 0.35 * noise
    x[: int(0.002 * SR)] *= np.linspace(0, 1, int(0.002 * SR))
    _write_wav(out, x, HIT_PEAK_DB)
    return out


def _riser(cfg) -> Path:
    """Itse syntetisoitu nousu (riser, jännitys ennen tärkeää): nouseva sini 180->900 Hz + kirkastuva kohina,
    voimistuu loppua kohti ja katkeaa iskuun."""
    out = brand._cache_dir(cfg) / "riser_v1.wav"
    if out.is_file() and out.stat().st_size > 0:
        return out
    n = int(RISER_S * SR)
    t = np.arange(n) / SR
    u = t / RISER_S
    f = 180 * (5 ** u)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR)
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(n)
    k = 64
    smooth = np.convolve(noise, np.ones(k) / k, mode="same")
    noise = smooth + (noise - smooth) * u  # tummasta kirkkaaseen
    x = (0.6 * tone + 0.5 * noise) * (u ** 2.2)
    x[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
    _write_wav(out, x, RISER_PEAK_DB)
    return out


def _read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)


def _sfx_track(cfg, b, times: list[float], total: float, out: Path, hits: list[float] | None = None,
               risers: list[float] | None = None) -> Path | None:
    """Ääniefektiraita: whooshit (times), iskut osioiden otsikoihin (hits) ja nousut, jotka PÄÄTTYVÄT annettuun
    hetkeen (risers). Kaikki itse syntetisoituja."""
    if not times and not hits and not risers:
        return None
    wh = _read_wav(_whoosh(cfg, b))
    buf = np.zeros(int((total + 1) * SR), dtype=np.float32)

    def put(clip: np.ndarray, t: float):
        i = int(t * SR)
        j = min(i + len(clip), len(buf))
        if 0 <= i < j:
            buf[i:j] += clip[:j - i]

    kept = []
    for t in sorted(times or []):  # päällekkäiset whooshit (alle 0,4 s välein) yhdeksi, ettei huippu kasva
        if not kept or t - kept[-1] >= 0.4:
            kept.append(t)
    for t in kept:
        put(wh, t)
    if hits and brand.features(cfg).get("hits"):
        hit = _read_wav(_hit(cfg))
        for t in hits:
            put(hit, t)
    if risers and brand.features(cfg).get("risers"):
        rs = _read_wav(_riser(cfg))
        for t in risers:
            put(rs, max(t - len(rs) / SR, 0.0))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(buf, -32768, 32767).astype(np.int16).tobytes())
    return out


# ---------------------------------------------------------------- pääfunktio

def _pick_images(cfg, cards: list[dict]):
    """Yksi virallinen kuva per paneeli (kategoria kortin 'image'-kentästä) + 4 muuta pikkukuvariviin."""
    from . import thumbnail
    idx = thumbnail.background_index(cfg)
    if not idx:
        return [(None, []) for _ in cards]
    used, out = set(), []
    for card in cards:
        img = thumbnail._pick_background(idx, card.get("image"), used)
        if img is None:  # kaikki käytetty -> aloitetaan alusta
            used.clear()
            img = thumbnail._pick_background(idx, card.get("image"), used)
        cat = next((c for c, fs in idx.items() if img in fs), None)
        same = [f for f in idx.get(cat, []) if f != img]
        others = random.sample(same, min(4, len(same)))
        if len(others) < 4:
            pool = [f for fs in idx.values() for f in fs if f != img and f not in others]
            others += random.sample(pool, min(4 - len(others), len(pool)))
        strip = others[:1] + [img] + others[1:4]  # 2. paikka = nykyinen kuva
        out.append((img, strip))
    return out


def prepare(cfg, script: dict, articles: list[dict], spans: list[dict], seg_times: list[dict], bg: Path,
            plan: dict | None, work: Path, total: float | None = None) -> tuple[list[dict], Path | None]:
    """build_cards-spanit (jo lopullisella aikajanalla) -> render_final-spanit + whoosh-raita.

    seg_times: TTS-segmenttien ajat ilman intro-siirtoa. Palauttaa (spans, sfx_wav | None).
    """
    b = brand.settings(cfg)
    feat = brand.features(cfg)
    spans = [c for c in spans if c.get("png") or c.get("kind") in ("panel", "quote")]
    if not b:
        return [c for c in spans if c.get("png")], None
    fps = int(cfg.video.fps)
    off = plan["offset"] if plan else 0.0
    shift = (off - plan["flash"]) if plan and plan.get("intro") else 0.0  # lopullinen aika -> bg.mp4:n aika
    outro = plan["outro_start"] if plan else None
    fx = work / "fx"

    titles = []
    if cfg.format != "short" and feat["section_titles"]:
        for seg, t in zip(script["segments"], seg_times):
            if not seg.get("section"):
                continue
            s = round((t["s"] + off) * fps) / fps
            e = s + TITLE_S if outro is None else min(s + TITLE_S, outro)
            if e - s >= 0.6:
                titles.append({"text": seg["section"], "s": s, "e": e})

    out, panels, quotes = [], [], []
    for c in spans:
        if c.get("kind") in ("panel", "quote"):
            for tt in titles:  # alkaa otsikon aikana -> alkaa heti otsikon jälkeen (loppu pysyy)
                if tt["s"] - 1.0 / fps <= c["s"] < tt["e"]:  # otsikon alku on pyöristetty ruuturuudukkoon
                    c["s"] = tt["e"]
            if c["e"] - c["s"] < MIN_SPAN:
                log.info("Jätetään %s-kortti pois (%.1f s otsikon jälkeen)", c["kind"], c["e"] - c["s"])
                continue
            (panels if c["kind"] == "panel" else quotes).append(c)
        out.append(c)

    # paneelin rivit: videon uutiskorttien artikkelit ensiesiintymisjärjestyksessä
    order = []
    for seg in script["segments"]:
        card = seg.get("card") or {}
        if card.get("type") == "news" and card.get("article") not in order:
            order.append(card["article"])
    for c in panels:
        if c["card"]["article"] not in order:
            order.append(c["card"]["article"])

    result, sfx_times = [], []
    for k, (c, (img, strip)) in enumerate(zip(panels, _pick_images(cfg, [c["card"] for c in panels]))):
        a = c["card"]["article"]
        cur = order.index(a)
        clip = _panel_clip(cfg, b, c, [articles[i] for i in order], cur, img, strip, bg, shift, fx / f"panel_{k:02d}")
        result.append({"video": clip, "s": c["s"], "e": c["e"], "kind": "panel"})
        sfx_times.append(c["s"])
        log.info("Uutispaneeli %d: %s %.2f-%.2f s, kuva %s", k + 1, articles[a].get("source"), c["s"], c["e"],
                 img.parent.name if img else "-")
    for k, c in enumerate(quotes):
        clip = _quote_clip(cfg, b, c, articles[c["card"]["article"]], bg, shift, fx / f"quote_{k:02d}")
        result.append({"video": clip, "s": c["s"], "e": c["e"]})
        log.info("Lainaus %d: %s %.2f-%.2f s", k + 1, articles[c["card"]["article"]].get("source"), c["s"], c["e"])
    result.sort(key=lambda x: x["s"])
    result += [c for c in out if c.get("png")]
    for k, tt in enumerate(titles):
        d = fx / f"title_{k:02d}"
        n_anim = _title_frames(cfg, b, tt["text"], d)
        dur = tt["e"] - tt["s"]
        result.append({"seq": {"pattern": str((d / "f_%03d.png").resolve()), "static": str((d / "static.png").resolve()),
                               "n": n_anim, "x": 0, "y": TITLE_BAND_Y,
                               "fade_st": max(dur - TITLE_OUT, 0.0), "fade_d": min(TITLE_OUT, dur)},
                       "s": tt["s"], "e": tt["e"]})
        sfx_times.append(tt["s"])
        log.info("Osion otsikko %d: %r %.2f-%.2f s", k + 1, tt["text"], tt["s"], tt["e"])
    sfx = None
    if plan is not None and titles and feat.get("music_dips"):  # musiikin notkahdus aiheen vaihtuessa (music.mix)
        plan["music_dips"] = [(tt["s"], tt["e"], k == len(titles) - 1 and len(titles) >= 3)
                              for k, tt in enumerate(titles)]
    if feat["whoosh"] and sfx_times:
        end = total if total is not None else (plan["total"] if plan else max(c["e"] for c in result))
        hits = [tt["s"] for tt in titles]
        risers = [titles[-1]["s"]] if len(titles) >= 3 else []  # vain lunastusosioon (viimeinen), ei joka kohtaan
        sfx = _sfx_track(cfg, b, sorted(sfx_times), end, work / "sfx.wav", hits=hits, risers=risers)
    if panels or quotes or titles:
        log.info("Paneelit %d, lainaukset %d, osioiden otsikot %d", len(panels), len(quotes), len(titles))
    return result, sfx
