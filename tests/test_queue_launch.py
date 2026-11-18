"""Latausrajan jono (ajastetut ja käsin ajetut), kertaluonteiset/ohitetut työt ajastimessa ja julkaisupäivän
tutkimus (launch_streams) valeilla: ei YouTubea, ei yt-dlp:tä, ei Claudea."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import fake_output, make_cfg, patched

from bot import alerts, main, qa, research, scheduler, uploader
from bot.state import State
from test_jobs import FakeUploads, _no_render, _ok_qa

DAY = "2026-11-17"
HKI = ZoneInfo("Europe/Helsinki")


class LimitUploads(FakeUploads):
    """Ensimmäinen lataus osuu YouTuben kanavan latausrajaan."""

    def __init__(self):
        super().__init__()
        self.limited = True

    def upload(self, cfg, video, meta, thumb, publish_at=None, on_uploaded=None, **kw):
        if self.limited:
            self.limited = False
            raise RuntimeError('<HttpError 400 "uploadLimitExceeded: The user has exceeded the number of videos">')
        return super().upload(cfg, video, meta, thumb, publish_at, on_uploaded, **kw)


def test_upload_limit_queues_job_then_queue_finishes_it_once():
    cfg = make_cfg()
    fo = fake_output(cfg, n_shorts=1)
    st = State(cfg.paths.db)
    st.job_begin("news", DAY)
    st.job_update("news", DAY, stage="rendered", output=str(fo["out"]), work_dir=str(fo["work"]))
    fake = LimitUploads()
    sent = []
    qa_calls = []

    def counting_qa(*a, **k):
        qa_calls.append(1)
        return _ok_qa(*a, **k)

    with patched(uploader, "upload", fake.upload), patched(uploader, "find_recent_upload", fake.find), \
            patched(main, "produce", _no_render), patched(main, "run_qa", counting_qa), \
            patched(alerts, "send", lambda text, level=0: sent.append(text)):
        assert main.run_job(cfg, "news", DAY, preflight_fn=lambda c, youtube=True: []) == main.EXIT_OK
        row = st.job("news", DAY)
        assert row["stage"] == "upload_queued" and fake.calls == [], row
        q = main._load_pending(cfg)
        assert q and q[0]["job"] == ["news", DAY], q
        assert any("latausraja" in s for s in sent), sent
        assert len(qa_calls) == 1
        # tunnin välein: jono purkautuu, QA ei aja uudelleen (hyväksytty jo), sama video vain kerran
        assert main.upload_pending(cfg) == 0
        assert [c["file"] for c in fake.calls] == [fo["out"].name, fo["shorts"][0].name], fake.calls
        assert len(qa_calls) == 1
        assert st.job("news", DAY)["stage"] == "done"
        assert main.upload_pending(cfg) == 0 and len(fake.calls) == 2


def test_manual_queue_entry_with_windows_paths_is_mapped_and_finished():
    cfg = make_cfg()
    fo = fake_output(cfg, n_shorts=1)
    win = "E:\\gta6-bot\\data\\output\\"
    short = fo["shorts"][0]
    entry = {"video": win + fo["out"].name, "meta": win + fo["out"].with_suffix(".json").name, "thumb": None,
             "publish_at": None, "long_id": "LONG9", "done_shorts": [],
             "shorts": [{"path": win + short.name, "title": "GTA 6: queued short test", "hook": "Hook?"}]}
    main._save_pending(cfg, [entry])
    fake = FakeUploads()
    with patched(uploader, "upload", fake.upload), patched(main, "run_qa", _ok_qa):
        assert main.upload_pending(cfg) == 0
    assert [c["file"] for c in fake.calls] == [short.name], fake.calls
    assert "youtu.be/LONG9" in fake.calls[0]["description"]


def _sched_cfg():
    cfg = make_cfg()
    cfg["schedule"] = {"timezone": "Europe/Helsinki",
                       "jobs": [{"name": "news", "time": "04:30", "format": "long"},
                                {"name": "guide", "time": "06:30", "format": "guide"}],
                       "once": [{"name": "launchday", "date": "2026-11-18", "time": "15:00", "format": "guide",
                                 "topic": "GTA 6 First Things To Do", "research": "launch_streams",
                                 "privacy": "public"}],
                       "skip": [{"name": "guide", "date": "2026-11-18"}]}
    return cfg


def test_once_and_skip_in_scheduler():
    cfg = _sched_cfg()
    j = scheduler.find_job(cfg, "launchday", "2026-11-18")
    assert j["privacy"] == "public" and j["research"] == "launch_streams"
    guide = scheduler.find_job(cfg, "guide")
    t = scheduler.next_job_run(cfg, guide, dt.datetime(2026, 11, 17, 20, 0, tzinfo=HKI), HKI)
    assert t == dt.datetime(2026, 11, 19, 6, 30, tzinfo=HKI), t
    text = scheduler.dry_run(cfg, now=dt.datetime(2026, 11, 17, 20, 0, tzinfo=HKI))
    assert "[launchday]" in text and "privacy=public" in text and "OHITETAAN" in text, text
    assert text.index("[news]") < text.index("[launchday]")

    calls = []
    now = [dt.datetime(2026, 11, 18, 14, 59, tzinfo=HKI)]

    def sleep(s):
        now[0] = now[0] + dt.timedelta(seconds=max(s, 60))

    scheduler.loop(lambda: cfg, lambda c, job, day: calls.append((job["name"], day)), sleep=sleep,
                   clock=lambda: now[0], max_iterations=6, once_seen=lambda c, job, day: False)
    assert calls == [("launchday", "2026-11-18")], calls
    # käynnistys kesken päivän: jo aloitettua kertaluonteista työtä ei aloiteta uudelleen
    calls.clear()
    now[0] = dt.datetime(2026, 11, 18, 16, 0, tzinfo=HKI)
    scheduler.loop(lambda: cfg, lambda c, job, day: calls.append(job["name"]), sleep=sleep,
                   clock=lambda: now[0], max_iterations=3, once_seen=lambda c, job, day: True)
    assert calls == []


def test_job_privacy_override_only_for_once_job():
    cfg = _sched_cfg()
    seen = {}

    def fake_pre(c, youtube=True):
        seen["privacy"] = c.upload.privacy
        return ["stop here"]

    with patched(alerts, "send", lambda *a, **k: None):
        assert main.run_job(cfg, "launchday", "2026-11-18", preflight_fn=fake_pre) == main.EXIT_PREFLIGHT
    assert seen["privacy"] == "public"
    assert cfg.upload.privacy == "public"  # työn oma (kopioitu) config
    # tavallinen työ käyttää upload.privacy-asetusta (ei launchdayn ohitusta)
    base = _sched_cfg()
    base["upload"] = dict(base["upload"], privacy="private")
    with patched(alerts, "send", lambda *a, **k: None):
        assert main.run_job(base, "news", "2026-11-18", preflight_fn=fake_pre) == main.EXIT_PREFLIGHT
    assert seen["privacy"] == "private"


# ---------------------------------------------------------------- launch_streams

def _api(calls):
    def api(path, params):
        calls.append((path, dict(params)))
        if path == "search":
            return {"items": [{"id": {"videoId": v}} for v in ("A", "B", "C", "D", "E")]}
        mk = lambda vid, ch, views, dur, title="GTA 6 first hour gameplay": {  # noqa: E731
            "id": vid, "snippet": {"title": title, "channelId": ch, "channelTitle": f"Creator {ch}",
                                   "description": "", "liveBroadcastContent": "none"},
            "statistics": {"viewCount": str(views)}, "contentDetails": {"duration": dur}}
        return {"items": [mk("A", "c1", 500, "PT2H"), mk("B", "c1", 900, "PT40M"), mk("C", "own", 99999, "PT20M"),
                          mk("D", "c3", 700, "PT45S"), mk("E", "c4", 300, "PT15M", "GTA 6 tips #shorts")]}
    return api


def test_launch_search_filters_and_ranks():
    cfg = make_cfg()
    cfg["channel"] = dict(cfg["channel"], own_channel_id="own")
    calls = []
    vids = research.launch_search(cfg, "KEY", "2026-11-18T11:00:00Z", queries=["GTA 6 live"], api=_api(calls))
    assert [v["id"] for v in vids] == ["B"], vids   # c1 kerran (katsotuin), oma kanava/Short/lyhyt pois
    searches = [p for path, p in calls if path == "search"]
    assert len(searches) == 2 and searches[1]["eventType"] == "completed"
    assert all(p["publishedAfter"] == "2026-11-18T11:00:00Z" and p["order"] == "viewCount"
               and p["relevanceLanguage"] == "en" for p in searches)


VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000 align:start position:0%
okay<00:00:00.500><c> so</c><00:00:01.000><c> first</c>

00:00:02.000 --> 00:00:02.010 align:start position:0%
okay so first

00:00:02.010 --> 00:00:04.000 align:start position:0%
okay so first
make sure you hit the ATM

00:00:04.000 --> 00:00:06.000
make sure you hit the ATM
[Music]
"""


