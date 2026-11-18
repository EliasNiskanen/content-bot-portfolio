"""Breakdown-vahti ja -muoto valeilla: uploads-soittolistan suodatus (ei search.list), vahdin työ ajastimessa heti,
kertojan hetkiviittaukset taustaan ja kuvat käsikirjoituskehotteeseen."""
import datetime as dt
from zoneinfo import ZoneInfo

from helpers import make_cfg, make_thumbs, tmpdir

from bot import breakdown, scheduler, scriptwriter

HKI = ZoneInfo("Europe/Helsinki")


def _api(calls):
    def api(path, params):
        calls.append(path)
        assert path != "search", "vahti ei saa käyttää search.list-hakua"
        if path == "channels":
            assert params["forHandle"] == "RockstarGames"
            return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
        if path == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": x}} for x in ("NEW", "OLD", "RDR", "SHORT", "SEEN")]}
        mk = lambda vid, title, pub, dur: {"id": vid, "snippet": {"title": title, "description": "d",  # noqa: E731
                                                                  "publishedAt": pub}, "contentDetails": {"duration": dur}}
        return {"items": [mk("NEW", "Grand Theft Auto VI: Launch Day Trailer", "2026-11-18T12:00:00Z", "PT2M"),
                          mk("OLD", "Grand Theft Auto VI Trailer 2", "2025-05-06T12:00:00Z", "PT2M"),
                          mk("RDR", "Red Dead Online", "2026-11-18T12:00:00Z", "PT2M"),
                          mk("SHORT", "GTA VI teaser", "2026-11-18T12:00:00Z", "PT20S"),
                          mk("SEEN", "GTA VI Gameplay", "2026-11-18T13:00:00Z", "PT3M")]}
    return api


def test_find_new_uses_uploads_playlist_and_filters():
    calls = []
    got = breakdown.find_new(make_cfg(), "KEY", seen=lambda v: v == "SEEN",
                             watch_from="2026-11-18T00:00:00+02:00", api=_api(calls))
    assert [v["id"] for v in got] == ["NEW"], got
    assert calls == ["channels", "playlistItems", "videos"]


def test_watch_hook_runs_instant_job_once():
    cfg = make_cfg()
    cfg["schedule"] = {"timezone": "Europe/Helsinki", "jobs": [{"name": "news", "time": "04:30", "format": "long"}]}
    now = [dt.datetime(2026, 11, 18, 12, 0, tzinfo=HKI)]
    fired, calls = [], []

    def watch(c, t):
        if fired:
            return []
        fired.append(t)
        return [({"name": "breakdown", "format": "breakdown", "once": True}, "2026-11-18")]

    def sleep(s):
        now[0] += dt.timedelta(seconds=max(s, 60))

    scheduler.loop(lambda: cfg, lambda c, job, day: calls.append((job["name"], day)), sleep=sleep,
                   clock=lambda: now[0], max_iterations=5, watch=watch, watch_every_s=0)
    assert calls == [("breakdown", "2026-11-18")], calls


def test_moments_and_prompt_images():
    script = {"segments": [{"text": "a"}, {"text": "b", "moment": "0:42"}, {"text": "c", "moment": "x"}]}
    assert breakdown.moments(script, [{"s": 0.0}, {"s": 5.5}, {"s": 9.0}]) == [(5.5, 42.0)]
    d = tmpdir()
    frames = [{"t": 1.0, "stamp": "0:01", "path": str(p)} for p in make_thumbs(d, "f", n=2)]
    blocks = scriptwriter.user_content("PROMPT", frames)
    assert blocks[0] == {"type": "text", "text": "PROMPT"}
    assert [b["type"] for b in blocks[1:]] == ["text", "image", "text", "image"]
    assert blocks[2]["source"]["media_type"] == "image/jpeg" and blocks[1]["text"] == "Frame at 0:01:"
    assert scriptwriter.user_content("P", []) == "P"
    cfg = make_cfg()
    cfg["format"] = "breakdown"
    prompt = scriptwriter.build_prompt(cfg, None, [{"title": "T", "source": "Rockstar Games (YouTube)", "url": "u",
                                                    "date": "", "text": "x"}], [],
                                       breakdown={"info": {"title": "GTA VI Trailer 3", "url": "u"}})
    assert "BREAKDOWN video" in prompt and '"moment"' in prompt and "our read" in prompt
    v = scriptwriter._validate({"video_title": "GTA 6 NEW Trailer Breakdown", "segments": [
        {"text": "hook"}, {"text": "x", "moment": "1:05", "section": "The Shot"}]}, 1, None)
    assert v["segments"][1]["moment"] == "1:05"
