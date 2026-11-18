"""Voiceover. Tuottaa yhden WAV-tiedoston + segmenttien ja sanojen ajoitukset tekstitystä varten.

Moottorit:
  edge        - Microsoftin neuroääni (ilmainen, luonnollinen, toimii Pillä). Oletus.
  elevenlabs  - paras / inhimillisin, maksullinen, antaa tarkat ajoitukset.
  piper       - täysin offline Pillä, robottimaisempi.
  mock        - hiljaisuus testaukseen.
"""
import asyncio
import base64
import hashlib
import json
import logging
import time
import wave
from pathlib import Path

import requests

from . import proc

log = logging.getLogger(__name__)
SR = 48000
EDGE_TIMEOUT = 60.0     # yksi edge-tts-segmentti (yksi yritys)
PIPER_TIMEOUT = 300.0   # yksi piper-segmentti


def _to_wav(src: Path, dst: Path):
    proc.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(SR),
              "-c:a", "pcm_s16le", str(dst)], proc.PIECE)


def _wav_dur(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def _even_words(text: str, dur: float) -> list[dict]:
    words = text.split()
    total = sum(len(w) + 1 for w in words) or 1
    t, out = 0.0, []
    for w in words:
        d = dur * (len(w) + 1) / total
        out.append({"w": w, "s": t, "e": t + d})
        t += d
    return out


# ---------- engines: each returns list of word dicts (relative to segment start) ----------

class TTSTimeout(RuntimeError):
    """TTS-vaihe (kaikki segmentit yhteensä) ylitti tts.stage_timeout_minutes."""


def _deadline_left(deadline: float | None) -> float:
    return float("inf") if deadline is None else deadline - time.monotonic()


async def _edge_stream(text: str, cfg, mp3: Path, words: list):
    import edge_tts

    com = edge_tts.Communicate(text, cfg.tts.edge_voice, rate=cfg.tts.edge_rate,
                               pitch=cfg.tts.edge_pitch, boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append({"w": ch["text"], "s": s, "e": s + ch["duration"] / 1e7})


def _edge_retryable() -> tuple:
    import edge_tts
    errs = [asyncio.TimeoutError, TimeoutError, ConnectionError, OSError, edge_tts.exceptions.NoAudioReceived,
            edge_tts.exceptions.WebSocketError, edge_tts.exceptions.UnexpectedResponse,
            edge_tts.exceptions.UnknownResponse]
    try:
        import aiohttp
        errs.append(aiohttp.ClientError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(errs)


def _edge(text: str, out_wav: Path, cfg, deadline: float | None = None) -> list[dict]:
    """Yksi segmentti. Jokainen yritys enintään tts.edge_timeout_s (60 s, asyncio.wait_for); palvelu palauttaa
    satunnaisesti NoAudioReceived tai yhteys katkeaa/jumittuu -> uusi yritys (10 kertaa)."""
    mp3 = out_wav.with_suffix(".mp3")
    words: list = []
    per_try = float(cfg.tts.get("edge_timeout_s", EDGE_TIMEOUT) or EDGE_TIMEOUT)
    retryable = _edge_retryable()
    tries = 10
    for attempt in range(tries):
        words.clear()
        left = _deadline_left(deadline)
        if left <= 0:
            raise TTSTimeout("TTS-vaihe ylitti aikarajansa (tts.stage_timeout_minutes)")
        limit = min(per_try, left)
        try:
            asyncio.run(asyncio.wait_for(_edge_stream(text, cfg, mp3, words), timeout=limit))
            if not mp3.is_file() or mp3.stat().st_size == 0:
                raise OSError("edge-tts: tyhjä äänitiedosto")
            break
        except retryable as e:
            if attempt == tries - 1:
                raise
            kind = "aikaraja %.1f s" % limit if isinstance(e, (asyncio.TimeoutError, TimeoutError))                 else ("ei ääntä" if type(e).__name__ == "NoAudioReceived" else f"{type(e).__name__}: {e}")
            log.warning("edge-tts: %s (yritys %d), yritetään uudelleen", kind, attempt + 1)
            time.sleep(min(2 + 2 * attempt, 10, max(_deadline_left(deadline), 0)))
    _to_wav(mp3, out_wav)
    return words or _even_words(text, _wav_dur(out_wav))


def _elevenlabs(text: str, out_wav: Path, cfg, api_key: str) -> list[dict]:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg.tts.elevenlabs_voice_id}/with-timestamps",
        headers={"xi-api-key": api_key}, timeout=120,
        json={"text": text, "model_id": cfg.tts.elevenlabs_model,
              "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35}})
    r.raise_for_status()
    js = r.json()
    mp3 = out_wav.with_suffix(".mp3")
    mp3.write_bytes(base64.b64decode(js["audio_base64"]))
    _to_wav(mp3, out_wav)
    al = js.get("alignment") or {}
    chars, starts, ends = al.get("characters", []), al.get("character_start_times_seconds", []), \
        al.get("character_end_times_seconds", [])
    words, cur, cs = [], "", None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append({"w": cur, "s": cs, "e": e})
            cur, cs = "", None
        else:
            cs = s if cs is None else cs
            cur += c
            last_e = e
    if cur:
        words.append({"w": cur, "s": cs, "e": last_e})
    return words or _even_words(text, _wav_dur(out_wav))


def _piper(text: str, out_wav: Path, cfg) -> list[dict]:
    raw = out_wav.with_suffix(".piper.wav")
    proc.run(["piper", "--model", cfg.tts.piper_model, "--output_file", str(raw)], PIPER_TIMEOUT,
             input=text.encode())
    _to_wav(raw, out_wav)
    return _even_words(text, _wav_dur(out_wav))


def _mock(text: str, out_wav: Path, cfg) -> list[dict]:
    dur = max(1.0, len(text.split()) * 0.36)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"\x00\x00" * int(SR * dur))
    return _even_words(text, dur)


def synthesize(cfg, segments: list[dict], work: Path, eleven_key: str = "",
               cache_dir: Path | None = None) -> dict:
    """Palauttaa {'wav': Path, 'duration': float, 'segments': [{s,e}], 'words': [...]}

    Segmentit välimuistitetaan (cache_dir), joten Shortsit käyttävät pitkän videon ääntä
    uudelleen eikä esim. ElevenLabs-krediittejä kulu kahteen kertaan.
    """
    engine = cfg.tts.engine
    voice_id = {"edge": cfg.tts.edge_voice, "elevenlabs": cfg.tts.elevenlabs_voice_id,
                "piper": cfg.tts.piper_model}.get(engine, "mock")
    cache = Path(cache_dir or work / "tts_cache")
    cache.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    pause = cfg.tts.segment_pause_ms / 1000
    stage_min = float(cfg.tts.get("stage_timeout_minutes", 60) or 60)
    deadline = time.monotonic() + stage_min * 60
    seg_files, timings, all_words, t = [], [], [], 0.0
    for i, seg in enumerate(segments):
        key = hashlib.sha1(f"{engine}|{voice_id}|{seg['text']}".encode()).hexdigest()[:16]
        wav, wj = cache / f"{key}.wav", cache / f"{key}.json"
        if wav.exists() and wj.exists():
            words = json.loads(wj.read_text(encoding="utf-8"))
        else:
            if time.monotonic() > deadline:
                raise TTSTimeout(f"TTS-vaihe ylitti {stage_min:.0f} min ({i}/{len(segments)} segmenttiä valmiina)")
            if engine == "edge":
                words = _edge(seg["text"], wav, cfg, deadline)
            elif engine == "elevenlabs":
                words = _elevenlabs(seg["text"], wav, cfg, eleven_key)
            elif engine == "piper":
                words = _piper(seg["text"], wav, cfg)
            else:
                words = _mock(seg["text"], wav, cfg)
            wj.write_text(json.dumps(words), encoding="utf-8")
        d = _wav_dur(wav)
        timings.append({"s": t, "e": t + d})
        all_words += [{"w": w["w"], "s": w["s"] + t, "e": w["e"] + t} for w in words]
        seg_files.append(wav)
        t += d + pause
        log.info("TTS %d/%d (%.1fs)", i + 1, len(segments), d)

    out = work / "voice.wav"
    silence = b"\x00\x00" * int(SR * pause)
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(1); dst.setsampwidth(2); dst.setframerate(SR)
        for f in seg_files:
            with wave.open(str(f)) as src:
                dst.writeframes(src.readframes(src.getnframes()))
            dst.writeframes(silence)
    return {"wav": out, "duration": _wav_dur(out), "segments": timings, "words": all_words}
