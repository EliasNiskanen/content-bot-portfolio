"""Siivous onnistuneen ajon jälkeen ja pyynnöstä (python -m bot.main cleanup [--dry-run]).

1) ajon työkansiosta isot välitiedostot pois (piece_*.mp4, bg.mp4, fx/*/clip.mp4, sfx.wav ...);
   script.json, subs.ass, voice.wav, kortit ja lokit jäävät (run --script toimii yhä)
2) työkansioista (data/work/YYYYMMDD-HHMM_*) vain cleanup.keep_work uusinta
3) data/output-tiedostot, jotka ovat vanhempia kuin cleanup.keep_output_days päivää
4) lokit vanhempia kuin cleanup.keep_logs_days päivää

Mitään ei poisteta work_dirin ja output_dirin ulkopuolelta, eikä koskaan suojatuista kansioista (PROTECTED).
"""
import logging
import re
import shutil
import time
from pathlib import Path

from . import logsetup

log = logging.getLogger(__name__)

INTERMEDIATE_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts"}
INTERMEDIATE_NAMES = {"sfx.wav"}
RUN_DIR = re.compile(r"^\d{8}-\d{4}_")
PROTECTED = ("secrets", "promo", "official_images", "music", "brand_music", "brand_assets", "gta6_footage",
             "models", "brand_cache", "clips", "piper", "voice_samples")
RECENT_S = 3 * 3600  # näin tuoreeseen työkansioon ei kosketa (ajo voi olla käynnissä)


def _settings(cfg) -> dict:
    c = cfg.get("cleanup") or {}
    return {"keep_work": int(c.get("keep_work", 10)), "keep_output_days": float(c.get("keep_output_days", 14)),
            "keep_logs_days": int(c.get("keep_logs_days", 30))}


def _protected(cfg) -> tuple[list[Path], set[Path]]:
    data = Path(cfg.paths.db).resolve().parent
    dirs = [data / n for n in PROTECTED] + [Path(cfg.paths.secrets_dir).resolve()]
    dirs += [Path(d).resolve() for d in (cfg.paths.get("clip_dirs") or []) + (cfg.paths.get("still_dirs") or [])]
    for k in ("music_dir",):
        if cfg.paths.get(k):
            dirs.append(Path(cfg.paths[k]).resolve())
    return dirs, {Path(cfg.paths.db).resolve()}


def _is_safe(p: Path, root: Path, cfg) -> bool:
    """p on rootin sisällä (ei root itse), ei suojatussa kansiossa eikä state.db."""
    p, root = p.resolve(), root.resolve()
    if p == root or root not in p.parents:
        return False
    dirs, files = _protected(cfg)
    if p in files:
        return False
    return not any(p == d or d in p.parents or p in d.parents for d in dirs)


def _recent(d: Path, now: float) -> bool:
    """Alle RECENT_S vanha ajo (kansion nimen aikaleima; muuten mtime) -> ajo voi olla yhä käynnissä."""
    try:
        started = time.mktime(time.strptime(d.name[:13], "%Y%m%d-%H%M"))
    except ValueError:
        started = d.stat().st_mtime
    return now - started < RECENT_S


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _mb(n: int) -> str:
    return f"{n / 1e6:.0f} Mt" if n < 1e9 else f"{n / 1e9:.2f} Gt"


def clean_run(cfg, work: Path, dry_run: bool = False) -> tuple[int, int]:
    """Yhden ajon työkansion isot välitiedostot (myös short_N/ ja fx/ -alikansiot)."""
    work = Path(work)
    root = Path(cfg.paths.work_dir)
    if not work.is_dir() or not _is_safe(work, root, cfg):
        return 0, 0
    n = size = 0
    for f in work.rglob("*"):
        if f.is_file() and (f.suffix.lower() in INTERMEDIATE_EXT or f.name in INTERMEDIATE_NAMES):
            size += f.stat().st_size
            n += 1
            if not dry_run:
                f.unlink(missing_ok=True)
    if n:
        log.info("Siivous: %s -> %d välitiedostoa pois (%s)", work.name, n, _mb(size))
    return n, size


def prune(cfg, dry_run: bool = False, keep: Path | None = None) -> dict:
    """Vanhat työkansiot, vanhat output-tiedostot ja lokit. keep = tämän ajon työkansio (ei koskaan poisteta)."""
    s = _settings(cfg)
    verb = "poistettaisiin" if dry_run else "poistettu"
    now = time.time()
    total = {"work": 0, "output": 0, "logs": 0, "bytes": 0}

    work_root = Path(cfg.paths.work_dir)
    runs = sorted((d for d in work_root.iterdir() if d.is_dir() and RUN_DIR.match(d.name)),
                  key=lambda d: d.name, reverse=True) if work_root.is_dir() else []
    for d in runs[max(s["keep_work"], 1):]:
        if keep and d.resolve() == Path(keep).resolve():
            continue
        if _recent(d, now) or not _is_safe(d, work_root, cfg):
            continue
        n = _size(d)
        if not dry_run:
            shutil.rmtree(d, ignore_errors=True)
        total["work"] += 1
        total["bytes"] += n
        log.info("Siivous: työkansio %s %s (%s)", d.name, verb, _mb(n))

    out_root = Path(cfg.paths.output_dir)
    cutoff = now - s["keep_output_days"] * 86400
    for f in sorted(out_root.iterdir()) if out_root.is_dir() else []:
        if f.is_file() and f.stat().st_mtime < cutoff and _is_safe(f, out_root, cfg):
            n = f.stat().st_size
            if not dry_run:
                f.unlink(missing_ok=True)
            total["output"] += 1
            total["bytes"] += n
            log.info("Siivous: output %s %s (%s)", f.name, verb, _mb(n))

    logs = logsetup.prune_logs(logsetup.logs_dir(cfg), s["keep_logs_days"], dry_run=dry_run)
    for p in logs:
        log.info("Siivous: loki %s %s", p.name, verb)
    total["logs"] = len(logs)
    log.info("Siivous valmis: %d työkansiota, %d output-tiedostoa, %d lokia %s, yhteensä %s "
             "(keep_work=%d, keep_output_days=%g, keep_logs_days=%d)", total["work"], total["output"],
             total["logs"], verb, _mb(total["bytes"]), s["keep_work"], s["keep_output_days"], s["keep_logs_days"])
    return total


def after_run(cfg, work: Path, prune_old: bool = True):
    """Onnistuneen ajon jälkeen. Siivousvirhe ei koskaan kaada ajoa."""
    try:
        clean_run(cfg, work)
        if prune_old:
            cleanup_all(cfg, keep=work)
    except Exception:  # noqa: BLE001
        log.exception("Siivous epäonnistui (video on silti valmis)")


def cleanup_all(cfg, dry_run: bool = False, keep: Path | None = None) -> dict:
    """Välitiedostot kaikista vanhoista työkansioista (paitsi alle 3 h tuoreista) + prune."""
    work_root = Path(cfg.paths.work_dir)
    now = time.time()
    for d in sorted(work_root.iterdir()) if work_root.is_dir() else []:
        if d.is_dir() and RUN_DIR.match(d.name) and not _recent(d, now):
            clean_run(cfg, d, dry_run=dry_run)
    return prune(cfg, dry_run=dry_run, keep=keep)
