"""Mikään ei jää jumiin: ffmpeg-tyyppisen aliprosessin aikaraja (prosessipuu tapetaan), edge-tts:n aikaraja +
uusinta, TTS-vaiheen vahtikoira, ajastimen lapsiprosessin aikaraja."""
import asyncio
import time
import wave
from pathlib import Path

from helpers import PY, make_cfg, patched, pid_alive, tmpdir

from bot import main, proc, render, tts

# isä kirjoittaa lapsen PID:n tiedostoon ja nukkuu; lapsi nukkuu myös (prosessipuu)
TREE = ("import subprocess, sys, time; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        "open(sys.argv[1], 'w').write(str(c.pid)); time.sleep(120)")


def _wait_file(p: Path, s: float = 15) -> str:
    t = time.time() + s
    while time.time() < t:
        if p.is_file() and p.read_text().strip():
            return p.read_text().strip()
        time.sleep(0.1)
    raise AssertionError(f"{p} ei syntynyt")


def test_proc_run_timeout_kills_tree():
    d = tmpdir()
    pidf = d / "child.pid"
    t0 = time.monotonic()
    try:
        proc.run([PY, "-c", TREE, str(pidf)], 3)
        raise AssertionError("ProcTimeout puuttuu")
    except proc.ProcTimeout as e:
        assert "aikarajan 3 s" in str(e), str(e)
    took = time.monotonic() - t0
    assert took < 30, took
    child = int(_wait_file(pidf))
    time.sleep(0.5)
    assert not pid_alive(child), f"lapsiprosessi {child} jäi henkiin"


def test_render_run_uses_timeout():
    """render._run (taustan palat, ffmpeg) välittää aikarajan: nukkuva valekomento tapetaan."""
    t0 = time.monotonic()
    try:
        render._run([PY, "-c", "import time; time.sleep(60)"], timeout=1)
        raise AssertionError("ProcTimeout puuttuu")
    except proc.ProcTimeout:
        pass
    assert time.monotonic() - t0 < 20
    assert proc.render_timeout(300) == 1200 and proc.render_timeout(900) == 2700


def test_proc_run_ok_and_error():
    r = proc.run([PY, "-c", "print('hei')"], 30, capture_output=True, text=True)
    assert r.stdout.strip() == "hei"
    try:
        proc.run([PY, "-c", "import sys; sys.exit(3)"], 30)
        raise AssertionError("CalledProcessError puuttuu")
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == "CalledProcessError"


def _fake_wav(src, dst):
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(tts.SR)
        w.writeframes(b"\x00\x00" * tts.SR)


def test_edge_tts_timeout_then_retry():
    cfg = make_cfg()
    cfg["tts"] = dict(cfg["tts"], edge_timeout_s=0.5)
    calls = []

    async def fake_stream(text, c, mp3, words):
        calls.append(time.monotonic())
        if len(calls) == 1:
            await asyncio.sleep(30)  # jumi -> wait_for katkaisee
        Path(mp3).write_bytes(b"ID3fake")
        words.append({"w": "hello", "s": 0.0, "e": 0.4})

    out = tmpdir() / "seg.wav"
    with patched(tts, "_edge_stream", fake_stream), patched(tts, "_to_wav", _fake_wav), \
            patched(tts.time, "sleep", lambda s: None):
        t0 = time.monotonic()
        words = tts._edge("hello", out, cfg)
    assert len(calls) == 2, calls
    assert time.monotonic() - t0 < 10
    assert words == [{"w": "hello", "s": 0.0, "e": 0.4}]


def test_tts_stage_watchdog():
    cfg = make_cfg()
    cfg["tts"] = dict(cfg["tts"], engine="edge", stage_timeout_minutes=0.0001)

    async def slow(text, c, mp3, words):
        await asyncio.sleep(0.2)
        Path(mp3).write_bytes(b"x")

    work = tmpdir()
    segs = [{"text": f"segment number {i} text"} for i in range(5)]
    with patched(tts, "_edge_stream", slow), patched(tts, "_to_wav", _fake_wav), \
            patched(tts.time, "sleep", lambda s: None):
        try:
            tts.synthesize(cfg, segs, work, cache_dir=work / "cache")
            raise AssertionError("TTSTimeout puuttuu")
        except tts.TTSTimeout as e:
            assert "TTS-vaihe" in str(e)


def test_scheduler_child_timeout_kills_tree():
    d = tmpdir()
    pidf = d / "child.pid"
    t0 = time.monotonic()
    try:
        main.spawn_job([PY, "-c", TREE, str(pidf)], 3, "testi")
        raise AssertionError("JobTimeout puuttuu")
    except main.JobTimeout as e:
        assert "ylitti aikarajan" in str(e)
    assert time.monotonic() - t0 < 30
    child = int(_wait_file(pidf))
    time.sleep(0.5)
    assert not pid_alive(child), f"ffmpeg-tyyppinen lapsenlapsi {child} jäi henkiin"
    assert main.spawn_job([PY, "-c", "import sys; sys.exit(0)"], 30) == 0
    assert main.spawn_job([PY, "-c", "import sys; sys.exit(3)"], 30) == 3


def test_scheduler_loop_timeout_goes_to_retry():
    """Silmukka: työ aikakatkaistaan -> uusinta samalle päivälle -> onnistuu -> päivän yhteenveto."""
    import datetime as dt

    from bot import scheduler
    cfg = make_cfg()
    cfg["schedule"] = dict(cfg["schedule"], jobs=[{"name": "news", "time": "04:30", "format": "long"}],
                           retry_minutes=1)
    zone = scheduler.tz(cfg)
    now = [dt.datetime(2026, 11, 17, 4, 29, tzinfo=zone)]
    calls, finals, days_done = [], [], []

    def run_job(c, job, day):
        calls.append((job["name"], day))
        if len(calls) == 1:
            main.spawn_job([PY, "-c", "import time; time.sleep(60)"], 1, job["name"])

    def sleep(s):
        now[0] += dt.timedelta(seconds=s)

    scheduler.loop(lambda: cfg, run_job, sleep=sleep, clock=lambda: now[0], max_iterations=12,
                   on_final_failure=lambda *a: finals.append(a), on_day_done=lambda c, d: days_done.append(d))
    assert calls[:2] == [("news", "2026-11-17"), ("news", "2026-11-17")], calls
    assert not finals
    assert days_done == ["2026-11-17"], days_done
