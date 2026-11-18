"""Idempotentti uusinta: jokainen ajastettu työ (nimi, päivä) jatkaa siitä mitä puuttuu, eikä samaa videota ladata
koskaan kahdesti. Lataus, QA ja esitarkistus korvataan valeilla (ei YouTubea, ei renderöintiä)."""
from helpers import fake_output, make_cfg, patched

from bot import main, qa, uploader
from bot.state import State

DAY = "2026-11-17"


class FakeUploads:
    def __init__(self, fail_on: set | None = None, found: dict | None = None):
        self.calls, self.lookups = [], []
        self.fail_on = set(fail_on or ())
        self.found = dict(found or {})

    def upload(self, cfg, video, meta, thumb, publish_at=None, on_uploaded=None, **kw):
        name = video.name
        if name in self.fail_on:
            self.fail_on.discard(name)
            raise RuntimeError(f"verkkovirhe ladattaessa {name}")
        vid = f"ID{len(self.calls) + 1}"
        self.calls.append({"file": name, "title": meta["title"], "description": meta["description"],
                           "thumb": thumb.name if thumb else None, "id": vid})
        if on_uploaded:
            on_uploaded(vid)
        return vid

    def find(self, cfg, title, since, yt=None):
        self.lookups.append(title)
        return self.found.get(title)


def _no_render(*a, **k):
    raise AssertionError("tallennettu tuotos olisi pitänyt ladata ilman uudelleenrenderöintiä")


def _ok_qa(cfg, prod, mock, shorts_only=None):
    rep = qa.Report()
    rep.add("test", "ok", True)
    if shorts_only is not None:
        rep.notes.append(f"shorts_only={[s['path'].name for s in shorts_only]}")
    _ok_qa.last = rep
    return rep


def _run(cfg, fake, qa_fn=_ok_qa, pre=lambda c, youtube=True: []):
    with patched(uploader, "upload", fake.upload), patched(uploader, "find_recent_upload", fake.find), \
            patched(main, "produce", _no_render), patched(main, "run_qa", qa_fn):
        return main.run_job(cfg, "news", DAY, preflight_fn=pre)


def _setup(stage: str, **kw):
    cfg = make_cfg()
    fo = fake_output(cfg, n_shorts=2)
    st = State(cfg.paths.db)
    st.job_begin("news", DAY)
    st.job_update("news", DAY, stage=stage, output=str(fo["out"]), work_dir=str(fo["work"]), **kw)
    return cfg, fo, st


def test_long_uploaded_only_shorts_go_up_and_never_twice():
    cfg, fo, st = _setup("long_uploaded", long_id="LONG1")
    fake = FakeUploads()
    assert _run(cfg, fake) == main.EXIT_OK
    assert [c["file"] for c in fake.calls] == [p.name for p in fo["shorts"]], fake.calls
    assert all("https://youtu.be/LONG1" in c["description"] for c in fake.calls)
    assert "shorts_only" in _ok_qa.last.notes[0]
    row = st.job("news", DAY)
    assert row["stage"] == "done" and row["long_id"] == "LONG1"
    assert sorted(row["shorts"].values()) == ["ID1", "ID2"], row["shorts"]
    # uusi kutsu samalle päivälle: ei mitään
    fake2 = FakeUploads()
    assert _run(cfg, fake2) == main.EXIT_OK
    assert fake2.calls == [] and fake2.lookups == []


def test_rendered_uploads_saved_files_and_failed_short_retried_alone():
    cfg, fo, st = _setup("rendered")
    s1, s2 = (p.name for p in fo["shorts"])
    fake = FakeUploads(fail_on={s2})
    try:
        _run(cfg, fake)
        raise AssertionError("epäonnistuneen Shortsin pitäisi kaataa työ (uusintaa varten)")
    except RuntimeError as e:
        assert "Shortsia jäi lataamatta" in str(e), e
    assert [c["file"] for c in fake.calls] == [fo["out"].name, s1]
    assert fake.calls[0]["thumb"] == fo["thumbs"][0].name
    row = st.job("news", DAY)
    assert row["stage"] == "long_uploaded" and row["long_id"] == "ID1"
    assert row["shorts"] == {s1: "ID2", s2: main.PENDING}, row["shorts"]
    # uusinta: vain s2 (ensin tarkistus ehtikö se YouTubeen), pitkä ja s1 eivät uudelleen
    fake2 = FakeUploads()
    assert _run(cfg, fake2) == main.EXIT_OK
    assert [c["file"] for c in fake2.calls] == [s2], fake2.calls
    assert len(fake2.lookups) == 1 and fake2.lookups[0].endswith("#Shorts")
    row = st.job("news", DAY)
    assert row["stage"] == "done" and row["shorts"][s2] == "ID1"


def test_interrupted_long_upload_is_adopted_not_reuploaded():
    title = "GTA 6 Test Video: Everything We Know"  # helpers.fake_output
    cfg, fo, st = _setup("uploading_long", meta={"long_upload_started": 1.0e9, "long_title": title})
    fake = FakeUploads(found={fo["meta"]["title"]: "LONGX"})
    assert _run(cfg, fake) == main.EXIT_OK
    assert fo["out"].name not in [c["file"] for c in fake.calls], fake.calls
    assert [c["file"] for c in fake.calls] == [p.name for p in fo["shorts"]]
    assert all("youtu.be/LONGX" in c["description"] for c in fake.calls)
    assert st.job("news", DAY)["long_id"] == "LONGX"


def test_qa_failure_blocks_upload_and_keeps_files():
    cfg, fo, st = _setup("rendered")

    def bad_qa(cfg, prod, mock, shorts_only=None):
        rep = qa.Report()
        rep.add("video", "äänen keskitaso", False, "-80 dB")
        return rep

    fake = FakeUploads()
    assert _run(cfg, fake, qa_fn=bad_qa) == main.EXIT_QA
    assert fake.calls == []
    row = st.job("news", DAY)
    assert row["stage"] == "qa_failed" and "keskitaso" in row["error"]
    assert fo["out"].is_file() and all(p.is_file() for p in fo["shorts"])


def test_preflight_failure_stops_before_anything():
    cfg, fo, st = _setup("rendered")
    fake = FakeUploads()
    code = _run(cfg, fake, pre=lambda c, youtube=True: ["levytila: vain 1 Gt"])
    assert code == main.EXIT_PREFLIGHT and fake.calls == []
    row = st.job("news", DAY)
    assert row["stage"] == "rendered" and "levytila" in row["error"] and row["attempts"] == 2


def test_daily_summary_lists_links():
    cfg, fo, st = _setup("done", long_id="LONG1", shorts={"a_short1.mp4": "S1"})
    text = main.daily_summary(cfg, DAY)
    assert "news: done" in text and "youtu.be/LONG1" in text and "youtu.be/S1" in text, text