def test_vtt_cleaning_windows_and_quote_verification():
    text = research.vtt_to_text(VTT)
    assert text == "okay so first make sure you hit the ATM", text
    long = " ".join(["filler"] * 3000 + ["make", "sure", "you", "save", "money", "early"] + ["filler"] * 3000)
    kept = research.tip_windows(long, max_words=200)
    assert "make sure you save money early" in kept and len(kept.split()) <= 201
    arts = research.launch_articles({"creators": [{"creator": "Creator X", "title": "GTA 6 first hour",
                                                   "url": "https://www.youtube.com/watch?v=B", "text": text}]})
    script = {"segments": [{"text": "x", "card": {"type": "quote", "article": 0,
                                                  "text": "make sure you hit the ATM"}}]}
    rep = qa.Report()
    qa.check_script(rep, make_cfg(), script, arts, "short", qa.settings(make_cfg()), True)
    assert rep.ok, rep.text()
    from bot import scriptwriter
    assert "TRANSCRIPT | creator: Creator X" in scriptwriter._articles_block(arts)


def test_fetch_transcript_retries_on_429(tmp=None):
    from helpers import tmpdir
    d = tmpdir()
    n = {"runs": 0}
    sleeps = []

    class R:
        def __init__(self, err):
            self.stderr, self.stdout, self.returncode = err, "", 1

    def run(cmd):
        n["runs"] += 1
        assert "--skip-download" in cmd and "en,en-orig" in cmd
        if n["runs"] == 1:
            return R("ERROR: Unable to download video subtitles: HTTP Error 429: Too Many Requests")
        (d / "VID.en.vtt").write_text(VTT, encoding="utf-8")
        return R("")

    text = research.fetch_transcript("https://www.youtube.com/watch?v=VID", "VID", d, run=run, sleep=sleeps.append)
    assert text.startswith("okay so first") and n["runs"] == 2 and sleeps == [30]


