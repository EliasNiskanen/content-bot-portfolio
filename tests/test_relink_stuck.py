"""Shortsien linkit uusimpaan pitkään videoon ja YouTuben käsittelyyn jumiin jääneen videon korjaus (valeasiakas)."""
import json
import time

from helpers import make_cfg, patched, tmpdir

from bot import alerts, main, uploader
from bot.state import State


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeYT:
    """videos: {id: {"description", "title", "processing", "duration", "privacy"}}; uploads uusin ensin."""
    def __init__(self, videos: dict, order: list[str]):
        self.v, self.order, self.updates, self.deleted = videos, order, [], []

    def channels(self):
        return self

    def playlistItems(self):
        return self

    def videos(self):
        return self

    # channels().list / playlistItems().list / videos().list jakavat nimen: erotetaan parametreista
    def list(self, part, mine=None, playlistId=None, maxResults=None, pageToken=None, id=None):
        if mine:
            return _Exec(lambda: {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]})
        if playlistId:
            return _Exec(lambda: {"items": [{"contentDetails": {"videoId": i}} for i in self.order]})
        ids = [i for i in id.split(",") if i in self.v]
        return _Exec(lambda: {"items": [
            {"id": i, "snippet": {"title": self.v[i].get("title", i), "description": self.v[i].get("description", ""),
                                  "categoryId": "20", "tags": ["a"]},
             "status": {"privacyStatus": self.v[i].get("privacy", "public"), "uploadStatus": "uploaded"},
             "processingDetails": {"processingStatus": self.v[i].get("processing", "succeeded")},
             "contentDetails": {"duration": self.v[i].get("duration", "PT14M")}} for i in ids]})

    def update(self, part, body):
        def run():
            self.updates.append(body["id"])
            self.v[body["id"]]["description"] = body["snippet"]["description"]
            return body
        return _Exec(run)

    def delete(self, id):
        def run():
            self.deleted.append(id)
            self.v.pop(id, None)
            self.order.remove(id)
        return _Exec(run)


def _short(long_id):
    return {"title": "x #Shorts", "description": f"hook\n\nFull video: https://youtu.be/{long_id}\nSubscribe: s"}


def test_relink_points_all_shorts_to_newest_long():
    vids = {"NEWLONG0001": {"description": "long"}, "SHORT000001": _short("OLDLONG0001"),
            "SHORT000002": _short("OLDLONG0002"), "SHORT000003": _short("NEWLONG0001"),
            "OLDLONG0001": {"description": "no link here"}}
    yt = FakeYT(vids, list(vids))
    n = uploader.relink_shorts(make_cfg(), "NEWLONG0001", yt=yt)
    assert n == 2 and sorted(yt.updates) == ["SHORT000001", "SHORT000002"]
    for k in ("SHORT000001", "SHORT000002", "SHORT000003"):
        assert "Full video: https://youtu.be/NEWLONG0001\nSubscribe: s" in vids[k]["description"]
    # only_from: vain tämän vanhan id:n linkit
    vids["SHORT000004"] = _short("OTHERLONG01")
    yt = FakeYT(vids, list(vids))
    assert uploader.relink_shorts(make_cfg(), "REPLACED001", only_from="NEWLONG0001", yt=yt) == 3
    assert "OTHERLONG01" in vids["SHORT000004"]["description"]


def _job(cfg, long_id, started, out):
    st = State(cfg.paths.db)
    day = time.strftime("%Y-%m-%d")
    st.job_begin("news", day)
    st.job_update("news", day, stage="done", long_id=long_id, output=str(out),
                  meta={"long_upload_started": started})
    return st, day


def test_stuck_video_is_reuploaded_relinked_and_deleted():
    cfg = make_cfg()
    d = tmpdir()
    out = d / "v.mp4"
    out.write_bytes(b"x")
    (d / "v_thumb1.jpg").write_bytes(b"j")
    out.with_suffix(".json").write_text(json.dumps({"title": "T", "description": "D", "thumbnails": ["v_thumb1.jpg"]}))
    st, day = _job(cfg, "STUCK000001", time.time() - 80 * 60, out)
    vids = {"STUCK000001": {"processing": "processing", "duration": "P0D", "privacy": "public"},
            "SHORT000001": _short("STUCK000001")}
    yt = FakeYT(vids, list(vids))
    calls = []

    def fake_upload(c, video, meta, thumb, publish_at=None):
        calls.append((c.upload.privacy, video.name, thumb.name if thumb else None, publish_at))
        vids["FRESH000001"] = {"description": "D"}
        yt.order.insert(0, "FRESH000001")
        return "FRESH000001"

    with patched(alerts, "send", lambda *a, **k: None):
        assert main.recover_stuck(cfg, yt=yt, st=st, upload_fn=fake_upload) == 1
        assert calls == [("public", "v.mp4", "v_thumb1.jpg", None)]
        assert st.job("news", day)["long_id"] == "FRESH000001"
        assert yt.deleted == ["STUCK000001"]
        assert "youtu.be/FRESH000001" in vids["SHORT000001"]["description"]
        # kerran per työ: ei uutta latausta seuraavalla tarkistuksella
        assert main.recover_stuck(cfg, yt=yt, st=st, upload_fn=fake_upload) == 0 and len(calls) == 1


