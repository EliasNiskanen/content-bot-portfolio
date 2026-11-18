"""Taustamateriaalin aiheindeksi: jokainen virallinen video pilkotaan kohtauksiin ja jokainen kohtaus ja still-kuva
merkitään aihetunnisteilla (kartta, ilmakuva, ranta, poliisi, autot...). Tausta valitaan sitten sen mukaan, mistä
kertoja puhuu, eikä samaa kohtausta toisteta (render.build_background).

Indeksi: data/footage_index.json (avain = polku data-kansiosta, joten sama indeksi toimii Windowsissa ja Dockerissa).
Tunnisteet antaa Claude kuvista (sama malli ja avain kuin käsikirjoituksessa), kerran per kohtaus; ilman avainta tai
virheessä käytetään tiedostonimiä (esim. "Places/Vice City" -> city). Komento: python -m bot.main index-footage
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from pathlib import Path

from . import proc

log = logging.getLogger(__name__)

INDEX_VERSION = 1
# Kiinteä sanasto: käsikirjoituksen "visual"-kenttä ja kuvien tunnisteet käyttävät samoja sanoja
TAGS = {
    "map": "the map, a minimap or a GPS route on screen",
    "aerial": "aerial or drone view, a wide shot from high above",
    "skyline": "city skyline or downtown towers",
    "city": "city streets, neighbourhoods, buildings",
    "beach": "beach, sand, boardwalk",
    "ocean": "sea, water, islands, keys",
    "boat": "boats, jet skis, yachts",
    "swamp": "swamp, wetlands, airboats, everglades",
    "nature": "forest, mountains, countryside, national park",
    "wildlife": "animals: alligators, birds, dogs, wildlife",
    "driving": "driving shots, cars moving on a road",
    "car": "a car or truck as the subject, garages, car customization",
    "motorcycle": "motorcycles, dirt bikes, ATVs",
    "race": "racing, drag race, race track",
    "police": "police officers, cop cars, arrests, sirens",
    "chase": "a chase or pursuit",
    "action": "shootout or fight (no gore)",
    "weapon": "guns or weapons shown",
    "heist": "robbery, masks, stealing, store holdup",
    "money": "cash, money, ATMs, valuables",
    "store": "shops, stores, gas stations, diners",
    "interior": "indoor scene: homes, motels, bars, offices",
    "nightlife": "clubs, neon, parties at night",
    "crowd": "crowds, festivals, many NPCs",
    "sports": "sports, gym, fitness, basketball",
    "phone": "phones, social media, in-game internet, livestreams",
    "news_tv": "TV news, in-game news broadcast",
    "characters": "the main characters Jason and/or Lucia",
    "people": "other characters or NPCs as the focus",
    "fashion": "clothes, outfits, tattoos, salons",
    "sunset": "sunset or golden hour",
    "night": "night scene",
    "weather": "storm, rain, dramatic weather",
    "gameplay_hud": "real gameplay with HUD elements visible",
    "logo_card": "logo, title card, release date card, ad text (avoid as background)",
    "text_heavy": "large text or subtitles burned into the image (avoid)",
    "dark": "mostly black or very dark (avoid)",
    "suggestive": "sexualised or revealing imagery (never use)",
    "gore": "blood or gore (never use)",
}
AVOID = {"logo_card", "text_heavy", "dark", "suggestive", "gore"}

# Käsikirjoituksen tekstistä tunnisteiksi, jos segmentillä ei ole "visual"-kenttää (vanhat käsikirjoitukset)
KEYWORDS = {
    "map": ["map", "leonida", "world size", "how big", "region", "regions", "miles", "explore", "minimap", "gps"],
    "aerial": ["map", "world", "size", "bigger", "scale", "skyline", "from above", "aerial", "flying", "helicopter",
               "plane"],
    "skyline": ["vice city", "downtown", "skyline"],
    "city": ["vice city", "city", "street", "neighborhood", "neighbourhood", "downtown"],
    "beach": ["beach", "sand", "ocean drive", "boardwalk"],
    "ocean": ["keys", "island", "sea", "ocean", "water"],
    "boat": ["boat", "jet ski", "yacht", "airboat"],
    "swamp": ["swamp", "grassrivers", "everglades", "wetland", "airboat"],
    "nature": ["mount kalaga", "national park", "forest", "countryside", "hunting", "camping"],
    "wildlife": ["alligator", "gator", "animal", "animals", "wildlife", "dog", "flamingo", "bird"],
    "driving": ["drive", "driving", "road", "gas", "fuel", "traffic", "physics"],
    "car": ["car", "cars", "vehicle", "vehicles", "garage", "customs", "customize", "cheetah", "muscle car"],
    "motorcycle": ["motorcycle", "bike", "dirt bike", "atv"],
    "race": ["race", "racing", "drag"],
    "police": ["police", "cop", "cops", "wanted", "arrest", "sirens", "lspd", "law enforcement", "jail", "prison"],
    "chase": ["chase", "pursuit", "escape", "getaway"],
    "action": ["shootout", "combat", "fight", "gunfight", "shooting"],
    "weapon": ["weapon", "weapons", "gun", "guns", "revolver", "rifle", "pistol"],
    "heist": ["heist", "robbery", "rob", "steal", "stealing", "holdup", "mask"],
    "money": ["money", "cash", "price", "prices", "dollar", "dollars", "$", "atm", "economy", "pre-order", "sales",
              "revenue", "billion", "edition"],
    "store": ["store", "shop", "gas station", "diner", "restaurant"],
    "nightlife": ["club", "nightlife", "party", "neon"],
    "crowd": ["crowd", "festival", "npc", "npcs", "people"],
    "sports": ["gym", "fitness", "basketball", "sports", "muscle", "workout"],
    "phone": ["phone", "social media", "internet", "stream", "streamer", "app", "online", "viral"],
    "news_tv": ["news", "tv", "broadcast", "weazel"],
    "characters": ["jason", "lucia", "duo", "protagonist", "protagonists", "story", "couple"],
    "fashion": ["outfit", "outfits", "clothes", "tattoo", "hair", "hairstyle", "salon"],
    "weather": ["weather", "storm", "rain", "hurricane"],
    "night": ["night", "midnight"],
    "sunset": ["sunset"],
}

STILL_HINTS = {  # kansion/tiedoston nimestä, kun Claudea ei ole
    "vice city": ["city", "skyline"], "leonida keys": ["ocean", "beach"], "grassrivers": ["swamp", "nature"],
    "port gellhorn": ["city"], "ambrosia": ["city", "nature"], "mount kalaga": ["nature"], "jason": ["characters"],
    "lucia": ["characters"], "squalo": ["boat", "ocean"], "cheetah": ["car"], "buggy": ["car", "offroad"],
    "car_collection": ["car"], "vehicles": ["car"], "revolvers": ["weapon"], "weapon": ["weapon"],
    "salon": ["fashion"], "style": ["fashion"], "store": ["store"], "customs": ["car"], "stanier": ["car"],
    "ganado": ["car"], "stock_305": ["car", "race"], "electric_fang": ["fashion", "interior"],
}

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
BATCH = 24                 # kuvaa per Claude-kutsu
SCENE_MIN, SCENE_MAX = 1.2, 8.0


def data_dir(cfg) -> Path:
    return Path(cfg.paths.db).parent


def index_path(cfg) -> Path:
    return data_dir(cfg) / "footage_index.json"


def key_of(cfg, p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(data_dir(cfg).resolve()).as_posix()
    except ValueError:
        return Path(p).as_posix()


def path_of(cfg, key: str) -> Path:
    p = Path(key)
    return p if p.is_absolute() else data_dir(cfg) / p


def load(cfg) -> dict:
    p = index_path(cfg)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("version") == INDEX_VERSION:
            return d
    except (OSError, ValueError):
        pass
    return {"version": INDEX_VERSION, "videos": {}, "stills": {}}


def save(cfg, idx: dict):
    p = index_path(cfg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def text_tags(text: str) -> set[str]:
    """Segmentin tekstistä tunnisteet avainsanoilla (sanarajoin)."""
    t = " " + re.sub(r"[^a-z0-9$' -]+", " ", (text or "").lower()) + " "
    out = set()
    for tag, words in KEYWORDS.items():
        for w in words:
            if (f" {w} " in t) if w != "$" else ("$" in t):
                out.add(tag)
                break
    return out


def clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = [tags]
    return [t for t in (str(x).strip().lower() for x in (tags or [])) if t in TAGS]


# ---------------------------------------------------------------- kohtaukset

def scenes(path: Path, duration: float, threshold: float = 10.0) -> list[tuple[float, float]]:
    """Kohtausrajat (ffmpeg scdet) -> [(alku, loppu)]; pitkät pilkotaan SCENE_MAX-paloiksi, lyhyet (< SCENE_MIN) pois."""
    r = proc.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                  f"scale=320:-2,scdet=threshold={threshold}", "-f", "null", "-"],
                 max(120.0, duration * 2), check=False, capture_output=True, text=True)
    cuts = sorted({float(x) for x in re.findall(r"lavfi\.scd\.time: ([\d.]+)", r.stderr or "")})
    edges = [0.0] + [c for c in cuts if 0.2 < c < duration - 0.2] + [duration]
    out = []
    for a, b in zip(edges, edges[1:]):
        a2, b2 = a + 0.08, b - 0.08  # ei leikkauksen reunaruutuja
        while b2 - a2 > SCENE_MAX:
            out.append((round(a2, 2), round(a2 + SCENE_MAX * 0.75, 2)))
            a2 += SCENE_MAX * 0.75
        if b2 - a2 >= SCENE_MIN:
            out.append((round(a2, 2), round(b2, 2)))
    return out


def _frame_jpeg(path: Path, t: float | None, w: int = 384) -> bytes | None:
    """Yksi ruutu (video: hetkellä t, kuva: t=None) pieneksi JPEGiksi Claudelle."""
    if t is None:
        try:
            from PIL import Image
            im = Image.open(path).convert("RGB")
            im.thumbnail((w, w))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            return buf.getvalue()
        except OSError:
            return None
    r = proc.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                  "-vf", f"scale={w}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
                 proc.PROBE, check=False, capture_output=True)
    return r.stdout or None


# ---------------------------------------------------------------- Claude-tunnisteet

PROMPT = """You tag official Grand Theft Auto VI images so a video editor can pick matching background footage.
For EACH numbered image return one object. Use ONLY these tags (2-6 per image, the most important first):
{vocab}
Also give "desc": max 10 words of what is visible. Mark avoid-tags honestly (logo_card, text_heavy, dark,
suggestive, gore): those images will not be used.
Return ONLY a JSON array: [{{"i": 1, "tags": ["..."], "desc": "..."}}, ...]"""


def _tag_batch(cfg, client, images: list[bytes]) -> list[dict]:
    vocab = "\n".join(f"- {k}: {v}" for k, v in TAGS.items())
    content = [{"type": "text", "text": PROMPT.format(vocab=vocab)}]
    for i, img in enumerate(images, 1):
        content.append({"type": "text", "text": f"Image {i}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(img).decode()}})
    msg = client.messages.create(model=cfg.script.model, max_tokens=4000, messages=[{"role": "user", "content": content}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", raw, re.S)
    data = json.loads(m.group(0)) if m else []
    out = [{"tags": [], "desc": ""} for _ in images]
    for d in data:
        try:
            i = int(d.get("i")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(images):
            out[i] = {"tags": clean_tags(d.get("tags")), "desc": str(d.get("desc") or "")[:80]}
    return out


def _hint_tags(key: str) -> list[str]:
    k = key.lower().replace("-", "_")
    out = []
    for hint, tags in STILL_HINTS.items():
        if hint.replace(" ", "_") in k.replace(" ", "_"):
            out += [t for t in tags if t in TAGS and t not in out]
    return out


def _client(cfg):
    from .config import env
    key = env("ANTHROPIC_API_KEY", required=False)
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=180, max_retries=3)


def _landscape(p: Path) -> bool:
    from .render import _is_landscape
    return _is_landscape(p)


def build(cfg, client=None, tag: bool = True, limit_new: int | None = None) -> dict:
    """Päivittää indeksin: uudet/muuttuneet videot pilkotaan ja kaikki merkitsemättömät kohtaukset/kuvat merkitään.
    Jo merkityt ohitetaan (halpa ajaa joka videon alussa). Palauttaa indeksin."""
    from .render import probe_duration
    idx = load(cfg)
    vids = []
    for d in cfg.paths.get("clip_dirs") or []:
        if Path(d).is_dir():
            vids += [f for f in sorted(Path(d).rglob("*")) if f.suffix.lower() in VIDEO_EXT]
    stills = []
    excluded = {str(x).lower() for x in (cfg.paths.get("exclude_images") or [])}
    for d in cfg.paths.get("still_dirs") or []:
        if Path(d).is_dir():
            stills += [f for f in sorted(Path(d).rglob("*")) if f.suffix.lower() in IMG_EXT
                       and f.stem.lower() not in excluded]
    keep_v, keep_s = set(), set()
    for f in vids:
        k = key_of(cfg, f)
        keep_v.add(k)
        size = f.stat().st_size
        old = idx["videos"].get(k)
        if old and old.get("size") == size:
            continue
        if not _landscape(f):
            idx["videos"][k] = {"size": size, "skip": "not landscape", "scenes": []}
            continue
        dur = probe_duration(f)
        sc = scenes(f, dur) if dur > 2 else []
        idx["videos"][k] = {"size": size, "duration": round(dur, 2),
                            "scenes": [{"s": a, "e": b, "tags": None, "desc": ""} for a, b in sc]}
        log.info("Taustaindeksi: %s -> %d kohtausta", f.name, len(sc))
    for f in stills:
        k = key_of(cfg, f)
        keep_s.add(k)
        if k not in idx["stills"]:
            idx["stills"][k] = {"tags": None, "desc": ""}
    idx["videos"] = {k: v for k, v in idx["videos"].items() if k in keep_v}
    idx["stills"] = {k: v for k, v in idx["stills"].items() if k in keep_s}
    save(cfg, idx)

    todo = [("v", k, i) for k, v in idx["videos"].items() for i, s in enumerate(v["scenes"]) if s["tags"] is None]
    todo += [("s", k, None) for k, v in idx["stills"].items() if v["tags"] is None]
    if limit_new is not None:
        todo = todo[:limit_new]
    client = client if client is not None else (_client(cfg) if tag else None)
    done = 0
    for j in range(0, len(todo), BATCH):
        batch = todo[j:j + BATCH]
        imgs, ok = [], []
        for kind, k, i in batch:
            if kind == "v":
                s = idx["videos"][k]["scenes"][i]
                img = _frame_jpeg(path_of(cfg, k), (s["s"] + s["e"]) / 2)
            else:
                img = _frame_jpeg(path_of(cfg, k), None)
            if img:
                imgs.append(img)
                ok.append((kind, k, i))
        if not imgs:
            continue
        res = None
        if client is not None:
            try:
                res = _tag_batch(cfg, client, imgs)
            except Exception as e:  # noqa: BLE001 - nimivihjeet käyttöön, uusi yritys seuraavalla kerralla
                log.warning("Taustaindeksin merkintä epäonnistui (%s) -> nimivihjeet", e)
        for n, (kind, k, i) in enumerate(ok):
            r = res[n] if res else {"tags": _hint_tags(k), "desc": "", "hint": True}
            target = idx["videos"][k]["scenes"][i] if kind == "v" else idx["stills"][k]
            target.update(r)
            if not res:
                target["tags"] = r["tags"]
                target["hint"] = True
        done += len(ok)
        save(cfg, idx)
        log.info("Taustaindeksi: %d/%d merkitty", done, len(todo))
        time.sleep(0.2)
    return idx


def candidates(cfg, idx: dict | None = None) -> list[dict]:
    """Käyttökelpoiset palat: [{"kind": "video"|"still", "path", "s", "e", "tags": set, "key"}], ei AVOID-tunnisteita."""
    idx = idx or load(cfg)
    out = []
    for k, v in (idx.get("videos") or {}).items():
        p = path_of(cfg, k)
        if v.get("skip") or not p.is_file():
            continue
        for i, s in enumerate(v.get("scenes") or []):
            tags = set(s.get("tags") or [])
            if tags & AVOID:
                continue
            out.append({"kind": "video", "path": p, "s": s["s"], "e": s["e"], "tags": tags, "key": f"{k}#{i}"})
    for k, v in (idx.get("stills") or {}).items():
        p = path_of(cfg, k)
        tags = set(v.get("tags") or [])
        if not p.is_file() or tags & AVOID:
            continue
        out.append({"kind": "still", "path": p, "tags": tags, "key": k})
    return out


def wants_from_script(script: dict, seg_times: list[dict], shift: float = 0.0) -> list[tuple[float, float, set]]:
    """[(alku, loppu, tunnisteet)] taustan aikajanalla: segmentin "visual" + avainsanat tekstistä ja osion nimestä."""
    out, section = [], ""
    for seg, t in zip(script.get("segments") or [], seg_times):
        if seg.get("section"):
            section = seg["section"]
        tags = set(clean_tags(seg.get("visual")))
        if not tags:
            tags = text_tags(seg.get("text", "")) | text_tags(section)
        out.append((float(t["s"]) + shift, float(t["e"]) + shift, tags))
    return out