def test_launch_research_waits_and_retries_until_enough_creators():
    cfg = make_cfg()
    results = [{"creators": [1]}, {"creators": [1, 2]}, {"creators": [1, 2, 3]}]
    clock = [dt.datetime(2026, 11, 18, 15, 5, tzinfo=HKI)]
    sleeps = []

    def run(c, key, **kw):
        return dict(results.pop(0))

    def sleep(s):
        sleeps.append(s)
        clock[0] += dt.timedelta(seconds=s)

    res = research.launch_research(cfg, "KEY", retry_at=["15:30", "16:00"], now_fn=lambda: clock[0], sleep=sleep,
                                   run=run)
    assert len(res["creators"]) == 3 and res["early"] is False and sleeps == [25 * 60, 30 * 60], sleeps
    results[:] = [{"creators": []}]
    res = research.launch_research(cfg, "KEY", retry_at=["15:30"], now_fn=lambda: clock[0], sleep=sleep, run=run)
    assert res["early"] is True  # 15:30 meni jo -> ei odotusta, varasuunnitelma


def test_rockstar_footage_downloads_new_gta_vi_videos_only():
    from helpers import tmpdir
    d = tmpdir()
    (d / "rockstar_OLD_Grand_Theft_Auto_VI_Trailer_1.mp4").write_bytes(b"x")

    def api(path, params):
        if path == "channels":
            assert params["forHandle"] == "RockstarGames"
            return {"items": [{"id": "UCR"}]}
        assert params["channelId"] == "UCR" and params["publishedAfter"] == "2026-11-18T00:00:00Z"
        return {"items": [{"id": {"videoId": "NEW"}, "snippet": {"title": "Grand Theft Auto VI Launch Trailer"}},
                          {"id": {"videoId": "OLD"}, "snippet": {"title": "Grand Theft Auto VI Trailer 1"}},
                          {"id": {"videoId": "RDR"}, "snippet": {"title": "Red Dead Online update"}}]}
    cmds = []

    def run(cmd):
        cmds.append(cmd)
        (d / "rockstar_NEW_Grand_Theft_Auto_VI_Launch_Trailer.mp4").write_bytes(b"x")

    new = research.rockstar_footage(make_cfg(), "KEY", api=api, run=run, out_dir=d)
    assert [p.name for p in new] == ["rockstar_NEW_Grand_Theft_Auto_VI_Launch_Trailer.mp4"]
    assert len(cmds) == 1 and "--restrict-filenames" in cmds[0]


def test_picture_failure_reuses_script():
    assert main._picture_failure("video: mustaa < 3 s (4 s - loppu-20 s) (5.6 s)")
    assert main._picture_failure("video: pysähtynyt kuva <= 4 s (4 s - loppu-20 s) (6.0 s)")
    assert not main._picture_failure("video: otsikko alkaa 'GTA 6' (Hello)")
    assert not main._picture_failure("short 1: mustaa < 3 s (x) (4 s)")
    assert not main._picture_failure("käsikirjoitus: vähintään 2200 sanaa (1900 sanaa)")
