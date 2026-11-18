"""Piirtää läpinäkyvät PNG-overlayt: uutiskortit ja fakta/huhu-kortit pelikuvan päälle."""
import io
import logging
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from . import brand

log = logging.getLogger(__name__)

STATUS_COLORS = {
    "confirmed": ((34, 197, 94), "CONFIRMED"),
    "rumor": ((245, 158, 11), "RUMOR"),
    "leak": ((239, 68, 68), "LEAK"),
    "opinion": ((99, 102, 241), "MY TAKE"),
}
ACCENT = (236, 72, 153)  # Vice City -pinkki (oletus ilman brändiä)


def _accent(cfg):
    """brand.enabled -> brändin pääväri, muuten vanha pinkki."""
    return brand.accent(cfg, ACCENT)


def _font(cfg, size):
    try:
        return ImageFont.truetype(cfg.video.font, size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pill(draw, xy, text, font, fill):
    x, y = xy
    w = draw.textlength(text, font=font)
    h = font.size
    draw.rounded_rectangle([x, y, x + w + 36, y + h + 22], radius=(h + 22) // 2, fill=fill)
    draw.text((x + 18, y + 9), text, font=font, fill=(255, 255, 255))
    return x + w + 36


def _canvas(cfg):
    W, H = (1080, 1920) if cfg.format == "short" else (1920, 1080)
    return Image.new("RGBA", (W, H), (0, 0, 0, 0)), W, H


def news_card(cfg, article: dict, highlight: str | None, out: Path) -> Path:
    img, W, H = _canvas(cfg)
    d = ImageDraw.Draw(img)
    short = cfg.format == "short"
    pw = W - 120 if short else 1100
    x0 = 60
    y0 = 400 if short else 80
    f_src, f_head, f_hi, f_date = _font(cfg, 34), _font(cfg, 58 if short else 50), _font(cfg, 40), _font(cfg, 28)

    head_lines = _wrap(d, article["title"], f_head, pw - 80)[:4]
    hi_lines = _wrap(d, highlight, f_hi, pw - 110)[:2] if highlight else []
    thumb = None
    if article.get("image"):
        try:
            r = requests.get(article["image"], timeout=10)
            thumb = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:  # noqa: BLE001
            thumb = None
    thumb_h = int(pw * 9 / 16 * 0.55) if thumb else 0

    ph = 40 + 56 + 20 + thumb_h + (20 if thumb else 0) + len(head_lines) * int(f_head.size * 1.2) \
        + (30 + len(hi_lines) * int(f_hi.size * 1.25) + 20 if hi_lines else 0) + 60
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=28, fill=(12, 12, 18, 225))
    d.rectangle([x0, y0 + 28, x0 + 10, y0 + ph - 28], fill=_accent(cfg) + (255,))
    y = y0 + 40
    nx = _pill(d, (x0 + 40, y), article["source"].upper()[:28], f_src, _accent(cfg))
    d.text((nx + 20, y + 12), article.get("date", ""), font=f_date, fill=(190, 190, 200))
    y += 56 + 20
    if thumb:
        tw = pw - 80
        thumb = thumb.resize((tw, int(tw * thumb.height / thumb.width)))
        thumb = thumb.crop((0, 0, tw, thumb_h))
        img.paste(thumb, (x0 + 40, y))
        y += thumb_h + 20
    for line in head_lines:
        d.text((x0 + 40, y), line, font=f_head, fill=(255, 255, 255))
        y += int(f_head.size * 1.2)
    if hi_lines:
        y += 30
        bh = len(hi_lines) * int(f_hi.size * 1.25) + 10
        d.rectangle([x0 + 40, y - 5, x0 + 48, y + bh], fill=(250, 204, 21))
        for line in hi_lines:
            d.text((x0 + 70, y), line, font=f_hi, fill=(250, 204, 21))
            y += int(f_hi.size * 1.25)
    img.save(out)
    return out


def fact_card(cfg, status: str, text: str, out: Path) -> Path:
    img, W, H = _canvas(cfg)
    d = ImageDraw.Draw(img)
    color, label = STATUS_COLORS.get(status, STATUS_COLORS["opinion"])
    short = cfg.format == "short"
    f_lab, f_txt = _font(cfg, 40 if short else 36), _font(cfg, 64 if short else 54)
    pw = W - 120 if short else 980
    lines = _wrap(d, text, f_txt, pw - 80)[:3]
    ph = 40 + 62 + 24 + len(lines) * int(f_txt.size * 1.2) + 40
    x0 = 60
    y0 = 420 if short else 90
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=26, fill=(12, 12, 18, 220))
    _pill(d, (x0 + 40, y0 + 40), label, f_lab, color)
    y = y0 + 40 + 62 + 24
    for line in lines:
        d.text((x0 + 40, y), line, font=f_txt, fill=(255, 255, 255))
        y += int(f_txt.size * 1.2)
    img.save(out)
    return out


