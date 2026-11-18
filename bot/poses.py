"""Maskotin eleet thumbnaileihin.

Pohjana on logosta irrotettu maskotti (assets/brand/mascot_cutout.png). Jokainen ele piirretään sen päälle
tarvikkeina ja efekteinä (hattu + suurennuslasi, isot silmät + huutomerkit, ajatuskupla ...). Kaikilla
eleillä on sama kangaskoko, joten maskotti näkyy thumbnailissa aina samassa koossa ja kohdassa.

Oman piirroksen voi vaihtaa tilalle milloin vain: tallenna läpinäkyvä PNG nimellä
assets/brand/poses/<ele>.png (ks. thumbnail.POSES). Generaattori ei ylikirjoita tiedostoja, joita
ei ole merkitty generoiduiksi (poses/.generated), paitsi --force-valinnalla.

  python -m bot.poses            # luo puuttuvat / generoidut eleet
  python -m bot.poses --force    # luo kaikki uudelleen
"""
import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "assets/brand/mascot_cutout.png"
OUT = ROOT / "assets/brand/poses"
FONT = ROOT / "assets/fonts/Bangers-Regular.ttf"

PAD_L, PAD_T, PAD_R, PAD_B = 140, 260, 40, 0          # tilaa tarvikkeille pään yläpuolella ja vasemmalla
EYE_L, EYE_R = (578, 215), (805, 283)                  # silmien keskipisteet pohjakuvassa (1140x695)
BLACK, WHITE = (10, 12, 20, 255), (255, 255, 255, 255)
YELLOW, BLUE, CYAN = (250, 204, 21, 255), (30, 155, 255, 255), (79, 208, 255, 255)
RED = (232, 44, 44, 255)


def P(x, y):
    """Pohjakuvan koordinaatti -> kankaan koordinaatti."""
    return (x + PAD_L, y + PAD_T)


def _font(size):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default(size)


def _canvas(base):
    c = Image.new("RGBA", (base.width + PAD_L + PAD_R, base.height + PAD_T + PAD_B), (0, 0, 0, 0))
    return c


def _text(layer, xy, s, size, fill, angle=0, stroke=12):
    f = _font(size)
    tmp = Image.new("RGBA", (int(size * len(s) * 0.8) + 80, int(size * 1.4) + 60), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((40, 30), s, font=f, fill=fill, stroke_width=stroke, stroke_fill=BLACK)
    tmp = tmp.crop(tmp.getbbox()).rotate(angle, expand=True, resample=Image.BICUBIC)
    layer.alpha_composite(tmp, (int(xy[0] - tmp.width / 2), int(xy[1] - tmp.height / 2)))


def _outlined_ellipse(d, box, fill, w=8):
    x0, y0, x1, y1 = box
    d.ellipse([x0 - w, y0 - w, x1 + w, y1 + w], fill=BLACK)
    d.ellipse(box, fill=fill)


def _drop(d, cx, cy, r, fill):
    """Kyynel/hikipisara: ympyrä + kärki ylöspäin."""
    pts = [(cx, cy - r * 2.1), (cx - r * 0.95, cy - r * 0.3), (cx + r * 0.95, cy - r * 0.3)]
    for grow, col in ((7, BLACK), (0, fill)):
        d.polygon([(x + (grow if x > cx else -grow if x < cx else 0), y - (grow if y < cy - r else 0))
                   for x, y in pts], fill=col)
        d.ellipse([cx - r - grow, cy - r - grow, cx + r + grow, cy + r + grow], fill=col)
    d.ellipse([cx - r * 0.45, cy - r * 0.5, cx - r * 0.05, cy - r * 0.1], fill=(255, 255, 255, 220))


def _star(d, cx, cy, r, fill):
    """Nelisakarainen kimallus."""
    def pts(rr, k):
        out = []
        for i in range(8):
            a = math.pi / 4 * i - math.pi / 2
            rad = rr if i % 2 == 0 else rr * k
            out.append((cx + math.cos(a) * rad, cy + math.sin(a) * rad))
        return out
    d.polygon(pts(r + 9, 0.34), fill=BLACK)
    d.polygon(pts(r, 0.3), fill=fill)


def _magnify(base_img, canvas, center, r, zoom):
    """Suurennettu ympyräalue (esim. silmä suurennuslasin läpi)."""
    cx, cy = center
    src = r / zoom
    crop = base_img.crop((int(cx - src), int(cy - src), int(cx + src), int(cy + src))).resize(
        (2 * r, 2 * r), Image.LANCZOS)
    mask = Image.new("L", (8 * r, 8 * r), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 8 * r - 1, 8 * r - 1], fill=255)
    mask = mask.resize((2 * r, 2 * r), Image.LANCZOS)
    glass = Image.new("RGBA", (2 * r, 2 * r), (190, 225, 255, 255))   # lasin sävy kun alla on läpinäkyvää
    glass.alpha_composite(crop)
    glass.putalpha(mask)
    canvas.alpha_composite(glass, (int(P(cx, cy)[0] - r), int(P(cx, cy)[1] - r)))