def test_processed_or_young_video_is_left_alone():
    cfg = make_cfg()
    d = tmpdir()
    out = d / "v.mp4"
    out.write_bytes(b"x")
    st, day = _job(cfg, "GOOD0000001", time.time() - 80 * 60, out)
    yt = FakeYT({"GOOD0000001": {"processing": "succeeded", "duration": "PT14M31S"}}, ["GOOD0000001"])
    up = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ei saa ladata"))  # noqa: E731
    assert main.recover_stuck(cfg, yt=yt, st=st, upload_fn=up) == 0
    assert st.job("news", day)["meta"]["processed_ok"] is True
    assert main.stuck_candidates(cfg, st) == []  # ei enää API-tarkistuksia
    cfg2 = make_cfg()
    st2, _ = _job(cfg2, "YOUNG000001", time.time() - 10 * 60, out)
    assert main.stuck_candidates(cfg2, st2) == []  # alle stuck_minutes: ei vielä tarkisteta


def test_description_is_compact_with_timeline_chapters():
    cfg = make_cfg()
    arts = [{"source": "PUSHSQUARE", "url": "https://www.pushsquare.com/guides/very-long-url-here", "kind": "news"},
            {"source": "Creator X", "url": "https://youtu.be/abc", "kind": "transcript"},
            {"source": "IGN", "url": "https://www.ign.com/articles/x"},
            {"source": "PUSHSQUARE", "url": "https://www.pushsquare.com/news/y"}]
    script = {"video_title": "GTA 6: Test", "description": "Summary.", "comment_question": "Q?"}
    chapters = [(0.0, "Intro"), (76.0, "One"), (128.0, "Two")]
    src = {"title": "Trending video", "channel": "Somebody", "url": "u", "id": "i"}
    with patched(main, "_channel_links", lambda c: ""):
        d = main.build_description(cfg, script, arts, chapters, [0, 1, 2, 3], src, "long")
    assert "Chapters:" not in d and "\n\n0:00 Intro\n1:16 One\n2:08 Two\n\n" in d
    assert "Sources: Push Square, IGN\n" in d
    assert "http" not in d.split("Sources:")[1].split("\n")[0]
    assert "Creator X" not in d and "Topic trending" not in d and "Somebody" not in d


def test_topic_bar_highlights_current_and_skips_intro_panels_and_outro():
    from bot import render
    cfg = make_cfg()
    cfg["format"] = "long"
    d = tmpdir()
    subs = d / "subs.ass"
    subs.write_text("[Script Info]\n\n[V4+ Styles]\nFormat: Name\nStyle: Main,x\n\n[Events]\nFormat: Layer\n"
                    "Dialogue: 0,0:00:01.00,0:00:02.00,Main,,0,0,0,,HI\n", encoding="utf-8")
    chs = [(0, "Intro"), (10, "Cars"), (70, "Money"), (130, "Cops")]
    n = render.add_topic_bar(cfg, subs, chs, end=180, hide=[(80, 90)])
    txt = subs.read_text(encoding="utf-8")
    assert "Style: TopicBox" in txt and "Style: Topic," in txt and "INTRO" not in txt
    ev = [l for l in txt.splitlines() if l.startswith("Dialogue: 2,")]
    assert n == len(ev) * 2 and len(ev) == 4  # Cars, Money (paneeli välissä -> 2 palaa), Cops
    assert ev[0].startswith("Dialogue: 2,0:00:10.00,0:01:10.00") and ev[-1].split(",")[2] == "0:03:00.00"
    assert ev[1].split(",")[2] == "0:01:20.00" and ev[2].split(",")[1] == "0:01:30.00"
    assert "&HFFB42F&" in ev[0].split("CARS")[0]  # nykyinen aihe brändin sinisellä
    assert "CARS" in ev[0] and "MONEY" in ev[0]
    # kapea tila: nykyinen ja yksi edellinen mahtuvat aina ensin
    names = ["A VERY LONG TOPIC NAME HERE"] * 9
    a, z = render.topic_window(names, 4)
    assert a == 3 and z > 4


def test_music_dips_and_synth_sounds():
    import math
    import wave
    from bot import music, panels
    e = music.dip_expr([(60.0, 62.4, False), (300.0, 302.4, True)])
    # arvioidaan lauseke Pythonilla: if/lt -> ehtolauseet
    def ev(t):
        x = e.replace("if(", "_if(").replace("lt(", "_lt(")
        return eval(x, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "t": t})
    assert ev(10) == 1 and abs(ev(61) - music.DIP_DEPTH) < 1e-6 and ev(65) == 1
    assert ev(299) == 0 and ev(301) == 0 and ev(310) == 1  # lunastusosiossa tauko
    cfg = make_cfg()
    for f, peak in ((panels._hit(cfg), panels.HIT_PEAK_DB), (panels._riser(cfg), panels.RISER_PEAK_DB)):
        x = panels._read_wav(f)
        with wave.open(str(f)) as w:
            assert w.getframerate() == 48000 and w.getnchannels() == 1
        got = 20 * math.log10(float(abs(x).max()) / 32767)
        assert abs(got - peak) < 0.5, (f, got)