def _title_font(cfg, size):
    for f in (cfg.thumbnail.font, cfg.video.font):
        try:
            return ImageFont.truetype(f, size)
        except (OSError, AttributeError, KeyError):
            continue
    return ImageFont.load_default(size)


def header_card(cfg, hook: str, out: Path) -> Path:
    """Shortsin yläpalkki: koukku-otsikko koko klipin ajan (pysäyttää selaajan)."""
    img, W, H = _canvas(cfg)
    d = ImageDraw.Draw(img)
    size = 92
    while size > 50:
        f = _title_font(cfg, size)
        lines = _wrap(d, hook.upper(), f, W - 140)
        if len(lines) <= 2:
            break
        size -= 6
    lines = lines[:2]
    lh = int(size * 1.12)
    y0, ph = 120, 50 + len(lines) * lh
    d.rounded_rectangle([50, y0, W - 50, y0 + ph], radius=30, fill=(255, 255, 255, 240))
    d.rectangle([50, y0 + ph - 12, W - 50, y0 + ph], fill=_accent(cfg) + (255,))
    y = y0 + 22
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, y), line, font=f, fill=(15, 15, 20) if i == 0 else _accent(cfg))
        y += lh
    img.save(out)
    return out


def cta_card(cfg, text: str, handle: str, out: Path) -> Path:
    """Shortsin loppukortti: ohjaa pitkään videoon (nuoli alas 'related video' -linkkiin)."""
    img, W, H = _canvas(cfg)
    d = ImageDraw.Draw(img)
    f, fs = _title_font(cfg, 110), _font(cfg, 46)
    lines = _wrap(d, text.upper(), f, W - 200)[:3]
    lh = 122
    ph = 80 + len(lines) * lh + (70 if handle else 0)
    y0 = (H - ph) // 2 - 120
    d.rounded_rectangle([70, y0, W - 70, y0 + ph], radius=36, fill=_accent(cfg) + (235,))
    y = y0 + 40
    for line in lines:
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, y), line, font=f, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
        y += lh
    if handle:
        tw = d.textlength(handle, font=fs)
        d.text(((W - tw) / 2, y + 10), handle, font=fs, fill=(255, 255, 255))
    cx, ay = W // 2, y0 + ph + 60
    d.polygon([(cx - 70, ay), (cx + 70, ay), (cx, ay + 90)], fill=(250, 204, 21, 255))
    img.save(out)
    return out


def build_cards(cfg, script: dict, articles: list[dict], seg_times: list[dict], work: Path) -> list[dict]:
    """Yhdistää peräkkäiset samaa korttia käyttävät segmentit -> [{png, s, e}]

    Brändin uudet elementit (brand.features): pitkän videon uutiskortti -> {kind: 'panel', card}, lainaus ->
    {kind: 'quote', card} ilman PNG:tä; panels.prepare rakentaa niistä klipit. Muut kortit kuten ennen.
    """
    feat = brand.features(cfg)
    long_video = cfg.format != "short"
    spans = []
    for i, (seg, t) in enumerate(zip(script["segments"], seg_times)):
        card = seg.get("card")
        if not card:
            continue
        if card.get("type") == "quote" and not feat["quotes"]:
            card = {"type": "news", "article": card.get("article"), "highlight": None}
        key = (card.get("type"), card.get("article"), card.get("text"), card.get("highlight"))
        if spans and spans[-1]["key"] == key and abs(spans[-1]["e"] - t["s"]) < 1.0:
            spans[-1]["e"] = t["e"]
            continue
        kind = "quote" if card["type"] == "quote" else \
            "panel" if card["type"] == "news" and long_video and feat["news_panel"] else None
        if kind:
            spans.append({"key": key, "kind": kind, "card": card, "s": t["s"], "e": t["e"]})
            continue
        png = work / f"card_{i:03d}.png"
        if card["type"] == "news":
            news_card(cfg, articles[card["article"]], card.get("highlight"), png)
        elif card["type"] == "cta":
            cta_card(cfg, card.get("text", "FULL VIDEO ON THE CHANNEL"), card.get("handle", ""), png)
        else:
            fact_card(cfg, card.get("status", "opinion"), card.get("text", ""), png)
        spans.append({"key": key, "png": png, "s": t["s"], "e": t["e"]})
    for s in spans:
        s.pop("key")
        s["e"] = max(s["e"], s["s"] + 2.5)
    return spans
