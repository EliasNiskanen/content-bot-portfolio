"""Lokit: konsoli + päiväkohtainen tiedosto ${DATA_DIR}/logs/bot-YYYY-MM-DD.log (vanhat pois keep_days jälkeen),
ja schedulerin heartbeat-tiedosto Dockerin healthcheckille."""
import datetime as dt
import logging
import threading
import time
from pathlib import Path

FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
log = logging.getLogger(__name__)


def logs_dir(cfg) -> Path:
    return Path(cfg.paths.get("logs_dir") or Path(cfg.paths.db).parent / "logs")


def prune_logs(d: Path, keep_days: int, dry_run: bool = False) -> list[Path]:
    """Poistaa bot-YYYY-MM-DD.log -tiedostot, jotka ovat vanhempia kuin keep_days päivää (nimen päiväys)."""
    cutoff = dt.date.today() - dt.timedelta(days=max(int(keep_days), 1))
    gone = []
    for p in sorted(d.glob("bot-*.log")):
        try:
            day = dt.date.fromisoformat(p.stem[4:])
        except ValueError:
            continue
        if day < cutoff:
            if not dry_run:
                p.unlink(missing_ok=True)
            gone.append(p)
    return gone


class DailyFileHandler(logging.Handler):
    """Kirjoittaa (append) tiedostoon bot-<tämän päivän päiväys>.log; päivän vaihtuessa avaa uuden tiedoston."""

    def __init__(self, directory: Path, keep_days: int = 30):
        super().__init__()
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep_days = keep_days
        self.day = None
        self.stream = None

    def _roll(self, day: dt.date):
        if self.stream:
            self.stream.close()
        self.day = day
        self.stream = open(self.dir / f"bot-{day.isoformat()}.log", "a", encoding="utf-8")
        try:
            prune_logs(self.dir, self.keep_days)
        except OSError:
            pass

    def emit(self, record):
        try:
            msg = self.format(record)
            with self.lock:
                day = dt.date.today()
                if day != self.day or self.stream is None:
                    self._roll(day)
                self.stream.write(msg + "\n")
                self.stream.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def close(self):
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = None
        super().close()


def setup(verbose: bool = False):
    """Konsoliloki heti (ennen configin latausta)."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=FMT)


def add_file_log(cfg) -> Path:
    keep = int((cfg.get("cleanup") or {}).get("keep_logs_days", 30) or 30)
    d = logs_dir(cfg)
    h = DailyFileHandler(d, keep)
    h.setFormatter(logging.Formatter(FMT))
    logging.getLogger().addHandler(h)
    return d


class Heartbeat:
    """Koskettaa tiedostoa logs/heartbeat minuutin välein omassa säikeessä (renderöinti varaa pääsäikeen).
    Jos yksi ajo on kestänyt yli max_job_hours (jumi), heartbeat lakkaa -> Docker näyttää 'unhealthy'."""

    def __init__(self, path: Path, interval: float = 60.0, max_job_hours: float = 4.0):
        self.path = Path(path)
        self.interval = interval
        self.max_job_s = max_job_hours * 3600
        self.job_started: float | None = None
        self._stop = threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(self):
        if self.job_started and time.time() - self.job_started > self.max_job_s:
            return
        try:
            self.path.write_text(dt.datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
        except OSError as e:
            log.debug("Heartbeat epäonnistui: %s", e)

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.beat()

    def start(self):
        self.beat()
        threading.Thread(target=self._loop, name="heartbeat", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
