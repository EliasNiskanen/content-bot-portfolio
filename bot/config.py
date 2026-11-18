import glob
import logging
import os
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = sys.platform == "win32"
log = logging.getLogger(__name__)


def _expand(obj):
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, list):
        return [_expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    return obj


class Cfg(dict):
    """dict with attribute access: cfg.video.fps"""

    def __getattr__(self, k):
        try:
            v = self[k]
        except KeyError as e:
            raise AttributeError(k) from e
        return Cfg(v) if isinstance(v, dict) else v


def _load_dotenv(p: Path):
    """Minimaalinen .env-lukija (Dockerissa compose hoitaa tämän). Ei ylikirjoita olemassa olevia."""
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


def _ensure_ffmpeg():
    """Windows: winget asentaa ffmpegin, mutta PATH ei välttämättä ole päivittynyt tähän shelliin."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        cands = [str(Path(local) / "Microsoft" / "WinGet" / "Links")] if local else []
        if local:
            pattern = str(Path(local) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "*" / "bin")
            cands += sorted(glob.glob(pattern), reverse=True)
        for d in cands:
            if Path(d, "ffmpeg.exe").is_file() and Path(d, "ffprobe.exe").is_file():
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                break
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        hint = "winget install Gyan.FFmpeg" if IS_WINDOWS else "sudo apt install ffmpeg"
        raise RuntimeError(f"ffmpeg/ffprobe ei löydy PATHista. Asenna se ({hint}) ja avaa uusi terminaali.")


def _font_fallbacks(cfg: Cfg):
    """Configin DejaVu-polut ovat Linuxia varten; Windowsilla käytetään vastaavaa lihavoitua fonttia."""
    video = cfg["video"]
    if not Path(video.get("font", "")).is_file():
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("arialbd.ttf", "segoeuib.ttf"):
            if (windir / name).is_file():
                log.debug("Fonttia %s ei löydy -> %s", video.get("font"), windir / name)
                video["font"] = str(windir / name)
                break
    if IS_WINDOWS and video.get("subtitle_font_name") == "DejaVu Sans":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        user_fonts = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
        if not any(d.is_dir() and any(d.glob("DejaVuSans*.ttf")) for d in (windir, user_fonts)):
            video["subtitle_font_name"] = "Arial"


def load(path: str | None = None) -> Cfg:
    _load_dotenv(ROOT / ".env")
    if "DATA_DIR" not in os.environ:
        use_root = not IS_WINDOWS and Path("/data").is_dir()
        os.environ["DATA_DIR"] = "/data" if use_root else str(ROOT / "data")
    os.environ.setdefault("APP_DIR", str(ROOT))
    os.environ.setdefault("U2NET_HOME", str(Path(os.environ["DATA_DIR"]) / "models"))
    _ensure_ffmpeg()
    path = path or os.environ.get("BOT_CONFIG", str(ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = Cfg(_expand(raw))
    _font_fallbacks(cfg)
    for key in ("output_dir", "work_dir", "secrets_dir"):
        Path(cfg.paths[key]).mkdir(parents=True, exist_ok=True)
    return cfg


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise RuntimeError(f"Ympäristömuuttuja {name} puuttuu (.env)")
    return v
