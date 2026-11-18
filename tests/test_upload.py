"""Latauksen luotettavuus valeasiakkaalla (ei oikeaa YouTubea): palojen uusinta 5xx-virheissä, käsittelyn odotus,
hylätyn latauksen uusinta kerran, thumbnailin asetus + vahvistus uusintoineen, keskeytyneen latauksen löytäminen."""
import httplib2
from googleapiclient.errors import HttpError

from helpers import make_cfg, tmpdir

from bot import uploader


def _err(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error": "x"}')


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Progress:
    def progress(self):
        return 0.5


class FakeYT:
    """chunk_errors: virheet ennen onnistuvaa latausta (per lataus); statuses: {id: [uploadStatus, ...]};
    thumb_errors: thumbnails.set-virheet ennen onnistumista."""

    def __init__(self, chunk_errors=(), statuses=None, thumb_errors=(), thumbs=("default", "high", "maxres"),
                 playlist=()):
        self.chunk_errors = list(chunk_errors)
        self.statuses = {k: list(v) for k, v in (statuses or {}).items()}
        self.thumb_errors = list(thumb_errors)
        self.thumbs = thumbs
        self.playlist = list(playlist)
        self.inserted, self.next_chunks, self.thumb_sets, self.lists = [], 0, 0, []

    # --- videos
    def videos(self):
        return self

    def insert(self, part, body, media_body):
        vid = f"VID{len(self.inserted) + 1}"
        self.inserted.append({"id": vid, "body": body})
        steps = list(self.chunk_errors) + ["progress", "done"]
        fake = self

        class Req:
            def next_chunk(self_inner):
                fake.next_chunks += 1
                s = steps.pop(0)
                if isinstance(s, Exception):
                    raise s
                return (_Progress(), None) if s == "progress" else (None, {"id": vid})
        return Req()

    def list(self, part, id=None, mine=None, **kw):
        self.lists.append(part)
        if part == "snippet":
            return _Exec(lambda: {"items": [{"id": id, "snippet": {"thumbnails": {k: {} for k in self.thumbs}}}]})
        if part == "contentDetails":
            return _Exec(lambda: {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})

        def status():
            seq = self.statuses.get(id) or ["processed"]
            us = seq.pop(0) if len(seq) > 1 else seq[0]
            st = {"uploadStatus": us, "privacyStatus": "private"}
            if us == "rejected":
                st["rejectionReason"] = "duplicate"
            return {"items": [{"id": id, "status": st, "processingDetails": {"processingStatus": "processing"}}]}
        return _Exec(status)

    # --- thumbnails
    def thumbnails(self):
        return self

    def set(self, videoId, media_body):
        self.thumb_sets += 1

        def go():
            if self.thumb_errors:
                raise self.thumb_errors.pop(0)
            return {"items": [{"default": {"url": "x"}}]}
        return _Exec(go)

    # --- find_recent_upload
    def channels(self):
        return self

    def playlistItems(self):  # noqa: N802
        fake = self

        class P:
            def list(self_inner, **kw):
                return _Exec(lambda: {"items": fake.playlist})
        return P()


def _files():
    d = tmpdir()
    v = d / "video.mp4"
    v.write_bytes(b"\x00" * 1024)
    t = d / "thumb.jpg"
    t.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return v, t


META = {"title": "GTA 6 Test", "description": "desc", "tags": ["a"]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_chunk_retry_on_5xx_then_processed_and_thumbnail_retry():
    cfg = make_cfg()
    v, t = _files()
    yt = FakeYT(chunk_errors=[_err(503), ConnectionResetError("reset")], statuses={"VID1": ["uploaded", "processed"]},
                thumb_errors=[_err(500)])
    c = Clock()
    got = []
    vid = uploader.upload(cfg, v, META, t, yt=yt, sleep=c.sleep, clock=c, on_uploaded=got.append)
    assert vid == "VID1" and got == ["VID1"]
    assert yt.next_chunks == 4 and len(yt.inserted) == 1
    assert yt.thumb_sets == 2, yt.thumb_sets
    assert yt.lists.count("status,processingDetails") == 2


def test_chunk_4xx_not_retried_and_too_many_errors():
    cfg = make_cfg()
    v, t = _files()
    yt = FakeYT(chunk_errors=[_err(403)])
    try:
        uploader.upload(cfg, v, META, None, yt=yt, sleep=lambda s: None)
        raise AssertionError("403 olisi pitänyt nostaa")
    except HttpError:
        pass
    assert yt.next_chunks == 1
    yt = FakeYT(chunk_errors=[_err(500)] * 11)
    try:
        uploader.upload(cfg, v, META, None, yt=yt, sleep=lambda s: None)
        raise AssertionError("UploadError puuttuu")
    except uploader.UploadError as e:
        assert "10 uusinnan" in str(e)
    assert yt.next_chunks == 11


def test_rejected_upload_is_retried_once():
    cfg = make_cfg()
    v, t = _files()
    yt = FakeYT(statuses={"VID1": ["rejected"], "VID2": ["processed"]})
    got = []
    c = Clock()
    vid = uploader.upload(cfg, v, META, t, yt=yt, sleep=c.sleep, clock=c, on_uploaded=got.append)
    assert vid == "VID2" and got == ["VID1", "VID2"] and len(yt.inserted) == 2
    yt = FakeYT(statuses={"VID1": ["failed"], "VID2": ["rejected"]})
    try:
        uploader.upload(cfg, v, META, None, yt=yt, sleep=c.sleep, clock=c)
        raise AssertionError("toinen hylkäys olisi pitänyt nostaa")
    except uploader.UploadError as e:
        assert "myös uusinnassa" in str(e)
    assert len(yt.inserted) == 2


def test_processing_wait_has_limit_and_thumbnail_unconfirmed():
    cfg = make_cfg()
    cfg["upload"] = dict(cfg["upload"], verify_minutes=1)
    v, t = _files()
    yt = FakeYT(statuses={"VID1": ["uploaded"]}, thumbs=("default",))
    c = Clock()
    vid = uploader.upload(cfg, v, META, t, yt=yt, sleep=c.sleep, clock=c)
    assert vid == "VID1"
    assert 60 <= c.t < 400, c.t  # 1 min odotus + thumbnailin uusinnat, ei ikuista silmukkaa
    assert yt.thumb_sets == 3  # ei vahvistusta (ei high/maxres) -> 3 yritystä
    yt = FakeYT(thumb_errors=[_err(403)])
    assert uploader.set_thumbnail(yt, "VID1", t, sleep=lambda s: None) is False and yt.thumb_sets == 1


def test_find_recent_upload():
    cfg = make_cfg()
    items = [{"snippet": {"title": "GTA 6 Test", "publishedAt": "2026-11-17T02:40:00Z",
                          "resourceId": {"videoId": "OLD"}}},
             {"snippet": {"title": "Other", "publishedAt": "2026-11-17T03:00:00Z", "resourceId": {"videoId": "X"}}}]
    yt = FakeYT(playlist=items)
    import datetime as dt
    since = dt.datetime(2026, 11, 17, 2, 30, tzinfo=dt.timezone.utc).timestamp()
    assert uploader.find_recent_upload(cfg, "GTA 6 Test", since, yt=yt) == "OLD"
    assert uploader.find_recent_upload(cfg, "GTA 6 Test", since + 3600, yt=yt) is None
    assert uploader.find_recent_upload(cfg, "Missing", since, yt=yt) is None


class _VideosPriv:
    """videos().list/update: aluksi private, update vaihtaa public (fail_updates kertaa virhe ensin)."""
    def __init__(self, fail_updates=0, stuck=False):
        self.priv, self.fail, self.stuck, self.updates = "private", fail_updates, stuck, 0

    def list(self, part, id):
        return _Exec(lambda: {"items": [{"id": id, "status": {"privacyStatus": self.priv,
                                                             "selfDeclaredMadeForKids": False}}]})

    def update(self, part, body):
        def run():
            self.updates += 1
            assert body["status"]["privacyStatus"] == "public" and "publishAt" not in body["status"]
            if self.fail > 0:
                self.fail -= 1
                raise _err(500)
            if not self.stuck:
                self.priv = "public"
            return body
        return _Exec(run)


class _YTPriv:
    def __init__(self, v):
        self.v = v

    def videos(self):
        return self.v


def test_ensure_public_switches_private_to_public():
    from bot import alerts
    sent = []
    old = alerts.send
    alerts.send = lambda *a, **k: sent.append(a)
    try:
        assert uploader.ensure_public(None, "X", {"privacyStatus": "public"}) is True  # jo julkinen: ei kutsuja
        v = _VideosPriv(fail_updates=1)
        assert uploader.ensure_public(_YTPriv(v), "X", {"privacyStatus": "private"}, sleep=lambda s: None) is True
        assert v.priv == "public" and v.updates == 2 and not sent
        v = _VideosPriv(stuck=True)
        assert uploader.ensure_public(_YTPriv(v), "X", {"privacyStatus": "private"}, sleep=lambda s: None) is False
        assert sent  # hälytys omistajalle
    finally:
        alerts.send = old


def test_service_http_does_not_treat_308_as_redirect():
    """Jatkettava lataus (308 Resume Incomplete) ei saa olla httplib2:n uudelleenohjaus (RedirectMissingLocation)."""
    import json
    d = tmpdir()
    (d / "token.json").write_text(json.dumps({"token": "x", "refresh_token": "r", "client_id": "c",
                                              "client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token",
                                              "expiry": "2999-01-01T00:00:00Z"}))
    yt = uploader._service(str(d))
    raw = yt._http.http
    assert 308 not in raw.redirect_codes and 302 in raw.redirect_codes
