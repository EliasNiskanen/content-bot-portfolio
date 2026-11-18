"""Yhteiset apuvälineet testeille (ilman pytestiä: python tests/run_all.py; pytestillä: python -m pytest -q)."""
import atexit
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import Cfg, load  # noqa: E402

PY = sys.executable


_TMP_DIRS: list[Path] = []


def _cleanup_tmp():
    """Testien väliaikaiskansiot pois ajon lopussa (ennen tätä 600+ kansiota täytti C:-levyn 18.11.2026)."""
    import shutil
    if os.environ.get("BOTTEST_KEEP_TMP"):
        return
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_tmp)


# Testien väliaikaiskansiot botin omaan kansioon E:-levylle (ei C:\...\Temp, joka täyttyi 18.11.2026).
# Myös kaikki muu tempfile-käyttö testiajossa menee tänne.
TMP_ROOT = ROOT / ".tmp_tests"
TMP_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TMP_ROOT)


def tmpdir(prefix: str = "bottest_") -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix, dir=TMP_ROOT))
    _TMP_DIRS.append(d)
    return d


def make_cfg(base: Path | None = None) -> Cfg:
    """Oikea config.yaml, mutta kaikki kirjoitettavat polut väliaikaiskansioon (ei kosketa data/-kansioon)."""
    base = Path(base or tmpdir())
    cfg = load()
    paths = dict(cfg["paths"])
    for k in ("output_dir", "work_dir", "secrets_dir", "logs_dir"):
        paths[k] = str(base / k)
        Path(paths[k]).mkdir(parents=True, exist_ok=True)
    paths["db"] = str(base / "state.db")
    cfg["paths"] = paths
    return cfg


@contextlib.contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def ffmpeg(*args, timeout: float = 120):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *map(str, args)], check=True, timeout=timeout)


def make_video(path: Path, seconds: float, size=(1920, 1080), video="testsrc2", audio="sine", volume_db=0.0):
    """Synteettinen testivideo: video = testsrc2 (liikkuu) | black; audio = sine | silent."""
    w, h = size
    vsrc = f"testsrc2=s={w}x{h}:r=30:d={seconds}" if video == "testsrc2" else f"color=c=black:s={w}x{h}:r=30:d={seconds}"
    asrc = (f"sine=frequency=440:sample_rate=48000:duration={seconds},volume={volume_db}dB" if audio == "sine"
            else f"anullsrc=r=48000:cl=stereo:d={seconds}")
    ffmpeg("-f", "lavfi", "-i", vsrc, "-f", "lavfi", "-i", asrc, "-shortest", "-c:v", "libx264", "-preset",
           "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", path)
    return path


def make_thumbs(out_dir: Path, name: str, n: int = 3, size=(1280, 720)) -> list[Path]:
    from PIL import Image
    out = []
    for i in range(n):
        p = Path(out_dir) / f"{name}_thumb{i + 1}.jpg"
        Image.new("RGB", size, (30 * i, 80, 160)).save(p, quality=85)
        out.append(p)
    return out


ARTICLE_TEXT = ("Rockstar Games confirmed that the map of Leonida is the biggest it has ever built, and that the "
                "world reacts to the player in ways that were not possible before on older consoles.")


def script_doc() -> dict:
    quote = "the map of Leonida is the biggest it has ever built"
    return {"video_title": "GTA 6 Test Video: Everything We Know",
            "segments": [{"text": "This is a clean test segment about the game.", "card": None},
                         {"text": "Rockstar said something big.", "card": {"type": "quote", "article": 0,
                                                                          "text": quote, "highlight": None}}],
            "shorts": [], "articles": [{"title": "Leonida", "source": "Test", "url": "https://example.com/a",
                                        "text": ARTICLE_TEXT, "date": "Nov 17"}]}


def fake_output(cfg, name: str = "20260101-0000_gta-6-test", n_shorts: int = 2, long_s: float = 30.0,
                **video_kw) -> dict:
    """Tallennettu tuotos levylle kuten main.produce jättää sen: mp4 + json + thumbnailit + Shortsit + script.json."""
    out_dir, work = Path(cfg.paths.output_dir), Path(cfg.paths.work_dir) / name
    work.mkdir(parents=True, exist_ok=True)
    out = make_video(out_dir / f"{name}.mp4", long_s, **video_kw)
    thumbs = make_thumbs(out_dir, name)
    shorts = []
    for k in range(n_shorts):
        sp = make_video(out_dir / f"{name}_short{k + 1}.mp4", 15, size=(1080, 1920))
        shorts.append({"file": sp.name, "title": f"GTA 6: test short number {k + 1}", "hook": "Hook?",
                       "duration": 15.0})
    meta = {"title": "GTA 6 Test Video: Everything We Know", "description": "Desc #GTA6 #GTAVI\n",
            "tags": ["gta 6"], "format": "long", "guide_topic": None, "source_video": None, "source_id": None,
            "thumbnails": [t.name for t in thumbs], "shorts": shorts}
    (out_dir / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")
    (work / "script.json").write_text(json.dumps(script_doc()), encoding="utf-8")
    return {"out": out, "work": work, "meta": meta, "thumbs": thumbs,
            "shorts": [out_dir / s["file"] for s in shorts]}


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except FileNotFoundError:
        return False
