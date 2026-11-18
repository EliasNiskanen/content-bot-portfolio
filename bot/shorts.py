"""Shortsit pitkän videon rinnalle (catfish-shorts: Hook-Progression-Climax).

Uusi muoto: Claude kirjoittaa jokaiselle Shortsille oman 60-120 sanan minikäsikirjoituksen
({"hook", "title", "segments": [{"text", "card"}]}): koukku -> eteneminen -> vastaus LOPUSSA -> yksi rivi,
joka ohjaa koko videoon. Viimeinen (lyhyt) ohjausrivi saa "FULL VIDEO ON THE CHANNEL" -kortin; jos sitä ei ole,
lisätään configin cta_voice. Vanha muoto (pitkän videon segmenttien indeksit) toimii yhä.
Jokainen renderöidään pystymuotoon (ei croppaus): yläpalkissa koukku, taustalla musiikkimatto satunnaisesta
kohdasta. Ääni jaetun TTS-välimuistin kautta.
"""
import copy
import logging
from pathlib import Path

from . import cards as cardmod
from . import panels, render, tts

log = logging.getLogger(__name__)


CTA_MAX_WORDS = 20  # tätä pidempi viimeinen segmentti on sisältöä (vastaus), ei ohjausrivi


def _pick(script: dict, max_n: int) -> list[dict]:
    n_seg = len(script["segments"])
    out = []
    for sh in script.get("shorts", [])[:max_n]:
        if sh.get("script"):  # oma HPC-käsikirjoitus
            out.append({"script": sh["script"], "hook": sh.get("hook") or script.get("thumbnail_text", ""),
                        "title": sh.get("title") or script["video_title"]})
            continue
        idx = [i for i in sh.get("segments", []) if isinstance(i, int) and 0 <= i < n_seg]
        if not idx:
            continue
        idx = list(range(min(idx), max(idx) + 1))  # pakotetaan yhtenäiseksi
        out.append({"segments": idx, "hook": sh.get("hook") or script.get("thumbnail_text", ""),
                    "title": sh.get("title") or script["video_title"]})
    return out


def make_shorts(cfg, script: dict, articles: list[dict], long_work: Path, out_dir: Path, name: str,
                eleven_key: str = "") -> list[dict]:
    sc = cfg.shorts
    if not sc.enabled:
        return []
    scfg = copy.deepcopy(cfg)
    scfg["format"] = "short"
    results = []
    for k, sh in enumerate(_pick(script, sc.count)):
        cta_card = {"type": "cta", "text": sc.cta_text, "handle": sc.channel_handle}
        own = "script" in sh
        if own:
            segs = [dict(x) for x in sh["script"]]
            if len(segs) > 1 and len(segs[-1]["text"].split()) <= CTA_MAX_WORDS:
                cta = {"text": segs.pop()["text"], "card": cta_card}  # käsikirjoituksen oma ohjausrivi
            else:
                cta = {"text": sc.cta_voice, "card": cta_card}
        else:
            segs = [script["segments"][i] for i in sh["segments"]]
            cta = {"text": sc.cta_voice, "card": cta_card}
        work = long_work / f"short_{k + 1}"
        while True:
            voice = tts.synthesize(scfg, segs + [cta], work, eleven_key, cache_dir=long_work.parent / "tts_cache")
            if voice["duration"] <= sc.max_seconds or len(segs) == 1:
                break
            # liian pitkä: omassa käsikirjoituksessa vastaus on lopussa -> pudotetaan keskeltä (koukku ja vastaus
            # säilyvät), vanhassa muodossa viimeinen segmentti kuten ennen
            drop = len(segs) - 2 if own and len(segs) > 2 else len(segs) - 1
            log.warning("Short %d liian pitkä (%.0f s) -> pudotetaan segmentti %d", k + 1, voice["duration"], drop)
            segs = segs[:drop] + segs[drop + 1:]
        dur = voice["duration"]
        spans = cardmod.build_cards(scfg, {"segments": segs + [cta]}, articles, voice["segments"], work)
        header = cardmod.header_card(scfg, sh["hook"], work / "header.png")
        cta_start = voice["segments"][-1]["s"]
        spans = [{"png": header, "s": 0.0, "e": cta_start}] + spans
        bg = render.build_background(scfg, dur, work)
        subs = render.write_subtitles(scfg, voice["words"], work) if cfg.video.subtitles else None
        out = out_dir / f"{name}_short{k + 1}.mp4"
        # lainauskortit omiksi klipeikseen; koukkupalkki (PNG) piirretään niiden päälle
        spans, sfx = panels.prepare(scfg, {"segments": segs + [cta]}, articles, spans, voice["segments"], bg,
                                    None, work, total=dur)
        render.render_final(scfg, bg, voice["wav"], dur, spans, subs, out, work, sfx=sfx)
        results.append({"path": out, "title": sh["title"], "hook": sh["hook"], "duration": dur,
                        "segments": sh.get("segments"), "words": sum(len(x["text"].split()) for x in segs + [cta])})
        log.info("Short %d valmis (%.0f s): %s", k + 1, dur, sh["title"])
    return results