def _bulge_eye(base_img, canvas, box, scale):
    """Silmä isommaksi (yllättynyt): rajattu alue suurennetaan ja häivytetään reunoilta."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    part = base_img.crop(box).resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    mask = Image.new("L", part.size, 0)
    ImageDraw.Draw(mask).ellipse([6, 6, part.width - 6, part.height - 6], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(7))
    a = part.split()[3].point(lambda v: v)
    part.putalpha(Image.composite(a, Image.new("L", part.size, 0), mask))
    cx, cy = P((x0 + x1) / 2, (y0 + y1) / 2)
    canvas.alpha_composite(part, (int(cx - part.width / 2), int(cy - part.height / 2)))


# ---------------------------------------------------------------- eleet

def pose_default(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))


def pose_detective(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))
    # huopahattu (fedora) kallellaan
    hat = Image.new("RGBA", c.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(hat)
    brown, dark = (128, 88, 54, 255), (40, 26, 16, 255)
    _outlined_ellipse(d, [*P(290, 30), *P(940, 140)], brown, 10)
    d.rounded_rectangle([*P(410, -165), *P(820, 105)], radius=80, fill=BLACK)
    d.rounded_rectangle([*P(422, -153), *P(808, 95)], radius=72, fill=brown)
    d.polygon([P(560, -150), P(615, -95), P(670, -150)], fill=(98, 66, 40, 255))        # painauma
    d.rectangle([*P(422, 30), *P(808, 82)], fill=dark)                                 # nauha
    d.line([P(300, 98), P(930, 98)], fill=(150, 108, 70, 255), width=6)               # lierin valo
    hat = hat.rotate(-9, center=P(615, 40), resample=Image.BICUBIC)
    c.alpha_composite(hat)
    # suurennuslasi vasemman silmän päälle
    r = 105
    _magnify(base, c, EYE_L, r, 1.55)
    d = ImageDraw.Draw(c)
    cx, cy = P(*EYE_L)
    hx0, hy0 = cx - r * 0.72, cy + r * 0.72
    d.line([(hx0, hy0), (hx0 - 170, hy0 + 190)], fill=BLACK, width=52)
    d.line([(hx0, hy0), (hx0 - 165, hy0 + 184)], fill=(96, 58, 30, 255), width=36)
    d.ellipse([cx - r - 18, cy - r - 18, cx + r + 18, cy + r + 18], outline=BLACK, width=34)
    d.ellipse([cx - r - 12, cy - r - 12, cx + r + 12, cy + r + 12], outline=(214, 162, 42, 255), width=20)
    d.arc([cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18], 200, 255, fill=(255, 255, 255, 230), width=10)


def pose_shocked(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))
    _bulge_eye(base, c, (515, 165, 641, 265), 1.85)
    _bulge_eye(base, c, (762, 248, 848, 318), 1.85)
    d = ImageDraw.Draw(c)
    hc = P(610, 150)
    for ang in (-165, -135, -105, -75, -45, -15):
        a = math.radians(ang)
        p0 = (hc[0] + math.cos(a) * 430, hc[1] + math.sin(a) * 300)
        p1 = (hc[0] + math.cos(a) * 545, hc[1] + math.sin(a) * 395)
        d.line([p0, p1], fill=BLACK, width=34)
        d.line([p0, p1], fill=YELLOW, width=20)
    _drop(d, *P(905, 150), 30, (130, 205, 255, 255))
    _text(c, P(105, -95), "!!", 330, YELLOW, angle=10)


def pose_thinking(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))
    d = ImageDraw.Draw(c)
    cloud = [(P(105, -150), 95), (P(215, -195), 105), (P(320, -150), 90), (P(160, -70), 85), (P(280, -75), 85)]
    for (x, y), r in cloud:
        d.ellipse([x - r - 9, y - r - 9, x + r + 9, y + r + 9], fill=BLACK)
    for (x, y), r in cloud:
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
    for (x, y), r in ((P(395, 25), 30), (P(445, 80), 18)):
        d.ellipse([x - r - 7, y - r - 7, x + r + 7, y + r + 7], fill=BLACK)
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
    _text(c, P(215, -130), "?", 230, BLUE, angle=-6, stroke=10)
    _text(c, P(935, -95), "?", 170, CYAN, angle=14, stroke=10)


def pose_hype(base, c):
    # säteet maskotin taakse (puoliläpinäkyvä efekti -> thumbnailin ääriviiva ei kierrä niitä)
    rays = Image.new("RGBA", c.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(rays)
    cx, cy = P(640, 300)
    for i in range(18):
        a0, a1 = math.radians(i * 20), math.radians(i * 20 + 9)
        d.polygon([(cx, cy), (cx + math.cos(a0) * 900, cy + math.sin(a0) * 900),
                   (cx + math.cos(a1) * 900, cy + math.sin(a1) * 900)], fill=(79, 208, 255, 95))
    rays = rays.filter(ImageFilter.GaussianBlur(3))
    # säteet häipyvät ennen kankaan reunaa (muuten thumbnailiin jäisi suorakulmainen reuna)
    fade = Image.radial_gradient("L").resize((1400, 1400)).point(lambda v: max(0, 255 - int(v * 1.15)))
    fmask = Image.new("L", c.size, 0)
    fmask.paste(fade, (int(cx - 700), int(cy - 700)))
    ra = rays.split()[3]
    rays.putalpha(Image.composite(ra, Image.new("L", c.size, 0), fmask))
    c.alpha_composite(rays)
    c.alpha_composite(base, (PAD_L, PAD_T))
    d = ImageDraw.Draw(c)
    for (x, y), r in ((P(170, -120), 95), (P(985, -70), 72), (P(95, 240), 55), (P(1055, 190), 50),
                      (P(470, -175), 48)):
        _star(d, x, y, r, YELLOW)


def pose_angry(base, c):
    aura = Image.new("RGBA", c.size, (0, 0, 0, 0))
    ImageDraw.Draw(aura).ellipse([*P(260, -120), *P(1000, 520)], fill=(232, 44, 44, 120))
    c.alpha_composite(aura.filter(ImageFilter.GaussianBlur(60)))
    c.alpha_composite(base, (PAD_L, PAD_T))
    d = ImageDraw.Draw(c)
    # suuttumusmerkki otsalle (neljä kaarta)
    mx, my = P(760, 95)
    for k, (dx, dy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
        bx, by = mx + dx * 42, my + dy * 42
        box = [bx - 34, by - 34, bx + 34, by + 34]
        start = (180 + k * 90 + 180) % 360
        d.arc([box[0] - 6, box[1] - 6, box[2] + 6, box[3] + 6], start, start + 90, fill=BLACK, width=30)
        d.arc(box, start, start + 90, fill=RED, width=18)
    # höyrypilvet pään sivuilta
    for (x, y), r in ((P(330, -10), 55), (P(270, -70), 42), (P(225, -125), 30),
                      (P(905, 20), 50), (P(965, -35), 38), (P(1010, -85), 26)):
        d.ellipse([x - r - 7, y - r - 7, x + r + 7, y + r + 7], fill=BLACK)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(235, 238, 245, 255))


def pose_laughing(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))
    d = ImageDraw.Draw(c)
    tear = (110, 195, 255, 255)
    for (x, y), r in ((P(500, 270), 26), (P(470, 335), 21), (P(875, 330), 22), (P(900, 390), 17)):
        _drop(d, x, y, r, tear)
    _text(c, P(150, -150), "HA", 190, YELLOW, angle=14)
    _text(c, P(950, -140), "HA", 160, YELLOW, angle=-12)


def pose_pointing(base, c):
    c.alpha_composite(base, (PAD_L, PAD_T))
    d = ImageDraw.Draw(c)
    # lyhyt nuoli pään vierestä kohti otsikkoa: kärki jää thumbnailin tekstialueen ulkopuolelle
    y = P(0, -40)[1]
    tip_x, tail_x = 250, P(395, 0)[0]
    body = [(tail_x, y - 42), (tip_x + 125, y - 42), (tip_x + 125, y - 100), (tip_x, y),
            (tip_x + 125, y + 100), (tip_x + 125, y + 42), (tail_x, y + 42)]
    grown = [(x + (12 if x == tail_x else -18 if x == tip_x else 0),
              yy + (-12 if yy < y else 12 if yy > y else 0)) for x, yy in body]
    d.polygon(grown, fill=BLACK)
    d.polygon(body, fill=YELLOW)


POSE_FUNCS = {
    "default": pose_default, "detective": pose_detective, "shocked": pose_shocked, "thinking": pose_thinking,
    "hype": pose_hype, "angry": pose_angry, "laughing": pose_laughing, "pointing": pose_pointing,
}


def build(force: bool = False) -> list[Path]:
    base = Image.open(BASE).convert("RGBA")
    OUT.mkdir(parents=True, exist_ok=True)
    marker = OUT / ".generated"
    generated = set(json.loads(marker.read_text(encoding="utf-8"))) if marker.exists() else set()
    written = []
    for name, fn in POSE_FUNCS.items():
        p = OUT / f"{name}.png"
        if p.exists() and name not in generated and not force:
            continue  # käyttäjän oma piirros -> ei kosketa
        c = _canvas(base)
        fn(base, c)
        c.save(p)
        generated.add(name)
        written.append(p)
    marker.write_text(json.dumps(sorted(generated)), encoding="utf-8")
    return written


def contact_sheet(path: Path):
    files = [OUT / f"{n}.png" for n in POSE_FUNCS if (OUT / f"{n}.png").exists()]
    tw, th = 440, 318
    sheet = Image.new("RGB", (tw * 4, (th + 40) * ((len(files) + 3) // 4)), (60, 66, 80))
    d = ImageDraw.Draw(sheet)
    for i, f in enumerate(files):
        im = Image.open(f).convert("RGBA")
        im.thumbnail((tw, th))
        x, y = (i % 4) * tw, (i // 4) * (th + 40)
        sheet.paste(im, (x + (tw - im.width) // 2, y), im)
        d.text((x + 10, y + th + 8), f.stem, fill=(255, 255, 255), font=_font(28))
    sheet.save(path, quality=90)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for p in build(a.force):
        print(p)
    contact_sheet(OUT.parent / "poses_preview.jpg")
    print(OUT.parent / "poses_preview.jpg")