def test_trend_topic_prefers_trending_and_uses_strong_new_trend():
    cfg = make_cfg()
    topics = [{"topic": "The GTA 6 map", "keywords": ["map", "leonida"]},
              {"topic": "GTA 6 police", "keywords": ["police", "cops", "wanted"]},
              {"topic": "GTA 6 money", "keywords": ["money", "cash"]}]
    vids = [{"title": "GTA 6 police are INSANE", "description": "", "score": 900, "outlier": 2.0, "url": "u1"},
            {"title": "GTA 6 cops chase", "description": "", "score": 400, "outlier": 1.5, "url": "u2"},
            {"title": "GTA 6 full map", "description": "", "score": 100, "outlier": 1.0, "url": "u3"}]
    assert main.trend_topic(cfg, topics, set(), vids)["topic"] == "GTA 6 police"
    assert main.trend_topic(cfg, topics, {"GTA 6 police"}, vids)["topic"] == "The GTA 6 map"
    strong = [{"title": "GTA 6 secret ending found #shorts", "description": "", "score": 5000, "outlier": 8.0,
               "url": "u9"}] + vids
    t = main.trend_topic(cfg, topics, set(), strong)
    assert t["topic"] == "GTA 6 secret ending found" and t["trend"] == "u9" and "secret" in t["keywords"]
    assert main.trend_topic(cfg, topics, set(), []) is None


class _PLApi:
    """Valesoittolistat: listaus päivittyy viiveellä (kuten YouTube), id-haku heti."""
    def __init__(self):
        self.lists, self.listed, self.items = {}, {}, {}

    def playlists(self):
        return self

    def playlistItems(self):
        return _PLItems(self)

    def list(self, part, mine=None, maxResults=None, pageToken=None, id=None):
        if id is not None:
            return _Exec(lambda: {"items": [{"id": i} for i in id.split(",") if i in self.lists]})
        return _Exec(lambda: {"items": [{"id": i, "snippet": {"title": t}} for i, t in self.listed.items()]})

    def insert(self, part, body):
        def run():
            pid = f"PL{len(self.lists):04d}"
            self.lists[pid] = body["snippet"]["title"]  # listaukseen vasta myöhemmin (viive)
            return {"id": pid}
        return _Exec(run)


class _PLItems:
    def __init__(self, api):
        self.api = api

    def list(self, part, playlistId, maxResults=None, pageToken=None):
        return _Exec(lambda: {"items": [{"contentDetails": {"videoId": v}} for v in self.api.items.get(playlistId, [])]})

    def insert(self, part, body):
        sn = body["snippet"]
        return _Exec(lambda: self.api.items.setdefault(sn["playlistId"], []).insert(sn["position"], sn["resourceId"]["videoId"]))


def test_playlists_created_once_and_videos_go_first():
    from bot import playlists
    cfg = make_cfg()
    api = _PLApi()
    a = playlists.ensure(cfg, api)
    b = playlists.ensure(cfg, api)  # listaus ei vielä näytä uusia -> silti ei tuplia (tallennettu id)
    assert a == b and len(api.lists) == 2
    pid = playlists.add(cfg, api, "OLDVIDEO001", "long")
    playlists.add(cfg, api, "NEWVIDEO001", "long")
    playlists.add(cfg, api, "NEWVIDEO001", "long")  # ei kahdesti
    assert api.items[pid] == ["NEWVIDEO001", "OLDVIDEO001"] and pid == a["news"]
    assert playlists.add(cfg, api, "GUIDE000001", "guide") == a["guides"]
    assert playlists.watch_url("ABCDEFGHIJK", pid) == f"https://www.youtube.com/watch?v=ABCDEFGHIJK&list={pid}"


def test_topic_picker_matches_tags_and_avoids_repeats():
    from pathlib import Path
    from bot import footage, render
    assert {"map", "aerial"} <= footage.text_tags("How big is the map of Leonida?")
    assert "police" in footage.text_tags("The cops in GTA 6 react to sirens")
    cands = [{"kind": "video", "path": Path(f"v{i}.mp4"), "s": 0, "e": 6, "tags": set(t), "key": f"k{i}"}
             for i, t in enumerate([["map", "aerial"], ["police"], ["beach"], ["police", "chase"], ["city"]])]
    tp = render.TopicPicker(cands, [(0, 10, {"police"}), (10, 20, {"map"})], seed=1)
    a, b = tp.pick(0, 5), tp.pick(5, 5)
    assert {a["key"], b["key"]} == {"k1", "k3"}  # molemmat poliisikohtaukset, ei sama kahdesti
    assert tp.pick(12, 5)["key"] == "k0"
    assert len({tp.pick(20 + i, 5)["key"] for i in range(5)}) >= 2  # ei toistoa, kun aihetta ei ole
