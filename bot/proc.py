"""Aliprosessit aikarajalla: mikään ffmpeg/ffprobe/piper-kutsu ei voi jäädä jumiin.

run(cmd, timeout) = subprocess.run, mutta aikarajan ylittyessä koko prosessipuu tapetaan, tapahtuma kirjataan ja
nostetaan ProcTimeout (selkeä virhe, jossa komento ja aikaraja). Aikarajat:
  PROBE    60 s   ffprobe ja muut kevyet mittaukset
  PIECE   120 s   yksi taustan pala, kortti, äänitiedoston muunnos
  render_timeout(dur) = max(20 min, 3 x videon kesto) loppurenderöinnille
"""
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

PROBE = 60.0
PIECE = 120.0
ANALYZE_MIN = 300.0     # koko videon läpikäynti (LUFS, QA-analyysi) vähintään


class ProcTimeout(RuntimeError):
    """Aliprosessi ylitti aikarajansa ja tapettiin."""

    def __init__(self, cmd, timeout: float):
        self.cmd = list(map(str, cmd))
        self.timeout = timeout
        super().__init__(f"{Path(self.cmd[0]).name} ylitti aikarajan {timeout:.0f} s ja tapettiin: "
                         f"{_short(self.cmd)}")


def _short(cmd: list[str], n: int = 240) -> str:
    s = " ".join(cmd)
    return s if len(s) <= n else s[:n] + " ..."


def render_timeout(duration: float, factor: float = 3.0, minimum: float = 1200.0) -> float:
    """Loppurenderöinti: max(20 min, 3 x kesto)."""
    return max(float(minimum), float(factor) * max(float(duration or 0.0), 0.0))


def analyze_timeout(duration: float, factor: float = 1.5) -> float:
    """Koko tiedoston analyysi (ebur128, volumedetect, blackdetect): max(5 min, 1,5 x kesto)."""
    return max(ANALYZE_MIN, factor * max(float(duration or 0.0), 0.0))


def _descendants_posix(pid: int) -> list[int]:
    """Kaikki jälkeläiset /proc-puusta (Linux). Muualla tyhjä lista."""
    children: dict[int, list[int]] = {}
    try:
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat", encoding="utf-8", errors="replace") as f:
                    stat = f.read()
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(ppid, []).append(int(d))
    except OSError:
        return []
    out, todo = [], [pid]
    while todo:
        for c in children.get(todo.pop(), []):
            if c not in out:
                out.append(c)
                todo.append(c)
    return out


def kill_tree(p: subprocess.Popen, group: bool = False):
    """Tappaa prosessin ja kaikki sen lapset. group=True: prosessi on käynnistetty omaan istuntoonsa
    (start_new_session) -> POSIXissa tapetaan koko prosessiryhmä."""
    if p.poll() is not None and not group:
        return
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("taskkill epäonnistui: %s", e)
    else:
        kids = _descendants_posix(p.pid)
        if group:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        for pid in reversed(kids):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        log.error("Prosessi %s ei kuollut 30 s:ssa tappamisen jälkeen", p.pid)


def run(cmd, timeout: float, *, check: bool = True, input=None, capture_output: bool = False,
        text: bool | None = None, encoding: str | None = None, errors: str | None = None,
        cwd=None, stdin=None) -> subprocess.CompletedProcess:
    """subprocess.run aikarajalla. Aikaraja ylittyy -> prosessipuu tapetaan, loki, ProcTimeout."""
    cmd = [str(c) for c in cmd]
    log.debug(_short(cmd, 2000))
    if input is not None:
        stdin = subprocess.PIPE
    elif stdin is None:
        stdin = subprocess.DEVNULL
    out = subprocess.PIPE if capture_output else None
    kw = {}
    if text or encoding or errors:
        kw.update(text=True, encoding=encoding or "utf-8", errors=errors or "replace")
    p = subprocess.Popen(cmd, stdin=stdin, stdout=out, stderr=out, cwd=cwd, **kw)
    try:
        so, se = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            p.communicate(timeout=10)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        err = ProcTimeout(cmd, timeout)
        log.error("%s", err)
        raise err from None
    except BaseException:
        kill_tree(p)  # esim. Ctrl+C: ei jätetä ffmpegiä taustalle
        raise
    res = subprocess.CompletedProcess(cmd, p.returncode, so, se)
    if check and p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd, so, se)
    return res


class Watchdog:
    """Popen-prosessille, jota syötetään putken kautta (intro): tappaa puun, jos se on yhä käynnissä aikarajalla."""

    def __init__(self, p: subprocess.Popen, timeout: float, cmd=None):
        self.p, self.timeout, self.fired = p, timeout, False
        self.cmd = [str(c) for c in (cmd or getattr(p, "args", []) or ["?"])]
        self._t = threading.Timer(timeout, self._fire)
        self._t.daemon = True

    def _fire(self):
        if self.p.poll() is None:
            self.fired = True
            log.error("%s", ProcTimeout(self.cmd, self.timeout))
            kill_tree(self.p)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._t.cancel()
        if self.fired:
            raise ProcTimeout(self.cmd, self.timeout)
        return False


def wait_child(p: subprocess.Popen, timeout: float, poll: float = 1.0, on_tick=None) -> int | None:
    """Odottaa lapsiprosessia enintään timeout s. Palauttaa paluukoodin tai None (aikaraja ylittyi, EI tapettu).
    on_tick() kutsutaan joka kierroksella (esim. heartbeat)."""
    deadline = time.monotonic() + timeout
    while True:
        rc = p.poll()
        if rc is not None:
            return rc
        if time.monotonic() >= deadline:
            return None
        if on_tick:
            try:
                on_tick()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(min(poll, max(deadline - time.monotonic(), 0.01)))
